"""Keep long-running HTTP streams alive behind proxies.

A streamed render can go minutes without writing a byte (a CPU-only chunk, a
cold model load, a long chapter). Reverse proxies treat that silence as a dead
origin: Cloudflare answers 524 after ~100 s, nginx closes after its read
timeout. ``with_keepalive`` interleaves a protocol-neutral filler whenever the
wrapped stream has been silent for ``interval`` seconds:

* NDJSON (``/generate`` with ``stream=true``): a blank line, which NDJSON
  readers skip (``generate.ts`` and ``streamingTts.js`` drop empty lines).
* SSE (long-form renders): a ``: keepalive`` comment, which SSE readers ignore.

Real items are passed through unchanged and in order; a stream that never goes
quiet gets no filler at all.

Each step of the source runs in its own task so the wrapper can wake on a
timer, but every step shares one ``contextvars.Context``, so context variables
behave as under a plain ``async for``. Closing or cancelling the wrapper
(client disconnect) cancels the in-flight step and closes the source before
returning, so renderers still finalize promptly (#2536).
"""
from __future__ import annotations

import asyncio
import contextvars
from typing import AsyncIterator, TypeVar

T = TypeVar("T")

#: Seconds of stream silence before a keepalive goes out. Well under the
#: shortest common proxy idle limits (Cloudflare ~100 s, nginx 60 s).
STREAM_KEEPALIVE_S = 15.0

#: Ready-made fillers for the two stream formats we serve.
NDJSON_KEEPALIVE = b"\n"
SSE_KEEPALIVE = ": keepalive\n\n"

#: Headers that stop intermediaries from buffering (and so swallowing) the
#: keepalives. Same set the dub task stream uses.
NO_BUFFER_HEADERS = {"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"}

_END = object()
# Close tasks that outlived a cancelled caller; held so they run to completion.
_closing: set[asyncio.Task] = set()


async def with_keepalive(
    source: AsyncIterator[T], ping: T, interval: float | None = None
) -> AsyncIterator[T]:
    """Yield ``source``'s items, plus ``ping`` after each ``interval`` of silence
    (default ``STREAM_KEEPALIVE_S``, read at call time)."""
    if interval is None:
        interval = STREAM_KEEPALIVE_S
    ctx = contextvars.copy_context()

    async def _next():
        try:
            return await source.__anext__()
        except StopAsyncIteration:
            return _END

    step: asyncio.Task | None = None
    try:
        while True:
            step = asyncio.create_task(_next(), context=ctx)
            while not (await asyncio.wait({step}, timeout=interval))[0]:
                yield ping
            item = step.result()  # re-raises the source's own error
            step = None
            if item is _END:
                return
            yield item
    finally:
        await _close(step, source, ctx)


async def _close(step: asyncio.Task | None, source, ctx: contextvars.Context) -> None:
    async def _finish():
        if step is not None:
            if not step.done():
                step.cancel()
                await asyncio.wait({step})
            if not step.cancelled():
                step.exception()  # retrieved; it already reached the caller or is moot
        aclose = getattr(source, "aclose", None)
        if aclose is not None:
            await aclose()

    closer = asyncio.create_task(_finish(), context=ctx)
    try:
        # Shielded: a cancelled response task must not abandon the source
        # half-closed; the close finishes even if this await is interrupted.
        await asyncio.shield(closer)
    except asyncio.CancelledError:
        _closing.add(closer)
        closer.add_done_callback(_closing.discard)
        raise
