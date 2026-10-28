"""The full isolation matrix on the real app: every per-user route bob can name alice's work with.

Run by test_isolation_matrix.py in a subprocess. alice owns one of everything;
bob calls every USER route that takes a path parameter with alice's ids, then
the routes that take ids or file paths in their body. Afterwards alice's rows
and files must be exactly as they were. Prints one JSON object.
"""
import io
import json
import os
import re
import sys
import time
import uuid
import zipfile

from real_app_setup import PUBLIC_URL, REPO, boot, wait_healthy

main, idp, users = boot({"alice": "voicestudio-user", "bob": "voicestudio-user"})
alice, bob = users["alice"], users["bob"]

from fastapi.testclient import TestClient  # noqa: E402

from core import db, event_bus, job_store  # noqa: E402
from core.config import DUB_DIR, OUTPUTS_DIR, PREVIEW_DIR, VOICES_DIR  # noqa: E402
from core.tasks import task_manager  # noqa: E402
from xelto import resources  # noqa: E402
from xelto.events import USER_KINDS  # noqa: E402
from xelto.identity import Identity, system_scope, use_identity  # noqa: E402
from xelto.policy import Owner, RouteClass, classify, path_owner  # noqa: E402

sys.path.insert(0, str(REPO / "scripts" / "xelto"))
from dump_unclassified_routes import snapshot_routes  # noqa: E402

SECRET = "ALICE-SECRET"
GEN = "a1b2c3d4"
VOICE = "xa-voice"
DUB = "xa-dub"
LONGFORM = "xa-longform"
PROJECT = "xa-proj"
TERM = "xa-term"
EXPORT = "xa-exp"
BATCH = "xa-batch"
LIVE_TASK = "xa-live"


def bearer(user):
    return {"authorization": f"Bearer {idp.access_token(user)}"}


def as_user(user):
    return use_identity(Identity(sub=user.sub, username=user.username))


