"""Shared fixtures: multi-user configuration, a fake Keycloak, a small gated app."""
from __future__ import annotations

import sqlite3
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import Depends, FastAPI, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

import core.db as db_module
from api.dependencies import require_admin, require_consumer
from xelto import install, migrate
from xelto.identity import Identity, current_identity

from .fake_idp import CLIENT_ID, CLIENT_SECRET, ISSUER, FakeIdP

PUBLIC_URL = "https://studio.test"
MARKER = "x-test-backend"

MULTIUSER_ENV = {
    "OMNIVOICE_MULTIUSER": "1",
    "OMNIVOICE_SERVER_MODE": "1",
    "OIDC_ISSUER": ISSUER,
    "OIDC_CLIENT_ID": CLIENT_ID,
    "OIDC_CLIENT_SECRET": CLIENT_SECRET,
    "OMNIVOICE_PUBLIC_URL": PUBLIC_URL,
    "OMNIVOICE_ALLOWED_ORIGINS": PUBLIC_URL,
    "OMNIVOICE_MULTIUSER_SECRET": "x" * 40,
    # Recorded as empty so monkeypatch also undoes what install() sets.
    "OMNIVOICE_TRUSTED_NETWORKS": "",
    "OMNIVOICE_TRUST_HOST_LOOPBACK": "",
    "OMNIVOICE_API_KEY": "",
    "OMNIVOICE_MULTIUSER_ALLOW_MASTER_KEY": "",
    "OMNIVOICE_MULTIUSER_INSECURE_HTTP": "",
    "OIDC_DISCOVERY_URL": "",
    "OIDC_API_AUDIENCE": "",
}


class _Marker:
    """Stand-in for upstream's BackendMarkerMiddleware (outermost)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        async def marked(message):
            if message["type"] == "http.response.start":
                message.setdefault("headers", []).append((MARKER.encode(), b"1"))
            await send(message)

        await self.app(scope, receive, marked)


def build_app() -> FastAPI:
    """Real upstream dependencies on templates the policy classifies like main's routes."""
    app = FastAPI()

    def who(request: Request) -> dict:
        identity = current_identity.get()
        return {"sub": identity.sub if identity else None,
                "client": request.client.host if request.client else None}

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/profiles", dependencies=[Depends(require_consumer)])
    def profiles(request: Request):
        return who(request)

    @app.post("/profiles", dependencies=[Depends(require_consumer)])
    def create_profile(request: Request):
        return who(request)

    @app.get("/engines")
    def engines(request: Request):
        return who(request)

    @app.post("/engines/select", dependencies=[Depends(require_admin)])
    def select_engine(request: Request):
        return who(request)

    @app.get("/system/info", dependencies=[Depends(require_admin)])
    def system_info(request: Request):
        return who(request)

    @app.get("/system/logs", dependencies=[Depends(require_admin)])
    def logs(request: Request):
        return who(request)

    @app.get("/pronunciation", dependencies=[Depends(require_admin)])
    def pronunciation(request: Request):
        return who(request)

    @app.post("/pronunciation", dependencies=[Depends(require_admin)])
    def add_pronunciation(request: Request):
        return who(request)

    @app.post("/system/network/enable")
    def network_enable():
        return {"enabled": True}

    @app.websocket("/ws/events")
    async def events(ws: WebSocket):
        await ws.accept()
        identity = current_identity.get()
        await ws.send_json({"sub": identity.sub if identity else None})
        await ws.close()

    app.add_middleware(CORSMiddleware, allow_origins=[PUBLIC_URL], allow_credentials=True,
                       allow_methods=["*"], allow_headers=["*"])
    app.add_middleware(_Marker)
    return app


@pytest.fixture
def keep_init_db(monkeypatch):
    # install() wraps core.db.init_db and the dub job creators, turns on row
    # scoping and patches the executors; undo all of it after each test so
    # upstream tests in the same session see plain upstream behaviour.
    from services import dub_pipeline

    from xelto import db_scope, hooks, propagation

    monkeypatch.setattr(db_module, "init_db", db_module.init_db)
    monkeypatch.setattr(dub_pipeline, "put_job", dub_pipeline.put_job)
    monkeypatch.setattr(dub_pipeline, "put_and_save_job", dub_pipeline.put_and_save_job)
    monkeypatch.setattr(db_scope, "_active", db_scope._active)
    yield
    propagation.uninstall()
    hooks.forget_dub_owners()


@pytest.fixture
def multiuser_env(monkeypatch, keep_init_db):
    for name, value in MULTIUSER_ENV.items():
        monkeypatch.setenv(name, value)
    return monkeypatch


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "omnivoice.db"
    monkeypatch.setattr(db_module, "DB_PATH", str(path))
    migrate.upgrade(str(path))
    return path


ALICE = Identity(sub="alice-sub", username="alice")
BOB = Identity(sub="bob-sub", username="bob")


@pytest.fixture
def scoped_db(db_path, keep_init_db, monkeypatch):
    """Upstream's schema plus the fork's tables, row scoping on, one shared demo voice."""
    from core.db import _BASE_SCHEMA
    from xelto import db_scope

    conn = sqlite3.connect(str(db_path))
    conn.executescript(_BASE_SCHEMA)
    conn.execute("INSERT INTO voice_profiles(id, name, is_demo) VALUES ('demo', 'Demo', 1)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(db_scope, "_active", True)
    return db_path


@pytest.fixture
def idp():
    idp = FakeIdP()
    idp.alice = idp.add_user("alice", "voicestudio-user")
    idp.bob = idp.add_user("bob", "voicestudio-user")
    idp.admin = idp.add_user("admin", "voicestudio-admin")
    idp.carol = idp.add_user("carol")  # signs in to Keycloak, but has no VoiceStudio role
    return idp


@pytest.fixture
def gated_app(multiuser_env, db_path, idp):
    app = build_app()
    install(app, oidc_transport=idp.transport)
    return app


def new_client(app, **kwargs) -> TestClient:
    return TestClient(app, base_url=PUBLIC_URL, follow_redirects=False, **kwargs)


@pytest.fixture
def client(gated_app):
    with new_client(gated_app) as client:
        yield client


def sign_in(client: TestClient, idp: FakeIdP, user, next_path: str = "/"):
    """Run the browser side of the code flow; returns the callback response."""
    login = client.get("/api/auth/oidc/login", params={"next": next_path})
    assert login.status_code == 302, login.text
    params = idp.authorize(user, login.headers["location"])
    return client.get("/api/auth/oidc/callback", params=params)


def query(url: str) -> dict:
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


CSRF = {"x-voicestudio-csrf": "1", "origin": PUBLIC_URL}
