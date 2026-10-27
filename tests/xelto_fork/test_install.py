"""xelto.install: a no-op with the flag off, a gate inside CORS with it on."""
import os
import sqlite3

import pytest
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

import core.db as db_module
from xelto import install
from xelto.config import USER_PEER_NETWORK, ConfigError
from xelto.middleware import XeltoMultiUserMiddleware

from .conftest import _Marker, build_app


def test_flag_off_changes_nothing(monkeypatch, keep_init_db):
    monkeypatch.delenv("OMNIVOICE_MULTIUSER", raising=False)
    app = build_app()
    before = list(app.user_middleware)
    original_init_db = db_module.init_db

    install(app)

    assert app.user_middleware == before
    assert db_module.init_db is original_init_db
    conn = db_module.get_db()
    try:
        assert type(conn) is sqlite3.Connection
    finally:
        conn.close()
    with TestClient(app) as client:
        assert client.get("/engines").status_code == 200


def test_gate_sits_directly_inside_cors(gated_app):
    classes = [m.cls for m in gated_app.user_middleware]
    assert classes == [_Marker, CORSMiddleware, XeltoMultiUserMiddleware]


def test_users_get_a_synthetic_trusted_network(gated_app):
    assert os.environ["OMNIVOICE_TRUSTED_NETWORKS"] == USER_PEER_NETWORK


def test_misconfiguration_stops_startup(multiuser_env):
    multiuser_env.setenv("OIDC_CLIENT_SECRET", "")
    with pytest.raises(ConfigError, match="OIDC_CLIENT_SECRET"):
        install(build_app())


def test_init_db_runs_fork_migrations(gated_app, tmp_path, monkeypatch):
    db = tmp_path / "fresh.db"
    monkeypatch.setattr(db_module, "DB_PATH", str(db))

    db_module.init_db()

    conn = sqlite3.connect(str(db))
    try:
        assert conn.execute("SELECT version_num FROM xelto_alembic_version").fetchone() == ("x0001",)
    finally:
        conn.close()


def test_install_twice_wraps_init_db_once(gated_app, idp):
    wrapped = db_module.init_db
    install(build_app(), oidc_transport=idp.transport)
    assert db_module.init_db is wrapped
