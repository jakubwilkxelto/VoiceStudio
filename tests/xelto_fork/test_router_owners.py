"""Owners of what routers keep outside the database: batch jobs and project bundles."""
import asyncio
import importlib

import pytest

from xelto import resources
from xelto.identity import current_identity, use_identity
from xelto.resolvers import filter_list

from .conftest import ALICE, BOB

# The package exports install() under the module's name.
install_module = importlib.import_module("xelto.install")


@pytest.fixture
def scoped_routers(keep_init_db, db_path):
    from api.routers import batch

    seen = []

    async def fake_pipeline(job_id, job):
        seen.append((job_id, current_identity.get()))

    batch._run_batch_pipeline = fake_pipeline
    batch._jobs = {}
    install_module._scope_routers()
    return batch, seen


def test_each_batch_job_runs_as_its_creator(scoped_routers):
    batch, seen = scoped_routers
    with use_identity(ALICE):
        batch._jobs["ja"] = {"id": "ja"}
    with use_identity(BOB):
        batch._jobs["jb"] = {"id": "jb"}
    batch._jobs["nobody"] = {"id": "nobody"}

    async def worker_as_alice():
        # The worker task inherits whoever made the first enqueue.
        with use_identity(ALICE):
            await batch._run_batch_pipeline("jb", batch._jobs["jb"])
            await batch._run_batch_pipeline("ja", batch._jobs["ja"])
            with pytest.raises(RuntimeError, match="no owner"):
                await batch._run_batch_pipeline("nobody", batch._jobs["nobody"])

    asyncio.run(worker_as_alice())
    assert seen == [("jb", BOB), ("ja", ALICE)]
    assert resources.batch_owner("ja") == ALICE and resources.batch_owner("nobody") is None


def test_new_bundle_directories_belong_to_their_creator(scoped_routers, tmp_path, monkeypatch):
    import uuid

    from api.routers import project_bundles

    monkeypatch.setattr(project_bundles, "OUTPUTS_DIR", str(tmp_path))

    new = str(uuid.uuid4())
    with use_identity(ALICE):
        project_bundles._directory(new).mkdir()
    with use_identity(BOB):
        project_bundles._directory(new)  # exists already: not bob's
    assert resources.owner_of(f"bundle/{new}") == ALICE.sub


def test_scope_routers_is_idempotent(scoped_routers):
    batch, _ = scoped_routers
    jobs, run = batch._jobs, batch._run_batch_pipeline
    install_module._scope_routers()
    assert batch._jobs is jobs and batch._run_batch_pipeline is run


@pytest.mark.parametrize("body, key, expected", [
    (b'[{"id": "a"}, {"id": "b"}]', None, b'[{"id": "a"}]'),
    (b'{"jobs": [{"id": "a"}, {"id": "b"}], "n": 2}', "jobs", b'{"jobs": [{"id": "a"}], "n": 2}'),
    # A count next to the list follows it.
    (b'{"notifications": [{"id": "a"}, {"id": "b"}], "count": 2}', "notifications",
     b'{"notifications": [{"id": "a"}], "count": 1}'),
    # Unexpected shapes fail closed.
    (b'{"items": [{"id": "a"}]}', None, b"[]"),
    (b'[{"id": "a"}]', "jobs", b'{"jobs": []}'),
    (b"not json", None, b"[]"),
])
def test_list_filter_keeps_only_matching_items(body, key, expected):
    assert filter_list(body, key, lambda item: item.get("id") == "a") == expected
