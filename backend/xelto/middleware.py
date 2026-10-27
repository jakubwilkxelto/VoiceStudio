"""ASGI gate for multi-user mode.

Sits just inside CORS (see :mod:`xelto.install`), so preflights and its own
401s keep the CORS and backend-marker headers while every upstream auth gate
runs after it. P0 has no credential source yet, so it fails closed: only
PUBLIC routes pass, everything else is ``401 login required``.
"""
from __future__ import annotations

import json

from starlette.routing import Match, Mount
from starlette.types import ASGIApp, Receive, Scope, Send

from xelto.identity import use_identity
from xelto.policy import RouteClass, classify


def route_template(routes, scope: Scope) -> str | None:
    """The template of the route ``scope`` resolves to, in snapshot spelling.

    A method mismatch (``Match.PARTIAL``) still names the route; a mount
    yields its prefix plus ``/*`` (``/audio/*``), which policy globs match.
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


class XeltoMultiUserMiddleware:
    def __init__(self, app: ASGIApp, *, routes_app) -> None:
        self.app = app
        self._routes_app = routes_app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        method = "WS" if scope["type"] == "websocket" else scope["method"]
        template = route_template(self._routes_app.router.routes, scope) or scope["path"]
        route_class, _ = classify(method, template)

        if route_class is RouteClass.PUBLIC:
            with use_identity(None):
                await self.app(scope, receive, send)
            return

        await _deny(scope, receive, send)


async def _deny(scope: Scope, receive: Receive, send: Send) -> None:
    if scope["type"] == "websocket":
        await receive()
        await send({"type": "websocket.close", "code": 1008})
        return
    body = json.dumps({"detail": "login required"}).encode()
    await send({
        "type": "http.response.start",
        "status": 401,
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ],
    })
    await send({"type": "http.response.body", "body": body})
