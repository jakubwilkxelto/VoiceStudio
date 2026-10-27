#!/usr/bin/env python3
"""Regenerate the list of routes the XELTO policy does not classify yet.

Reads upstream's route snapshot (``tests/fixtures/api_routes.txt``) and writes
every route that no rule in ``backend/xelto/policy.py`` matches to
``tests/xelto_fork/fixtures/unclassified_routes.txt``. Those routes fall back to
ADMIN. Run after classifying routes or after an upstream merge adds some:

    uv run python scripts/xelto/dump_unclassified_routes.py
"""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "backend"))

from xelto.policy import classify  # noqa: E402

ROUTES = _REPO / "tests" / "fixtures" / "api_routes.txt"
UNCLASSIFIED = _REPO / "tests" / "xelto_fork" / "fixtures" / "unclassified_routes.txt"

_HEADER = (
    "# Routes no XELTO policy rule classifies yet (they default to ADMIN).\n"
    "# Regenerate with scripts/xelto/dump_unclassified_routes.py; checked by\n"
    "# tests/xelto_fork/test_policy_completeness.py. P1 shrinks this to nothing.\n"
)


def snapshot_routes() -> list[tuple[str, str]]:
    rows = []
    for line in ROUTES.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        method, path = line.split(" ", 1)
        rows.append((method, path))
    return rows


def unclassified() -> list[str]:
    return sorted(
        f"{method} {path}" for method, path in snapshot_routes()
        if not classify(method, path)[1]
    )


def main() -> None:
    UNCLASSIFIED.parent.mkdir(parents=True, exist_ok=True)
    UNCLASSIFIED.write_text(_HEADER + "\n".join(unclassified()) + "\n", encoding="utf-8")
    print(f"wrote {UNCLASSIFIED.relative_to(_REPO)}")


if __name__ == "__main__":
    main()
