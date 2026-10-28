"""The fork's alembic chain runs on its own file (xelto.db) and never disturbs upstream's."""
import sqlite3

import pytest

import core.db as db_module
from core.db import _plan_alembic, init_db
from xelto import migrate

XELTO_TABLES = {"xelto_users", "xelto_sessions", "xelto_row_owner", "xelto_file_owner", "xelto_meta"}


def _tables(path) -> set[str]:
    conn = sqlite3.connect(str(path))
    try:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()


def _scalar(path, sql):
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute(sql).fetchone()[0]
    finally:
        conn.close()


@pytest.fixture
def upstream_db(tmp_path, monkeypatch):
    """A DB brought to upstream's head by the real startup path."""
    db = tmp_path / "omnivoice.db"
    monkeypatch.setattr(db_module, "DB_PATH", str(db))
    init_db()
    return db


def _upstream_cfg(db):
    from pathlib import Path

    from alembic.config import Config

    cfg = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db}")
    cfg.attributes["configure_logger"] = False
    return cfg


def _fork(db):
    return db.with_name("xelto.db")


def test_upgrade_creates_fork_tables_in_their_own_file(upstream_db):
    upstream_before = _tables(upstream_db)
    upstream_head = _scalar(upstream_db, "SELECT version_num FROM alembic_version")

    migrate.upgrade_for(str(upstream_db))

    assert XELTO_TABLES <= _tables(_fork(upstream_db))
    assert _scalar(_fork(upstream_db), "SELECT version_num FROM xelto_alembic_version") == "x0001"
    # Upstream's file is untouched: no fork table, its chain still at head.
    assert _tables(upstream_db) == upstream_before
    assert _scalar(upstream_db, "SELECT version_num FROM alembic_version") == upstream_head
    assert _plan_alembic(_upstream_cfg(upstream_db)) == "up_to_date"


def test_fork_db_location_can_be_overridden(upstream_db, tmp_path, monkeypatch):
    elsewhere = tmp_path / "data" / "accounts.db"
    elsewhere.parent.mkdir()
    monkeypatch.setenv("OMNIVOICE_MULTIUSER_DB", str(elsewhere))
    migrate.upgrade_for(str(upstream_db))
    assert XELTO_TABLES <= _tables(elsewhere)
    assert not _fork(upstream_db).exists()


def test_upgrade_is_idempotent(upstream_db):
    migrate.upgrade_for(str(upstream_db))
    migrate.upgrade_for(str(upstream_db))
    assert _scalar(_fork(upstream_db), "SELECT COUNT(*) FROM xelto_alembic_version") == 1


def test_downgrade_empties_the_fork_db(upstream_db):
    migrate.upgrade_for(str(upstream_db))
    migrate.downgrade("base", str(_fork(upstream_db)))
    assert not (XELTO_TABLES & _tables(_fork(upstream_db)))


def test_tables_from_before_the_split_move_out_of_upstreams_file(upstream_db):
    # Earlier fork versions created their tables inside omnivoice.db.
    upstream_before = _tables(upstream_db)
    migrate.upgrade(str(upstream_db))
    conn = sqlite3.connect(str(upstream_db))
    conn.execute("INSERT INTO xelto_users(sub, username, created_at) VALUES ('s1', 'alice', 0)")
    conn.execute("INSERT INTO xelto_row_owner VALUES ('voice_profiles', 'p1', 's1')")
    conn.commit()
    conn.close()

    migrate.upgrade_for(str(upstream_db))

    assert _tables(upstream_db) == upstream_before
    assert _scalar(_fork(upstream_db), "SELECT username FROM xelto_users") == "alice"
    assert _scalar(_fork(upstream_db), "SELECT owner FROM xelto_row_owner") == "s1"
    assert _scalar(_fork(upstream_db), "SELECT COUNT(*) FROM xelto_alembic_version") == 1
    assert migrate.adopt_legacy_tables(str(upstream_db)) == {}  # nothing left to move
