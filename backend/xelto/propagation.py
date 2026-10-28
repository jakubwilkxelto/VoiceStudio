"""Carry the caller's identity into the threads and queues that do its work.

Installed by :func:`xelto.install` only in multi-user mode. ``asyncio.to_thread``
copies contextvars itself; these patches cover the rest:

- ``ThreadPoolExecutor.submit`` (``run_in_executor``, the GPU and CPU pools),
- ``threading.Thread.start`` (the dub pipeline's worker threads and others),
- ``JobQueue.submit`` (``gpu_queue``), whose workers are started at boot.

``TaskManager`` gets the same treatment through its hook in ``core/tasks.py``
(:mod:`xelto.hooks`). Without these, the work would run with no identity and
the database would show it nothing (fail closed), not another user's data.
"""
from __future__ import annotations

import contextvars
import functools
import threading
from concurrent.futures import ThreadPoolExecutor

from xelto.identity import current_identity, use_identity

_ORIGINALS: dict[str, object] = {}


def install() -> None:
    if _ORIGINALS:
        return
    from core.job_queue import JobQueue

    _ORIGINALS["pool_submit"] = ThreadPoolExecutor.submit
    _ORIGINALS["thread_start"] = threading.Thread.start
    _ORIGINALS["queue_submit"] = JobQueue.submit
    ThreadPoolExecutor.submit = _pool_submit
    threading.Thread.start = _thread_start
    JobQueue.submit = _queue_submit


def uninstall() -> None:
    """Restore the originals (tests)."""
    if not _ORIGINALS:
        return
    from core.job_queue import JobQueue

    ThreadPoolExecutor.submit = _ORIGINALS.pop("pool_submit")
    threading.Thread.start = _ORIGINALS.pop("thread_start")
    JobQueue.submit = _ORIGINALS.pop("queue_submit")


def _pool_submit(self, fn, /, *args, **kwargs):
    ctx = contextvars.copy_context()
    return _ORIGINALS["pool_submit"](self, ctx.run, fn, *args, **kwargs)


def _thread_start(self):
    ctx = contextvars.copy_context()
    run = self.run
    self.run = functools.partial(ctx.run, run)
    return _ORIGINALS["thread_start"](self)


async def _queue_submit(self, label, fn, job_id=None):
    identity = current_identity.get()

    async def as_submitter(job):
        with use_identity(identity):
            return await fn(job)

    return await _ORIGINALS["queue_submit"](self, label, as_submitter, job_id)
