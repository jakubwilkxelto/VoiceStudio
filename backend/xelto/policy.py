"""Route policy: which class of caller may reach each route.

Rules match ``(method, path template)`` exactly as the route snapshot
(``tests/fixtures/api_routes.txt``) spells them; mounts match as
``<prefix>/*``. The first matching rule wins. Anything no rule matches is
ADMIN, so a route upstream adds stays closed until it is classified here:
``tests/xelto_fork/test_policy_completeness.py`` fails until it is.

A class is an upper bound. Upstream's own gates still run afterwards and may
refuse more (for example ``require_loopback`` on some deletes).
"""
from __future__ import annotations

import fnmatch
from enum import Enum


class RouteClass(str, Enum):
    PUBLIC = "public"
    USER = "user"
    USER_READ_ADMIN_WRITE = "user_read_admin_write"
    ADMIN = "admin"
    DISABLED = "disabled"


DEFAULT_CLASS = RouteClass.ADMIN

P, U, R, A, D = (RouteClass.PUBLIC, RouteClass.USER, RouteClass.USER_READ_ADMIN_WRITE,
                 RouteClass.ADMIN, RouteClass.DISABLED)
_ANY = "*"
_WRITE = "POST,PUT,PATCH,DELETE"

# (methods, template glob, class). Methods: "*" or a comma-separated list.
RULES: list[tuple[str, str, RouteClass]] = [
    # ── Off in multi-user mode: LAN/PIN sharing, desktop-only, cloud sync,
    #    upstream's own sign-in, and paths only the host machine may touch.
    (_ANY, "/api/cloud-sync/*", D),
    (_ANY, "/system/network/*", D),
    (_ANY, "/system/tailscale/*", D),
    (_ANY, "/api/auth/session", D),
    (_ANY, "/api/auth/ws-ticket", D),
    (_ANY, "/system/shutdown-intent", D),
    (_ANY, "/system/set-env", D),
    ("POST", "/export", D),
    ("POST", "/export/reveal", D),
    ("POST", "/tools/probe", D),
    ("POST", "/engines/audiocpp/runtime/install", D),
    ("POST", "/engines/sidecar/*/install", D),
    ("POST", "/engines/sonitranslate/dub", D),

    # ── No sign-in needed: health, startup progress, the SPA shell, demo audio.
    ("GET", "/health", P),
    ("GET", "/startup/progress", P),
    ("GET", "/.well-known/voicestudio-speech", P),
    (_ANY, "/demo_audio/*", P),
    # The SPA mount, which only receives paths no API route claimed. "[*]" is a
    # literal "*": a bare "/*" glob would match every template.
    (_ANY, "/[*]", P),

    # ── Administration: engines, models, setup, system, settings, integrations.
    ("GET", "/engines/*/health", A),
    ("GET", "/engines/*/disk-usage", A),
    ("GET", "/engines/*/install/status", A),
    ("GET", "/engines/audiocpp/*", A),
    ("GET", "/engines*", U),
    (_ANY, "/engines*", A),
    ("GET", "/models/licenses/receipts/*", R),
    ("GET", "/models*", U),
    (_ANY, "/models*", A),
    ("GET", "/model/status", U),
    ("GET", "/model/loaded", U),
    (_ANY, "/model/*", A),
    ("GET", "/setup/*", U),
    (_ANY, "/setup/*", A),
    ("GET", "/system/info", U),
    ("GET", "/system/notifications", U),
    ("GET", "/system/asr-backends", U),
    (_ANY, "/system/*", A),
    ("GET", "/sysinfo", U),
    ("GET", "/api/settings/analytics", U),
    ("GET", "/api/settings/compute-device", U),
    ("GET", "/api/settings/dictation-refinement", U),
    ("GET", "/api/settings/history-retention", U),
    ("GET", "/api/settings/performance-profile", U),
    ("GET", "/api/settings/changelog", U),
    (_ANY, "/api/settings/*", A),
    (_ANY, "/api/mcp/*", A),
    (_ANY, "/api/integrations/*", A),
    (_ANY, "/mcp/*", A),
    (_ANY, "/media-tools/*", A),
    (_ANY, "/calls*", A),
    (_ANY, "/clean-audio", A),
    (_ANY, "/stats/*", A),
    (_ANY, "/docs", A),
    (_ANY, "/openapi.json", A),
    (_ANY, "/watermark/settings", A),

    # ── Shared catalogues: everyone reads and imports, the admin curates.
    (_WRITE, "/gallery/voices/*/to-profile", U),
    (_WRITE, "/gallery/voices/*/save-as-profile", U),
    ("GET", "/gallery/*", U),
    (_ANY, "/gallery/*", A),
    ("PUT", "/archetypes/previews", A),
    ("POST", "/archetypes/previews/check", A),
    (_ANY, "/archetypes*", U),
    ("POST", "/marketplace/install/*", A),
    ("POST", "/marketplace/publish/*", A),
    ("DELETE", "/marketplace/*", A),
    (_ANY, "/marketplace/*", U),
    (_ANY, "/community/*", U),
    (_ANY, "/pronunciation*", R),
    (_ANY, "/personalities", U),
    # Dictation preferences live in the global prefs.json: one set for the
    # server, which the admin chooses.
    ("POST", "/dictation/prefs", A),

    # ── Per-user work.
    *((_ANY, glob, U) for glob in (
        "/profiles*", "/profile-images/*", "/generate*", "/history*", "/audio/*",
        "/voice_audio/*", "/design*", "/convert", "/transcribe", "/dub/*", "/preview/*",
        "/projects*", "/project-bundles*", "/glossary*", "/export/*", "/batch/*",
        "/audiobook*", "/longform/*", "/stories/*", "/personas*", "/dictation/*",
        "/tasks/*", "/jobs*", "/tools/*", "/watermark/*", "/v1/*", "/ws/*",
    )),
]


