"""The caller's identity follows its work into threads, pools and queues."""
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from core.db import db_conn
from xelto import db_scope, propagation
from xelto.identity import current_identity, system_scope, use_identity

from .conftest import ALICE, BOB


def _who():
    identity = current_identity.get()
    return identity.sub if identity else None


@pytest.fixture
def patched(keep_init_db):
    propagation.install()
    yield
    propagation.uninstall()


def test_thread_start_carries_identity(patched):
    seen = []
    with use_identity(ALICE):
        thread = threading.Thread(target=lambda: seen.append(_who()))
    with use_identity(BOB):
        thread.start()  # the context at start() is the one that counts
    thread.join()
    assert seen == ["bob-sub"]


def test_pool_submit_carries_identity_per_item(patched):
    with ThreadPoolExecutor(max_workers=1) as pool:
        with use_identity(ALICE):
            first = pool.submit(_who)
        with use_identity(BOB):
            second = pool.submit(_who)
        third = pool.submit(_who)
        assert (first.result(), second.result(), third.result()) == ("alice-sub", "bob-sub", None)


def test_event_loop_executors_carry_identity(patched):
    async def main():
        loop = asyncio.get_running_loop()
        with use_identity(ALICE):
            in_executor = await loop.run_in_executor(None, _who)
            to_thread = await asyncio.to_thread(_who)
        return in_executor, to_thread

    assert asyncio.run(main()) == ("alice-sub", "alice-sub")


def test_job_queue_runs_each_job_as_its_submitter(patched):
    from core.job_queue import JobQueue

    async def main():
        with system_scope():  # workers start at boot
            queue = JobQueue(name="t", concurrency=1)
            await queue.start()

        async def job(_job):
            return _who()

        with use_identity(ALICE):
            a = await queue.submit("a", job)
        with use_identity(BOB):
            b = await queue.submit("b", job)
        results = [(await queue.wait(j.id, timeout=5)).result for j in (a, b)]
        await queue.stop()
        return results

    assert asyncio.run(main()) == ["alice-sub", "bob-sub"]


def test_task_manager_runs_each_task_as_its_submitter(scoped_db):
    from core.tasks import TaskManager

    seen = {}

    async def task(name):
        seen[name] = _who()
        # The task's own database work sees its submitter's rows.
        with db_conn() as conn:
            seen[name + "_jobs"] = sorted(r[0] for r in conn.execute("SELECT id FROM jobs"))

    async def main():
        manager = TaskManager()
        with system_scope():
            worker = asyncio.create_task(manager.worker())
        with use_identity(ALICE):
            await manager.add_task("ta", "test", task, "a")
        with use_identity(BOB):
            await manager.add_task("tb", "test", task, "b")
        await manager.queue.join()
        worker.cancel()
        return manager

    manager = asyncio.run(main())
    assert seen == {"a": "alice-sub", "a_jobs": ["ta"], "b": "bob-sub", "b_jobs": ["tb"]}
    assert manager.active_tasks["ta"]["status"] == "done"
    with use_identity(ALICE), db_conn() as conn:
        assert conn.execute("SELECT status FROM jobs WHERE id = 'ta'").fetchone()[0] == "done"


def test_task_manager_hooks_are_inert_when_mode_is_off(db_path):
    from xelto import hooks

    assert not db_scope.is_active()
    with use_identity(ALICE):
        assert hooks.capture() is None
    hooks.enter({"xelto": ALICE})
    assert current_identity.get() is None


def test_install_and_uninstall_restore_the_originals():
    from core.job_queue import JobQueue

    originals = (ThreadPoolExecutor.submit, threading.Thread.start, JobQueue.submit)
    propagation.install()
    try:
        assert ThreadPoolExecutor.submit is not originals[0]
    finally:
        propagation.uninstall()
    assert (ThreadPoolExecutor.submit, threading.Thread.start, JobQueue.submit) == originals
