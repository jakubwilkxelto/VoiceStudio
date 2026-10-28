"""Per-user files, tasks, batch jobs, bundles and events on the real app.

Run by test_isolation_runtime.py in a subprocess. alice owns one of each; bob
tries to reach them through the real routes. Prints one JSON object.
"""
import json
import os
import time
import uuid

from real_app_setup import PUBLIC_URL, boot, wait_healthy

main, idp, users = boot({"alice": "voicestudio-user", "bob": "voicestudio-user"})
alice, bob = users["alice"], users["bob"]

from fastapi.testclient import TestClient  # noqa: E402

from core import db, event_bus, job_store  # noqa: E402
from core.config import DUB_DIR, OUTPUTS_DIR, PREVIEW_DIR, VOICES_DIR  # noqa: E402
from core.tasks import task_manager  # noqa: E402
from services import dub_pipeline  # noqa: E402
from xelto import resources  # noqa: E402
from xelto.identity import Identity, use_identity  # noqa: E402

GEN = "a1b2c3d4"
LONGFORM = "xa-longform"
DUB = "xa-dub"


def bearer(user):
    return {"authorization": f"Bearer {idp.access_token(user)}"}


def as_user(user):
    return use_identity(Identity(sub=user.sub, username=user.username))


def write(path, data=b"RIFF"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return path


def seed_alice():
    from api.routers import batch, project_bundles

    now = time.time()
    write(os.path.join(OUTPUTS_DIR, f"{GEN}.wav"))
    write(os.path.join(VOICES_DIR, "xa-voice.wav"))
    write(os.path.join(PREVIEW_DIR, "xa-clip.mp4"))
    write(os.path.join(OUTPUTS_DIR, f"audiobook_{LONGFORM}", "resume.json"), b"{}")
    write(os.path.join(OUTPUTS_DIR, "stray.wav"))
    write(os.path.join(DUB_DIR, DUB, "dubbed.mp4"))
    with as_user(alice):
        with db.db_conn() as conn:
            conn.execute("INSERT INTO generation_history(id, text, audio_path, created_at) "
                         "VALUES ('xa-gen', 'hi', ?, ?)", (f"{GEN}.wav", now))
            conn.execute("INSERT INTO voice_profiles(id, name, ref_audio_path, created_at) "
                         "VALUES ('xa-voice', 'Alice voice', 'xa-voice.wav', ?)", (now,))
            conn.execute("INSERT INTO dub_history(id, filename, job_data, created_at) "
                         "VALUES (?, 'a.mp4', '{}', ?)", (DUB, now))
        job_store.create(LONGFORM, type="audiobook")
        job_store.create("xa-task", type="tts")
        resources.claim("preview/xa-clip.mp4", alice.sub)
        output = write(os.path.join(os.path.dirname(db.DB_PATH), "batch", "xa-batch-de.mp4"))
        batch._jobs["xa-batch"] = {"id": "xa-batch", "status": "done", "filename": "a.mp4",
                                   "langs": ["de"], "created_at": now, "outputs": {"de": output}}
        bundle = str(uuid.uuid4())
        directory = project_bundles._directory(bundle)
        directory.mkdir()
        (directory / "index.json").write_text(json.dumps(
            {"id": bundle, "title": "Alice bundle", "kind": "stories", "createdAt": now,
             "incoming": False}))
        (directory / "project.vsproject").write_bytes(b"PK")
    # A live task that is not in the jobs table (the old bypass).
    task_manager.active_tasks["xa-live"] = {
        "status": "running", "type": "tts", "history": [], "listeners": [],
        "xelto": Identity(sub=alice.sub, username=alice.username)}
    return bundle


def first_kind(ws):
    return json.loads(ws.receive_text())["kind"]


out = {}
with TestClient(main.app, base_url=PUBLIC_URL, raise_server_exceptions=False) as client:
    out["health"] = wait_healthy(client)
    bundle = seed_alice()
    ha, hb = bearer(alice), bearer(bob)
    reads = {
        "audio_file": f"/audio/{GEN}.wav",
        "audio_quality": f"/audio/{GEN}/quality",
        "audio_ogg": f"/audio/{GEN}.ogg",
        "longform_dir": f"/audio/audiobook_{LONGFORM}/resume.json",
        "voice_file": "/voice_audio/xa-voice.wav",
        "preview": "/preview/xa-clip.mp4",
        "task_stream": "/tasks/stream/xa-task",
        "live_task_stream": "/tasks/stream/xa-live",
        "batch_job": "/batch/jobs/xa-batch",
        "batch_download": "/batch/download/xa-batch/de",
        "bundle": f"/project-bundles/{bundle}",
        "dub_media": f"/dub/media/{DUB}",
    }
    out["bob_reads"] = {k: client.get(path, headers=hb).status_code for k, path in reads.items()}
    out["bob_writes"] = {
        "cancel_live_task": client.post("/tasks/cancel/xa-live", headers=hb).status_code,
        "batch_cancel": client.post("/batch/jobs/xa-batch/cancel", headers=hb).status_code,
        "batch_delete": client.delete("/batch/jobs/xa-batch", headers=hb).status_code,
        "resume": client.post(f"/audiobook/resume/{LONGFORM}", headers=hb, json={}).status_code,
        "dub_delete": client.delete(f"/dub/history/{DUB}", headers=hb).status_code,
    }
    out["alice_dub_dir_kept"] = os.path.isdir(os.path.join(DUB_DIR, DUB))
    out["stray_for_bob"] = client.get("/audio/stray.wav", headers=hb).status_code
    out["bob_lists"] = {
        "batch": client.get("/batch/jobs", headers=hb).json(),
        "bundles": client.get("/project-bundles", headers=hb).json(),
        "audiobook": client.get("/audiobook/jobs", headers=hb).json(),
        "voices": client.get("/v1/audio/voices", headers=hb).text,
    }
    out["alice_reads"] = {
        k: client.get(reads[k], headers=ha).status_code
        for k in ("audio_file", "longform_dir", "voice_file", "preview", "batch_job",
                  "batch_download", "bundle")
    }
    out["alice_lists"] = {
        "batch": [j["id"] for j in client.get("/batch/jobs", headers=ha).json()],
        "bundles": [b["id"] for b in client.get("/project-bundles", headers=ha).json()],
        "audiobook": [j["job_id"] for j in client.get("/audiobook/jobs", headers=ha).json()["jobs"]],
        "voices": client.get("/v1/audio/voices", headers=ha).text,
    }
    out["bundle"] = bundle

    # TestClient's WebSocket sends Host "testserver", which upstream's host check refuses.
    origin = {"origin": PUBLIC_URL, "host": "studio.test"}
    with client.websocket_connect("/ws/events", headers={**ha, **origin}) as wa, \
            client.websocket_connect("/ws/events", headers={**hb, **origin}) as wb:
        time.sleep(0.5)  # both subscribed
        with as_user(alice):
            event_bus.emit("profiles", {"id": "xa-voice"})
        event_bus.emit("model_status", {"sub_stage": "ready"})
        out["alice_events"] = [first_kind(wa), first_kind(wa)]
        out["bob_first_event"] = first_kind(wb)

    task_manager.active_tasks.pop("xa-live", None)

print(json.dumps(out))
