"""The full isolation matrix (P4): bob cannot list, read, fetch, edit or delete alice's work.

Runs ``isolation_matrix_probe.py`` in a subprocess (``main`` installs the gate
at import from the environment, which must not leak into this process).
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PROBE = Path(__file__).with_name("isolation_matrix_probe.py")


@pytest.fixture(scope="module")
def probe():
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("OIDC_", "OMNIVOICE_"))}
    proc = subprocess.run([sys.executable, str(PROBE)], env=env, capture_output=True,
                          text=True, timeout=600, cwd=str(PROBE.parents[2]))
    assert proc.returncode == 0, proc.stderr[-4000:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_every_gated_route_refuses_alices_ids(probe):
    assert probe["health"] == 200
    assert probe["gated"], "no gated routes exercised"
    refused = {route: status for route, status in probe["gated"].items() if status != 404}
    assert not refused, refused


def test_routes_behind_the_views_reveal_nothing(probe):
    # These answer as for an id that does not exist (404, or an empty glossary);
    # what matters is that no response carries alice's data and nothing of hers changes.
    assert probe["via_views"]
    assert probe["leaks"] == {}


def test_alices_rows_and_files_survive_everything_bob_tried(probe):
    assert probe["alice_state_unchanged"] is True, probe["alice_state_diff"]


def test_alice_still_reaches_her_own(probe):
    assert set(probe["alice_reads"].values()) == {200}, probe["alice_reads"]


def test_ids_in_request_bodies_are_checked(probe):
    assert probe["ingest_url"] == 404
    assert probe["translate"] == 404


def test_bundle_export_packs_only_the_callers_files(probe):
    assert probe["bundle_named_bob"] == []
    assert len(probe["bundle_named_alice"]) == 1
    assert probe["bundle_retained_bob"] == 400
    assert probe["bundle_retained_alice"] == ["assets/take.wav"]


def test_dictation_preferences_are_the_admins(probe):
    assert probe["dictation_prefs_write"] == 403
    assert probe["dictation_prefs_read"] == 200


def test_openai_speech_cannot_use_alices_voice(probe):
    assert probe["v1_speech_is_audio"] is False, probe["v1_speech_with_alices_voice"]


def test_an_echoed_path_does_not_make_a_file_bobs(probe):
    assert probe["echo_project_created"] < 300
    bob_sub = "sub-bob"
    assert bob_sub not in probe["echo_claims"].values(), probe["echo_claims"]
    assert set(probe["bob_files_after_echo"].values()) == {404}, probe["bob_files_after_echo"]


def test_emotion_clips_resolve_only_to_the_callers_files(probe):
    assert probe["emo_clip_bob"] == "refused"
    assert probe["emo_clip_alice"] == "xa-voice.wav"


def test_every_user_data_event_reaches_only_its_owner(probe):
    assert sorted(probe["alice_events"]) == sorted([*probe["user_kinds"], "model_status"])
    assert probe["bob_first_event"] == "model_status"
