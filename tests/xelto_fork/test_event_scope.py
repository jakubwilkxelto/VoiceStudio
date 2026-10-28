"""``/ws/events``: user-data events reach only their owner, app events reach everyone."""
import asyncio
import json
import threading

import pytest

from core import event_bus
from xelto import events
from xelto.identity import SYSTEM, use_identity

from .conftest import ALICE, BOB


@pytest.fixture
def scoped_bus(monkeypatch):
    monkeypatch.setattr(event_bus, "_listeners", [])
    monkeypatch.setattr(event_bus, "_serving_loop", None)
    events.install()
    yield
    events.uninstall()


def _drain(q) -> list[str]:
    kinds = []
    while not q.empty():
        kinds.append(json.loads(q.get_nowait())["kind"])
    return kinds


def test_events_follow_their_owner(scoped_bus):
    async def scenario():
        with use_identity(ALICE):
            qa = await event_bus.subscribe()
        with use_identity(BOB):
            qb = await event_bus.subscribe()
        with use_identity(ALICE):
            event_bus.emit("profiles", {"id": "pa"})
        with use_identity(BOB):
            event_bus.emit("dub_history", {"action": "cleared"})

        def from_worker():  # sync endpoints and pipeline threads hand off to the loop
            with use_identity(ALICE):
                event_bus.emit("generation_history", {"id": "h1"})

        thread = threading.Thread(target=from_worker)
        thread.start()
        thread.join()
        event_bus.emit("model_status", {"sub_stage": "ready"})  # no user: app-wide
        with use_identity(SYSTEM):
            event_bus.emit("projects", {"id": "x"})  # user data with no user: nobody
        for _ in range(5):
            await asyncio.sleep(0)
        return _drain(qa), _drain(qb)

    alice, bob = asyncio.run(scenario())
    # The thread's event is handed off, so it may land after the others.
    assert sorted(alice) == ["generation_history", "model_status", "profiles"]
    assert sorted(bob) == ["dub_history", "model_status"]


def test_without_multiuser_every_listener_hears_everything(monkeypatch):
    monkeypatch.setattr(event_bus, "_listeners", [])
    monkeypatch.setattr(event_bus, "_serving_loop", None)

    async def scenario():
        with use_identity(ALICE):
            qa = await event_bus.subscribe()
        with use_identity(BOB):
            event_bus.emit("profiles", {"id": "pb"})
        await asyncio.sleep(0)
        return type(qa), _drain(qa)

    queue_type, kinds = asyncio.run(scenario())
    assert queue_type is asyncio.Queue
    assert kinds == ["profiles"]
