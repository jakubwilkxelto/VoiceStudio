"""Merge guard: every table upstream creates is classified as per-user or shared.

A new upstream table fails here until it is added to ``USER_TABLES`` (rows
scoped per user) or ``GLOBAL_TABLES`` (shared, admin-managed) in the fork.
"""
import re
from pathlib import Path

from xelto.db_scope import GLOBAL_TABLES
from xelto.db_views import USER_TABLES

BACKEND = Path(__file__).resolve().parents[2] / "backend"
_CREATE = re.compile(
    r"CREATE\s+(?:VIRTUAL\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"'`\[]?(?!IF\b)(\w+)", re.IGNORECASE
)
_OP_CREATE = re.compile(r"create_table\(\s*[\"'](\w+)")


def upstream_tables() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for path in BACKEND.rglob("*.py"):
        rel = path.relative_to(BACKEND).as_posix()
        if rel.startswith(("xelto/", "tests/")) or "/tests/" in rel:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for name in _CREATE.findall(text) + _OP_CREATE.findall(text):
            if not name.startswith(("xelto_", "sqlite_")):
                found.setdefault(name, set()).add(rel)
    return found


def test_scan_finds_upstreams_tables():
    tables = upstream_tables()
    assert {"voice_profiles", "jobs", "job_events", "settings"} <= set(tables)


def test_every_upstream_table_is_classified():
    unclassified = {
        name: sorted(files) for name, files in upstream_tables().items()
        if name not in USER_TABLES and name not in GLOBAL_TABLES
    }
    assert not unclassified, (
        "New upstream tables: add each to xelto.db_views.USER_TABLES (per-user rows) "
        f"or xelto.db_scope.GLOBAL_TABLES (shared): {unclassified}"
    )


def test_no_table_is_both():
    assert not set(USER_TABLES) & GLOBAL_TABLES
