"""The fork's own alembic chain (``backend/xelto/migrations``).

Kept apart from upstream's chain, with its own ``xelto_alembic_version`` table,
so upstream's next migration never meets a second head. Fork migrations only
create ``xelto_*`` tables, so no pre-migration backup is taken here.
"""
from __future__ import annotations

from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
VERSION_TABLE = "xelto_alembic_version"


def _config(db_path: str | None = None):
    from alembic.config import Config

    from core.config import DB_PATH

    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path or DB_PATH}")
    # Same reason as core.db._run_alembic_upgrade (#1174): never touch app logging.
    cfg.attributes["configure_logger"] = False
    return cfg


def upgrade(db_path: str | None = None) -> None:
    from alembic import command

    command.upgrade(_config(db_path), "head")


def downgrade(revision: str = "base", db_path: str | None = None) -> None:
    from alembic import command

    command.downgrade(_config(db_path), revision)
