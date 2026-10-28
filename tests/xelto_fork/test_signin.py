"""The sign-in endpoints: authorization code flow with PKCE, sessions, sign-out."""
import sqlite3

import pytest

from xelto.bff import safe_next

from .conftest import CSRF, PUBLIC_URL, fork_db, query, sign_in
from .fake_idp import ISSUER


def test_login_redirects_to_keycloak_with_pkce(client):
    res = client.get("/api/auth/oidc/login", params={"next": "/dub"})
    assert res.status_code == 302
    location = res.headers["location"]
    assert location.startswith(f"{ISSUER}/protocol/openid-connect/auth?")
    params = query(location)
    assert params["client_id"] == "voicestudio"
    assert params["redirect_uri"] == f"{PUBLIC_URL}/api/auth/oidc/callback"
    assert params["code_challenge_method"] == "S256"
    assert params["state"] and params["nonce"] and params["code_challenge"]
    cookie = res.headers["set-cookie"]
    assert cookie.startswith("__Host-xelto_tx=")
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=lax" in cookie


def test_callback_signs_in_and_lands_on_next(client, idp, db_path):
    res = sign_in(client, idp, idp.alice, "/dub?job=1")
    assert res.status_code == 302
    assert res.headers["location"] == "/dub?job=1"
    session_cookie = [c for c in res.headers.get_list("set-cookie") if c.startswith("__Host-xelto_sid=")]
    assert session_cookie and "HttpOnly" in session_cookie[0] and "Secure" in session_cookie[0]

    me = client.get("/api/auth/oidc/me").json()
    assert me["authenticated"] and me["username"] == "alice" and not me["is_admin"]
    assert me["roles"] == ["voicestudio-user"]

    conn = sqlite3.connect(str(fork_db(db_path)))
    try:
        stored = conn.execute("SELECT id_hash, refresh_token_enc FROM xelto_sessions").fetchone()
    finally:
        conn.close()
    raw = client.cookies.get("__Host-xelto_sid")
    assert stored[0] != raw and raw not in str(stored[1])  # hash and ciphertext only


def test_admin_role_is_reported(client, idp):
    sign_in(client, idp, idp.admin)
    assert client.get("/api/auth/oidc/me").json()["is_admin"] is True


def test_me_without_session(client):
    assert client.get("/api/auth/oidc/me").json() == {"enabled": True, "authenticated": False}


def test_state_mismatch_is_refused(client, idp):
    login = client.get("/api/auth/oidc/login")
    params = idp.authorize(idp.alice, login.headers["location"])
    res = client.get("/api/auth/oidc/callback", params={**params, "state": "forged"})
    assert res.status_code == 400
    assert client.get("/api/auth/oidc/me").json()["authenticated"] is False


def test_callback_without_transaction_cookie_is_refused(gated_app, idp):
    from .conftest import new_client

    with new_client(gated_app) as attacker, new_client(gated_app) as victim:
        login = attacker.get("/api/auth/oidc/login")
        params = idp.authorize(idp.alice, login.headers["location"])
        # Login CSRF: the victim's browser never started this sign-in.
        assert victim.get("/api/auth/oidc/callback", params=params).status_code == 400


def test_code_cannot_be_replayed(client, idp):
    login = client.get("/api/auth/oidc/login")
    params = idp.authorize(idp.alice, login.headers["location"])
    assert client.get("/api/auth/oidc/callback", params=params).status_code == 302
    client.get("/api/auth/oidc/login")  # fresh transaction cookie, same old code
    assert client.get("/api/auth/oidc/callback", params=params).status_code == 400


def test_user_without_voicestudio_role_is_not_enrolled(client, idp):
    res = sign_in(client, idp, idp.carol)
    assert res.status_code == 403
    assert "not enrolled" in res.text
    assert client.get("/api/auth/oidc/me").json()["authenticated"] is False


def test_identity_provider_error_is_reported(client, idp):
    res = client.get("/api/auth/oidc/callback", params={"error": "access_denied"})
    assert res.status_code == 400


def test_identity_provider_down(client, idp):
    idp.down = True
    assert client.get("/api/auth/oidc/login").status_code == 503


@pytest.mark.parametrize("value, expected", [
    ("/dub", "/dub"), ("/a?b=c#d", "/a?b=c#d"), (None, "/"), ("", "/"),
    ("https://evil.test/", "/"), ("//evil.test/", "/"), ("/\\evil.test", "/"),
    ("javascript:alert(1)", "/"), ("/x\r\nSet-Cookie: a=b", "/"),
])
def test_next_stays_on_this_site(value, expected):
    assert safe_next(value) == expected


def test_logout_needs_csrf_and_ends_the_session(client, idp):
    sign_in(client, idp, idp.alice)
    assert client.post("/api/auth/oidc/logout").status_code == 403

    res = client.post("/api/auth/oidc/logout", headers=CSRF)
    assert res.status_code == 200
    assert res.json()["logout_url"].startswith(f"{ISSUER}/protocol/openid-connect/logout?")
    assert client.get("/api/auth/oidc/me").json()["authenticated"] is False


def test_stolen_cookie_is_useless_after_logout(gated_app, idp):
    from .conftest import new_client

    with new_client(gated_app) as browser:
        sign_in(browser, idp, idp.alice)
        stolen = browser.cookies.get("__Host-xelto_sid")
        browser.post("/api/auth/oidc/logout", headers=CSRF)
    with new_client(gated_app, cookies={"__Host-xelto_sid": stolen}) as thief:
        assert thief.get("/profiles").status_code == 401
