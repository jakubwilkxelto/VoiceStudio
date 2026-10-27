"""Keepalive for long silent streams (Cloudflare 524 behind proxies).

``with_keepalive`` must fill silence without changing the stream: items keep
their order, fast streams get no filler, errors propagate, context variables
persist across steps, and closing/cancelling finalizes the source promptly
(#2536). The long-form SSE routes use it.
"""
import asyncio
import contextvars

import pytest

from core import stream_keepalive as ka


async def _collect(gen):
    return [item async for item in gen]


async def _slow(items, delay):
    for item in items:
        await asyncio.sleep(delay)
        yield item


def test_silence_is_filled_and_order_kept():
    out = asyncio.run(_collect(ka.with_keepalive(_slow(["a", "b"], 0.12), "P", 0.03)))
    assert [x for x in out if x != "P"] == ["a", "b"]
    assert out.index("a") > 0 and out.count("P") >= 4
    assert out[-1] == "b"


def test_fast_stream_gets_no_filler():
    out = asyncio.run(_collect(ka.with_keepalive(_slow(list("abc"), 0), "P", 1.0)))
    assert out == ["a", "b", "c"]


def test_source_error_propagates_after_items():
    async def broken():
        yield "a"
        raise ValueError("boom")

    async def run():
        got = []
        with pytest.raises(ValueError, match="boom"):
            async for item in ka.with_keepalive(broken(), "P", 1.0):
                got.append(item)
        return got

    assert asyncio.run(run()) == ["a"]


def test_context_variables_persist_across_steps():
    var = contextvars.ContextVar("v", default="unset")

    async def src():
        var.set("set-in-step-1")
        yield 1
        await asyncio.sleep(0.05)
        yield var.get()

    out = asyncio.run(_collect(ka.with_keepalive(src(), "P", 0.01)))
    assert out[0] == 1 and out[-1] == "set-in-step-1"


def test_close_mid_step_cancels_and_finalizes_source():
    events = []

    async def src():
        try:
            yield "first"
            await asyncio.sleep(10)
            yield "never"
        except asyncio.CancelledError:
            events.append("cancelled")
            raise
        finally:
            events.append("finalized")

    async def run():
        gen = ka.with_keepalive(src(), "P", 0.01)
        assert await gen.__anext__() == "first"
        assert await gen.__anext__() == "P"  # source is now blocked mid-step
        await gen.aclose()
        return list(events)

    assert asyncio.run(run()) == ["cancelled", "finalized"]


def test_cancelled_consumer_still_finalizes_source():
    events = []

    async def src():
        try:
            await asyncio.sleep(10)
            yield "never"
        finally:
            events.append("finalized")

    async def run():
        gen = ka.with_keepalive(src(), "P", 5.0)
        task = asyncio.create_task(gen.__anext__())
        await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0)
        return list(events)

    assert asyncio.run(run()) == ["finalized"]


def test_longform_route_sends_sse_comments_during_a_silent_chapter(monkeypatch):
    import api.routers.audiobook as ab

    async def slow_render(plan, **kwargs):
        yield 'data: {"type": "started", "chapters": 1}\n\n'
        await asyncio.sleep(0.2)
        yield 'data: {"type": "done"}\n\n'

    monkeypatch.setattr(ka, "STREAM_KEEPALIVE_S", 0.03)
    monkeypatch.setattr(ab, "_ensure_active_engine_licence", lambda: None)
    monkeypatch.setattr(ab, "_public_longform_stream", slow_render)

    async def run():
        resp = await ab.longform_render(ab.LongformRenderRequest(chapters=[]), None)
        return resp, await _collect(resp.body_iterator)

    resp, chunks = asyncio.run(run())
    assert resp.headers["x-accel-buffering"] == "no"
    assert ka.SSE_KEEPALIVE in chunks
    assert [c for c in chunks if c != ka.SSE_KEEPALIVE] == [
        'data: {"type": "started", "chapters": 1}\n\n',
        'data: {"type": "done"}\n\n',
    ]
