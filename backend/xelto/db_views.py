"""Per-connection row scoping for SQLite (P0 spike prototype; P2 builds on it).

For each per-user table ``t`` a scoped connection gets, in the temp schema:

- ``TEMP VIEW t`` that shadows ``main.t`` (temp resolves first) and shows only
  rows the current owner holds in ``xelto_row_owner`` (plus shared demo rows);
- ``INSTEAD OF`` triggers that forward writes on the view to ``main.t``;
- triggers on ``main.t`` itself that stamp ownership on insert and refuse
  writes to another owner's row, so a statement that names ``main.t`` directly
  (the rewritten UPSERT) is still guarded.

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
        f"EXISTS (SELECT 1 FROM main.xelto_row_owner WHERE tbl = '{table}' "
        f"AND row_id = {row}.id AND owner = xelto_owner())"
    )


def scope_ddl(table: str, cols: list[Column]) -> list[str]:
    """Statements that scope ``table`` on one connection, in execution order.

    Triggers on ``main.t`` come first: once the view exists, the bare name
    ``t`` resolves to it.
    """
    names = [c.name for c in cols]
    col_list = ", ".join(names)
    values = ", ".join(
        f"COALESCE(NEW.{c.name}, {c.default})" if c.default not in (None, "NULL") else f"NEW.{c.name}"
        for c in cols
    )
    assignments = ", ".join(f"{n} = NEW.{n}" for n in names)
    shared = SHARED_READ.get(table)
    visible = f"({_owned(table, 'b')}" + (f" OR b.{shared})" if shared else ")")
    return [
        # Guards on the base table, reached by INSTEAD OF bodies and by any
        # statement that names main.t explicitly.
        f"""CREATE TEMP TRIGGER xelto_{table}_bi BEFORE INSERT ON main.{table}
            WHEN EXISTS (SELECT 1 FROM main.xelto_row_owner WHERE tbl = '{table}'
                         AND row_id = NEW.id AND owner <> xelto_owner())
            BEGIN SELECT RAISE(ABORT, '{OWNERSHIP_REFUSAL}'); END""",
        f"""CREATE TEMP TRIGGER xelto_{table}_ai AFTER INSERT ON main.{table}
            BEGIN
              INSERT OR REPLACE INTO main.xelto_row_owner(tbl, row_id, owner)
              VALUES ('{table}', NEW.id, xelto_owner());
              SELECT xelto_count();
            END""",
        f"""CREATE TEMP TRIGGER xelto_{table}_bu BEFORE UPDATE ON main.{table}
            WHEN NOT {_owned(table, 'OLD')}
            BEGIN SELECT RAISE(ABORT, '{OWNERSHIP_REFUSAL}'); END""",
        f"""CREATE TEMP TRIGGER xelto_{table}_au AFTER UPDATE ON main.{table}
            BEGIN SELECT xelto_count(); END""",
        f"""CREATE TEMP TRIGGER xelto_{table}_bd BEFORE DELETE ON main.{table}
            WHEN NOT {_owned(table, 'OLD')}
            BEGIN SELECT RAISE(ABORT, '{OWNERSHIP_REFUSAL}'); END""",
        f"""CREATE TEMP TRIGGER xelto_{table}_ad AFTER DELETE ON main.{table}
            BEGIN
              DELETE FROM main.xelto_row_owner WHERE tbl = '{table}' AND row_id = OLD.id;
              SELECT xelto_count();
            END""",
        f"CREATE TEMP VIEW {table} AS SELECT b.* FROM main.{table} AS b WHERE {visible}",
        f"""CREATE TEMP TRIGGER xelto_{table}_vi INSTEAD OF INSERT ON {table}
            BEGIN INSERT INTO main.{table} ({col_list}) VALUES ({values}); END""",
        f"""CREATE TEMP TRIGGER xelto_{table}_vu INSTEAD OF UPDATE ON {table}
            BEGIN UPDATE main.{table} SET {assignments} WHERE id = OLD.id; END""",
        f"""CREATE TEMP TRIGGER xelto_{table}_vd INSTEAD OF DELETE ON {table}
            BEGIN DELETE FROM main.{table} WHERE id = OLD.id; END""",
    ]


class RowCounter:
    """Counts base-table rows changed by the scoping triggers.

    ``cursor.rowcount`` is 0 for a write through a view (SQLite does not count
    INSTEAD OF trigger work) and ``total_changes`` also counts the ownership
    bookkeeping, so the triggers report each real row through ``xelto_count()``.
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
        for suffix in ("bi", "ai", "bu", "au", "bd", "ad"):
            conn.execute(f"DROP TRIGGER IF EXISTS temp.xelto_{table}_{suffix}")
