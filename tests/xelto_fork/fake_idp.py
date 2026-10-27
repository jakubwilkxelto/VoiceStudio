"""An in-process stand-in for Keycloak: discovery, JWKS and the token endpoint.

Tokens are real RS256 JWTs signed with a throwaway key, so the code under test
runs its full validation path. Serve it through :attr:`FakeIdP.transport`.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass, field
from urllib.parse import parse_qs

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

ISSUER = "https://auth.test/realms/voicestudio"
CLIENT_ID = "voicestudio"
CLIENT_SECRET = "s3cret-client"


@dataclass
class User:
    sub: str
    username: str
    roles: set[str]
    enabled: bool = True


@dataclass
class FakeIdP:
    users: dict[str, User] = field(default_factory=dict)
    codes: dict[str, tuple[str, str, str]] = field(default_factory=dict)  # code -> sub, nonce, challenge
    refresh_tokens: dict[str, str] = field(default_factory=dict)  # token -> sub
    token_requests: list[dict] = field(default_factory=list)
    down: bool = False

    def __post_init__(self):
        self._key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self._kid = "test-key"
        self.transport = httpx.MockTransport(self._handle)

    def add_user(self, username: str, *roles: str) -> User:
        user = User(sub=f"sub-{username}", username=username, roles=set(roles))
        self.users[user.sub] = user
        return user

    # ── Token minting ────────────────────────────────────────────────────

    def sign(self, claims: dict, *, key=None, kid: str | None = None) -> str:
        return jwt.encode(claims, key or self._key, algorithm="RS256",
                          headers={"kid": kid or self._kid})

    def access_token(self, user: User, *, aud=CLIENT_ID, iss=ISSUER, exp_in=300) -> str:
        now = int(time.time())
        return self.sign({
            "iss": iss, "sub": user.sub, "aud": aud, "azp": CLIENT_ID, "iat": now,
            "exp": now + exp_in, "preferred_username": user.username,
            "resource_access": {CLIENT_ID: {"roles": sorted(user.roles)}},
        })

    def id_token(self, user: User, nonce: str) -> str:
        now = int(time.time())
        return self.sign({
            "iss": ISSUER, "sub": user.sub, "aud": CLIENT_ID, "iat": now, "exp": now + 300,
            "nonce": nonce, "preferred_username": user.username,
            "name": user.username.title(), "email": f"{user.username}@example.test",
        })

    def authorize(self, user: User, authorization_url: str) -> dict:
        """What Keycloak would do after the user signs in: return ``code`` and ``state``."""
        query = parse_qs(authorization_url.split("?", 1)[1])
        code = secrets.token_urlsafe(16)
        self.codes[code] = (user.sub, query["nonce"][0], query["code_challenge"][0])
        return {"code": code, "state": query["state"][0]}

    # ── HTTP ─────────────────────────────────────────────────────────────

    def _handle(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("identity provider down", request=request)
        path = request.url.path
        if path.endswith("/.well-known/openid-configuration"):
            base = f"{ISSUER}/protocol/openid-connect"
            return httpx.Response(200, json={
                "issuer": ISSUER,
                "authorization_endpoint": f"{base}/auth",
                "token_endpoint": f"{base}/token",
                "jwks_uri": f"{base}/certs",
                "end_session_endpoint": f"{base}/logout",
            })
        if path.endswith("/certs"):
            jwk = json.loads(RSAAlgorithm.to_jwk(self._key.public_key()))
            jwk.update(kid=self._kid, use="sig", alg="RS256")
            return httpx.Response(200, json={"keys": [jwk]})
        if path.endswith("/token"):
            return self._token(request)
        return httpx.Response(404)

    def _token(self, request: httpx.Request) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        self.token_requests.append(form)
        expected = httpx.BasicAuth(CLIENT_ID, CLIENT_SECRET)._auth_header
        if request.headers.get("authorization") != expected:
            return httpx.Response(401, json={"error": "invalid_client"})
        if form.get("grant_type") == "authorization_code":
            entry = self.codes.pop(form.get("code", ""), None)
            if entry is None:
                return httpx.Response(400, json={"error": "invalid_grant"})
            sub, nonce, challenge = entry
            verifier = form.get("code_verifier", "")
            digest = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            if digest.rstrip(b"=").decode() != challenge:
                return httpx.Response(400, json={"error": "invalid_grant"})
            return self._issue(self.users[sub], nonce)
        if form.get("grant_type") == "refresh_token":
            sub = self.refresh_tokens.get(form.get("refresh_token", ""))
            user = self.users.get(sub) if sub else None
            if user is None or not user.enabled:
                return httpx.Response(400, json={"error": "invalid_grant"})
            return self._issue(user, None)
        return httpx.Response(400, json={"error": "unsupported_grant_type"})

    def _issue(self, user: User, nonce: str | None) -> httpx.Response:
        refresh = secrets.token_urlsafe(16)
        self.refresh_tokens[refresh] = user.sub
        body = {"access_token": self.access_token(user), "refresh_token": refresh,
                "token_type": "Bearer", "expires_in": 300}
        if nonce is not None:
            body["id_token"] = self.id_token(user, nonce)
        return httpx.Response(200, json=body)
