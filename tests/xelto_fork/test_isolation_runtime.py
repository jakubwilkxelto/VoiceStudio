"""Per-user files, tasks, batch jobs, bundles and events on the real app.

Runs ``isolation_runtime_probe.py`` in a subprocess (``main`` installs the gate
at import from the environment, which must not leak into this process).
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PROBE = Path(__file__).with_name("isolation_runtime_probe.py")


@pytest.fixture(scope="module")
def probe():
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("OIDC_", "OMNIVOICE_"))}
    proc = subprocess.run([sys.executable, str(PROBE)], env=env, capture_output=True,
                          text=True, timeout=300, cwd=str(PROBE.parents[2]))
    assert proc.returncode == 0, proc.stderr[-4000:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_bob_cannot_fetch_alices_files_tasks_or_jobs(probe):
    assert probe["health"] == 200
    assert set(probe["bob_reads"].values()) == {404}, probe["bob_reads"]


def test_bob_cannot_steer_alices_work(probe):
    assert set(probe["bob_writes"].values()) == {404}, probe["bob_writes"]
    # Deleting a dub removes its directory upstream; bob's attempt must not.
    assert probe["alice_dub_dir_kept"] is True


def test_bob_lists_none_of_alices_items(probe):
    lists = probe["bob_lists"]
    assert lists["batch"] == [] and lists["bundles"] == []
    assert lists["audiobook"] == {"jobs": []}
    assert "xa-voice" not in lists["voices"]


def test_alice_keeps_access_to_her_own(probe):
    assert set(probe["alice_reads"].values()) == {200}, probe["alice_reads"]
    lists = probe["alice_lists"]
    assert lists["batch"] == ["xa-batch"]
    assert lists["bundles"] == [probe["bundle"]]
    assert lists["audiobook"] == ["xa-longform"]
    assert "xa-voice" in lists["voices"]


def test_unaccounted_files_are_refused_by_default(probe):
    assert probe["stray_for_bob"] == 404


def test_events_about_alices_data_reach_only_alice(probe):
    assert probe["alice_events"] == ["profiles", "model_status"]
    assert probe["bob_first_event"] == "model_status"
