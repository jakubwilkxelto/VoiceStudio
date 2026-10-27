#!/usr/bin/env python3
"""Per-connection cost of row scoping (P0 spike; target < 3 ms median).

Compares a plain ``core.db.get_db``-style connection with the same connection
after ``xelto.db_views.scope_connection`` on all seven user tables, against a
temporary DB with upstream's real schema.

    uv run python scripts/xelto/bench_db_views.py [iterations]
"""
import sqlite3
import statistics
import sys
import tempfile
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "backend"))

from core.db import _BASE_SCHEMA  # noqa: E402
from xelto.db_views import scope_connection  # noqa: E402


def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _measure(path: str, scoped: bool, n: int) -> list[float]:
    samples = []
    for _ in range(n):
        start = time.perf_counter()
        conn = _connect(path)
        if scoped:
            scope_connection(conn, lambda: "alice")
        conn.execute("SELECT id FROM voice_profiles LIMIT 1").fetchall()
        conn.close()
        samples.append((time.perf_counter() - start) * 1000)
    return samples


def _report(label: str, samples: list[float]) -> None:
    ordered = sorted(samples)
    p95 = ordered[int(len(ordered) * 0.95) - 1]
    print(f"{label:<8} median {statistics.median(samples):6.3f} ms   p95 {p95:6.3f} ms")


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 1000
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "omnivoice.db")
        conn = sqlite3.connect(path)
        conn.executescript(_BASE_SCHEMA)
        conn.execute(
            "CREATE TABLE xelto_row_owner (tbl TEXT NOT NULL, row_id TEXT NOT NULL, "
            "owner TEXT NOT NULL, PRIMARY KEY (tbl, row_id))"
        )
        conn.commit()
        conn.close()
        _measure(path, True, 20)  # warm-up
        plain = _measure(path, False, n)
        scoped = _measure(path, True, n)
    print(f"{n} connections, SQLite {sqlite3.sqlite_version}, Python {sys.version.split()[0]}")
    _report("plain", plain)
    _report("scoped", scoped)
    print(f"overhead median {statistics.median(scoped) - statistics.median(plain):.3f} ms")


if __name__ == "__main__":
    main()
