"""``/ws/events`` per user: a user hears about their own data, everyone about the app.

Upstream's ``event_bus`` puts every event into every listener's queue. Each
listener's queue becomes a :class:`ScopedQueue` that drops events about
another user's data. ``emit`` schedules ``_broadcast`` with ``create_task`` or
``call_soon_threadsafe``, both of which copy the emitter's context, so the
identity in force when the queue is fed is the emitter's.

Events about user data (:data:`USER_KINDS`) reach only the listeners signed
in as the emitter; one emitted with no user (startup, SYSTEM) reaches nobody.
Every other kind (``model_status``, ``system_error``, ...) reaches everyone.
"""
from __future__ import annotations

import asyncio
import functools
import json

from xelto.identity import current_identity

USER_KINDS = frozenset({
    "profiles", "generation_history", "dub_history", "export_history", "projects", "glossary",
})

_ORIGINAL: dict[str, object] = {}


class ScopedQueue(asyncio.Queue):
    """A listener queue that accepts only events meant for ``xelto_sub``."""

    xelto_sub: str | None = None

    def put_nowait(self, item) -> None:
        if _audience_allows(item, self.xelto_sub):
            super().put_nowait(item)


@functools.lru_cache(maxsize=256)
def _kind(event_str: str) -> str:
    try:
        event = json.loads(event_str)
    except (TypeError, ValueError):
        return ""
    return str(event.get("kind", "")) if isinstance(event, dict) else ""


def _audience_allows(event_str, sub: str | None) -> bool:
    if not isinstance(event_str, str) or _kind(event_str) not in USER_KINDS:
        return True
    emitter = current_identity.get()
    if emitter is None or emitter.is_system:
        return False
    return sub is not None and emitter.sub == sub


def install() -> None:
    from core import event_bus

    if _ORIGINAL:
        return
    _ORIGINAL["subscribe"] = original = event_bus.subscribe

    @functools.wraps(original)
    async def subscribe() -> asyncio.Queue:
        q = await original()
        identity = current_identity.get()
        q.__class__ = ScopedQueue
        q.xelto_sub = None if identity is None or identity.is_system else identity.sub
        return q

    event_bus.subscribe = subscribe


def uninstall() -> None:
    """Restore upstream's ``subscribe`` (tests)."""
    from core import event_bus

    if _ORIGINAL:
        event_bus.subscribe = _ORIGINAL.pop("subscribe")
