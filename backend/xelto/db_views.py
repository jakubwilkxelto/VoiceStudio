"""Per-connection row scoping for SQLite (P0 spike prototype; P2 builds on it).

For each per-user table ``t`` a scoped connection gets, in the temp schema:

- ``TEMP VIEW t`` that shadows ``main.t`` (temp resolves first) and shows only
  rows the current owner holds in ``xelto.xelto_row_owner`` (plus shared demo
  rows); the ``xelto`` schema is the fork's own database, attached. Reads go
  through it;
- triggers on ``main.t`` that stamp ownership on insert, skip another owner's
  rows on update and delete (``RAISE(IGNORE)``: the row is left alone, as if
  it were not there) and refuse writes to shared rows. Writes reach ``main.t``
  because :func:`xelto.db_scope.rewrite` retargets every write statement on a
  per-user table there.

No ``INSTEAD OF`` triggers, and no schema-qualified table in any trigger's
INSERT, UPDATE or DELETE: SQLite before 3.51 refuses both ("qualified table
names are not allowed ... within triggers"; the Docker image ships 3.50).
An unqualified name in a TEMP trigger body still finds the attached
``xelto.xelto_row_owner``, as long as ``main`` has no table of that name.

The owner is read through the ``xelto_owner()`` SQL function rather than baked
into the DDL, so the DDL is constant per schema version. Column lists come from
``PRAGMA table_info`` because upstream keeps adding columns.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

USER_TABLES = (
    "voice_profiles",
    "generation_history",
    "dub_history",
    "studio_projects",
    "export_history",
    "glossary_terms",
    "jobs",
)

# The guard triggers' error message (the gate turns it into a 403).
OWNERSHIP_REFUSAL = "xelto: row belongs to another user"
# A write to a per-user table on a connection that acts for nobody: the work
# lost its identity on the way (a thread or task the propagation patches miss).
NO_IDENTITY_REFUSAL = "xelto: no identity for a write to a per-user table"

# Rows every user may read but nobody may change through a scoped connection.
SHARED_READ = {"voice_profiles": "is_demo = 1"}


@dataclass(frozen=True)
class Column:
    name: str
    default: str | None  # raw SQL from PRAGMA table_info, None = no DEFAULT


def columns(conn: sqlite3.Connection, table: str) -> list[Column]:
    rows = conn.execute(f"PRAGMA main.table_info({table})").fetchall()
    return [Column(r[1], r[4]) for r in rows]


def schema_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA main.schema_version").fetchone()[0]


def _owned(table: str, row: str) -> str:
    return (
        f"EXISTS (SELECT 1 FROM xelto.xelto_row_owner WHERE tbl = '{table}' "
        f"AND row_id = {row}.id AND owner = xelto_owner())"
    )


def scope_ddl(table: str, cols: list[Column]) -> list[str]:
    """Statements that scope ``table`` on one connection, in execution order.

    Triggers on ``main.t`` come first: once the view exists, the bare name
    ``t`` resolves to it.
    """
    shared = SHARED_READ.get(table)
    visible = f"({_owned(table, 'b')}" + (f" OR b.{shared})" if shared else ")")
    # Shared rows are read-only for everyone: refused loudly (a 403 at the
    # gate). Other users' rows are skipped silently, as a view would.
    shared_guards = [
        f"""CREATE TEMP TRIGGER xelto_{table}_{op}s BEFORE {verb} ON main.{table}
            WHEN OLD.{shared}
            BEGIN SELECT RAISE(ABORT, '{OWNERSHIP_REFUSAL}'); END"""
        for op, verb in (("bu", "UPDATE"), ("bd", "DELETE"))
    ] if shared else []
    # Excludes shared rows, so which BEFORE trigger fires first never matters.
    foreign = f"NOT {_owned(table, 'OLD')}" + (f" AND NOT COALESCE(OLD.{shared}, 0)" if shared else "")
    return [
        *shared_guards,
        f"""CREATE TEMP TRIGGER xelto_{table}_bn BEFORE INSERT ON main.{table}
            WHEN xelto_owner() IS NULL
            BEGIN SELECT RAISE(ABORT, '{NO_IDENTITY_REFUSAL}'); END""",
        # An id already taken by a row that is not the caller's (another
        # user's, a shared demo row, an unowned one): INSERT OR REPLACE would
        # otherwise delete it and take the id over.
        f"""CREATE TEMP TRIGGER xelto_{table}_bi BEFORE INSERT ON main.{table}
            WHEN EXISTS (SELECT 1 FROM main.{table} AS e
                         WHERE e.id = NEW.id AND NOT {_owned(table, 'e')})
            BEGIN SELECT RAISE(ABORT, '{OWNERSHIP_REFUSAL}'); END""",
        f"""CREATE TEMP TRIGGER xelto_{table}_ai AFTER INSERT ON main.{table}
            BEGIN
              INSERT OR REPLACE INTO xelto_row_owner(tbl, row_id, owner)
              VALUES ('{table}', NEW.id, xelto_owner());
              SELECT xelto_count();
            END""",
        f"""CREATE TEMP TRIGGER xelto_{table}_bu BEFORE UPDATE ON main.{table}
            WHEN {foreign}
            BEGIN SELECT RAISE(IGNORE); END""",
        f"""CREATE TEMP TRIGGER xelto_{table}_au AFTER UPDATE ON main.{table}
            BEGIN SELECT xelto_count(); END""",
        f"""CREATE TEMP TRIGGER xelto_{table}_bd BEFORE DELETE ON main.{table}
            WHEN {foreign}
            BEGIN SELECT RAISE(IGNORE); END""",
        f"""CREATE TEMP TRIGGER xelto_{table}_ad AFTER DELETE ON main.{table}
            BEGIN
              DELETE FROM xelto_row_owner WHERE tbl = '{table}' AND row_id = OLD.id;
              SELECT xelto_count();
            END""",
        f"CREATE TEMP VIEW {table} AS SELECT b.* FROM main.{table} AS b WHERE {visible}",
    ]


class RowCounter:
    """Counts base-table rows changed by the scoping triggers.

    ``total_changes`` also counts the ownership bookkeeping, so the triggers
    report each real row through ``xelto_count()``; rows a guard skipped are
    not counted.
    """

    def __init__(self) -> None:
        self.value = 0

    def __call__(self) -> None:
        self.value += 1


def scope_connection(
    conn: sqlite3.Connection,
    owner: Callable[[], str | None],
    tables: tuple[str, ...] = USER_TABLES,
) -> RowCounter:
    """Install the scoping objects on ``conn``; returns its row counter."""
    counter = RowCounter()
    conn.create_function("xelto_owner", 0, owner)
    conn.create_function("xelto_count", 0, counter)
    for table in tables:
        for stmt in scope_ddl(table, columns(conn, table)):
            conn.execute(stmt)
    return counter


def unscope_connection(conn: sqlite3.Connection, tables: tuple[str, ...] = USER_TABLES) -> None:
    """Drop the scoping objects (before DDL, or to rebuild after a schema change)."""
    for table in tables:
        conn.execute(f"DROP VIEW IF EXISTS temp.{table}")
        for suffix in ("bus", "bds", "bn", "bi", "ai", "bu", "au", "bd", "ad"):
            conn.execute(f"DROP TRIGGER IF EXISTS temp.xelto_{table}_{suffix}")
