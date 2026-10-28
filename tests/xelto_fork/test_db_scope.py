"""Row scoping through ``core.db.get_db``, on upstream's real schema and code paths."""
import sqlite3

import pytest
from fastapi import HTTPException

import core.db as db_module
from core.db import db_conn, ensure_schema, get_db
from xelto import db_scope, hooks
from xelto.db_views import OWNERSHIP_REFUSAL
from xelto.identity import system_scope, use_identity

from .conftest import ALICE, BOB


def _insert_profile(identity, pid, name="v"):
    with use_identity(identity), db_conn() as conn:
        conn.execute("INSERT INTO voice_profiles(id, name) VALUES (?, ?)", (pid, name))


def _profile_ids(identity):
    with use_identity(identity), db_conn() as conn:
        return sorted(r[0] for r in conn.execute("SELECT id FROM voice_profiles"))


def test_inactive_mode_gives_upstream_a_plain_connection(db_path):
    with use_identity(ALICE):
        conn = get_db()
    try:
        assert type(conn) is sqlite3.Connection
    finally:
        conn.close()


def test_system_scope_gets_a_plain_connection_and_sees_everything(scoped_db):
    _insert_profile(ALICE, "a1")
    _insert_profile(BOB, "b1")
    with system_scope():
        conn = get_db()
    try:
        assert type(conn) is sqlite3.Connection
        assert sorted(r[0] for r in conn.execute("SELECT id FROM voice_profiles")) == ["a1", "b1", "demo"]
    finally:
        conn.close()


def test_each_user_sees_own_rows_and_shared_demo(scoped_db):
    _insert_profile(ALICE, "a1")
    _insert_profile(BOB, "b1")
    assert _profile_ids(ALICE) == ["a1", "demo"]
    assert _profile_ids(BOB) == ["b1", "demo"]


def test_no_identity_fails_closed(scoped_db):
    _insert_profile(ALICE, "a1")
    with use_identity(None):
        conn = get_db()
        try:
            assert isinstance(conn, db_scope.XeltoConnection)
            assert conn.execute("SELECT COUNT(*) FROM voice_profiles WHERE is_demo = 0").fetchone()[0] == 0
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO voice_profiles(id, name) VALUES ('x', 'x')")
        finally:
            conn.close()


def test_shared_demo_row_is_read_only(scoped_db):
    with use_identity(ALICE), db_conn() as conn:
        with pytest.raises(sqlite3.IntegrityError, match=OWNERSHIP_REFUSAL):
            conn.execute("UPDATE voice_profiles SET name = 'mine' WHERE id = 'demo'")


def test_rowcount_on_upstream_paths(scoped_db):
    """Upstream reads rowcount to tell 'changed' from 'not found'; views must not break that."""
    from api.routers import generation, glossary
    from core import job_store

    with use_identity(ALICE), db_conn() as conn:
        conn.execute("INSERT INTO generation_history(id, text) VALUES ('h1', 'x')")
    with use_identity(ALICE):
        assert generation.set_history_starred("h1", generation._StarBody(starred=True))["starred"] is True
        term = glossary.add_term("p1", glossary.GlossaryTerm(source="a", target="b"))
        assert glossary.update_term("p1", term["id"], glossary.GlossaryTermUpdate(target="c"))
        assert glossary.delete_term("p1", term["id"]) == {"deleted": True}
        glossary.add_term("p1", glossary.GlossaryTerm(source="a", target="b"))
        assert glossary.clear_terms("p1") == {"deleted": 1}
        job_store.create("j1", type="tts")
        assert job_store.retire_if_active("j1", "failed", "x") is True
    with use_identity(BOB):
        with pytest.raises(HTTPException) as err:
            generation.set_history_starred("h1", generation._StarBody(starred=False))
        assert err.value.status_code == 404
        assert job_store.retire_if_active("j1", "failed", "x") is False


def test_upstream_upsert_is_rewritten_and_still_guarded(scoped_db):
    from services import dub_pipeline

    with use_identity(ALICE):
        dub_pipeline.save_job("d1", {"id": "d1"}, "a.mp4", 1.0, "hash")
        dub_pipeline.save_job("d1", {"id": "d1", "v": 2}, "b.mp4", 2.0, "")
    with use_identity(ALICE), db_conn() as conn:
        row = conn.execute("SELECT filename, content_hash FROM dub_history WHERE id = 'd1'").fetchone()
    assert tuple(row) == ("b.mp4", "hash")  # the DO UPDATE kept its CASE on dub_history.*
    with use_identity(BOB):
        dub_pipeline.save_job("d1", {"id": "d1"}, "bob.mp4", 1.0, "")  # upstream logs the refusal
    with system_scope(), db_conn() as conn:
        assert conn.execute("SELECT filename FROM dub_history WHERE id = 'd1'").fetchone()[0] == "b.mp4"


def test_rewrite_only_touches_upserts_on_user_tables():
    upsert = "INSERT INTO dub_history (id) VALUES (?) ON CONFLICT(id) DO UPDATE SET id=excluded.id"
    assert db_scope.rewrite(upsert).startswith("INSERT INTO main.dub_history AS dub_history (id)")
    plain = "INSERT INTO dub_history (id) VALUES (?)"
    assert db_scope.rewrite(plain) == plain
    other = "INSERT INTO settings (key) VALUES (?) ON CONFLICT(key) DO UPDATE SET key=excluded.key"
    assert db_scope.rewrite(other) == other


def test_ensure_schema_works_for_a_user(scoped_db):
    _insert_profile(ALICE, "a1")
    with use_identity(ALICE):
        ensure_schema()
    assert _profile_ids(ALICE) == ["a1", "demo"]


def test_new_upstream_column_reaches_the_views(scoped_db):
    _profile_ids(ALICE)  # warm the DDL cache
    with system_scope(), db_conn() as conn:
        conn.execute("ALTER TABLE voice_profiles ADD COLUMN xelto_test TEXT DEFAULT 'dflt'")
    _insert_profile(ALICE, "a2")
    with use_identity(ALICE), db_conn() as conn:
        assert conn.execute("SELECT xelto_test FROM voice_profiles WHERE id = 'a2'").fetchone()[0] == "dflt"


def test_dub_job_cache_answers_only_its_owner(scoped_db):
    from services import dub_pipeline

    with use_identity(ALICE):
        hooks.record_dub_owner("live")
        dub_pipeline.put_job("live", {"id": "live"})
        dub_pipeline.save_job("saved", {"id": "saved"}, "a.mp4", 1.0, "")
    hooks.forget_dub_owners()
    with use_identity(ALICE):
        hooks.record_dub_owner("live")
        assert dub_pipeline.get_job("live") is not None
        assert dub_pipeline.get_job("saved") is not None  # owner from xelto_row_owner
    with use_identity(BOB):
        assert dub_pipeline.get_job("live") is None
        assert dub_pipeline.get_job("saved") is None
    with use_identity(None):
        assert dub_pipeline.get_job("live") is None


def test_audit_logs_connections_without_identity(scoped_db, monkeypatch, caplog):
    monkeypatch.setenv("OMNIVOICE_MULTIUSER_AUDIT", "1")
    with use_identity(None):
        get_db().close()
    assert "no identity" in caplog.text


def test_get_db_hook_is_marked():
    import inspect

    assert inspect.getsource(db_module.get_db).count("XELTO:") == 2
