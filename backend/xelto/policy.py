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

    # ── Per-user work.
    *((_ANY, glob, U) for glob in (
        "/profiles*", "/profile-images/*", "/generate*", "/history*", "/audio/*",
        "/voice_audio/*", "/design*", "/convert", "/transcribe", "/dub/*", "/preview/*",
        "/projects*", "/project-bundles*", "/glossary*", "/export/*", "/batch/*",
        "/audiobook*", "/longform/*", "/stories/*", "/personas*", "/dictation/*",
        "/tasks/*", "/jobs*", "/tools/*", "/watermark/*", "/v1/*", "/ws/*",
    )),
]


def classify(method: str, template: str) -> tuple[RouteClass, bool]:
    """Return the route's class and whether an explicit rule matched."""
    method = method.upper()
    for methods, pattern, cls in RULES:
        if methods != _ANY and method not in methods.split(","):
            continue
        if fnmatch.fnmatchcase(template, pattern):
            return cls, True
    return DEFAULT_CLASS, False
