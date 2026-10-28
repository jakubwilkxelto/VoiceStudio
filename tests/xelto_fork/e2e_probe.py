"""End-to-end work in strict multi-user mode on the real app, with fake models.

Run by test_e2e_strict.py in a subprocess. alice generates, clones a voice,
dubs a clip, runs a batch job and renders an audiobook through the real
routes; after each, alice can fetch what she made and bob cannot. Every
log record about lost identities or unowned files is collected: each one is
a producer the fork does not yet account for. Prints one JSON object.

The fakes stand in for the models only (engine, ASR, demucs, licences); the
routes, task queue, workers and files are upstream's own. They are the ones
upstream's tests use: tests/test_dub_batch_engine_selection.py,
tests/test_dub_transcribe.py and tests/conftest.py.
"""
import io
import json
import logging
import math
import os
import re
import struct
import subprocess
import time
import wave

os.environ["OMNIVOICE_MULTIUSER_FILES"] = "strict"
os.environ["OMNIVOICE_MULTIUSER_AUDIT"] = "1"
os.environ["OMNIVOICE_TTS_BACKEND"] = "fake"
os.environ["OMNIVOICE_PRELOAD_WATERMARK"] = "0"
os.environ["OMNIVOICE_DISABLE_GPU_INVENTORY"] = "1"
os.environ["OMNIVOICE_PRELOAD_CAPTURE_ASR"] = "0"

# ── What the fork must never log during ordinary work ──────────────────────

_TROUBLE = re.compile(r"xelto (audit|file guard)|no identity|lost its identity|has no owner")


class Collector(logging.Handler):
    def __init__(self):
        super().__init__(logging.WARNING)
        self.records: list[str] = []

    def emit(self, record):
        message = record.getMessage()
        if _TROUBLE.search(message):
            self.records.append(f"{record.name}: {message.splitlines()[0]}")


collector = Collector()
# omnivoice loggers may not propagate to the root; a record seen twice counts once.
collector.addFilter(lambda record, seen=set(): id(record) not in seen and not seen.add(id(record)))


def attach():
    """Before the app is imported (its build logs too) and again after it set up logging."""
    for logger in (logging.getLogger(), logging.getLogger("omnivoice")):
        if collector not in logger.handlers:
            logger.addHandler(collector)


attach()

from real_app_setup import PUBLIC_URL, boot, wait_healthy  # noqa: E402

main, idp, users = boot({"alice": "voicestudio-user", "bob": "voicestudio-user"})
alice, bob = users["alice"], users["bob"]

attach()

import torch  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

# ── Fake models ─────────────────────────────────────────────────────────────

from services import tts_backend  # noqa: E402


class FakeEngine(tts_backend.TTSBackend):
    id = "fake"
    display_name = "Fake engine (test)"
    supports_cloning = True
    applies_own_mastering = False
    gpu_compat = ("cpu",)
    calls: list = []

    @property
    def sample_rate(self) -> int:
        return 24000

    @property
    def supported_languages(self) -> list[str]:
        return ["multi"]

    @classmethod
    def is_available(cls):
        return True, "ready"

    def generate(self, text, **kw) -> torch.Tensor:
        type(self).calls.append((text, {k: v for k, v in kw.items() if isinstance(v, (str, int, float))}))
        return torch.full((1, 24000), 0.25)


tts_backend._REGISTRY["fake"] = FakeEngine
tts_backend.reset_active_backend()


class FakeASR:
    id = "fake"

    def ensure_loaded(self):
        pass

    def transcribe(self, path, *, word_timestamps=True, **kw):
        return {"chunks": [{"text": "hello there", "timestamp": (0.0, 0.8)}],
                "segments": [], "language": "en"}

    def unload(self):
        pass


from services import asr_backend, model_acceptance  # noqa: E402

model_acceptance.ensure_accepted = lambda *a, **k: None
asr_backend.asr_model_missing_error = lambda *a, **k: None
asr_backend.transcribe_reference = lambda *a, **k: "hello there"
asr_backend.get_active_asr_backend = lambda *a, **k: FakeASR()

