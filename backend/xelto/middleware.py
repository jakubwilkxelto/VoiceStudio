"""ASGI gate for multi-user mode.

Sits just inside CORS (see :mod:`xelto.install`), so preflights and its own
refusals keep the CORS and backend-marker headers while every upstream gate
runs after it. For each request it:

1. serves the sign-in endpoints (``/api/auth/oidc/*``) itself;
2. resolves the caller (Keycloak JWT or session cookie; nothing upstream);
3. applies the route policy and, for cookie callers, CSRF;
4. hands upstream an attached principal and a synthetic client address, so
   upstream gates that look at ``request.client.host`` cannot mistake a
   reverse proxy or an in-process caller for the trusted local machine;
5. runs the request inside the caller's identity context.
"""
from __future__ import annotations

import json
import sqlite3

from starlette.requests import HTTPConnection
from starlette.routing import Match, Mount
from starlette.types import ASGIApp, Receive, Scope, Send

from core.auth import (
    ADMIN_CAPABILITIES,
    CONSUME_CAPABILITIES,
    AuthPrincipal,
    CredentialTransport,
    PrincipalKind,
)
from core.csrf import SAFE_HTTP_METHODS, cookie_csrf_allowed, origin_allowed
from xelto.authn import Authenticator, AuthResult
from xelto.bff import PREFIX as SIGN_IN_PREFIX
from xelto.bff import SignInEndpoints
from xelto.config import ADMIN_PEER, USER_PEER
from xelto.db_views import OWNERSHIP_REFUSAL
from xelto.identity import system_scope, use_identity
from xelto.policy import RouteClass, classify

ANONYMOUS_PEER = "192.0.2.30"
_STATE_KEY = "auth_principal"  # core.auth._AUTH_STATE_KEY


def route_template(routes, scope: Scope) -> str | None:
    """The template of the route ``scope`` resolves to, in snapshot spelling.

    A method mismatch (``Match.PARTIAL``) still names the route; a mount
    yields its prefix plus ``/*`` (``/audio/*``, the SPA's ``/*``), which
    policy globs match. Routes are read live: upstream adds some after start.
    """
    partial = None
    for route in routes:
        match, _ = route.matches(scope)
        if match is Match.NONE:
            continue
        template = f"{route.path}/*" if isinstance(route, Mount) else route.path
        if match is Match.FULL:
            return template
        partial = partial or template
    return partial


def principal_and_peer(result: AuthResult) -> tuple[AuthPrincipal, str]:
    identity = result.identity
    if identity is None:
        return AuthPrincipal(PrincipalKind.ANONYMOUS, frozenset()), ANONYMOUS_PEER
    if identity.is_admin:
        kind = PrincipalKind.API_KEY if result.master_key else PrincipalKind.ADMIN_SESSION
        return (AuthPrincipal(kind, ADMIN_CAPABILITIES, result.credential_id, result.transport),
                ADMIN_PEER)
    return (AuthPrincipal(PrincipalKind.TRUSTED_NETWORK, CONSUME_CAPABILITIES,
                          result.credential_id, result.transport), USER_PEER)


class XeltoMultiUserMiddleware:
    def __init__(self, app: ASGIApp, *, routes_app, auth: Authenticator) -> None:
        self.app = app
        self._routes_app = routes_app
        self._auth = auth
        self._sign_in = SignInEndpoints(auth)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            # Startup and shutdown act for nobody: sweeps, migrations and the
            # workers started here use plain connections. Each queued task
            # takes its submitter's identity (xelto.hooks, xelto.propagation).
            with system_scope():
                await self.app(scope, receive, send)
            return
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        if scope["path"].startswith(SIGN_IN_PREFIX):
            await self._sign_in(scope, receive, send)
            return

        conn = HTTPConnection(scope)
        result = await self._auth.authenticate(conn)
        method = "WS" if scope["type"] == "websocket" else str(scope["method"]).upper()
        template = route_template(self._routes_app.router.routes, scope) or scope["path"]
        route_class, _ = classify("GET" if method == "HEAD" else method, template)

        refusal = self._refusal(route_class, method, result, conn)
        if refusal is not None:
            await _deny(scope, receive, send, *refusal)
            return

        principal, peer = principal_and_peer(result)
        state = scope.setdefault("state", {})
        state["xelto_peer"] = scope.get("client")
        state[_STATE_KEY] = principal
        scope["client"] = (peer, 0)
        started = False

        async def tracked(message) -> None:
            nonlocal started
            started = started or message["type"] == "http.response.start"
            await send(message)

        try:
            with use_identity(result.identity):
                await self.app(scope, receive, tracked)
        except sqlite3.DatabaseError as exc:
            # The base-table guard refused a write: in practice a shared demo
            # voice, since other users' rows are not even visible.
            if scope["type"] != "http" or started or not str(exc).startswith(OWNERSHIP_REFUSAL):
                raise
            await _deny(scope, receive, send, 403, "This item is shared and read-only.")

    def _refusal(self, route_class: RouteClass, method: str, result: AuthResult,
                 conn: HTTPConnection) -> tuple[int, str] | None:
        if route_class is RouteClass.PUBLIC:
            return None
        if route_class is RouteClass.DISABLED:
            return 403, "not available in multi-user mode"
        identity = result.identity
        if identity is None:
            return 401, result.error or "login required"
        needs_admin = route_class is RouteClass.ADMIN or (
            route_class is RouteClass.USER_READ_ADMIN_WRITE and method not in SAFE_HTTP_METHODS
        )
        if needs_admin and not identity.is_admin:
            return 403, "admin role required"
        if result.transport is CredentialTransport.COOKIE:
            if method == "WS":
                if not origin_allowed(conn):
                    return 403, "browser origin rejected"
            elif method not in SAFE_HTTP_METHODS and not cookie_csrf_allowed(conn):
                return 403, "CSRF validation failed"
        return None


async def _deny(scope: Scope, receive: Receive, send: Send, status: int, detail: str) -> None:
    if scope["type"] == "websocket":
        await receive()
        await send({"type": "websocket.close", "code": 1008, "reason": detail})
        return
    body = json.dumps({"detail": detail}).encode()
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
            (b"cache-control", b"no-store"),
        ],
    })
    await send({"type": "http.response.body", "body": body})
