"""Per-user data on the real app: run by test_isolation_db.py in a subprocess.

alice owns one row of every per-user resource; bob tries to list, read,
change and delete each of them through the real routes. Prints one JSON object.
"""
import json
import time

from real_app_setup import PUBLIC_URL, boot, wait_healthy

main, idp, users = boot({"alice": "voicestudio-user", "bob": "voicestudio-user"})
alice, bob = users["alice"], users["bob"]

from fastapi.testclient import TestClient  # noqa: E402

from core import db  # noqa: E402
from services import dub_pipeline  # noqa: E402
from xelto import store  # noqa: E402
from xelto.identity import Identity, system_scope, use_identity  # noqa: E402

A = {
    "profile": "xa-profile",
    "history": "xa-history",
    "dub": "xa-dub",
    "export": "xa-export",
    "job": "xa-job",
    "live_dub": "xa-live-dub",
}


def bearer(user):
    return {"authorization": f"Bearer {idp.access_token(user)}"}


def as_user(user):
    return use_identity(Identity(sub=user.sub, username=user.username))


def seed_alice():
    now = time.time()
    with as_user(alice), db.db_conn() as conn:
        conn.execute("INSERT INTO voice_profiles(id, name, created_at) VALUES (?, 'Alice voice', ?)",
                     (A["profile"], now))
        conn.execute("INSERT INTO generation_history(id, text, mode, created_at) "
                     "VALUES (?, 'hello', 'clone', ?)", (A["history"], now))
        conn.execute("INSERT INTO dub_history(id, filename, job_data, created_at) "
                     "VALUES (?, 'a.mp4', '{}', ?)", (A["dub"], now))
        conn.execute("INSERT INTO export_history(id, filename, mode, created_at) "
                     "VALUES (?, 'a.wav', 'audio', ?)", (A["export"], now))
    with as_user(alice):
        from core import job_store

        job_store.create(A["job"], type="tts")
        job_store.append_event(A["job"], "data: {}\n\n")
        dub_pipeline.put_job(A["live_dub"], {"id": A["live_dub"]})
    with system_scope(), db.db_conn() as conn:
        conn.execute("INSERT INTO voice_profiles(id, name, is_demo) VALUES ('xdemo', 'Demo', 1)")


def counts():
    """Rows per table as the system sees them."""
    with system_scope(), db.db_conn() as conn:
        return {
            t: conn.execute(f"SELECT COUNT(*) FROM {t} WHERE id LIKE 'xa-%'").fetchone()[0]
            for t in ("voice_profiles", "generation_history", "dub_history", "studio_projects",
                      "export_history", "glossary_terms", "jobs")
        }


def ids(resp):
    """The ids in a list response, bare or wrapped in an object."""
    body = resp.json()
    if isinstance(body, dict):
        body = next((v for v in body.values() if isinstance(v, list)), [])
    return sorted(str(item.get("id")) for item in body if isinstance(item, dict))


