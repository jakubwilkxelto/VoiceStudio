"""Route policy: which class of caller may reach each route.

Rules match ``(method, path template)`` exactly as the route snapshot
(``tests/fixtures/api_routes.txt``) spells them, so the completeness guard in
``tests/xelto/test_policy_completeness.py`` can check every upstream route
without importing the app. Anything no rule matches is ADMIN — a route that
upstream adds is closed until someone classifies it.
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

# (methods, template glob, class). First match wins. Methods "*" = any; the
# template glob uses fnmatch, so "/dub/*" covers every /dub/... template.
# P0 classifies only the health probe; P1 fills in the full matrix.
RULES: list[tuple[str, str, RouteClass]] = [
    ("GET", "/health", RouteClass.PUBLIC),
]


def classify(method: str, template: str) -> tuple[RouteClass, bool]:
    """Return the route's class and whether an explicit rule matched."""
    method = method.upper()
    for methods, pattern, cls in RULES:
        if methods != "*" and method not in methods.split(","):
            continue
        if fnmatch.fnmatchcase(template, pattern):
            return cls, True
    return DEFAULT_CLASS, False
