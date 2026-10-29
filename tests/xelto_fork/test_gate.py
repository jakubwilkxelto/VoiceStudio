"""The gate: who reaches which route, and what upstream sees behind it."""
import sqlite3

import pytest
from starlette.websockets import WebSocketDisconnect

from xelto import sessions
from xelto.config import ADMIN_PEER, USER_PEER

from .conftest import CSRF, MARKER, PUBLIC_URL, fork_db, new_client, sign_in


@pytest.fixture
def alice(gated_app, idp):
    with new_client(gated_app) as client:
        sign_in(client, idp, idp.alice)
        yield client


@pytest.fixture
def admin(gated_app, idp):
    with new_client(gated_app) as client:
        sign_in(client, idp, idp.admin)
        yield client


# ── Anonymous ────────────────────────────────────────────────────────────

def test_public_route_needs_no_sign_in(client):
    assert client.get("/health").status_code == 200


def test_anonymous_is_refused_with_marker_and_cors(client):
    res = client.get("/profiles", headers={"origin": PUBLIC_URL})
    assert res.status_code == 401
    assert res.json() == {"detail": "login required"}
    assert res.headers.get(MARKER) == "1"
    assert res.headers.get("access-control-allow-origin") == PUBLIC_URL


def test_preflight_reaches_cors(client):
    res = client.options("/profiles", headers={"origin": PUBLIC_URL,
                                               "access-control-request-method": "POST"})
    assert res.status_code == 200


def test_loopback_peer_gets_no_trust(gated_app):
    # A reverse proxy or an in-process caller on this machine.
    with new_client(gated_app, client=("127.0.0.1", 50000)) as local:
        assert local.get("/profiles").status_code == 401
        assert local.get("/system/logs").status_code == 401


def test_forwarded_for_is_meaningless(client):
    res = client.get("/profiles", headers={"x-forwarded-for": "127.0.0.1", "x-real-ip": "::1"})
    assert res.status_code == 401


@pytest.mark.parametrize("kwargs", [
    {"headers": {"x-omnivoice-pin": "123456"}},
    {"params": {"api_key": "anything"}},
    {"headers": {"cookie": "ov_session=anything"}},
    {"headers": {"cookie": "__Host-xelto_sid=forged"}},
])
def test_upstream_and_forged_credentials_are_refused(client, kwargs):
    assert client.get("/profiles", **kwargs).status_code == 401


# ── Users and admins ─────────────────────────────────────────────────────

def test_user_reaches_user_routes_as_a_synthetic_trusted_peer(alice):
    res = alice.get("/profiles")
    assert res.status_code == 200
    assert res.json() == {"sub": "sub-alice", "client": USER_PEER}


def test_user_write_needs_csrf(alice):
    assert alice.post("/profiles").status_code == 403
    assert alice.post("/profiles", headers={"x-voicestudio-csrf": "1",
                                            "origin": "https://evil.test"}).status_code == 403
    assert alice.post("/profiles", headers=CSRF).status_code == 200


def test_user_reads_shared_lists_but_cannot_administer(alice):
    assert alice.get("/engines").status_code == 200
    assert alice.get("/system/info").status_code == 200  # read-only allow-list
    assert alice.get("/pronunciation").status_code == 200
    for res in (alice.post("/engines/select", headers=CSRF),
                alice.post("/pronunciation", headers=CSRF),
                alice.get("/system/logs")):
        assert res.status_code == 403
        assert res.json() == {"detail": "admin role required"}


def test_admin_passes_upstream_admin_gates(admin):
    res = admin.post("/engines/select", headers=CSRF)
    assert res.status_code == 200
    assert res.json() == {"sub": "sub-admin", "client": ADMIN_PEER}
    assert admin.get("/system/logs").status_code == 200
    assert admin.post("/pronunciation", headers=CSRF).status_code == 200


# Browsers send no Origin on a same-origin GET; Sec-Fetch-Site is their proof.
SAME_ORIGIN_GET = {"x-voicestudio-csrf": "1", "sec-fetch-site": "same-origin"}
MODELS = "/api/settings/llm-providers/google-ai/models"


def test_admin_reaches_side_effectful_get_from_the_app(admin):
    res = admin.get(MODELS, headers=SAME_ORIGIN_GET)
    assert res.status_code == 200
    assert res.json() == {"sub": "sub-admin", "client": ADMIN_PEER}


@pytest.mark.parametrize("headers", [
    {},
    {"x-voicestudio-csrf": "1"},
    {"sec-fetch-site": "same-origin"},
    {"x-voicestudio-csrf": "1", "sec-fetch-site": "cross-site"},
    {"x-voicestudio-csrf": "1", "sec-fetch-site": "same-site"},
    {**SAME_ORIGIN_GET, "origin": "https://evil.example"},
])
def test_side_effectful_get_still_needs_the_browser_proof(admin, headers):
    assert admin.get(MODELS, headers=headers).status_code == 403


def test_side_effectful_get_is_admin_only(alice):
    res = alice.get(MODELS, headers=SAME_ORIGIN_GET)
    assert res.status_code == 403
    assert res.json() == {"detail": "admin role required"}


def test_admin_writes_need_csrf_too(admin):
    assert admin.post("/engines/select").status_code == 403


