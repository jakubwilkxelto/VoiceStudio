"""Who the current request (or background job) acts for.

A ContextVar rather than request state, so the identity follows the work into
tasks and (once P2 patches the executors) worker threads. ``None`` means "no
identity": in multi-user mode that must fail closed. :data:`SYSTEM` is the
explicit scope for startup, migrations and sweeps that act for nobody.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class Identity:
    sub: str
    username: str = ""
    roles: frozenset[str] = frozenset()
    is_admin: bool = False

    @property
    def is_system(self) -> bool:
        return self is SYSTEM


SYSTEM = Identity(sub="__system__", username="system")

current_identity: ContextVar[Identity | None] = ContextVar("xelto_identity", default=None)


@contextmanager
def use_identity(identity: Identity | None) -> Iterator[Identity | None]:
    """Run the block as ``identity``; the previous value is restored on exit."""
    token = current_identity.set(identity)
    try:
        yield identity
    finally:
        current_identity.reset(token)


def system_scope():
    """Run the block as :data:`SYSTEM` (startup, migrations, sweeps)."""
    return use_identity(SYSTEM)
