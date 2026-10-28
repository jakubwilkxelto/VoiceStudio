"""ASGI gate for multi-user mode.

Sits just inside CORS (see :mod:`xelto.install`), so preflights and its own
refusals keep the CORS and backend-marker headers while every upstream gate
runs after it. For each request it:

1. serves the sign-in endpoints (``/api/auth/oidc/*``) itself;
2. resolves the caller (Keycloak JWT or session cookie; nothing upstream);
3. applies the route policy and, for cookie callers, CSRF;
4. checks that what the path names (a dub job, task, batch job, bundle or
   file) belongs to the caller, answering 404 otherwise (:mod:`xelto.resolvers`);
5. hands upstream an attached principal and a synthetic client address, so
   upstream gates that look at ``request.client.host`` cannot mistake a
   reverse proxy or an in-process caller for the trusted local machine;
6. runs the request inside the caller's identity context, claims for the
   caller the files the response points to, and trims the lists upstream
   builds outside the database to the caller's items.
"""
from __future__ import annotations

import asyncio
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
from xelto.identity import Identity, system_scope, use_identity
from xelto.policy import Owner, RouteClass, classify, path_owner
from xelto.resolvers import LIST_FILTERS, filter_list, visible
from xelto.resources import ResponseTap

ANONYMOUS_PEER = "192.0.2.30"
_STATE_KEY = "auth_principal"  # core.auth._AUTH_STATE_KEY


def match_route(routes, scope: Scope) -> tuple[str | None, dict]:
    """The template and path parameters of the route ``scope`` resolves to.

    Templates use the snapshot's spelling. A method mismatch
    (``Match.PARTIAL``) still names the route; a mount yields its prefix plus
    ``/*`` (``/audio/*``, the SPA's ``/*``), which policy globs match, and its
    remaining path as the ``path`` parameter. Routes are read live: upstream
    adds some after start.
    """
    partial = None
    for route in routes:
        match, child = route.matches(scope)
        if match is Match.NONE:
            continue
        template = f"{route.path}/*" if isinstance(route, Mount) else route.path
        params = dict(child.get("path_params") or {})
        if isinstance(route, Mount):
            # Starlette keeps the mount's remainder out of path_params.
            params["path"] = scope["path"][len(route.path):]
        if match is Match.FULL:
            return template, params
        partial = partial or (template, params)
    return partial or (None, {})


def route_template(routes, scope: Scope) -> str | None:
    return match_route(routes, scope)[0]


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
        template, params = match_route(self._routes_app.router.routes, scope)
        template = template or scope["path"]
        policy_method = "GET" if method == "HEAD" else method
        route_class, _ = classify(policy_method, template)

        refusal = self._refusal(route_class, method, result, conn)
        if refusal is not None:
            await _deny(scope, receive, send, *refusal)
            return

        identity = result.identity
        if identity is not None and route_class is not RouteClass.PUBLIC:
            owner = path_owner(policy_method, template)
            if owner is not None and not await self._visible(owner, template, params, identity):
                await _deny(scope, receive, send, 404, "Not Found")
                return

        principal, peer = principal_and_peer(result)
        state = scope.setdefault("state", {})
        state["xelto_peer"] = scope.get("client")
        state[_STATE_KEY] = principal
        scope["client"] = (peer, 0)
        started = False
        out = send
        if scope["type"] == "http" and identity is not None:
            list_filter = LIST_FILTERS.get((policy_method, template))
            if list_filter is not None:
                out = _filtered(send, *list_filter, identity)
            elif route_class is not RouteClass.PUBLIC:
                out = _tapped(send, ResponseTap(identity.sub))

        async def tracked(message) -> None:
            nonlocal started
            started = started or message["type"] == "http.response.start"
            await out(message)

        try:
            with use_identity(result.identity):
                await self.app(scope, receive, tracked)
        except sqlite3.DatabaseError as exc:
            # The base-table guard refused a write: in practice a shared demo
            # voice, since other users' rows are not even visible.
            if scope["type"] != "http" or started or not str(exc).startswith(OWNERSHIP_REFUSAL):
                raise
            await _deny(scope, receive, send, 403, "This item is shared and read-only.")

    async def _visible(self, owner: Owner, template: str, params: dict, identity: Identity) -> bool:
        def check() -> bool:
            with use_identity(identity):
                return visible(owner, template, params, identity, self._auth.settings.files_mode)

        return await asyncio.to_thread(check)

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


def _tapped(send: Send, tap: ResponseTap) -> Send:
    async def tapping(message) -> None:
        # Claim before the client can follow the link.
        if message["type"] == "http.response.start":
            await asyncio.to_thread(tap.start, message)
        elif message["type"] == "http.response.body" and tap.scan:
            await asyncio.to_thread(tap.body, message.get("body", b""))
        await send(message)

    return tapping


def _filtered(send: Send, key, predicate, identity: Identity) -> Send:
    """Buffer a list response and pass on only the caller's items."""
    start = None
    chunks: list[bytes] = []

    async def filtering(message) -> None:
        nonlocal start
        if message["type"] == "http.response.start":
            start = message
            return
        if message["type"] != "http.response.body" or start is None:
            await send(message)
            return
        chunks.append(message.get("body", b""))
        if message.get("more_body"):
            return
        body = b"".join(chunks)
        if 200 <= start["status"] < 300:
            keep = await asyncio.to_thread(predicate, identity)
            body = filter_list(body, key, keep)
        headers = [(k, v) for k, v in start.get("headers", []) if k.lower() != b"content-length"]
        headers.append((b"content-length", str(len(body)).encode()))
        await send({**start, "headers": headers})
        await send({"type": "http.response.body", "body": body})

    return filtering


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
