"""Ownership checks the gate runs before upstream sees a request.

The database views already hide other users' rows from every query. What
they cannot cover is looked up here, keyed by the route's path parameters
(:data:`xelto.policy.PATH_OWNERS` says which check a route gets):

- dub jobs, which upstream also keeps in memory and on disk by id;
- background tasks, which stream live from ``task_manager`` and replay
  ``job_events`` (not a scoped table);
- batch jobs and project bundles, which never reach the database;
- files (:mod:`xelto.file_guard`).

The same owners filter the three lists upstream builds outside the database
(:data:`LIST_FILTERS`).
"""
from __future__ import annotations

import json
from collections.abc import Callable

from xelto import file_guard, hooks, resources
from xelto.identity import Identity
from xelto.policy import Owner


def _param(params: dict, *names: str) -> str:
    for name in names:
        if name in params:
            return str(params[name])
    return ""


def _dub(params: dict, identity: Identity, _mode: str, _template: str) -> bool:
    return hooks.dub_job_visible(_param(params, "job_id", "history_id"))


def _task(params: dict, identity: Identity, _mode: str, _template: str) -> bool:
    from core.tasks import task_manager

    task_id = _param(params, "task_id")
    live = task_manager.active_tasks.get(task_id)
    submitter = live.get("xelto") if isinstance(live, dict) else None
    if submitter is not None:
        return submitter.sub == identity.sub
    return resources.row_owner("jobs", task_id) == identity.sub


def _job(params: dict, identity: Identity, _mode: str, _template: str) -> bool:
    return resources.row_owner("jobs", _param(params, "job_id")) == identity.sub


def _batch(params: dict, identity: Identity, _mode: str, _template: str) -> bool:
    owner = resources.batch_owner(_param(params, "job_id"))
    return owner is not None and owner.sub == identity.sub


def _bundle(params: dict, identity: Identity, _mode: str, _template: str) -> bool:
    return resources.owner_of(f"bundle/{_param(params, 'identifier')}") == identity.sub


def file_key(template: str, params: dict) -> str | None:
    """The served file a ``/audio``, ``/voice_audio`` or ``/preview`` request names."""
    if template in ("/audio/{audio_id}.ogg", "/audio/{audio_id}.opus", "/audio/{audio_id}/quality"):
        return resources.file_key("audio", _param(params, "audio_id") + ".wav")
    if template == "/preview/{filename}":
        return resources.file_key("preview", _param(params, "filename"))
    if template in ("/audio/*", "/voice_audio/*"):
        return resources.file_key(template[1:-2], _param(params, "path"))
    return None


def _file(params: dict, identity: Identity, mode: str, template: str) -> bool:
    return file_guard.allowed(file_key(template, params), identity.sub, mode)


RESOLVERS: dict[Owner, Callable[[dict, Identity, str, str], bool]] = {
    Owner.DUB: _dub,
    Owner.TASK: _task,
    Owner.JOB: _job,
    Owner.BATCH: _batch,
    Owner.BUNDLE: _bundle,
    Owner.FILE: _file,
}


def visible(owner: Owner, template: str, params: dict, identity: Identity, mode: str) -> bool:
    """May ``identity`` reach the resource this request names?"""
    resolver = RESOLVERS.get(owner)
    if resolver is None or not params:  # DB_VIEW / SHARED, or nothing named
        return True
    return resolver(params, identity, mode, template)


# ── Lists built outside the database ────────────────────────────────────────


def _batch_ids(identity: Identity) -> Callable[[object], bool]:
    def mine(item) -> bool:
        owner = resources.batch_owner(str(item.get("id", "")))
        return owner is not None and owner.sub == identity.sub
    return mine


def _bundle_ids(identity: Identity) -> Callable[[object], bool]:
    ids = resources.owned_keys(identity.sub, "bundle")
    return lambda item: str(item.get("id", "")) in ids


def _job_ids(identity: Identity) -> Callable[[object], bool]:
    ids = resources.owned_rows("jobs", identity.sub)
    return lambda item: str(item.get("job_id", "")) in ids


# (method, template) -> (key holding the list, or None for a bare list; predicate factory)
LIST_FILTERS: dict[tuple[str, str], tuple[str | None, Callable[[Identity], Callable[[object], bool]]]] = {
    ("GET", "/batch/jobs"): (None, _batch_ids),
    ("GET", "/project-bundles"): (None, _bundle_ids),
    ("GET", "/audiobook/jobs"): ("jobs", _job_ids),
}


def filter_list(body: bytes, key: str | None, keep: Callable[[object], bool]) -> bytes:
    """Keep the caller's items; anything not shaped as expected becomes an empty list."""
    try:
        data = json.loads(body)
    except ValueError:
        data = None
    if key:
        items = data.get(key) if isinstance(data, dict) else None
    else:
        items = data
    kept = [i for i in items if isinstance(i, dict) and keep(i)] if isinstance(items, list) else []
    if key:
        data = data if isinstance(data, dict) else {}
        data[key] = kept
    else:
        data = kept
    return json.dumps(data).encode()
