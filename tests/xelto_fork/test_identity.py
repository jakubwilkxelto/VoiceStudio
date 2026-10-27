"""Identity ContextVar: scoping, and which thread hand-offs carry it."""
import asyncio

from xelto.identity import SYSTEM, Identity, current_identity, system_scope, use_identity

ALICE = Identity(sub="alice", username="alice", roles=frozenset({"voicestudio-user"}))


def test_default_is_no_identity():
    assert current_identity.get() is None


def test_scopes_restore_previous_value():
    with use_identity(ALICE):
        assert current_identity.get() is ALICE
        with system_scope():
            assert current_identity.get() is SYSTEM
            assert SYSTEM.is_system
        assert current_identity.get() is ALICE
    assert current_identity.get() is None


def test_to_thread_carries_identity():
    async def run():
        with use_identity(ALICE):
            return await asyncio.to_thread(current_identity.get)

    assert asyncio.run(run()) is ALICE


def test_bare_run_in_executor_drops_identity():
    # Why P2 must patch ThreadPoolExecutor.submit: loop.run_in_executor does
    # not copy the context, so ~50 upstream call sites would run as "nobody".
    async def run():
        with use_identity(ALICE):
            return await asyncio.get_running_loop().run_in_executor(None, current_identity.get)

    assert asyncio.run(run()) is None