from services import endpoint_race  # noqa: E402

endpoint_race.probe_endpoint = lambda *a, **k: None

from services import dub_pipeline  # noqa: E402


async def _fake_demucs(job_id, cmd, **kw):
    """Vocal separation without the model (which would be downloaded).

    Writes both stems where ``demucs.separate -o <dir>`` would: copies of the
    input, under htdemucs/<input name>/.
    """
    import shutil

    source, out_dir = cmd[cmd.index("-o") - 1], cmd[cmd.index("-o") + 1]
    stems = os.path.join(out_dir, "htdemucs", os.path.splitext(os.path.basename(source))[0])
    os.makedirs(stems, exist_ok=True)
    for name in ("vocals.wav", "no_vocals.wav"):
        shutil.copyfile(source, os.path.join(stems, name))
    yield ("done", 0, "")


dub_pipeline.run_proc_streaming_stderr = _fake_demucs
if hasattr(endpoint_race, "throughput_probe"):
    endpoint_race.throughput_probe = lambda *a, **k: None

# ── Media ──────────────────────────────────────────────────────────────────


def tone_wav(seconds=1.5, rate=24000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(
            struct.pack("<h", int(8000 * math.sin(2 * math.pi * 220 * i / rate)))
            for i in range(int(seconds * rate))))
    return buf.getvalue()


def ffmpeg() -> str | None:
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def tiny_mp4(path) -> bool:
    exe = ffmpeg()
    if exe is None:
        return False
    subprocess.run([exe, "-y", "-loglevel", "error",
                    "-f", "lavfi", "-i", "color=c=black:s=64x64:d=2",
                    "-f", "lavfi", "-i", "sine=frequency=220:duration=2",
                    "-shortest", "-c:v", "libx264", "-c:a", "aac", str(path)], check=True)
    return True


# ── Helpers ────────────────────────────────────────────────────────────────


def bearer(user):
    return {"authorization": f"Bearer {idp.access_token(user)}"}


def both(client, path):
    """(alice's status, bob's status) for fetching ``path``."""
    return [client.get(path, headers=bearer(alice)).status_code,
            client.get(path, headers=bearer(bob)).status_code]


