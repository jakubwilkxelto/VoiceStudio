"""P0 spike: SQLite semantics that emulated row-level security relies on.

Each test pins one behaviour the P2 ``XeltoConnection`` design depends on, on
upstream's real schema (``_BASE_SCHEMA``). They stay as regression tests: a
SQLite or schema change that breaks an assumption fails here first.
"""
import sqlite3

import pytest

from core.db import _BASE_SCHEMA
from xelto.db_views import (
    USER_TABLES,
    columns,
    schema_version,
    scope_connection,
    unscope_connection,
)

OWNER_TABLE = """CREATE TABLE xelto_row_owner (
    tbl TEXT NOT NULL, row_id TEXT NOT NULL, owner TEXT NOT NULL,
    PRIMARY KEY (tbl, row_id))"""


class Who:
    def __init__(self, sub):
        self.sub = sub

    def __call__(self):
        return self.sub


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "omnivoice.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(_BASE_SCHEMA)
    conn.execute(OWNER_TABLE)
    conn.execute("INSERT INTO voice_profiles(id, name, is_demo) VALUES ('demo', 'Demo', 1)")
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def scoped(db):
    """One connection whose owner can be switched between alice and bob."""
    conn = sqlite3.connect(str(db))
    who = Who("alice")
    counter = scope_connection(conn, who)
    conn.execute("INSERT INTO voice_profiles(id, name) VALUES ('a1', 'Alice voice')")
    conn.execute(
        "INSERT INTO glossary_terms(id, project_id, source, target) VALUES ('g1', 'p1', 'hi', 'hej')"
    )
    who.sub = "bob"
    conn.execute("INSERT INTO voice_profiles(id, name) VALUES ('b1', 'Bob voice')")
    who.sub = "alice"
    conn.commit()
    counter.value = 0
    yield conn, who, counter
    conn.close()


def _ids(conn, table="voice_profiles"):
    return sorted(r[0] for r in conn.execute(f"SELECT id FROM {table}"))


def test_select_sees_only_own_rows_plus_shared_demo(scoped):
    conn, who, _ = scoped
    assert _ids(conn) == ["a1", "demo"]
    assert _ids(conn, "glossary_terms") == ["g1"]
    who.sub = "bob"
    assert _ids(conn) == ["b1", "demo"]
    assert _ids(conn, "glossary_terms") == []


def test_insert_through_view_keeps_upstream_defaults_and_stamps_owner(scoped):
    conn, _, counter = scoped
    conn.execute("INSERT INTO voice_profiles(id, name) VALUES ('a2', 'Second')")
    row = conn.execute(
        "SELECT language, kind, is_demo, ref_text FROM main.voice_profiles WHERE id = 'a2'"
    ).fetchone()
    assert row == ("Auto", "clone", 0, "")
    assert conn.execute(
        "SELECT owner FROM xelto_row_owner WHERE tbl = 'voice_profiles' AND row_id = 'a2'"
    ).fetchone() == ("alice",)
    assert counter.value == 1


def test_insert_or_replace_own_row_replaces(scoped):
    conn, _, _ = scoped
    conn.execute("INSERT OR REPLACE INTO voice_profiles(id, name) VALUES ('a1', 'Renamed')")
    assert conn.execute("SELECT name FROM voice_profiles WHERE id = 'a1'").fetchone() == ("Renamed",)
    assert conn.execute("SELECT COUNT(*) FROM main.voice_profiles WHERE id = 'a1'").fetchone() == (1,)


def test_insert_or_replace_cannot_take_over_another_users_row(scoped):
    conn, _, _ = scoped
    with pytest.raises(sqlite3.IntegrityError, match="another user"):
        conn.execute("INSERT OR REPLACE INTO voice_profiles(id, name) VALUES ('b1', 'Stolen')")
    assert conn.execute("SELECT name FROM main.voice_profiles WHERE id = 'b1'").fetchone() == ("Bob voice",)


def test_foreign_and_demo_rows_cannot_be_changed(scoped):
    conn, _, _ = scoped
    # Through the view: invisible, so nothing happens.
    conn.execute("UPDATE voice_profiles SET name = 'x' WHERE id = 'b1'")
    conn.execute("DELETE FROM voice_profiles WHERE id = 'b1'")
    assert conn.execute("SELECT name FROM main.voice_profiles WHERE id = 'b1'").fetchone() == ("Bob voice",)
    # Naming main.t directly: refused by the base-table guards.
    for stmt in ("UPDATE main.voice_profiles SET name = 'x' WHERE id = 'b1'",
                 "DELETE FROM main.voice_profiles WHERE id = 'b1'",
                 "UPDATE voice_profiles SET name = 'x' WHERE id = 'demo'"):
        with pytest.raises(sqlite3.IntegrityError, match="another user"):
            conn.execute(stmt)


