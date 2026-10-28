"""Who may fetch a served file, and how files learn their owners from responses."""
import os
import time

import pytest

import importlib
import core.db as db_module
from xelto import file_guard, resources
from xelto.file_guard import Verdict
from xelto.identity import use_identity
from xelto.resources import ResponseTap

from .conftest import ALICE, BOB


def _config():
    # The live module: some upstream suites purge and re-import `core`, and
    # xelto.resources reads core.config at call time.
    return importlib.import_module("core.config")


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    roots = {}
    for name, attr in (("outputs", "OUTPUTS_DIR"), ("voices", "VOICES_DIR"), ("preview", "PREVIEW_DIR")):
        path = tmp_path / name
        path.mkdir()
        monkeypatch.setattr(_config(), attr, str(path))
        roots[name] = path
    return roots


@pytest.fixture
def alice_data(scoped_db, dirs):
    """alice: a generation, a longform job and a voice; plus files nobody accounts for."""
    from core import job_store

    now = time.time()
    with use_identity(ALICE):
        with db_module.db_conn() as conn:
            conn.execute("INSERT INTO generation_history(id, text, audio_path, created_at) "
                         "VALUES ('h1', 't', 'gen1.wav', ?)", (now,))
            conn.execute("INSERT INTO voice_profiles(id, name, ref_audio_path, created_at) "
                         "VALUES ('pa', 'v', 'pa.wav', ?)", (now,))
        job_store.create("lf1", type="audiobook")
    return dirs


def test_file_keys_stay_inside_their_root():
    assert resources.file_key("audio", "a/b.wav") == "audio/a/b.wav"
    assert resources.file_key("audio", "/x%20y.wav") == "audio/x y.wav"
    assert resources.file_key("audio", "../omnivoice.db") is None
    assert resources.file_key("audio", "a/%2e%2e/%2e%2e/x") is None
    assert resources.file_key("nope", "x") is None


@pytest.mark.parametrize("key, alice, bob", [
    ("audio/gen1.wav", Verdict.MINE, Verdict.OTHER),
    ("audio/audiobook_lf1.mp3", Verdict.MINE, Verdict.OTHER),
    ("audio/audiobook_lf1/chapter_01.wav", Verdict.MINE, Verdict.OTHER),
    ("voice_audio/pa.wav", Verdict.MINE, Verdict.OTHER),
    ("voice_audio/pa-1a2b3c4d.wav", Verdict.MINE, Verdict.OTHER),
    ("voice_audio/pa.portrait.jpg", Verdict.MINE, Verdict.OTHER),
    ("voice_audio/demo.wav", Verdict.SHARED, Verdict.SHARED),
    ("audio/voice_gallery/x.wav", Verdict.SHARED, Verdict.SHARED),
    ("audio/stray.wav", Verdict.UNKNOWN, Verdict.UNKNOWN),
    ("voice_audio/pab.wav", Verdict.UNKNOWN, Verdict.UNKNOWN),  # not pa's: no separator
    ("preview/u.mp4", Verdict.UNKNOWN, Verdict.UNKNOWN),
])
def test_database_ties_files_to_their_owner(alice_data, key, alice, bob):
    assert file_guard.verdict(key, ALICE.sub) is alice
    assert file_guard.verdict(key, BOB.sub) is bob


def test_a_claim_wins_and_is_never_reassigned(alice_data):
    resources.claim("preview/u.mp4", ALICE.sub)
    resources.claim("preview/u.mp4", BOB.sub)
    assert file_guard.verdict("preview/u.mp4", ALICE.sub) is Verdict.MINE
    assert file_guard.verdict("preview/u.mp4", BOB.sub) is Verdict.OTHER


def test_unaccounted_files_depend_on_the_mode(alice_data, caplog):
    assert file_guard.allowed("audio/stray.wav", BOB.sub, "lenient")
    assert "serving unowned audio/stray.wav" in caplog.text
    assert not file_guard.allowed("audio/stray.wav", BOB.sub, "strict")
    # Someone else's file is refused in either mode.
    assert not file_guard.allowed("audio/gen1.wav", BOB.sub, "lenient")
    assert not file_guard.allowed(None, BOB.sub, "lenient")


def _start(status=200, ctype=b"application/json", **headers):
    return {"type": "http.response.start", "status": status,
            "headers": [(b"content-type", ctype)] + [(k.encode(), v.encode()) for k, v in headers.items()]}


