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
import os
from collections.abc import Callable

from xelto import file_guard, hooks, resources, store
from xelto.identity import Identity
from xelto.policy import Owner


def _param(params: dict, *names: str) -> str:
    for name in names:
        if name in params:
            return str(params[name])
    return ""


def _dub(params: dict, identity: Identity, _mode: str, _template: str) -> bool:
    return hooks.dub_job_visible(_param(params, "job_id", "history_id"))


def _dub_body(params: dict, identity: Identity, _mode: str, _template: str) -> bool:
    """A job id the client picks: its own job, or one nobody holds yet."""
    from core.config import DUB_DIR

    job_id = _param(params, "job_id")
    if not job_id or hooks.dub_job_visible(job_id):
        return True
    if hooks.dub_job_owner(job_id) is not None:
        return False
    # Nobody is recorded, but a directory by that name is someone's work.
    return not os.path.lexists(os.path.join(DUB_DIR, job_id))


_PROFILE_SEPARATORS = (".", "-", "_")


def _profile(params: dict, identity: Identity, _mode: str, _template: str) -> bool:
    """The caller's profile, a shared demo voice, or an id that names nothing.

    Upstream's profile routes read and write files named after the id
    (``<id>.portrait.jpg``, reference audio) even when the scoped row is not
    visible, so another user's id must stop at the gate.
    """
    from core.config import VOICES_DIR

    profile_id = _param(params, "profile_id")
    if not profile_id:
        return True
    conn = store.connect_both()
    try:
        row = conn.execute(
            "SELECT COALESCE(p.is_demo, 0), o.owner FROM main.voice_profiles AS p "
            "LEFT JOIN xelto.xelto_row_owner AS o ON o.tbl = 'voice_profiles' AND o.row_id = p.id "
            "WHERE p.id = ?",
            (profile_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is not None:
        is_demo, owner = row
        return bool(is_demo) or owner == identity.sub
    # No such profile: upstream answers 404 itself, unless files by that name
    # remain (another user's leftovers), which the handler would touch.
    try:
        names = os.listdir(VOICES_DIR)
    except OSError:
        return True
    return not any(
        name.startswith(profile_id) and name[len(profile_id):len(profile_id) + 1] in _PROFILE_SEPARATORS
        for name in names
    )


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
    Owner.PROFILE: _profile,
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


# Body ids get their own checks where "new" is legitimate (the client picks it).
BODY_RESOLVERS: dict[Owner, Callable[[dict, Identity, str, str], bool]] = {
    Owner.DUB: _dub_body,
}


def body_visible(owner: Owner, field: str, value, identity: Identity, mode: str) -> bool:
    """May ``identity`` name ``value`` in the body field ``field``?"""
    if value is None or value == "":
        return True
    if not isinstance(value, (str, int)):
        return False  # an id in an unexpected shape: refuse rather than guess
    resolver = BODY_RESOLVERS.get(owner) or RESOLVERS[owner]
    return resolver({field: str(value)}, identity, mode, "")


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
