"""Admin commands for multi-user data: ``python -m xelto.cli <command>`` (run from ``backend/``).

    assign-unowned --to <user>   give <user> every row nobody owns yet
    purge-user <user>            delete <user>'s rows (files stay on disk)

<user> is a username or a Keycloak ``sub`` known from a previous sign-in.
``--db`` names the app's ``omnivoice.db``; the fork's ``xelto.db`` is the one
next to it (or ``OMNIVOICE_MULTIUSER_DB``).
Stop the server first or run it against a copy: these write the database
directly.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys

from xelto import ownership


def _resolve(conn: sqlite3.Connection, user: str) -> str:
    rows = conn.execute(
        "SELECT sub FROM xelto.xelto_users WHERE sub = ? OR username = ?", (user, user)
    ).fetchall()
    subs = {r[0] for r in rows}
    if len(subs) != 1:
        raise SystemExit(f"unknown or ambiguous user: {user!r} (they must have signed in once)")
    return subs.pop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m xelto.cli", description=__doc__.splitlines()[0])
    parser.add_argument("--db", help="the app's omnivoice.db (default: the configured one)")
    commands = parser.add_subparsers(dest="command", required=True)
    assign = commands.add_parser("assign-unowned", help="give a user every unowned row")
    assign.add_argument("--to", required=True, metavar="USER")
    purge = commands.add_parser("purge-user", help="delete a user's rows")
    purge.add_argument("user", metavar="USER")
    args = parser.parse_args(argv)

    if args.db:
        db_path = args.db
    else:
        from core.config import DB_PATH as db_path
    from xelto import migrate, store

    migrate.upgrade_for(db_path)
    conn = store.connect_both(db_path)
    try:
        if args.command == "assign-unowned":
            counts = ownership.assign_unowned(conn, _resolve(conn, args.to))
        else:
            counts = ownership.purge_user(conn, _resolve(conn, args.user))
        conn.commit()
    finally:
        conn.close()
    for table, n in counts.items():
        print(f"{table}: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
