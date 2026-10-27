"""XELTO multi-user base tables.

Revision ID: x0001
Revises:

Creates only ``xelto_*`` tables; no upstream table is touched. Row ownership
lives in a side table instead of an ``owner_id`` column, so upstream table
rebuilds and ``SELECT *`` stay exactly as upstream wrote them.
"""
from collections.abc import Sequence

from alembic import op

revision: str = "x0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = ("xelto",)
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS xelto_users (
            sub TEXT PRIMARY KEY,
            username TEXT NOT NULL DEFAULT '',
            name TEXT NOT NULL DEFAULT '',
            email TEXT NOT NULL DEFAULT '',
            roles TEXT NOT NULL DEFAULT '[]',
            disabled INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL,
            last_login_at REAL
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS xelto_sessions (
            id_hash TEXT PRIMARY KEY,
            sub TEXT NOT NULL REFERENCES xelto_users(sub) ON DELETE CASCADE,
            refresh_token_enc BLOB,
            roles TEXT NOT NULL DEFAULT '[]',
            created_at REAL NOT NULL,
            last_seen_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            roles_checked_at REAL NOT NULL,
            user_agent TEXT NOT NULL DEFAULT ''
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_xelto_sessions_sub ON xelto_sessions(sub)")
    op.execute("""
        CREATE TABLE IF NOT EXISTS xelto_row_owner (
            tbl TEXT NOT NULL,
            row_id TEXT NOT NULL,
            owner TEXT NOT NULL,
            PRIMARY KEY (tbl, row_id)
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_xelto_row_owner_owner ON xelto_row_owner(owner, tbl)"
    )
    op.execute("""
        CREATE TABLE IF NOT EXISTS xelto_file_owner (
            path TEXT PRIMARY KEY,
            owner TEXT NOT NULL,
            created_at REAL NOT NULL
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS xelto_meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)


def downgrade() -> None:
    for table in (
        "xelto_meta",
        "xelto_file_owner",
        "xelto_row_owner",
        "xelto_sessions",
        "xelto_users",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table}")
