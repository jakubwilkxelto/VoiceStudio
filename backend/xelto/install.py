"""Wire multi-user mode into the upstream app — the target of the one hook in main.py."""
from __future__ import annotations

import functools
import logging
import os

from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware

from xelto.config import USER_PEER_NETWORK, ConfigError, Settings, load_settings, multiuser_enabled

logger = logging.getLogger("omnivoice.xelto")


def install(app, *, oidc_transport=None) -> None:
    """No-op unless ``OMNIVOICE_MULTIUSER`` is on; must run before the first request.

    Raises :class:`~xelto.config.ConfigError` when the mode is on but unsafe
    to run, so a misconfigured server never starts half-protected.
    ``oidc_transport`` lets tests stand in for Keycloak.
    """
    if not multiuser_enabled():
        return
    settings = load_settings()
    _check_browser_origin(settings)
    # Users reach upstream as peers of this synthetic trusted network (see
    # xelto.config.USER_PEER); load_settings() refused any operator value.
    os.environ["OMNIVOICE_TRUSTED_NETWORKS"] = USER_PEER_NETWORK
    _insert_middleware(app, settings, oidc_transport)
    _chain_migrations_after_init_db()
    logger.info("multi-user mode on: sign-in through %s", settings.issuer)


def _check_browser_origin(settings: Settings) -> None:
    """The public origin must be one CORS, CSRF and the Host check accept."""
    from core.csrf import _origin_tuple, configured_allowed_origins

    if _origin_tuple(settings.public_url) not in configured_allowed_origins():
        raise ConfigError(
            "multi-user mode cannot start:\n  - OMNIVOICE_ALLOWED_ORIGINS must include "
            f"OMNIVOICE_PUBLIC_URL ({settings.public_url})"
        )


def _insert_middleware(app, settings: Settings, oidc_transport) -> None:
    """Place the gate directly inside CORS.

    Starlette builds ``user_middleware`` outermost-first. Just inside CORS keeps
    preflights and the gate's own refusals carrying CORS and the backend marker
    (#1385), while every upstream auth gate still runs after it.
    """
    from core import db

    from xelto.authn import Authenticator
    from xelto.middleware import XeltoMultiUserMiddleware
    from xelto.oidc import OidcClient
    from xelto.sessions import SessionStore

    if app.middleware_stack is not None:
        raise RuntimeError("xelto.install() must run before the app starts")
    auth = Authenticator(
        settings,
        OidcClient(settings, transport=oidc_transport),
        SessionStore(lambda: db.DB_PATH, settings.secret),
    )
    entry = Middleware(XeltoMultiUserMiddleware, routes_app=app, auth=auth)
    stack = app.user_middleware
    cors = next((i for i, m in enumerate(stack) if m.cls is CORSMiddleware), None)
    stack.insert(0 if cors is None else cors + 1, entry)


def _chain_migrations_after_init_db() -> None:
    """Run the fork's migrations right after upstream's ``init_db``.

    ``main._phase_b`` imports ``init_db`` lazily from ``core.db``, so wrapping
    the module attribute reaches the real startup without another hook.
    """
    from core import db

    original = db.init_db
    if getattr(original, "__xelto_wrapped__", False):
        return

    @functools.wraps(original)
    def init_db(*args, **kwargs):
        result = original(*args, **kwargs)
        from xelto import migrate

        # The same module's DB_PATH that init_db just used (tests repoint it).
        migrate.upgrade(db.DB_PATH)
        return result

    init_db.__xelto_wrapped__ = True
    db.init_db = init_db