def write(path, data=b"RIFF"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return path


FILES = {
    "generation": os.path.join(OUTPUTS_DIR, f"{GEN}.wav"),
    "voice": os.path.join(VOICES_DIR, f"{VOICE}.wav"),
    "portrait": os.path.join(VOICES_DIR, f"{VOICE}.portrait.jpg"),
    "preview": os.path.join(PREVIEW_DIR, "xa-clip.mp4"),
    "longform": os.path.join(OUTPUTS_DIR, f"audiobook_{LONGFORM}.m4b"),
    "longform_dir": os.path.join(OUTPUTS_DIR, f"audiobook_{LONGFORM}", "resume.json"),
    "dub_source": os.path.join(DUB_DIR, DUB, "original.mp4"),
}


def seed_alice():
    from api.routers import batch, project_bundles

    now = time.time()
    for path in FILES.values():
        write(path)
    with as_user(alice):
        with db.db_conn() as conn:
            conn.execute("INSERT INTO generation_history(id, text, audio_path, created_at) "
                         "VALUES ('xa-gen', ?, ?, ?)", (SECRET, f"{GEN}.wav", now))
            conn.execute("INSERT INTO voice_profiles(id, name, ref_audio_path, created_at) "
                         "VALUES (?, ?, ?, ?)", (VOICE, SECRET, f"{VOICE}.wav", now))
            conn.execute("INSERT INTO dub_history(id, filename, job_data, created_at) "
                         "VALUES (?, ?, '{}', ?)", (DUB, SECRET, now))
            conn.execute("INSERT INTO studio_projects(id, name, state_json, created_at, updated_at) "
                         "VALUES (?, ?, '{}', ?, ?)", (PROJECT, SECRET, now, now))
            conn.execute("INSERT INTO glossary_terms(id, project_id, source, target, created_at) "
                         "VALUES (?, ?, ?, 'x', ?)", (TERM, PROJECT, SECRET, now))
            conn.execute("INSERT INTO export_history(id, filename, created_at) VALUES (?, ?, ?)",
                         (EXPORT, SECRET, now))
        job_store.create(LONGFORM, type="audiobook")
        resources.claim("preview/xa-clip.mp4", alice.sub)
        output = write(os.path.join(os.path.dirname(db.DB_PATH), "batch", "xa-batch-de.mp4"))
        batch._jobs[BATCH] = {"id": BATCH, "status": "done", "filename": "a.mp4",
                              "langs": ["de"], "created_at": now, "outputs": {"de": output}}
        bundle = str(uuid.uuid4())
        directory = project_bundles._directory(bundle)
        directory.mkdir()
        (directory / "index.json").write_text(json.dumps(
            {"id": bundle, "title": SECRET, "kind": "stories", "createdAt": now, "incoming": False}))
        (directory / "project.vsproject").write_bytes(b"PK")
        (directory / "result.json").write_text(json.dumps({"title": SECRET}))
    task_manager.active_tasks[LIVE_TASK] = {
        "status": "running", "type": "tts", "history": [], "listeners": [],
        "xelto": Identity(sub=alice.sub, username=alice.username)}
    return bundle


def alice_state():
    """Everything of alice's that bob's calls might change."""
    with system_scope(), db.db_conn() as conn:
        rows = {
            table: [tuple(r) for r in conn.execute(f"SELECT * FROM {table} WHERE id = ?", (row_id,))]
            for table, row_id in (("generation_history", "xa-gen"), ("voice_profiles", VOICE),
                                  ("dub_history", DUB), ("studio_projects", PROJECT),
                                  ("glossary_terms", TERM), ("export_history", EXPORT),
                                  ("jobs", LONGFORM))
        }
    files = {name: os.path.isfile(path) for name, path in FILES.items()}
    live = task_manager.active_tasks.get(LIVE_TASK, {}).get("xelto")
    return {"rows": rows, "files": files, "live_task_owner": getattr(live, "sub", None)}


def fill(template, owner, bundle):
    """The route's URL with alice's ids in it."""
    by_owner = {
        "dub": {"job_id": DUB, "history_id": DUB},
        "profile": {"profile_id": VOICE},
        "batch": {"job_id": BATCH, "lang": "de"},
        "bundle": {"identifier": bundle},
        "task": {"task_id": LIVE_TASK},
        "job": {"job_id": LONGFORM},
        "file": {"audio_id": GEN, "filename": "xa-clip.mp4"},
        "db-view": {"project_id": PROJECT, "term_id": TERM, "history_id": "xa-gen",
                        "export_id": EXPORT, "job_id": LONGFORM},
    }[owner.value]
    return re.sub(r"{(\w+)(?::\w+)?}", lambda m: by_owner.get(m.group(1), "0"), template)


# Routes bob's call could reach the network through (an LLM provider).
SKIP = {("POST", "/glossary/{project_id}/auto-extract")}


def first_kind(ws):
    return json.loads(ws.receive_text())["kind"]


out = {}
with TestClient(main.app, base_url=PUBLIC_URL, raise_server_exceptions=False) as client:
    out["health"] = wait_healthy(client)
    bundle = seed_alice()
    ha, hb = bearer(alice), bearer(bob)
    before = alice_state()

    # 1. Every USER route with a path parameter, alice's ids filled in.
    gated, via_views, leaks = {}, {}, {}
    for method, template in sorted(snapshot_routes()):
        if "{" not in template or (method, template) in SKIP:
            continue
        if classify(method, template)[0] is not RouteClass.USER:
            continue
        owner = path_owner(method, template)
        if owner in (None, Owner.SHARED):
            continue
        url = fill(template, owner, bundle)
        response = client.request(method, url, headers=hb,
                                  json=None if method in ("GET", "DELETE") else {})
        key = f"{method} {template}"
        if owner is Owner.DB_VIEW:
            via_views[key] = response.status_code
        else:
            gated[key] = response.status_code
        if SECRET in response.text:
            leaks[key] = response.status_code
    out["gated"] = gated
    out["via_views"] = via_views
    out["leaks"] = leaks

    # 2. Ids and paths in request bodies.
    out["ingest_url"] = client.post("/dub/ingest-url", headers=hb,
                                    json={"url": "https://example.invalid/v", "job_id": DUB}).status_code
    out["translate"] = client.post("/dub/translate", headers=hb,
                                   json={"job_id": DUB, "segments": [], "target_lang": "de"}).status_code
    def exported_assets(headers, project):
        response = client.post("/project-bundles/export", headers=headers,
                               json={"kind": "stories", "project": project})
        if response.status_code != 200:
            return response.status_code
        names = zipfile.ZipFile(io.BytesIO(response.content)).namelist()
        return sorted(n for n in names if n.startswith("assets/"))

    # A field naming a file: a missing or foreign one is dropped from the bundle.
    named = {"name": "b", "draft": {"title": "b", "output": f"{GEN}.wav"}}
    out["bundle_named_bob"] = exported_assets(hb, named)
    out["bundle_named_alice"] = exported_assets(ha, named)
    # Retained handoff assets: a missing or foreign one fails the export.
    retained = {"name": "b", "draft": {"title": "b", "handoff": {"assets": [
        {"path": "assets/take.wav", "source": f"{GEN}.wav"}]}}}
    out["bundle_retained_bob"] = exported_assets(hb, retained)
    out["bundle_retained_alice"] = exported_assets(ha, retained)
    out["dictation_prefs_write"] = client.post("/dictation/prefs", headers=hb, json={"prompt": "x"}).status_code
    out["dictation_prefs_read"] = client.get("/dictation/prefs", headers=hb).status_code
    speech = client.post("/v1/audio/speech", headers=hb, json={"input": "hi", "voice": VOICE})
    out["v1_speech_with_alices_voice"] = speech.status_code
    out["v1_speech_is_audio"] = speech.headers.get("content-type", "").startswith("audio/")

    # 3. Bob's own project echoes alice's paths; that must not make them his.
    echo = {"name": "bob", "state": {
        "ref": f"/voice_audio/{VOICE}.wav", "take": f"/audio/{GEN}.wav", "clip": "/preview/xa-clip.mp4"}}
    created = client.post("/projects", headers=hb, json=echo)
    project_id = (created.json() if created.status_code < 300 else {}).get("id")
    if project_id:
        client.get(f"/projects/{project_id}", headers=hb)
    client.get("/projects", headers=hb)
    out["echo_project_created"] = created.status_code
    out["echo_claims"] = {key: resources.owner_of(key) for key in
                          (f"voice_audio/{VOICE}.wav", f"audio/{GEN}.wav", "preview/xa-clip.mp4")}
    out["bob_files_after_echo"] = {
        path: client.get(path, headers=hb).status_code
        for path in (f"/voice_audio/{VOICE}.wav", f"/audio/{GEN}.wav", "/preview/xa-clip.mp4")}

    # 4. emo_audio over /ws/tts resolves through the guarded resolver.
    from api.routers import tts_stream

    with as_user(bob):
        try:
            tts_stream.resolve_emotion_clip(f"{VOICE}.wav")
            out["emo_clip_bob"] = "resolved"
        except ValueError:
            out["emo_clip_bob"] = "refused"
    with as_user(alice):
        out["emo_clip_alice"] = os.path.basename(tts_stream.resolve_emotion_clip(f"{VOICE}.wav"))

    out["alice_state_unchanged"] = alice_state() == before
    out["alice_state_diff"] = {k: (before[k], v) for k, v in alice_state().items() if before[k] != v}
    out["alice_reads"] = {
        path: client.get(path, headers=ha).status_code
        for path in (f"/profiles/{VOICE}", f"/voice_audio/{VOICE}.wav", f"/voice_audio/{VOICE}.portrait.jpg",
                     f"/audio/{GEN}.wav", f"/audio/audiobook_{LONGFORM}.m4b", f"/projects/{PROJECT}",
                     f"/project-bundles/{bundle}/document", f"/batch/jobs/{BATCH}")}

    # 5. Every kind of event about user data reaches only its owner.
    origin = {"origin": PUBLIC_URL, "host": "studio.test"}
    with client.websocket_connect("/ws/events", headers={**ha, **origin}) as wa, \
            client.websocket_connect("/ws/events", headers={**hb, **origin}) as wb:
        time.sleep(0.5)  # both subscribed
        kinds = sorted(USER_KINDS)
        with as_user(alice):
            for kind in kinds:
                event_bus.emit(kind, {"id": "x"})
        event_bus.emit("model_status", {"sub_stage": "ready"})
        out["alice_events"] = [first_kind(wa) for _ in range(len(kinds) + 1)]
        out["user_kinds"] = kinds
        out["bob_first_event"] = first_kind(wb)

    task_manager.active_tasks.pop(LIVE_TASK, None)

print(json.dumps(out))
