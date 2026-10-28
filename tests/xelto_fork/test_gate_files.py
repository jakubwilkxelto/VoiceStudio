"""The gate and served files: claims from responses, the owner check, lenient vs strict."""
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
        for name in ("conv.wav", "stray.wav"):
            (outputs / name).write_bytes(b"RIFF")
        monkeypatch.setattr(_config(), "OUTPUTS_DIR", str(outputs))
        app = build_app()

        @app.post("/convert")
        def convert():  # a producer that answers with the file's URL
            return {"audio_url": "/audio/conv.wav"}

        app.mount("/audio", StaticFiles(directory=str(outputs)), name="audio")
        install(app, oidc_transport=idp.transport)
        return app
    return make


def _bearer(idp, user):
    return {"authorization": f"Bearer {idp.access_token(user)}"}


def test_a_file_belongs_to_whoever_was_handed_its_url(files_app, idp):
    with new_client(files_app()) as client:
        alice, bob = _bearer(idp, idp.alice), _bearer(idp, idp.bob)
        assert client.get("/audio/conv.wav", headers=bob).status_code == 200  # nobody's yet
        assert client.post("/convert", headers=alice).status_code == 200
        assert client.get("/audio/conv.wav", headers=alice).status_code == 200
        assert client.get("/audio/conv.wav", headers=bob).status_code == 404
        assert client.get("/audio/%2e%2e/omnivoice.db", headers=alice).status_code == 404


def test_strict_mode_refuses_files_nobody_accounts_for(files_app, idp):
    with new_client(files_app("strict")) as client:
        alice = _bearer(idp, idp.alice)
        assert client.get("/audio/stray.wav", headers=alice).status_code == 404
        client.post("/convert", headers=alice)
        assert client.get("/audio/conv.wav", headers=alice).status_code == 200


def test_files_mode_is_validated(multiuser_env):
    from xelto.config import ConfigError, load_settings

    multiuser_env.setenv("OMNIVOICE_MULTIUSER_FILES", "loose")
    with pytest.raises(ConfigError, match="OMNIVOICE_MULTIUSER_FILES"):
        load_settings()
