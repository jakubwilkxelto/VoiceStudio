"""The fork's alembic chain lives beside upstream's and never disturbs it."""
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


def test_upgrade_creates_fork_tables_and_own_version_table(upstream_db):
    upstream_head = _scalar(upstream_db, "SELECT version_num FROM alembic_version")

    migrate.upgrade(str(upstream_db))

    assert XELTO_TABLES <= _tables(upstream_db)
    assert _scalar(upstream_db, "SELECT version_num FROM xelto_alembic_version") == "x0001"
    # Upstream's chain is untouched and still considers itself at head.
    assert _scalar(upstream_db, "SELECT version_num FROM alembic_version") == upstream_head
    assert _plan_alembic(_upstream_cfg(upstream_db)) == "up_to_date"


def test_upgrade_is_idempotent(upstream_db):
    migrate.upgrade(str(upstream_db))
    migrate.upgrade(str(upstream_db))
    assert _scalar(upstream_db, "SELECT COUNT(*) FROM xelto_alembic_version") == 1


def test_downgrade_removes_only_fork_tables(upstream_db):
    before = _tables(upstream_db)
    migrate.upgrade(str(upstream_db))

    migrate.downgrade("base", str(upstream_db))

    assert not (XELTO_TABLES & _tables(upstream_db))
    assert before <= _tables(upstream_db)