def test_tap_claims_files_a_response_points_to(scoped_db, dirs):
    for name in ("conv.wav", "hdr.wav", "abc12345.wav", "audiobook_x.mp3"):
        (dirs["outputs"] / name).write_bytes(b"x")
    (dirs["preview"] / "clip.mp4").write_bytes(b"x")

    tap = ResponseTap(ALICE.sub)
    tap.start(_start(**{"x-audio-path": "hdr.wav"}))
    # A URL split across two chunks is still seen.
    tap.body(b'{"audio_url": "/aud')
    tap.body(b'io/conv.wav", "video": "/preview/clip.mp4", "gone": "/audio/missing.wav",'
             b' "api": "/audio/voices"}')
    sse = ResponseTap(ALICE.sub)
    sse.start(_start(ctype=b"text/event-stream", **{"x-audio-id": "abc12345"}))
    sse.body(b'data: {"type": "done", "output": "audiobook_x.mp3"}\n\n')

    owned = resources.owned_keys(ALICE.sub, "audio") | {
        "preview/" + k for k in resources.owned_keys(ALICE.sub, "preview")}
    assert owned == {"hdr.wav", "conv.wav", "abc12345.wav", "audiobook_x.mp3", "preview/clip.mp4"}


def test_tap_ignores_failures_and_binary_bodies(scoped_db, dirs):
    (dirs["outputs"] / "a.wav").write_bytes(b"x")
    failed = ResponseTap(ALICE.sub)
    failed.start(_start(status=404, **{"x-audio-path": "a.wav"}))
    failed.body(b'{"url": "/audio/a.wav"}')
    binary = ResponseTap(ALICE.sub)
    binary.start(_start(ctype=b"audio/wav"))
    binary.body(b"/audio/a.wav")
    assert resources.owner_of("audio/a.wav") is None


def test_tap_claims_only_files_written_during_the_request(scoped_db, dirs):
    old = dirs["outputs"] / "old.wav"
    old.write_bytes(b"x")
    hour_ago = time.time() - 3600
    os.utime(old, (hour_ago, hour_ago))
    tap = ResponseTap(ALICE.sub)
    tap.start(_start())
    tap.body(b'{"state": {"audio": "/audio/old.wav"}}')  # echoed from stored state
    assert resources.owner_of("audio/old.wav") is None


def test_tap_never_claims_voice_files(scoped_db, dirs):
    (dirs["voices"] / "pa.wav").write_bytes(b"x")
    tap = ResponseTap(BOB.sub)
    tap.start(_start())
    tap.body(b'{"ref": "/voice_audio/pa.wav"}')
    assert resources.owner_of("voice_audio/pa.wav") is None


def test_the_database_beats_a_wrong_claim(alice_data):
    resources.claim("audio/gen1.wav", BOB.sub)  # e.g. learned from an echo
    assert file_guard.verdict("audio/gen1.wav", ALICE.sub) is Verdict.MINE
    assert file_guard.verdict("audio/gen1.wav", BOB.sub) is Verdict.OTHER


def test_path_allowed_maps_data_folders_to_owners(alice_data, tmp_path, monkeypatch):
    from xelto import hooks

    dub = tmp_path / "dub_jobs"
    (dub / "dj1").mkdir(parents=True)
    monkeypatch.setattr(_config(), "DUB_DIR", str(dub))
    for name in ("gen1.wav", "stray.wav"):
        (alice_data["outputs"] / name).write_bytes(b"x")
    (alice_data["voices"] / "pa.wav").write_bytes(b"x")
    (dub / "dj1" / "original.mp4").write_bytes(b"x")
    with use_identity(ALICE):
        hooks.record_dub_owner("dj1")
    try:
        outputs, voices = alice_data["outputs"], alice_data["voices"]
        assert file_guard.path_allowed(outputs / "gen1.wav", ALICE.sub, "strict")
        assert not file_guard.path_allowed(outputs / "gen1.wav", BOB.sub, "strict")
        assert file_guard.path_allowed(voices / "pa.wav", ALICE.sub, "strict")
        assert not file_guard.path_allowed(voices / "pa.wav", BOB.sub, "lenient")
        assert file_guard.path_allowed(dub / "dj1" / "original.mp4", ALICE.sub, "strict")
        assert not file_guard.path_allowed(dub / "dj1" / "original.mp4", BOB.sub, "strict")
        assert not file_guard.path_allowed(outputs / "stray.wav", ALICE.sub, "strict")
        assert not file_guard.path_allowed(tmp_path / "elsewhere.wav", ALICE.sub, "lenient")
    finally:
        hooks.forget_dub_owners()
