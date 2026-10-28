"""The gate and served files: claims from responses, the owner check, lenient vs strict."""
import os
import time

import pytest
from fastapi.staticfiles import StaticFiles

import importlib
from xelto import install

from .conftest import build_app, new_client


def _config():
    # The live module: some upstream suites purge and re-import `core`, and
    # xelto.resources reads core.config at call time.
    return importlib.import_module("core.config")


@pytest.fixture
def files_app(multiuser_env, db_path, idp, tmp_path, monkeypatch):
    def make(mode=""):
        multiuser_env.setenv("OMNIVOICE_MULTIUSER_FILES", mode)
        outputs = tmp_path / "outputs"
        outputs.mkdir(exist_ok=True)
        for name in ("stray.wav", "old.wav"):
            (outputs / name).write_bytes(b"RIFF")
        hour_ago = time.time() - 3600
        os.utime(outputs / "old.wav", (hour_ago, hour_ago))
        monkeypatch.setattr(_config(), "OUTPUTS_DIR", str(outputs))
        app = build_app()

        @app.post("/convert")
        def convert():  # a producer: writes the file, answers with its URL
            (outputs / "conv.wav").write_bytes(b"RIFF")
            return {"audio_url": "/audio/conv.wav"}

        @app.get("/projects")
        def echo():  # hands back what a client stored earlier
            return {"state": {"audio": "/audio/old.wav"}}

        app.mount("/audio", StaticFiles(directory=str(outputs)), name="audio")
        install(app, oidc_transport=idp.transport)
        return app
    return make


def _bearer(idp, user):
    return {"authorization": f"Bearer {idp.access_token(user)}"}


def test_a_file_belongs_to_whoever_a_request_produced_it_for(files_app, idp):
    with new_client(files_app()) as client:
        alice, bob = _bearer(idp, idp.alice), _bearer(idp, idp.bob)
        assert client.post("/convert", headers=alice).status_code == 200
        assert client.get("/audio/conv.wav", headers=alice).status_code == 200
        assert client.get("/audio/conv.wav", headers=bob).status_code == 404
        assert client.get("/audio/%2e%2e/omnivoice.db", headers=alice).status_code == 404


def test_strict_by_default_refuses_files_nobody_accounts_for(files_app, idp):
    with new_client(files_app()) as client:
        alice = _bearer(idp, idp.alice)
        assert client.get("/audio/stray.wav", headers=alice).status_code == 404


def test_lenient_mode_serves_files_nobody_accounts_for(files_app, idp):
    with new_client(files_app("lenient")) as client:
        assert client.get("/audio/stray.wav", headers=_bearer(idp, idp.bob)).status_code == 200


def test_an_echoed_path_claims_nothing(files_app, idp):
    with new_client(files_app()) as client:
        bob = _bearer(idp, idp.bob)
        assert client.get("/projects", headers=bob).status_code == 200
        assert client.get("/audio/old.wav", headers=bob).status_code == 404


def test_files_mode_is_validated(multiuser_env):
    from xelto.config import ConfigError, load_settings

    multiuser_env.setenv("OMNIVOICE_MULTIUSER_FILES", "loose")
    with pytest.raises(ConfigError, match="OMNIVOICE_MULTIUSER_FILES"):
        load_settings()
