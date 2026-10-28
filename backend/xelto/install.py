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
    from xelto import file_guard

    file_guard.configured_mode = settings.files_mode
    _insert_middleware(app, settings, oidc_transport)
    _chain_migrations_after_init_db()
    _build_as_system()
    _scope_data()
    logger.info("multi-user mode on: sign-in through %s", settings.issuer)
    if settings.files_mode == "lenient":
        logger.warning("multi-user mode: OMNIVOICE_MULTIUSER_FILES=lenient serves files no "
                       "owner accounts for to every signed-in user; use it only to diagnose")


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
    from xelto import store
    from xelto.authn import Authenticator
    from xelto.middleware import XeltoMultiUserMiddleware
    from xelto.oidc import OidcClient
    from xelto.sessions import SessionStore

    if app.middleware_stack is not None:
        raise RuntimeError("xelto.install() must run before the app starts")
    auth = Authenticator(
        settings,
        OidcClient(settings, transport=oidc_transport),
        SessionStore(store.db_path, settings.secret),
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
        from xelto import migrate
        from xelto.identity import system_scope

        # Schema work acts for nobody, and must see the real tables.
        with system_scope():
            result = original(*args, **kwargs)
            # The fork's own database next to the one init_db just used
            # (tests repoint core.db.DB_PATH).
            migrate.upgrade_for(db.DB_PATH)
        _scope_routers()
        return result

    init_db.__xelto_wrapped__ = True
    db.init_db = init_db


def _build_as_system() -> None:
    """Run the app build that follows ``install()`` in ``main.py`` as SYSTEM.

    ``main`` calls ``_phase_a_build()`` at import time, after the hook; it
    reads saved settings and preferences for nobody. Without a scope those
    reads would open connections with no identity (and log as such).
    """
    import sys

    module = sys.modules.get("main")
    build = getattr(module, "_phase_a_build", None)
    if build is None or getattr(build, "__xelto_wrapped__", False):
        return

    @functools.wraps(build)
    def build_as_system(*args, __build=build, **kwargs):
        from xelto.identity import system_scope

        with system_scope():
            return __build(*args, **kwargs)

    build_as_system.__xelto_wrapped__ = True
    module._phase_a_build = build_as_system


def _scope_data() -> None:
    """Per-user rows (``get_db`` hook), identity carried into workers, dub job owners."""
    from services import dub_pipeline

    from xelto import db_scope, events, hooks, propagation

    db_scope.activate()
    propagation.install()
    events.install()
    for name in ("put_job", "put_and_save_job"):
        original = getattr(dub_pipeline, name)
        if getattr(original, "__xelto_wrapped__", False):
            continue

        # Callers reach these through the module (dub_pipeline's own globals,
        # dub_export's call-time import), so wrapping the attribute is enough.
        def recording(job_id, *args, __original=original, **kwargs):
            hooks.record_dub_owner(job_id)
            return __original(job_id, *args, **kwargs)

        functools.update_wrapper(recording, original)
        recording.__xelto_wrapped__ = True
        setattr(dub_pipeline, name, recording)


def _scope_routers() -> None:
    """Owners for what routers keep outside the database, and the files they open.

    Runs from the wrapped ``init_db`` (startup phase B): by then the deferred
    boot has imported the routers, and importing them from ``install()``
    would hold up the early ``/health``.
    """
    from api.routers import batch, project_bundles

    from xelto import resources
    from xelto.identity import use_identity

    # Batch jobs: remember who created each (the in-memory _jobs dict), and run
    # each as its creator. The worker is started by the first enqueue and
    # would otherwise run every later job as that first user.
    if not isinstance(batch._jobs, resources.BatchJobs):
        batch._jobs = resources.BatchJobs(batch._jobs)
    run = batch._run_batch_pipeline
    if not getattr(run, "__xelto_wrapped__", False):
        @functools.wraps(run)
        async def run_as_owner(job_id, job, *args, __run=run, **kwargs):
            owner = resources.batch_owner(job_id)
            if owner is None:
                raise RuntimeError("batch job has no owner in multi-user mode")
            with use_identity(owner):
                return await __run(job_id, job, *args, **kwargs)

        run_as_owner.__xelto_wrapped__ = True
        batch._run_batch_pipeline = run_as_owner

    # Project bundles: export and import create a fresh directory per bundle,
    # always through _directory(); a directory that does not exist yet is the
    # caller's new bundle.
    directory = project_bundles._directory
    if not getattr(directory, "__xelto_wrapped__", False):
        @functools.wraps(directory)
        def claiming(identifier, *args, __directory=directory, **kwargs):
            path = __directory(identifier, *args, **kwargs)
            user = resources.acting_user()
            if user is not None and not path.exists():
                resources.claim(f"bundle/{path.name}", user.sub)
            return path

        claiming.__xelto_wrapped__ = True
        project_bundles._directory = claiming

    # Project bundles: an export packs the local files the client's project
    # document names, always resolved through _asset(). Each must be one the
    # caller may fetch, or the export fails as if the file were missing.
    asset = project_bundles._asset
    if not getattr(asset, "__xelto_wrapped__", False):
        from core.safe_archive import ArchiveError

        from xelto import file_guard

        @functools.wraps(asset)
        def owned_asset(value, roots, *args, __asset=asset, **kwargs):
            path = __asset(value, roots, *args, **kwargs)
            user = resources.acting_user()
            if user is not None and not file_guard.path_allowed(path, user.sub):
                raise ArchiveError(400, "Project references a missing or unsafe local asset")
            return path

        owned_asset.__xelto_wrapped__ = True
        project_bundles._asset = owned_asset

    # /ws/tts conditions on an emo_audio clip named by the client, resolved
    # through resolve_emotion_clip(): it must be one the caller may fetch.
    from api.routers import tts_stream

    resolve = tts_stream.resolve_emotion_clip
    if not getattr(resolve, "__xelto_wrapped__", False):
        from xelto import file_guard

        @functools.wraps(resolve)
        def owned_clip(value, *args, __resolve=resolve, **kwargs):
            path = __resolve(value, *args, **kwargs)
            user = resources.acting_user()
            if user is not None and not file_guard.path_allowed(path, user.sub):
                raise ValueError(tts_stream.EMO_AUDIO_DETAIL)
            return path

        owned_clip.__xelto_wrapped__ = True
        tts_stream.resolve_emotion_clip = owned_clip
