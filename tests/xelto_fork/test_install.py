"""xelto.install: a no-op with the flag off, a fail-closed gate inside CORS with it on."""
import sqlite3

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import core.db as db_module
from xelto import install
from xelto.middleware import XeltoMultiUserMiddleware

MARKER = "x-test-backend"
ORIGIN = "http://localhost:3901"


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


def _app() -> FastAPI:
    """Same middleware order as backend/main.py: CORS, then the marker outermost."""
    app = FastAPI()

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/profiles")
    def profiles():
        return []

    @app.websocket("/ws/events")
    async def events(ws: WebSocket):
        await ws.accept()
        await ws.send_text("hello")
        await ws.close()

    app.add_middleware(CORSMiddleware, allow_origins=[ORIGIN], allow_credentials=True,
                       allow_methods=["*"], allow_headers=["*"])
    app.add_middleware(_Marker)
    return app


@pytest.fixture
def keep_init_db(monkeypatch):
    # install() wraps core.db.init_db; restore the original after each test.
    monkeypatch.setattr(db_module, "init_db", db_module.init_db)


def test_flag_off_changes_nothing(monkeypatch, keep_init_db):
    monkeypatch.delenv("OMNIVOICE_MULTIUSER", raising=False)
    app = _app()
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
        assert client.get("/profiles").status_code == 200


@pytest.fixture
def gated(monkeypatch, keep_init_db):
    monkeypatch.setenv("OMNIVOICE_MULTIUSER", "1")
    app = _app()
    install(app)
    return app


def test_gate_sits_directly_inside_cors(gated):
    classes = [m.cls for m in gated.user_middleware]
    assert classes == [_Marker, CORSMiddleware, XeltoMultiUserMiddleware]


def test_public_route_passes(gated):
    with TestClient(gated) as client:
        assert client.get("/health").status_code == 200


def test_unclassified_route_is_denied_with_marker_and_cors(gated):
    with TestClient(gated) as client:
        res = client.get("/profiles", headers={"Origin": ORIGIN})
    assert res.status_code == 401
    assert res.json() == {"detail": "login required"}
    assert res.headers.get(MARKER) == "1"
    assert res.headers.get("access-control-allow-origin") == ORIGIN


def test_unknown_path_is_denied(gated):
    with TestClient(gated) as client:
        assert client.get("/no-such-route").status_code == 401


def test_preflight_reaches_cors(gated):
    with TestClient(gated) as client:
        res = client.options("/profiles", headers={
            "Origin": ORIGIN, "Access-Control-Request-Method": "GET",
        })
    assert res.status_code == 200


def test_websocket_is_closed_1008(gated):
    with TestClient(gated) as client:
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect("/ws/events") as ws:
                ws.receive_text()
    assert exc.value.code == 1008


def test_init_db_runs_fork_migrations(gated, tmp_path, monkeypatch):
    db = tmp_path / "omnivoice.db"
    monkeypatch.setattr(db_module, "DB_PATH", str(db))

    db_module.init_db()

    conn = sqlite3.connect(str(db))
    try:
        assert conn.execute("SELECT version_num FROM xelto_alembic_version").fetchone() == ("x0001",)
    finally:
        conn.close()


def test_install_twice_wraps_init_db_once(gated):
    wrapped = db_module.init_db
    install(_app())
    assert db_module.init_db is wrapped
