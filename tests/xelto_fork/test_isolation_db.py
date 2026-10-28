"""Per-user rows on the real app: bob cannot list, read, change or delete alice's data.

Runs ``isolation_probe.py`` in a subprocess (``main`` installs the gate at
import from the environment, which must not leak into this process).
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PROBE = Path(__file__).with_name("isolation_probe.py")
TABLES = ("voice_profiles", "generation_history", "dub_history", "studio_projects",
          "export_history", "glossary_terms", "jobs")


@pytest.fixture(scope="module")
def probe():
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("OIDC_", "OMNIVOICE_"))}
    proc = subprocess.run([sys.executable, str(PROBE)], env=env, capture_output=True,
                          text=True, timeout=300, cwd=str(PROBE.parents[2]))
    assert proc.returncode == 0, proc.stderr[-4000:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_alice_owns_one_of_everything(probe):
    assert probe["health"] == 200
    assert probe["before"] == dict.fromkeys(TABLES, 1)


def test_bob_lists_nothing_of_alices(probe):
    lists = probe["bob_lists"]
    assert all(not any(i.startswith("xa-") for i in ids) for ids in lists.values()), lists
    # Shared demo voices stay visible to everyone.
    assert "xdemo" in lists["profiles"]


def test_bob_cannot_read_alices_items(probe):
    assert set(probe["bob_reads"].values()) == {404}, probe["bob_reads"]


def test_bob_cannot_change_or_delete_alices_items(probe):
    writes = probe["bob_writes"]
    for name in ("profile_put", "history_star", "project_put", "project_patch", "term_put"):
        assert writes[name] == 404, (name, writes[name])
    assert writes["demo_put"] == 403  # shared, read-only
    # Deletes and clears answer like upstream does for an unknown id, but
    # nothing of alice's is gone or changed.
    assert probe["after_bob"] == probe["before"]
    assert probe["names_after_bob"] == {
        "profile": "Alice voice", "project": "Alice project", "term": "hej",
        "starred": 0, "demo": "Demo",
    }


def test_dub_job_cache_is_per_user(probe):
    assert probe["bob_get_live_dub"] is False
    assert probe["alice_get_live_dub"] is True


def test_alice_keeps_full_use_of_her_data(probe):
    lists = probe["alice_lists"]
    assert "xa-profile" in lists["profiles"] and "xdemo" in lists["profiles"]
    for name in ("history", "dub", "projects", "exports"):
        assert any(i.startswith("xa-") for i in lists[name]), (name, lists[name])
    assert set(probe["alice_reads"].values()) == {200}
    writes = probe["alice_writes"]
    # rowcount through the views: upstream's "deleted"/"not found" answers stay right.
    assert writes["term_put"] == 200
    assert writes["term_delete"] == {"deleted": True}
    assert writes["profile_delete"] == 200 and writes["history_delete"] == 200
    after = probe["after_alice"]
    assert after["voice_profiles"] == after["generation_history"] == after["glossary_terms"] == 0