class Owner(str, Enum):
    """How the gate checks who owns what a route's path parameters name."""

    DB_VIEW = "db-view"  # the handler reads it through the scoped views
    SHARED = "shared"  # a shared catalogue entry (gallery, models, ...)
    DUB = "dub"
    PROFILE = "profile"
    TASK = "task"
    JOB = "job"
    BATCH = "batch"
    BUNDLE = "bundle"
    FILE = "file"


# (methods, template glob, owner check) for routes with path parameters and
# the file mounts. First match wins. Every USER route with a parameter must
# match one: tests/xelto_fork/test_policy_completeness.py fails otherwise.
PATH_OWNERS: list[tuple[str, str, Owner]] = [
    (_ANY, "/audio/*", Owner.FILE),
    (_ANY, "/voice_audio/*", Owner.FILE),
    (_ANY, "/preview/*", Owner.FILE),
    # get_job checks dub owners too, but not every /dub route goes through it,
    # and a delete removes the job's directory whether or not the row is visible.
    (_ANY, "/dub/*", Owner.DUB),
    (_ANY, "/tools/video-context/*", Owner.DUB),
    (_ANY, "/tasks/*", Owner.TASK),
    (_ANY, "/audiobook/resume/*", Owner.JOB),
    (_ANY, "/batch/*", Owner.BATCH),
    (_ANY, "/project-bundles/*", Owner.BUNDLE),
    # Profile routes also touch files named after the id (portrait, reference
    # audio) whether or not the row is visible, so the views are not enough.
    *((_ANY, glob, Owner.PROFILE) for glob in (
        "/profiles/*", "/personas/export/*", "/marketplace/export/*",
    )),
    *((_ANY, glob, Owner.DB_VIEW) for glob in (
        "/history/*", "/projects/*", "/glossary/*", "/jobs/*",
        "/export/history/*", "/longform/jobs/*",
    )),
    *((_ANY, glob, Owner.SHARED) for glob in (
        "/archetypes/*", "/community/*", "/gallery/*", "/models/*", "/v1/models/*",
        "/pronunciation/*",
    )),
]


# Ids a USER route takes in its body or query instead of its path:
# (method, template) -> (field, owner check). Only JSON bodies are read.
# Every *_id field of a USER route is listed here or in BODY_IDS_VIA_VIEWS:
# tests/xelto_fork/test_real_app.py fails otherwise.
BODY_OWNERS: dict[tuple[str, str], tuple[str, Owner]] = {
    # Creates DUB_DIR/<job_id> and the prep task for an id the client picks.
    ("POST", "/dub/ingest-url"): ("job_id", Owner.DUB),
    ("POST", "/dub/translate"): ("job_id", Owner.DUB),
}

# Body or query ids that need no gate check: the handler looks them up only
# through the scoped views, they name shared catalogue entries (models), or,
# for /dub/upload, an existing job directory is refused with 409.
BODY_IDS_VIA_VIEWS: dict[tuple[str, str], str] = {
    ("GET", "/dictation/readiness"): "model_id",
    ("GET", "/models/access/status"): "repo_id",
    ("POST", "/dub/upload"): "job_id",
    ("GET", "/jobs"): "project_id",
    ("POST", "/batch/enqueue"): "voice_id",
    ("POST", "/convert"): "profile_id",
    ("POST", "/generate"): "profile_id",
    ("POST", "/dub/generate/{job_id}"): "segment_ids",
    ("POST", "/dub/preview-segment/{job_id}"): "profile_id,segment_id",
    ("POST", "/profiles/{profile_id}/lock"): "history_id",
    ("POST", "/project-bundles/export"): "project_id",
}


def path_owner(method: str, template: str) -> Owner | None:
    method = method.upper()
    for methods, pattern, owner in PATH_OWNERS:
        if methods != _ANY and method not in methods.split(","):
            continue
        if fnmatch.fnmatchcase(template, pattern):
            return owner
    return None


def classify(method: str, template: str) -> tuple[RouteClass, bool]:
    """Return the route's class and whether an explicit rule matched."""
    method = method.upper()
    for methods, pattern, cls in RULES:
        if methods != _ANY and method not in methods.split(","):
            continue
        if fnmatch.fnmatchcase(template, pattern):
            return cls, True
    return DEFAULT_CLASS, False