def test_rowcount_needs_the_trigger_counter(scoped):
    conn, _, counter = scoped
    before = conn.total_changes
    cur = conn.execute("UPDATE voice_profiles SET name = 'x' WHERE id IN ('a1', 'b1')")
    # SQLite does not count INSTEAD OF work: upstream's `rowcount == 0` checks
    # would all read "not found". total_changes over-counts (it includes the
    # ownership bookkeeping), so the per-row counter is the correction to use.
    assert cur.rowcount == 0
    assert counter.value == 1
    counter.value = 0
    before = conn.total_changes
    conn.execute("DELETE FROM voice_profiles WHERE id = 'a1'")
    assert counter.value == 1
    assert conn.total_changes - before > 1


def test_upsert_on_view_fails_and_rewrite_to_main_works(scoped):
    conn, _, _ = scoped
    upsert = ("INSERT INTO {t}(id, name) VALUES ('a3', 'Up') "
              "ON CONFLICT(id) DO UPDATE SET name = excluded.name")
    with pytest.raises(sqlite3.OperationalError):
        conn.execute(upsert.format(t="voice_profiles"))
    conn.execute(upsert.format(t="main.voice_profiles"))
    conn.execute(upsert.format(t="main.voice_profiles").replace("'Up'", "'Up2'"))
    assert conn.execute("SELECT name FROM voice_profiles WHERE id = 'a3'").fetchone() == ("Up2",)
    # ...and the rewrite is still guarded against another user's id.
    with pytest.raises(sqlite3.IntegrityError, match="another user"):
        conn.execute(upsert.format(t="main.voice_profiles").replace("'a3'", "'b1'"))


def test_returning_on_view_silently_misses_defaults(scoped):
    # RETURNING on a view does not fail: it echoes the values given to the
    # INSTEAD OF trigger, before the base table applies its DEFAULTs. A silent
    # divergence, which is why job_events stays a base table and the P2 SQL
    # lint flags RETURNING on user tables.
    conn, _, _ = scoped
    returned = conn.execute(
        "INSERT INTO voice_profiles(id, name) VALUES ('a4', 'R') RETURNING id, language"
    ).fetchall()
    assert returned == [("a4", None)]
    assert conn.execute(
        "SELECT language FROM main.voice_profiles WHERE id = 'a4'"
    ).fetchone() == ("Auto",)


def test_ddl_fails_on_scoped_connection_and_works_on_system(db, scoped):
    conn, _, _ = scoped
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("CREATE INDEX IF NOT EXISTS idx_spike ON voice_profiles(name)")
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("ALTER TABLE voice_profiles ADD COLUMN spike TEXT")
    system = sqlite3.connect(str(db))
    try:
        system.execute("CREATE INDEX IF NOT EXISTS idx_spike ON voice_profiles(name)")
        system.execute("ALTER TABLE voice_profiles ADD COLUMN spike TEXT DEFAULT 'x'")
        system.commit()
    finally:
        system.close()


def test_new_upstream_column_is_picked_up_after_rebuild(db):
    conn = sqlite3.connect(str(db))
    try:
        scope_connection(conn, Who("alice"))
        seen = schema_version(conn)
        system = sqlite3.connect(str(db))
        system.execute("ALTER TABLE voice_profiles ADD COLUMN spike TEXT DEFAULT 'dflt'")
        system.commit()
        system.close()

        # The schema_version bump is the cache key P2 rebuilds on.
        assert schema_version(conn) != seen
        assert "spike" in [c.name for c in columns(conn, "voice_profiles")]
        unscope_connection(conn)
        scope_connection(conn, Who("alice"))
        conn.execute("INSERT INTO voice_profiles(id, name) VALUES ('a5', 'New col')")
        assert conn.execute("SELECT spike FROM voice_profiles WHERE id = 'a5'").fetchone() == ("dflt",)
    finally:
        conn.close()


def test_every_user_table_can_be_scoped(db):
    conn = sqlite3.connect(str(db))
    try:
        scope_connection(conn, Who("alice"), USER_TABLES)
        for table in USER_TABLES:
            conn.execute(f"SELECT * FROM {table} LIMIT 1").fetchall()
    finally:
        conn.close()
