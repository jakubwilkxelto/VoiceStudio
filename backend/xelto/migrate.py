"""The fork's own alembic chain (``backend/xelto/migrations``), run on ``xelto.db``.

The chain has its own ``xelto_alembic_version`` table and its own database
file (:mod:`xelto.store`), so upstream's next migration never meets a second
head and upstream's file never holds a fork table. Fork migrations only
create ``xelto_*`` tables, so no pre-migration backup is taken here.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from xelto import store

logger = logging.getLogger("omnivoice.xelto")

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
VERSION_TABLE = "xelto_alembic_version"


def _config(db_path: str | None = None):
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path or store.db_path()}")
    # Same reason as core.db._run_alembic_upgrade (#1174): never touch app logging.
    cfg.attributes["configure_logger"] = False
    return cfg


def upgrade(db_path: str | None = None) -> None:
    """Bring the fork's database (default: ``xelto.db`` next to the app's DB) to head."""
    from alembic import command

    command.upgrade(_config(db_path), "head")


def downgrade(revision: str = "base", db_path: str | None = None) -> None:
    from alembic import command

    command.downgrade(_config(db_path), revision)


def upgrade_for(core_path: str) -> None:
    """Startup path: migrate ``xelto.db``, then move any fork tables out of ``core_path``."""
    upgrade(store.db_path(core_path))
    adopt_legacy_tables(core_path)


def _fork_tables(conn: sqlite3.Connection, schema: str) -> list[str]:
    return [r[0] for r in conn.execute(
        f"SELECT name FROM {schema}.sqlite_master WHERE type = 'table' "
        r"AND name LIKE 'xelto\_%' ESCAPE '\'"
    )]


def adopt_legacy_tables(core_path: str) -> dict[str, int]:
    """Move fork tables that earlier versions created in ``omnivoice.db`` into ``xelto.db``.

    Until the split, the fork kept its tables in upstream's file. Their rows are
    copied (rows already in ``xelto.db`` win) and the tables are dropped from
    upstream's file, in one transaction. A no-op once nothing is left to move.
    """
    if not Path(core_path).exists():
        return {}
    conn = sqlite3.connect(core_path)
    try:
        legacy = _fork_tables(conn, "main")
        if not legacy:
            return {}
        store.attach(conn, core_path)
        current = set(_fork_tables(conn, store.SCHEMA))
        moved = {}
        conn.execute("BEGIN IMMEDIATE")
        for table in legacy:
            if table in current and table != VERSION_TABLE:
                main_cols = {r[1] for r in conn.execute(f"PRAGMA main.table_info({table})")}
                cols = ", ".join(
                    r[1] for r in conn.execute(f"PRAGMA {store.SCHEMA}.table_info({table})")
                    if r[1] in main_cols
                )
                moved[table] = conn.execute(
                    f"INSERT OR IGNORE INTO {store.SCHEMA}.{table} ({cols}) "
                    f"SELECT {cols} FROM main.{table}"
                ).rowcount
            conn.execute(f"DROP TABLE main.{table}")
        conn.commit()
        logger.info("moved multi-user tables out of %s into %s: %s",
                    Path(core_path).name, store.FILENAME, moved)
        return moved
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()
