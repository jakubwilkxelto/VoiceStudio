"""OpenID Connect client for Keycloak: discovery, JWKS, code exchange, token checks.

All HTTP goes through one ``httpx.AsyncClient`` whose transport tests replace,
so the whole flow runs against an in-process fake identity provider.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
import jwt

from xelto.config import ADMIN_ROLE, USER_ROLE, Settings
from xelto.identity import Identity

ALGORITHMS = ["RS256", "RS384", "RS512", "PS256", "ES256", "ES384"]
_METADATA_TTL = 3600.0
_JWKS_MIN_REFETCH = 60.0
_LEEWAY = 30


class OidcError(Exception):
    """The identity provider answered something unusable."""


class InvalidGrant(OidcError):
    """The provider refused the grant: the user signed out, was disabled, or the token expired."""


class TokenRejected(OidcError):
    """A presented token failed validation."""


@dataclass(frozen=True)
class TokenSet:
    access_token: str
    refresh_token: str | None
    id_token: str | None


class OidcClient:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self._http = httpx.AsyncClient(transport=transport, timeout=10.0)
        self._metadata: dict | None = None
        self._metadata_at = 0.0
        self._keys: dict[str, jwt.PyJWK] = {}
        self._keys_at = 0.0

    async def aclose(self) -> None:
        await self._http.aclose()

    # ── Provider metadata ────────────────────────────────────────────────

    async def metadata(self) -> dict:
        now = time.monotonic()
        if self._metadata is None or now - self._metadata_at > _METADATA_TTL:
            response = await self._http.get(self.settings.discovery_url)
            if response.status_code != 200:
                raise OidcError(f"OIDC discovery failed: HTTP {response.status_code}")
            self._metadata = response.json()
            self._metadata_at = now
        return self._metadata

    async def _key(self, kid: str | None) -> jwt.PyJWK:
        if kid in self._keys:
            return self._keys[kid]
        now = time.monotonic()
        if now - self._keys_at < _JWKS_MIN_REFETCH and self._keys:
            raise TokenRejected("unknown signing key")
        response = await self._http.get((await self.metadata())["jwks_uri"])
        if response.status_code != 200:
            raise OidcError(f"JWKS fetch failed: HTTP {response.status_code}")
        keys = {}
        for entry in response.json().get("keys", []):
            if entry.get("use", "sig") != "sig":
                continue
            try:
                keys[entry.get("kid")] = jwt.PyJWK(entry)
            except jwt.PyJWKError:
                continue
        self._keys, self._keys_at = keys, now
        if kid not in keys:
            raise TokenRejected("unknown signing key")
        return keys[kid]

    # ── Authorization code flow ──────────────────────────────────────────

    async def authorization_url(self, *, state: str, nonce: str, code_challenge: str) -> str:
        endpoint = (await self.metadata())["authorization_endpoint"]
        query = urlencode({
            "response_type": "code",
            "client_id": self.settings.client_id,
            "redirect_uri": self.settings.redirect_uri,
            "scope": "openid profile email",
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        })
        return f"{endpoint}?{query}"

    async def end_session_url(self) -> str | None:
        endpoint = (await self.metadata()).get("end_session_endpoint")
        if not endpoint:
            return None
        query = urlencode({
            "client_id": self.settings.client_id,
            "post_logout_redirect_uri": f"{self.settings.public_url}/",
        })
        return f"{endpoint}?{query}"

    async def _token_request(self, data: dict) -> TokenSet:
        response = await self._http.post(
            (await self.metadata())["token_endpoint"],
            data=data,
            auth=(self.settings.client_id, self.settings.client_secret),
        )
        if response.status_code in (400, 401):
            error = _json(response).get("error", "")
            if error in ("invalid_grant", "unauthorized_client", "invalid_client"):
                raise InvalidGrant(error)
        if response.status_code != 200:
            raise OidcError(f"token endpoint answered HTTP {response.status_code}")
        body = response.json()
        if not body.get("access_token"):
            raise OidcError("token response has no access_token")
        return TokenSet(body["access_token"], body.get("refresh_token"), body.get("id_token"))

    async def exchange_code(self, code: str, code_verifier: str) -> TokenSet:
        return await self._token_request({
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.settings.redirect_uri,
            "code_verifier": code_verifier,
        })

    async def refresh(self, refresh_token: str) -> TokenSet:
        return await self._token_request({
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        })

    # ── Token validation ─────────────────────────────────────────────────

    async def validate(self, token: str, *, audience: str, nonce: str | None = None) -> dict:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise TokenRejected("malformed token") from exc
        if header.get("alg") not in ALGORITHMS:
            raise TokenRejected("unsupported token algorithm")
        key = await self._key(header.get("kid"))
        try:
            claims = jwt.decode(
                token,
                key.key,
                algorithms=ALGORITHMS,
                audience=audience,
                issuer=self.settings.issuer,
                leeway=_LEEWAY,
                options={"require": ["exp", "iat", "iss", "sub"]},
            )
        except jwt.PyJWTError as exc:
            raise TokenRejected(str(exc)) from exc
        if nonce is not None and claims.get("nonce") != nonce:
            raise TokenRejected("nonce mismatch")
        return claims

    def identity(self, access_claims: dict, profile: dict | None = None) -> Identity | None:
        """The identity an access token proves, or None when it holds no VoiceStudio role."""
        roles = client_roles(access_claims, self.settings.client_id)
        if not roles & {ADMIN_ROLE, USER_ROLE}:
            return None
        source = {**access_claims, **(profile or {})}
        return Identity(
            sub=access_claims["sub"],
            username=source.get("preferred_username") or "",
            name=source.get("name") or "",
            email=source.get("email") or "",
            roles=roles,
            is_admin=ADMIN_ROLE in roles,
        )


def client_roles(claims: dict, client_id: str) -> frozenset[str]:
    access = claims.get("resource_access") or {}
    roles = (access.get(client_id) or {}).get("roles") or []
    return frozenset(r for r in roles if isinstance(r, str))


def _json(response: httpx.Response) -> dict:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}
