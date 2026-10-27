"""Merge guard: the XELTO policy is never looser than upstream's own gates by accident.

A route upstream reserves for admins (or the local machine) must be ADMIN or
DISABLED here, or USER_READ_ADMIN_WRITE for its reads, unless it is on the
reviewed allow-list below. When upstream adds such a gate to a route this
policy calls USER, this test fails until someone decides.
"""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "scripts" / "xelto"))
from dump_unclassified_routes import snapshot_routes  # noqa: E402
from route_gates import PRIVILEGED, route_gates  # noqa: E402

from xelto.policy import RouteClass, classify  # noqa: E402

# Reviewed relaxations: read-only status the ordinary UI shows every user.
# Upstream admits these reads anyway on a server without an API key, which is
# what multi-user mode requires.
USER_READS = {
    "GET /api/settings/analytics",
    "GET /api/settings/changelog",
    "GET /api/settings/compute-device",
    "GET /api/settings/dictation-refinement",
    "GET /api/settings/history-retention",
    "GET /api/settings/performance-profile",
    "GET /model/loaded",
    "GET /model/status",
    "GET /pronunciation",  # shared dictionary: users read, admins edit
    "GET /pronunciation/export",
    "GET /sysinfo",
    "GET /system/asr-backends",
    "GET /system/info",
    "GET /system/notifications",
}
# USER here, but upstream still refuses non-admins (loopback-only deletes).
UPSTREAM_STILL_GATES = {
    "DELETE /export/history/{export_id}",
    "DELETE /longform/jobs/{job_id}",
}
_STRICT = {RouteClass.ADMIN, RouteClass.DISABLED}


def _looser_than_upstream() -> list[str]:
    gates = route_gates()
    found = []
    for method, path in snapshot_routes():
        route = f"{method} {path}"
        if not gates.get(route, frozenset()) & PRIVILEGED:
            continue
        cls, _ = classify(method, path)
        if cls in _STRICT:
            continue
        if cls is RouteClass.USER_READ_ADMIN_WRITE and method != "GET":
            continue
        if route in USER_READS | UPSTREAM_STILL_GATES:
            continue
        found.append(f"{route} -> {cls.value} (upstream: {','.join(sorted(gates[route]))})")
    return found


def test_policy_is_not_looser_than_upstream():
    looser = _looser_than_upstream()
    assert not looser, (
        "Routes upstream restricts that the XELTO policy lets ordinary users reach:\n  "
        + "\n  ".join(looser)
        + "\nClassify them ADMIN/DISABLED in backend/xelto/policy.py, or review and add "
        "them to the allow-list in this test."
    )


def test_allow_lists_are_current():
    gates = route_gates()
    snapshot = {f"{m} {p}" for m, p in snapshot_routes()}
    for route in USER_READS | UPSTREAM_STILL_GATES:
        assert route in snapshot, f"{route} no longer exists; drop it from the allow-list"
        assert gates[route] & PRIVILEGED, f"{route} is no longer gated upstream; drop it"
        assert classify(*route.split(" ", 1))[0] in {RouteClass.USER,
                                                     RouteClass.USER_READ_ADMIN_WRITE}


def test_extractor_sees_the_routers():
    # Guards the guard: an AST change upstream must not make it silently see nothing.
    gates = route_gates()
    assert len(gates) > 300
    assert "require_admin" in gates["POST /engines/select"]
    assert "require_native_access" in gates["POST /export"]