out = {}
with TestClient(main.app, base_url=PUBLIC_URL, raise_server_exceptions=False) as client:
    out["health"] = wait_healthy(client)
    seed_alice()
    ha, hb = bearer(alice), bearer(bob)

    project = client.post("/projects", headers=ha, json={"name": "Alice project", "state": {}})
    out["alice_create_project"] = project.status_code
    A["project"] = project.json()["id"]
    term = client.post("/glossary/shared-project", headers=ha, json={"source": "hi", "target": "hej"})
    out["alice_create_term"] = term.status_code
    A["term"] = term.json()["id"]
    conn = store.connect_both()
    with conn:
        # Name alice's API-made rows like the seeded ones, so counts() sees them.
        for table, key in (("studio_projects", "project"), ("glossary_terms", "term")):
            new = "xa-" + key
            conn.execute(f"UPDATE main.{table} SET id = ? WHERE id = ?", (new, A[key]))
            conn.execute("UPDATE xelto.xelto_row_owner SET row_id = ? WHERE tbl = ? AND row_id = ?",
                         (new, table, A[key]))
            A[key] = new
    conn.close()
    out["before"] = counts()

    # bob: lists
    out["bob_lists"] = {
        "profiles": ids(client.get("/profiles", headers=hb)),
        "history": ids(client.get("/history", headers=hb)),
        "dub": ids(client.get("/dub/history", headers=hb)),
        "projects": ids(client.get("/projects", headers=hb)),
        "glossary": ids(client.get("/glossary/shared-project", headers=hb)),
        "exports": ids(client.get("/export/history", headers=hb)),
        "jobs": ids(client.get("/jobs", headers=hb)),
    }
    # bob: reads by id
    out["bob_reads"] = {
        "profile": client.get(f"/profiles/{A['profile']}", headers=hb).status_code,
        "project": client.get(f"/projects/{A['project']}", headers=hb).status_code,
        "job": client.get(f"/jobs/{A['job']}", headers=hb).status_code,
        "job_events": client.get(f"/jobs/{A['job']}/events", headers=hb).status_code,
    }
    # bob: changes and deletes (status codes for the record; the data is what counts)
    out["bob_writes"] = {
        "profile_put": client.put(f"/profiles/{A['profile']}", headers=hb, json={"name": "bob"}).status_code,
        "history_star": client.put(f"/history/{A['history']}/starred", headers=hb,
                                   json={"starred": True}).status_code,
        "project_put": client.put(f"/projects/{A['project']}", headers=hb,
                                  json={"name": "bob", "state": {}}).status_code,
        "project_patch": client.patch(f"/projects/{A['project']}", headers=hb,
                                      json={"name": "bob"}).status_code,
        "term_put": client.put(f"/glossary/shared-project/{A['term']}", headers=hb,
                               json={"target": "bob"}).status_code,
        "demo_put": client.put("/profiles/xdemo", headers=hb, json={"name": "bob"}).status_code,
        "profile_delete": client.delete(f"/profiles/{A['profile']}", headers=hb).status_code,
        "history_delete": client.delete(f"/history/{A['history']}", headers=hb).status_code,
        "history_clear": client.delete("/history", headers=hb).status_code,
        "dub_delete": client.delete(f"/dub/history/{A['dub']}", headers=hb).status_code,
        "dub_clear": client.delete("/dub/history", headers=hb).status_code,
        "project_delete": client.delete(f"/projects/{A['project']}", headers=hb).status_code,
        "term_delete": client.delete(f"/glossary/shared-project/{A['term']}", headers=hb).status_code,
        "glossary_clear": client.delete("/glossary/shared-project", headers=hb).status_code,
        "export_delete": client.delete(f"/export/history/{A['export']}", headers=hb).status_code,
    }
    out["after_bob"] = counts()
    with system_scope(), db.db_conn() as conn:
        out["names_after_bob"] = {
            "profile": conn.execute("SELECT name FROM voice_profiles WHERE id = ?", (A["profile"],)).fetchone()[0],
            "project": conn.execute("SELECT name FROM studio_projects WHERE id = ?", (A["project"],)).fetchone()[0],
            "term": conn.execute("SELECT target FROM glossary_terms WHERE id = ?", (A["term"],)).fetchone()[0],
            "starred": conn.execute("SELECT starred FROM generation_history WHERE id = ?", (A["history"],)).fetchone()[0],
            "demo": conn.execute("SELECT name FROM voice_profiles WHERE id = 'xdemo'").fetchone()[0],
        }
    with as_user(bob):
        out["bob_get_live_dub"] = dub_pipeline.get_job(A["live_dub"]) is not None
    with as_user(alice):
        out["alice_get_live_dub"] = dub_pipeline.get_job(A["live_dub"]) is not None

    # alice still sees everything, demo included, and her own writes report success
    out["alice_lists"] = {
        "profiles": ids(client.get("/profiles", headers=ha)),
        "history": ids(client.get("/history", headers=ha)),
        "dub": ids(client.get("/dub/history", headers=ha)),
        "projects": ids(client.get("/projects", headers=ha)),
        "exports": ids(client.get("/export/history", headers=ha)),
    }
    out["alice_reads"] = {
        "profile": client.get(f"/profiles/{A['profile']}", headers=ha).status_code,
        "job": client.get(f"/jobs/{A['job']}", headers=ha).status_code,
        "job_events": client.get(f"/jobs/{A['job']}/events", headers=ha).status_code,
    }
    out["alice_writes"] = {
        "term_put": client.put(f"/glossary/shared-project/{A['term']}", headers=ha,
                               json={"target": "hallo"}).status_code,
        "term_delete": client.delete(f"/glossary/shared-project/{A['term']}", headers=ha).json(),
        "profile_delete": client.delete(f"/profiles/{A['profile']}", headers=ha).status_code,
        "history_delete": client.delete(f"/history/{A['history']}", headers=ha).status_code,
    }
    out["after_alice"] = counts()

import os  # noqa: E402
import sqlite3  # noqa: E402

core = sqlite3.connect(db.DB_PATH)
out["fork_tables_in_upstream_db"] = [r[0] for r in core.execute(
    "SELECT name FROM sqlite_master WHERE name LIKE 'xelto%'")]
core.close()
out["fork_db_is_separate"] = os.path.exists(store.db_path()) and store.db_path() != db.DB_PATH

print(json.dumps(out))
