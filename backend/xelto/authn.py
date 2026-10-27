"""Resolve who a request comes from: a Keycloak JWT, a session cookie, or nobody.

Upstream's credentials (master key, ``ov_session``, WebSocket tickets, PIN)
are never consulted: the gate attaches the principal it resolves here, and
upstream's ``resolve_principal`` honours an attached principal first.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import httpx
from starlette.requests import HTTPConnection

from core.auth import CredentialTransport, credential_matches, remote_api_key
from xelto.config import ADMIN_ROLE, Settings
from xelto.identity import Identity
from xelto.oidc import InvalidGrant, OidcClient, OidcError, TokenRejected
from xelto.sessions import Session, SessionStore

logger = logging.getLogger("omnivoice.xelto")

MASTER_KEY_IDENTITY = Identity(
    sub="__master_key__", username="master-key", roles=frozenset({ADMIN_ROLE}), is_admin=True,
)


@dataclass(frozen=True)
class AuthResult:
    identity: Identity | None
    transport: CredentialTransport = CredentialTransport.NONE
    credential_id: str | None = None
    error: str | None = None  # why a presented credential was refused

    @property
    def master_key(self) -> bool:
        return self.identity is MASTER_KEY_IDENTITY


class Authenticator:
    def __init__(self, settings: Settings, oidc: OidcClient, store: SessionStore):
        self.settings = settings
        self.oidc = oidc
        self.store = store
        self._refresh_locks: dict[str, asyncio.Lock] = {}

    async def authenticate(self, conn: HTTPConnection) -> AuthResult:
        header = conn.headers.get("authorization", "")
        if header[:7].lower() == "bearer ":
            return await self._bearer(header[7:].strip())
        raw = conn.cookies.get(self.settings.session_cookie, "")
        if raw:
            return await self._cookie(raw)
        return AuthResult(None)

    async def _bearer(self, token: str) -> AuthResult:
        transport = CredentialTransport.HEADER
        if self.settings.allow_master_key and credential_matches(token, remote_api_key()):
            return AuthResult(MASTER_KEY_IDENTITY, transport, "api-key")
        try:
            claims = await self.oidc.validate(token, audience=self.settings.api_audience)
        except TokenRejected:
            return AuthResult(None, transport, error="invalid token")
        except (OidcError, httpx.HTTPError) as exc:
            logger.warning("bearer token check failed, identity provider unreachable: %s", exc)
            return AuthResult(None, transport, error="identity provider unavailable")
        identity = self.oidc.identity(claims)
        if identity is None:
            return AuthResult(None, transport, error="account not enrolled in VoiceStudio")
        return AuthResult(identity, transport, f"jwt:{identity.sub}")

    async def _cookie(self, raw: str) -> AuthResult:
        transport = CredentialTransport.COOKIE
        session = await asyncio.to_thread(self.store.get, raw)
        if session is not None and session.roles_stale and session.refresh_token:
            session = await self._recheck_roles(raw, session)
        if session is None:
            return AuthResult(None, transport, error="session expired")
        return AuthResult(session.identity, transport, f"oidc:{session.id_hash[:16]}")

    async def _recheck_roles(self, raw: str, session: Session) -> Session | None:
        """Refresh against Keycloak so a disabled user or a removed role takes effect."""
        lock = self._refresh_locks.setdefault(session.id_hash, asyncio.Lock())
        async with lock:
            current = await asyncio.to_thread(self.store.get, raw)
            if current is None or not current.roles_stale or not current.refresh_token:
                return current
            try:
                tokens = await self.oidc.refresh(current.refresh_token)
                claims = await self.oidc.validate(tokens.access_token,
                                                  audience=self.settings.client_id)
            except (InvalidGrant, TokenRejected):
                await asyncio.to_thread(self.store.delete, current.id_hash)
                return None
            except (OidcError, httpx.HTTPError) as exc:
                # Keycloak is down: keep the session on its last known roles.
                logger.warning("role re-check skipped, identity provider unreachable: %s", exc)
                return current
            finally:
                self._refresh_locks.pop(session.id_hash, None)
            identity = self.oidc.identity(claims, current.identity.public())
            if identity is None:
                await asyncio.to_thread(self.store.delete, current.id_hash)
                return None
            await asyncio.to_thread(self.store.update_roles, current.id_hash, identity,
                                    tokens.refresh_token)
            return await asyncio.to_thread(self.store.get, raw)
