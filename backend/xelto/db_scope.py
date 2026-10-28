"""Per-user row scoping for every connection ``core.db.get_db`` opens.

The target of the hook in ``get_db``. While multi-user mode is active, a
connection opened for a user (or for nobody) is a :class:`XeltoConnection`
whose per-user tables are shadowed by the views from :mod:`xelto.db_views`;
one opened in the :data:`~xelto.identity.SYSTEM` scope, or with the mode off,
is a plain ``sqlite3.Connection``. Ownership is kept in the fork's own
database (:mod:`xelto.store`), attached to each scoped connection.

The connection also hides the two places where a view differs from a table:

- ``cursor.rowcount`` of a write through a view is 0, so the cursor reports
  the rows the scoping triggers counted instead;
- an UPSERT cannot target a view, so ``INSERT INTO <user table> ... ON CONFLICT
  ... DO UPDATE`` is sent to ``main.<user table>``, which the base-table
  triggers still guard.

The owner is bound when the connection opens. With no identity it is NULL:
views show nothing and every write to a user table fails (fail closed).
"""
from __future__ import annotations

import logging
import os
import re
import sqlite3
import threading
import traceback

from xelto import store
from xelto.db_views import USER_TABLES, RowCounter, columns, schema_version, scope_ddl
from xelto.identity import current_identity

logger = logging.getLogger("omnivoice.xelto.db")

# Upstream tables that stay shared: settings and catalogues the admin manages,
# the voice gallery, telephony, remote workers, cloud sync (disabled in
# multi-user mode), and job_events, which is reached only through its parent
# job. Every table upstream creates must be in exactly one of USER_TABLES and
# GLOBAL_TABLES (tests/xelto_fork/test_table_classification.py).
GLOBAL_TABLES = frozenset({
    "job_events",
    "settings",
    "mcp_client_bindings",
    "pronunciation_entries",
    "call_sessions",
    "remote_workers",
    "remote_worker_enrollments",
    "remote_tasks",
    "remote_task_attempts",
    "cloud_sync_state",
    "cloud_sync_items",
    "cloud_sync_outbox",
    "cloud_sync_local",
    "voice_gallery",
    "alembic_version",
})

_active = False


def activate() -> None:
    """Called by :func:`xelto.install`; scoping applies only behind the gate."""
    global _active
    _active = True


def deactivate() -> None:
    global _active
    _active = False


def is_active() -> bool:
    return _active


def audit_enabled() -> bool:
    return os.environ.get("OMNIVOICE_MULTIUSER_AUDIT", "").strip().lower() in {"1", "true", "yes", "on"}


_TABLES = "|".join(USER_TABLES)
# The table an UPSERT on a user table names; the alias keeps upstream's
# "<table>.<column>" references inside DO UPDATE valid after the rewrite.
_UPSERT_TARGET = re.compile(
    rf"^(\s*INSERT\s+(?:OR\s+\w+\s+)?INTO\s+)({_TABLES})\b(?!\s*\.)(?!\s+AS\b)",
    re.IGNORECASE,
)
_DO_UPDATE = re.compile(r"\bON\s+CONFLICT\b.*\bDO\s+UPDATE\b", re.IGNORECASE | re.DOTALL)


def rewrite(sql: str) -> str:
    """Send an UPSERT on a user table to the base table (views refuse UPSERT)."""
    if not _DO_UPDATE.search(sql):
        return sql
    return _UPSERT_TARGET.sub(lambda m: f"{m.group(1)}main.{m.group(2)} AS {m.group(2)}", sql, count=1)


class XeltoCursor(sqlite3.Cursor):
    """A cursor whose ``rowcount`` also counts writes made through the views."""

    _xelto_rows = 0

    def execute(self, sql, parameters=(), /):
        counter = self.connection._xelto_counter
        before = counter.value
        super().execute(rewrite(sql), parameters)
        self._xelto_rows = counter.value - before
        return self

    def executemany(self, sql, seq_of_parameters, /):
        counter = self.connection._xelto_counter
        before = counter.value
        super().executemany(rewrite(sql), seq_of_parameters)
        self._xelto_rows = counter.value - before
        return self

    @property
    def rowcount(self) -> int:
        return self._xelto_rows or super().rowcount


class XeltoConnection(sqlite3.Connection):
    """A connection scoped to one owner; see the module docstring."""

    _xelto_counter = RowCounter()
    _xelto_owner: str | None = None
    _xelto_path = ""

    def cursor(self, factory=XeltoCursor):
        return super().cursor(factory)

    # sqlite3.Connection.execute builds a plain cursor in C, bypassing cursor().
    def execute(self, sql, parameters=(), /):
        return self.cursor().execute(sql, parameters)

    def executemany(self, sql, seq_of_parameters, /):
        return self.cursor().executemany(sql, seq_of_parameters)

    def executescript(self, sql_script, /):
        """Schema DDL (``ensure_schema``) runs on a separate system connection.

        DDL cannot run through the views, and the script is schema upkeep that
        acts for nobody. Like ``executescript`` itself, it commits first.
        """
        self.commit()
        system = sqlite3.connect(self._xelto_path)
        try:
            system.executescript(sql_script)
        finally:
            system.close()
        return self.cursor()


def connection_class() -> type[sqlite3.Connection]:
    """The ``factory`` ``get_db`` passes to ``sqlite3.connect``."""
    if not _active or (current_identity.get() or _NOBODY).is_system:
        return sqlite3.Connection
    return XeltoConnection


class _Nobody:
    is_system = False


_NOBODY = _Nobody()

_ddl_cache: dict[tuple[str, int], list[str]] = {}
_ddl_lock = threading.Lock()


def _scope_ddl(conn: sqlite3.Connection, path: str) -> list[str]:
    """The scoping DDL for this database's current schema, built once per schema version."""
    key = (path, schema_version(conn))
    with _ddl_lock:
        cached = _ddl_cache.get(key)
    if cached is not None:
        return cached
    # A table that does not exist yet (a fresh install reads settings before
    # init_db) holds nothing to hide, and upstream expects "no such table"
    # errors to name its own tables. Creating a table bumps schema_version,
    # so the next connection scopes it.
    ddl = [
        stmt
        for table in USER_TABLES
        if (cols := columns(conn, table))
        for stmt in scope_ddl(table, cols)
    ]
    with _ddl_lock:
        _ddl_cache.clear()  # one live schema per database; older versions are dead
        _ddl_cache[key] = ddl
    return ddl


def on_connect(conn: sqlite3.Connection) -> None:
    """Scope a connection ``get_db`` just opened; a no-op for plain connections."""
    if not isinstance(conn, XeltoConnection):
        return
    identity = current_identity.get()
    owner = identity.sub if identity is not None else None
    if owner is None and audit_enabled():
        logger.warning(
            "xelto audit: database connection opened with no identity\n%s",
            "".join(traceback.format_stack(limit=12)[:-1]),
        )
    path = conn.execute("PRAGMA database_list").fetchone()[2]
    conn._xelto_path = path
    conn._xelto_owner = owner
    conn._xelto_counter = RowCounter()
    conn.create_function("xelto_owner", 0, lambda: owner)
    conn.create_function("xelto_count", 0, conn._xelto_counter)
    store.attach(conn, path)  # ownership lives in the fork's own database
    for stmt in _scope_ddl(conn, path):
        sqlite3.Connection.execute(conn, stmt)
