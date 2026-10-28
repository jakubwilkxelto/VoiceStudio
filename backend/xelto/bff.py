"""Sign-in endpoints under ``/api/auth/oidc/`` (backend-for-frontend).

The backend is the OIDC client: the browser only ever holds an HttpOnly
session cookie, never a Keycloak token. Served straight from the gate rather
than registered as FastAPI routes, so upstream's route snapshot is unchanged
and the endpoints exist only in multi-user mode.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import secrets

import httpx
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response
from starlette.types import Receive, Scope, Send

from core.csrf import cookie_csrf_allowed
from xelto import ownership
from xelto.authn import Authenticator
from xelto.crypto import InvalidToken, fernet
from xelto.oidc import InvalidGrant, OidcError, TokenRejected
from xelto.sessions import ABSOLUTE_TIMEOUT

logger = logging.getLogger("omnivoice.xelto")

PREFIX = "/api/auth/oidc/"
_TX_TTL = 600


def safe_next(value: str | None) -> str:
    """A same-origin path to land on after sign-in; anything else becomes ``/``."""
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return "/"
    if any(ord(ch) < 0x20 for ch in value):
        return "/"
    return value


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class SignInEndpoints:
    def __init__(self, auth: Authenticator):
        self.auth = auth
        self.settings = auth.settings
        self._tx = fernet(self.settings.secret, "oidc-transaction")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await send({"type": "websocket.close", "code": 1008})
            return
        request = Request(scope, receive)
        name = scope["path"][len(PREFIX):]
        handler = {
            ("GET", "login"): self.login,
            ("GET", "callback"): self.callback,
            ("POST", "logout"): self.logout,
            ("GET", "me"): self.me,
        }.get((request.method.upper(), name))
        if handler is None:
            response: Response = JSONResponse({"detail": "Not Found"}, status_code=404)
        else:
            try:
                response = await handler(request)
            except (OidcError, httpx.HTTPError) as exc:
                logger.warning("sign-in failed, identity provider error: %s", exc)
                response = PlainTextResponse("The sign-in service is unavailable. Try again later.",
                                             status_code=503)
        response.headers["cache-control"] = "no-store"
        await response(scope, receive, send)

    def _cookie(self, response: Response, name: str, value: str, max_age: int) -> None:
        response.set_cookie(name, value, max_age=max_age, path="/", httponly=True,
                            secure=self.settings.secure_cookies, samesite="lax")

    def _clear(self, response: Response, name: str) -> None:
        response.delete_cookie(name, path="/", httponly=True,
                               secure=self.settings.secure_cookies, samesite="lax")

    async def login(self, request: Request) -> Response:
        state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
        verifier = secrets.token_urlsafe(48)
        challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
        url = await self.auth.oidc.authorization_url(state=state, nonce=nonce,
                                                     code_challenge=challenge)
        tx = json.dumps({"state": state, "nonce": nonce, "verifier": verifier,
                         "next": safe_next(request.query_params.get("next"))})
        response = RedirectResponse(url, status_code=302)
        self._cookie(response, self.settings.tx_cookie,
                     self._tx.encrypt(tx.encode()).decode(), _TX_TTL)
        return response

    def _refuse(self, message: str, status: int = 400) -> Response:
        response = PlainTextResponse(message, status_code=status)
        self._clear(response, self.settings.tx_cookie)
        return response

    async def callback(self, request: Request) -> Response:
        params = request.query_params
        if params.get("error"):
            return self._refuse("Sign-in was cancelled or refused by the identity provider.")
        sealed = request.cookies.get(self.settings.tx_cookie, "")
        try:
            tx = json.loads(self._tx.decrypt(sealed.encode(), ttl=_TX_TTL))
        except (InvalidToken, ValueError):
            return self._refuse("The sign-in attempt expired. Start again.")
        if not secrets.compare_digest(params.get("state", ""), tx["state"]) or not params.get("code"):
            return self._refuse("The sign-in response does not match this browser.")

        oidc = self.auth.oidc
        try:
            tokens = await oidc.exchange_code(params["code"], tx["verifier"])
            if not tokens.id_token:
                raise TokenRejected("no id_token")
            profile = await oidc.validate(tokens.id_token, audience=self.settings.client_id,
                                          nonce=tx["nonce"])
            claims = await oidc.validate(tokens.access_token, audience=self.settings.client_id)
        except (InvalidGrant, TokenRejected) as exc:
            logger.warning("sign-in rejected: %s", exc)
            return self._refuse("The sign-in response could not be verified.")
        if claims.get("sub") != profile.get("sub"):
            return self._refuse("The sign-in response could not be verified.")

        identity = oidc.identity(claims, profile)
        if identity is None:
            return self._refuse("Your account is not enrolled in VoiceStudio. Ask an administrator "
                                "for the voicestudio-user role.", 403)
        store = self.auth.store
        if not await asyncio.to_thread(store.upsert_user, identity):
            return self._refuse("Your VoiceStudio account is disabled.", 403)
        owner = self.settings.bootstrap_owner
        if owner and owner in (identity.username, identity.sub):
            counts = await asyncio.to_thread(ownership.bootstrap, store._connect, identity.sub)
            if counts is not None:
                logger.info("bootstrap: existing data assigned to %s: %s",
                            identity.username or identity.sub, counts)
        raw =await asyncio.to_thread(store.create, identity, tokens.refresh_token,
                                      request.headers.get("user-agent", ""))
        response = RedirectResponse(tx["next"], status_code=302)
        self._clear(response, self.settings.tx_cookie)
        self._cookie(response, self.settings.session_cookie, raw, int(ABSOLUTE_TIMEOUT))
        logger.info("signed in: %s (%s)", identity.username or identity.sub,
                    "admin" if identity.is_admin else "user")
        return response

    async def logout(self, request: Request) -> Response:
        if not cookie_csrf_allowed(request):
            return JSONResponse({"detail": "CSRF validation failed"}, status_code=403)
        raw = request.cookies.get(self.settings.session_cookie, "")
        await asyncio.to_thread(self.auth.store.delete_raw, raw)
        try:
            logout_url = await self.auth.oidc.end_session_url()
        except (OidcError, httpx.HTTPError):
            logout_url = None
        response = JSONResponse({"logout_url": logout_url or "/"})
        self._clear(response, self.settings.session_cookie)
        return response

    async def me(self, request: Request) -> Response:
        result = await self.auth.authenticate(request)
        if result.identity is None:
            return JSONResponse({"enabled": True, "authenticated": False})
        return JSONResponse({"enabled": True, "authenticated": True,
                             **result.identity.public()})
