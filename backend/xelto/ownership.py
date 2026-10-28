"""Bulk ownership changes: first-login bootstrap and the admin CLI.

All of it runs on plain connections to ``omnivoice.db`` with the fork's
database attached (:func:`xelto.store.connect_both`), because it has to see
rows across owners.
"""
from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable

from xelto import hooks
from xelto.db_views import SHARED_READ, USER_TABLES

BOOTSTRAP_KEY = "bootstrap_owner"


def _unowned_where(table: str) -> str:
    shared = SHARED_READ.get(table)
    clause = (f"NOT EXISTS (SELECT 1 FROM xelto.xelto_row_owner o WHERE o.tbl = '{table}' "
              f"AND o.row_id = t.id)")
    # Shared rows (demo voices) stay unowned, so every user keeps seeing them.
    return f"{clause} AND NOT COALESCE(t.{shared}, 0)" if shared else clause


def assign_unowned(conn: sqlite3.Connection, owner: str) -> dict[str, int]:
    """Give ``owner`` every row of the per-user tables that has no owner yet."""
    counts = {}
    for table in USER_TABLES:
        cur = conn.execute(
            f"INSERT INTO xelto.xelto_row_owner(tbl, row_id, owner) "
            f"SELECT '{table}', t.id, ? FROM main.{table} AS t WHERE {_unowned_where(table)}",
            (owner,),
        )
        counts[table] = cur.rowcount
    hooks.forget_dub_owners()
    return counts


def purge_user(conn: sqlite3.Connection, owner: str) -> dict[str, int]:
    """Delete the rows ``owner`` holds in the per-user tables, and their ownership.

    Files those rows point to stay on disk; removing them is the admin's call.
    """
    counts = {}
    for table in USER_TABLES:
        cur = conn.execute(
            f"DELETE FROM main.{table} WHERE id IN (SELECT row_id FROM xelto.xelto_row_owner "
            f"WHERE tbl = '{table}' AND owner = ?)",
            (owner,),
        )
        counts[table] = cur.rowcount
    conn.execute("DELETE FROM xelto.xelto_row_owner WHERE owner = ?", (owner,))
    hooks.forget_dub_owners()
    return counts


def bootstrap(connect: Callable[[], sqlite3.Connection], owner: str) -> dict[str, int] | None:
    """Hand the pre-existing data to the bootstrap owner, once per database.

    Returns the counts, or None when the bootstrap already happened.
    """
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        done = conn.execute("SELECT value FROM xelto.xelto_meta WHERE key = ?", (BOOTSTRAP_KEY,)).fetchone()
        if done is not None:
            conn.rollback()
            return None
        counts = assign_unowned(conn, owner)
        conn.execute(
            "INSERT INTO xelto.xelto_meta(key, value) VALUES (?, ?)",
            (BOOTSTRAP_KEY, f"{owner} {time.time():.0f}"),
        )
        conn.commit()
        return counts
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()
