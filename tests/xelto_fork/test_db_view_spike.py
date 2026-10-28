"""P0 spike: SQLite semantics that emulated row-level security relies on.

Each test pins one behaviour the ``XeltoConnection`` design depends on, on
upstream's real schema (``_BASE_SCHEMA``). They stay as regression tests: a
SQLite or schema change that breaks an assumption fails here first.

Reads go through the TEMP views; writes are retargeted to ``main.<t>``
(``xelto.db_scope.rewrite``), where the base-table triggers guard them. The
views have no INSTEAD OF triggers, which SQLite before 3.51 cannot run.
"""
import sqlite3

import pytest

from core.db import _BASE_SCHEMA
from xelto.db_scope import rewrite
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
    conn.execute("INSERT INTO voice_profiles(id, name, is_demo) VALUES ('demo', 'Demo', 1)")
    conn.commit()
    conn.close()
    # Ownership lives in the fork's own database, attached as "xelto".
    fork = sqlite3.connect(str(path.with_name("xelto.db")))
    fork.execute(OWNER_TABLE)
    fork.commit()
    fork.close()
    return path


class _Rewriting(sqlite3.Connection):
    """Writes retargeted as XeltoConnection retargets them."""

    def execute(self, sql, parameters=(), /):
        return super().execute(rewrite(sql), parameters)


def _open(db):
    """A connection on the app's DB with the fork's DB attached, as db_scope opens it."""
    conn = sqlite3.connect(str(db), factory=_Rewriting)
    conn.execute("ATTACH DATABASE ? AS xelto", (str(db.with_name("xelto.db")),))
    return conn


@pytest.fixture
def scoped(db):
    """One connection whose owner can be switched between alice and bob."""
    conn = _open(db)
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


def test_insert_keeps_upstream_defaults_and_stamps_owner(scoped):
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


def test_foreign_rows_are_skipped_and_demo_rows_refused(scoped):
    conn, _, _ = scoped
    # Another user's row: skipped, as if it were not there, however it is named.
    for stmt in ("UPDATE voice_profiles SET name = 'x' WHERE id = 'b1'",
                 "DELETE FROM voice_profiles WHERE id = 'b1'",
                 "UPDATE main.voice_profiles SET name = 'x' WHERE id = 'b1'",
                 "DELETE FROM main.voice_profiles WHERE id = 'b1'"):
        assert conn.execute(stmt).rowcount == 0, stmt
    assert conn.execute("SELECT name FROM main.voice_profiles WHERE id = 'b1'").fetchone() == ("Bob voice",)
    # The shared demo row: refused.
    for stmt in ("UPDATE voice_profiles SET name = 'x' WHERE id = 'demo'",
                 "DELETE FROM voice_profiles WHERE id = 'demo'",
                 "INSERT OR REPLACE INTO voice_profiles(id, name) VALUES ('demo', 'x')"):
        with pytest.raises(sqlite3.IntegrityError, match="another user"):
            conn.execute(stmt)


def test_rowcount_counts_only_the_callers_rows(scoped):
    conn, _, counter = scoped
    cur = conn.execute("UPDATE voice_profiles SET name = 'x' WHERE id IN ('a1', 'b1')")
    # bob's row was skipped by its guard, so it is not counted: upstream's
    # `rowcount == 0` checks read "not found" for other users' rows. total_changes
    # over-counts (it includes the ownership bookkeeping); the trigger counter does not.
    assert cur.rowcount == 1
    assert counter.value == 1
    counter.value = 0
    before = conn.total_changes
    conn.execute("DELETE FROM voice_profiles WHERE id = 'a1'")
    assert counter.value == 1
    assert conn.total_changes - before > 1


def test_upsert_works_and_is_guarded(scoped):
    conn, _, _ = scoped
    upsert = ("INSERT INTO voice_profiles(id, name) VALUES ('a3', 'Up') "
              "ON CONFLICT(id) DO UPDATE SET name = excluded.name")
    conn.execute(upsert)
    conn.execute(upsert.replace("'Up'", "'Up2'"))
    assert conn.execute("SELECT name FROM voice_profiles WHERE id = 'a3'").fetchone() == ("Up2",)
    # ...and guarded against another user's id.
    with pytest.raises(sqlite3.IntegrityError, match="another user"):
        conn.execute(upsert.replace("'a3'", "'b1'"))


def test_returning_sees_the_base_tables_defaults(scoped):
    # Writes reach main.t, so RETURNING reports what was stored. (With INSTEAD
    # OF triggers it echoed the values before DEFAULTs; the SQL lint still asks
    # for a review of RETURNING on user tables.)
    conn, _, _ = scoped
    returned = conn.execute(
        "INSERT INTO voice_profiles(id, name) VALUES ('a4', 'R') RETURNING id, language"
    ).fetchall()
    assert returned == [("a4", "Auto")]
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
    conn = _open(db)
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
    conn = _open(db)
    try:
        scope_connection(conn, Who("alice"), USER_TABLES)
        for table in USER_TABLES:
            conn.execute(f"SELECT * FROM {table} LIMIT 1").fetchall()
    finally:
        conn.close()
