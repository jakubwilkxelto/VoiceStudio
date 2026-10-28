"""The gate on the real app: upstream's own gates agree with the multi-user principals.

Runs ``real_app_probe.py`` in a subprocess (``main`` installs the gate at import
from the environment, which must not leak into this process).
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PROBE = Path(__file__).with_name("real_app_probe.py")


@pytest.fixture(scope="module")
def probe():
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("OIDC_", "OMNIVOICE_"))}
    proc = subprocess.run([sys.executable, str(PROBE)], env=env, capture_output=True,
                          text=True, timeout=300, cwd=str(PROBE.parents[2]))
    assert proc.returncode == 0, proc.stderr[-4000:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_route_snapshot_is_unchanged(probe):
    # The sign-in endpoints are served by the gate, not registered as routes.
    assert probe["routes_unchanged"] is True


def test_sign_in_endpoints_exist(probe):
    assert probe["me"] == {"enabled": True, "authenticated": False}


def test_anonymous_and_loopback_need_sign_in(probe):
    assert probe["health"] == 200
    assert probe["anonymous_profiles"] == 401
    assert probe["loopback_anonymous"] == 401


def test_user_on_the_real_app(probe):
    assert probe["user_profiles"] == 200
    assert probe["user_system_info"] == 200
    assert probe["user_system_logs"] == 403
    assert probe["user_engine_select"] == 403


def test_admin_passes_the_real_upstream_admin_gates(probe):
    assert probe["admin_system_logs"] == 200
    assert probe["admin_settings_storage"] == 200
    assert probe["admin_network_enable"] == 403  # DISABLED for everyone


def test_every_id_a_user_route_takes_outside_its_path_is_accounted_for(probe):
    """A new body or query id upstream adds must be checked by the gate or known safe."""
    from xelto.policy import BODY_IDS_VIA_VIEWS, BODY_OWNERS

    known = {f"{m} {p}": {field} for (m, p), (field, _) in BODY_OWNERS.items()}
    for (m, p), names in BODY_IDS_VIA_VIEWS.items():
        known.setdefault(f"{m} {p}", set()).update(names.split(","))
    missing = {route: sorted(set(ids) - known.get(route, set()))
               for route, ids in probe["body_ids"].items() if set(ids) - known.get(route, set())}
    assert not missing, (
        "USER routes take ids outside their path that the gate does not know about; add them "
        f"to BODY_OWNERS or BODY_IDS_VIA_VIEWS in backend/xelto/policy.py: {missing}"
    )
