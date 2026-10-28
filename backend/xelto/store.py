"""Where multi-user mode keeps its own data: a separate SQLite file.

Accounts, sessions, row ownership and the fork's migration history live in
``xelto.db``, next to upstream's ``omnivoice.db`` (or wherever
``OMNIVOICE_MULTIUSER_DB`` points). Upstream's file never holds a fork table,
so turning the mode off, or dropping the fork, leaves it exactly as upstream
wrote it, and the two can be backed up and cleaned separately.

Connections that need both (the scoping views, bootstrap, the CLI) open
``omnivoice.db`` and attach this file as the schema :data:`SCHEMA`.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

SCHEMA = "xelto"
FILENAME = "xelto.db"


def db_path(core_path: str | None = None) -> str:
    """The fork's database file; next to ``core_path`` (default: the app's DB)."""
    override = os.environ.get("OMNIVOICE_MULTIUSER_DB", "").strip()
    if override:
        return override
    if core_path is None:
        from core import db

        core_path = db.DB_PATH
    return str(Path(core_path).with_name(FILENAME))


def connect() -> sqlite3.Connection:
    """A connection to the fork's database alone."""
    conn = sqlite3.connect(db_path())
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def attach(conn: sqlite3.Connection, core_path: str | None = None) -> None:
    """Attach the fork's database to a connection on ``omnivoice.db``."""
    conn.execute(f"ATTACH DATABASE ? AS {SCHEMA}", (db_path(core_path),))


def connect_both(core_path: str | None = None) -> sqlite3.Connection:
    """A plain connection to ``omnivoice.db`` with the fork's database attached."""
    if core_path is None:
        from core import db

        core_path = db.DB_PATH
    conn = sqlite3.connect(core_path)
    attach(conn, core_path)
    return conn
