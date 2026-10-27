"""Wire multi-user mode into the upstream app — the target of the one hook in main.py."""
from __future__ import annotations

import functools
import logging

from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware

from xelto.config import multiuser_enabled

logger = logging.getLogger("omnivoice.xelto")


def install(app) -> None:
    """No-op unless ``OMNIVOICE_MULTIUSER`` is on; must run before the first request."""
    if not multiuser_enabled():
        return
    _insert_middleware(app)
    _chain_migrations_after_init_db()
    logger.warning(
        "OMNIVOICE_MULTIUSER is on: multi-user skeleton (P0) — every non-public "
        "route is denied until sign-in lands"
    )


def _insert_middleware(app) -> None:
    """Place the gate directly inside CORS.

    Starlette builds ``user_middleware`` outermost-first. Just inside CORS keeps
    preflights and the gate's own 401s carrying CORS and the backend marker
    (#1385), while every upstream auth gate still runs after it.
    """
    from xelto.middleware import XeltoMultiUserMiddleware

    if app.middleware_stack is not None:
        raise RuntimeError("xelto.install() must run before the app starts")
    entry = Middleware(XeltoMultiUserMiddleware, routes_app=app)
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
