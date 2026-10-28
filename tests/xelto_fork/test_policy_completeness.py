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

from xelto.policy import (  # noqa: E402
    BODY_IDS_VIA_VIEWS,
    BODY_OWNERS,
    DEFAULT_CLASS,
    Owner,
    RouteClass,
    classify,
    path_owner,
)
from xelto.resolvers import RESOLVERS  # noqa: E402

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


def test_every_user_route_with_a_parameter_has_an_owner_check():
    """A per-user route that names something by id must say how its owner is found.

    ``db-view`` means the handler only reaches it through the scoped views;
    anything kept in memory or on disk needs a resolver.
    """
    missing = sorted(
        f"{method} {path}" for method, path in snapshot_routes()
        if "{" in path
        and classify(method, path)[0] in (RouteClass.USER, RouteClass.USER_READ_ADMIN_WRITE)
        and path_owner(method, path) is None
    )
    assert not missing, (
        "Per-user routes with path parameters but no owner check; add them to "
        "PATH_OWNERS in backend/xelto/policy.py:\n  " + "\n  ".join(missing)
    )


def test_file_mounts_and_file_routes_are_guarded():
    for template in ("/audio/*", "/voice_audio/*", "/audio/{audio_id}.ogg",
                     "/audio/{audio_id}/quality", "/preview/{filename}"):
        assert path_owner("GET", template) is Owner.FILE, template
    assert path_owner("DELETE", "/dub/history/{history_id}") is Owner.DUB


def test_every_owner_check_except_views_and_shared_has_a_resolver():
    assert set(Owner) - set(RESOLVERS) == {Owner.DB_VIEW, Owner.SHARED}


def test_body_id_tables_name_live_user_routes():
    routes = set(snapshot_routes())
    for method, path in [*BODY_OWNERS, *BODY_IDS_VIA_VIEWS]:
        assert (method, path) in routes, f"{method} {path} is not in the route snapshot"
        assert classify(method, path)[0] is RouteClass.USER, f"{method} {path}"
    assert all(owner in RESOLVERS for _, owner in BODY_OWNERS.values())


def test_profile_routes_check_the_profile_owner():
    for method, path in (("DELETE", "/profiles/{profile_id}"), ("PUT", "/profiles/{profile_id}/image"),
                         ("GET", "/profiles/{profile_id}/audio"),
                         ("POST", "/personas/export/{profile_id}")):
        assert path_owner(method, path) is Owner.PROFILE, path


def test_dictation_preferences_are_the_admins():
    assert classify("POST", "/dictation/prefs")[0] is RouteClass.ADMIN
    assert classify("GET", "/dictation/prefs")[0] is RouteClass.USER
