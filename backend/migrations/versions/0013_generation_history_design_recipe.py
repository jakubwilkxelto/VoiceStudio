"""Generation takes: Voice Design recipe on generation_history

Revision ID: 0013_generation_history_design_recipe
Revises: 0012_call_sessions
Create Date: 2026-10-01 00:00:00.000000

Adds a nullable ``generation_history.design_recipe TEXT`` column. For a Voice
Design take it holds a small JSON document (the written description, the
details the user picked by hand, and the details the description mapped to) so
that reopening the take can rebuild the workspace. It is display metadata
only and never feeds synthesis. Older rows keep NULL and clients fall back to
the stored instruct text.

Additive and idempotent: the column is added only when ``PRAGMA table_info``
does not list it, so a fresh install whose ``_BASE_SCHEMA`` already declares
it passes through untouched. A DB with no ``generation_history`` table is
skipped, since the startup self-heal creates it complete. DBs where alembic
cannot run at all get the column from ``_reconcile_additive_columns``.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0013_generation_history_design_recipe"
down_revision: Union[str, None] = "0012_call_sessions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "generation_history"
_COLUMN = "design_recipe"


def _has_table(name: str) -> bool:
    row = op.get_bind().execute(
        sa.text("SELECT name FROM sqlite_master WHERE type='table' AND name=:n"),
        {"n": name},
    ).fetchone()
    return row is not None


def _has_column(table: str, column: str) -> bool:
    rows = op.get_bind().execute(sa.text(f"PRAGMA table_info({table})")).fetchall()
    return any(r[1] == column for r in rows)


def upgrade() -> None:
    if not _has_table(_TABLE):
        return
    if not _has_column(_TABLE, _COLUMN):
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.Text(), nullable=True))


def downgrade() -> None:
    if _has_table(_TABLE) and _has_column(_TABLE, _COLUMN):
        op.drop_column(_TABLE, _COLUMN)
