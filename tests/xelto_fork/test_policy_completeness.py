"""Merge guard: every upstream route is either classified or knowingly unclassified.

``tests/fixtures/api_routes.txt`` is kept equal to the live app by upstream's
``tests/test_api_route_inventory.py``, so reading it here covers the whole API
without importing the app. A route that an upstream merge adds shows up as a
diff below until it is classified in ``backend/xelto/policy.py`` (or, while P1
is pending, accepted into the unclassified list).
"""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "scripts" / "xelto"))
from dump_unclassified_routes import UNCLASSIFIED, snapshot_routes, unclassified  # noqa: E402

from xelto.policy import DEFAULT_CLASS, RouteClass, classify  # noqa: E402

_REGEN = "uv run python scripts/xelto/dump_unclassified_routes.py"


def _committed() -> set[str]:
    return {
        ln.strip() for ln in UNCLASSIFIED.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.startswith("#")
    }


def test_unclassified_routes_match_committed_list():
    live = set(unclassified())
    committed = _committed()
    new = sorted(live - committed)
    stale = sorted(committed - live)
    assert not new and not stale, (
        "XELTO route policy drifted from the route snapshot.\n"
        + (f"New unclassified routes (classify them in backend/xelto/policy.py):\n  "
           + "\n  ".join(new) + "\n" if new else "")
        + (f"Listed but now classified or removed:\n  " + "\n  ".join(stale) + "\n" if stale else "")
        + f"Then regenerate: {_REGEN}"
    )


def test_unclassified_default_is_admin():
    assert DEFAULT_CLASS is RouteClass.ADMIN
    assert classify("POST", "/a/route/upstream/adds/tomorrow") == (RouteClass.ADMIN, False)


def test_health_is_public():
    assert ("GET", "/health") in snapshot_routes()
    assert classify("GET", "/health") == (RouteClass.PUBLIC, True)