@pytest.mark.parametrize("who", ["alice", "admin"])
def test_disabled_routes_are_off_for_everyone(request, who):
    res = request.getfixturevalue(who).post("/system/network/enable", headers=CSRF)
    assert res.status_code == 403
    assert res.json() == {"detail": "not available in multi-user mode"}


# ── Bearer tokens (programmatic clients) ─────────────────────────────────

def test_bearer_jwt(client, idp):
    token = idp.access_token(idp.alice)
    res = client.get("/profiles", headers={"authorization": f"Bearer {token}"})
    assert res.status_code == 200 and res.json()["sub"] == "sub-alice"
    # Header credentials are not ambient, so no CSRF marker is needed.
    assert client.post("/profiles", headers={"authorization": f"Bearer {token}"}).status_code == 200


def test_bearer_admin_jwt(client, idp):
    token = idp.access_token(idp.admin)
    assert client.post("/engines/select",
                       headers={"authorization": f"Bearer {token}"}).status_code == 200


@pytest.mark.parametrize("mutate", ["wrong_aud", "wrong_iss", "expired", "foreign_key", "garbage",
                                    "no_role"])
def test_bad_bearer_tokens(client, idp, mutate):
    from cryptography.hazmat.primitives.asymmetric import rsa

    token = {
        "wrong_aud": lambda: idp.access_token(idp.alice, aud="account"),
        "wrong_iss": lambda: idp.access_token(idp.alice, iss="https://evil.test/realms/x"),
        "expired": lambda: idp.access_token(idp.alice, exp_in=-3600),
        "foreign_key": lambda: idp.sign({"sub": "sub-alice"},
                                        key=rsa.generate_private_key(public_exponent=65537,
                                                                     key_size=2048)),
        "garbage": lambda: "not-a-jwt",
        "no_role": lambda: idp.access_token(idp.carol),
    }[mutate]()
    res = client.get("/profiles", headers={"authorization": f"Bearer {token}"})
    assert res.status_code == 401


def test_master_key_only_with_explicit_opt_in(multiuser_env, db_path, idp):
    from xelto import install

    from .conftest import build_app

    multiuser_env.setenv("OMNIVOICE_API_KEY", "m" * 40)
    multiuser_env.setenv("OMNIVOICE_MULTIUSER_ALLOW_MASTER_KEY", "1")
    app = build_app()
    install(app, oidc_transport=idp.transport)
    with new_client(app) as client:
        res = client.post("/engines/select", headers={"authorization": "Bearer " + "m" * 40})
        assert res.status_code == 200
        assert client.get("/profiles", headers={"authorization": "Bearer wrong"}).status_code == 401


# ── Session lifetime and role changes ───────────────────────────────────

def _age_sessions(db_path, seconds):
    """Move every session's clocks into the past (patching time.time would also
    age the fake provider's tokens)."""
    conn = sqlite3.connect(str(fork_db(db_path)))
    try:
        with conn:
            conn.execute(
                "UPDATE xelto_sessions SET last_seen_at = last_seen_at - ?, "
                "roles_checked_at = roles_checked_at - ?, created_at = created_at - ?",
                (seconds, seconds, seconds),
            )
    finally:
        conn.close()


def test_user_disabled_in_keycloak_loses_access(alice, idp, db_path):
    idp.alice.enabled = False
    assert alice.get("/profiles").status_code == 200  # roles still fresh
    _age_sessions(db_path, sessions.ROLE_RECHECK + 1)
    assert alice.get("/profiles").status_code == 401
    assert alice.get("/api/auth/oidc/me").json()["authenticated"] is False


def test_role_change_takes_effect_on_recheck(admin, idp, db_path):
    idp.admin.roles = {"voicestudio-user"}
    _age_sessions(db_path, sessions.ROLE_RECHECK + 1)
    assert admin.get("/system/logs").status_code == 403
    assert admin.get("/profiles").status_code == 200


def test_keycloak_outage_keeps_existing_sessions(alice, idp, db_path):
    idp.down = True
    _age_sessions(db_path, sessions.ROLE_RECHECK + 1)
    assert alice.get("/profiles").status_code == 200


def test_idle_session_expires(alice, db_path):
    _age_sessions(db_path, sessions.IDLE_TIMEOUT + 1)
    assert alice.get("/profiles").status_code == 401


# ── WebSockets ───────────────────────────────────────────────────────────

def _ws_headers(client, origin=PUBLIC_URL):
    # TestClient does not send Secure cookies to wss:// URLs; browsers do.
    sid = client.cookies.get("__Host-xelto_sid")
    return {"origin": origin, **({"cookie": f"__Host-xelto_sid={sid}"} if sid else {})}


def test_websocket_carries_identity(alice):
    with alice.websocket_connect("/ws/events", headers=_ws_headers(alice)) as ws:
        assert ws.receive_json() == {"sub": "sub-alice"}


def test_websocket_refuses_anonymous_and_foreign_origin(client, alice):
    for conn, headers in ((client, _ws_headers(client)),
                          (alice, _ws_headers(alice, "https://evil.test"))):
        with pytest.raises(WebSocketDisconnect) as exc:
            with conn.websocket_connect("/ws/events", headers=headers) as ws:
                ws.receive_json()
        assert exc.value.code == 1008