out = {"ffmpeg": ffmpeg() is not None}
with TestClient(main.app, base_url=PUBLIC_URL, raise_server_exceptions=False) as client:
    out["health"] = wait_healthy(client)
    ha = bearer(alice)
    # The collector must see the fork's warnings, or an empty list proves nothing.
    logging.getLogger("omnivoice.xelto.db").warning("xelto audit: collector self-check")
    check = "omnivoice.xelto.db: xelto audit: collector self-check"
    out["collector_works"] = collector.records.count(check) == 1
    collector.records.remove(check)

    # 1. Generation.
    generated = client.post("/generate", headers=ha, data={"text": "Hello from alice.", "engine": "fake"})
    out["generate"] = generated.status_code
    audio_id = generated.headers.get("x-audio-id", "")
    out["generate_file"] = both(client, f"/audio/{audio_id}.wav") if audio_id else None
    history = client.get("/history", headers=ha).json()
    out["history_alice"] = len(history if isinstance(history, list) else history.get("items", []))
    history_b = client.get("/history", headers=bearer(bob)).json()
    out["history_bob"] = len(history_b if isinstance(history_b, list) else history_b.get("items", []))

    # 2. Cloning a voice, then speaking with it.
    created = client.post("/profiles", headers=ha,
                          data={"name": "Alice clone", "kind": "clone", "ref_text": "hello there"},
                          files={"ref_audio": ("ref.wav", tone_wav(), "audio/wav")})
    out["clone"] = created.status_code
    profile = created.json() if created.status_code < 300 else {}
    profile_id = profile.get("id", "")
    out["clone_profile_bob"] = client.get(f"/profiles/{profile_id}", headers=bearer(bob)).status_code
    ref = (client.get(f"/profiles/{profile_id}", headers=ha).json() or {}).get("ref_audio_path", "")
    out["clone_file"] = both(client, f"/voice_audio/{os.path.basename(ref)}") if ref else None
    spoken = client.post("/generate", headers=ha,
                         data={"text": "Cloned.", "engine": "fake", "profile_id": profile_id})
    out["generate_cloned"] = spoken.status_code
    FakeEngine.calls.clear()
    client.post("/generate", headers=bearer(bob),
                data={"text": "Stolen.", "engine": "fake", "profile_id": profile_id})
    # bob's request may succeed with the default voice, never with alice's reference.
    out["bob_used_alices_voice"] = any(
        profile_id in json.dumps(kw) or os.path.basename(ref or "?") in json.dumps(kw)
        for _, kw in FakeEngine.calls)

    def stream(task_id):
        """Follow a task to its end; returns the event types seen."""
        with client.stream("GET", f"/tasks/stream/{task_id}", headers=ha) as response:
            if response.status_code != 200:
                return [f"http {response.status_code}"]
            return [json.loads(line[5:]).get("type", "?") for line in response.iter_lines()
                    if line.startswith("data:")][-3:]

    # 3. Dubbing: upload, prepare, transcribe, generate, download.
    if out["ffmpeg"]:
        from core.config import DATA_DIR

        clip = os.path.join(DATA_DIR, "clip.mp4")
        tiny_mp4(clip)
        with open(clip, "rb") as f:
            uploaded = client.post("/dub/upload", headers=ha,
                                   files={"video": ("clip.mp4", f.read(), "video/mp4")})
        out["dub_upload"] = uploaded.status_code
        job = uploaded.json() if uploaded.status_code < 300 else {}
        job_id = job.get("job_id", "")
        out["dub_prepare"] = stream(job.get("task_id", ""))
        transcribed = client.post(f"/dub/transcribe/{job_id}", headers=ha)
        out["dub_transcribe"] = transcribed.status_code
        segments = [{"start": s.get("start", 0), "end": s.get("end", 1), "text": s.get("text") or "hello"}
                    for s in (transcribed.json().get("segments", []) if transcribed.status_code < 300 else [])]
        generated = client.post(f"/dub/generate/{job_id}", headers=ha, json={
            "segments": segments or [{"start": 0, "end": 1, "text": "hello"}],
            "language": "English", "language_code": "en"})
        out["dub_generate"] = generated.status_code
        if generated.status_code < 300 and generated.json().get("task_id"):
            out["dub_generate_task"] = stream(generated.json()["task_id"])
        out["dub_download"] = both(client, f"/dub/download/{job_id}")
        out["dub_download_audio"] = both(client, f"/dub/download-audio/{job_id}")
        out["dub_media"] = both(client, f"/dub/media/{job_id}")

        # 4. Batch dubbing into the source language (no translation needed).
        with open(clip, "rb") as f:
            queued = client.post("/batch/enqueue", headers=ha, data={"langs": "en"},
                                 files={"video": ("clip.mp4", f.read(), "video/mp4")})
        out["batch_enqueue"] = queued.status_code
        batch_id = (queued.json() if queued.status_code < 300 else {}).get("job_id", "")
        deadline = time.monotonic() + 120
        status = ""
        while time.monotonic() < deadline:
            status = client.get(f"/batch/jobs/{batch_id}", headers=ha).json().get("status", "")
            if status in ("done", "failed", "error", "cancelled"):
                break
            time.sleep(0.5)
        out["batch_status"] = status
        out["batch_download"] = both(client, f"/batch/download/{batch_id}/en")

    # 5. Longform: an audiobook rendered over SSE.
    with client.stream("POST", "/audiobook", headers=ha,
                       json={"text": "Chapter one. It was a quiet night.", "format": "mp3"}) as response:
        out["audiobook"] = response.status_code
        events = [json.loads(line[5:]) for line in response.iter_lines() if line.startswith("data:")]
    done = next((e for e in reversed(events) if e.get("output")), {})
    out["audiobook_events"] = [e.get("type") for e in events][-3:]
    out["audiobook_file"] = both(client, f"/audio/{done['output']}") if done.get("output") else None

    out["trouble"] = collector.records

print(json.dumps(out))
