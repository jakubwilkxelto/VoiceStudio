"""Ordinary work end to end in strict multi-user mode (P4), on the real app with fake models.

Runs ``e2e_probe.py`` in a subprocess (``main`` installs the gate at import
from the environment, which must not leak into this process).
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PROBE = Path(__file__).with_name("e2e_probe.py")


@pytest.fixture(scope="module")
def probe():
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("OIDC_", "OMNIVOICE_"))}
    proc = subprocess.run([sys.executable, str(PROBE)], env=env, capture_output=True,
                          text=True, timeout=600, cwd=str(PROBE.parents[2]))
    assert proc.returncode == 0, proc.stderr[-4000:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_no_lost_identity_or_unowned_file_during_ordinary_work(probe):
    assert probe["collector_works"] is True
    assert probe["trouble"] == [], probe["trouble"]


def test_generation(probe):
    assert probe["generate"] == 200
    assert probe["generate_file"] == [200, 404]
    assert (probe["history_alice"], probe["history_bob"]) == (1, 0)


def test_cloning(probe):
    assert probe["clone"] == 200
    assert probe["clone_profile_bob"] == 404
    assert probe["clone_file"] == [200, 404]
    assert probe["generate_cloned"] == 200
    assert probe["bob_used_alices_voice"] is False


def _needs_ffmpeg(probe):
    if not probe["ffmpeg"]:
        pytest.skip("no ffmpeg (imageio-ffmpeg) to build test media")


def test_dubbing(probe):
    _needs_ffmpeg(probe)
    assert probe["dub_upload"] == 202
    assert probe["dub_prepare"][-1] == "ready"
    assert probe["dub_transcribe"] == 200
    assert probe["dub_generate"] == 200
    assert probe["dub_generate_task"][-1] == "done"
    for key in ("dub_download", "dub_download_audio", "dub_media"):
        assert probe[key] == [200, 404], (key, probe[key])


def test_batch(probe):
    _needs_ffmpeg(probe)
    assert probe["batch_enqueue"] == 200
    assert probe["batch_status"] == "done"
    assert probe["batch_download"] == [200, 404]


def test_longform(probe):
    assert probe["audiobook"] == 200
    assert probe["audiobook_events"][-1] == "done"
    assert probe["audiobook_file"] == [200, 404]
