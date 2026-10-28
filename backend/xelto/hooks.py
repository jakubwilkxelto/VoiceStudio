"""Functions the ``XELTO:`` hooks in upstream files call.

Each is a no-op (or "allowed") while multi-user mode is inactive, so a hook
never changes upstream behaviour with the flag off.
"""
from __future__ import annotations

import sqlite3
import threading

from xelto import db_scope, store
from xelto.identity import Identity, current_identity

# ── core/tasks.py: TaskManager runs every task in its long-lived worker ─────


def capture() -> Identity | None:
    """``add_task``: remember who queued the task."""
    return current_identity.get() if db_scope.is_active() else None


def enter(task: dict) -> None:
    """``worker``: act as the task's submitter until the next task starts.

    The worker is one coroutine started at boot, so without this every task
    would run as whoever started it.
    """
    if db_scope.is_active():
        current_identity.set(task.get("xelto"))


# ── services/dub_pipeline.py: the in-memory dub job cache ───────────────────
# get_job serves _dub_jobs before it reads dub_history (where the views
# apply), so the cache needs its own owner check. Owners of jobs not yet saved
# come from the creators install() wraps; saved ones from xelto_row_owner.

_dub_owners: dict[str, str] = {}
_dub_owners_lock = threading.Lock()


def record_dub_owner(job_id: str) -> None:
    identity = current_identity.get()
    if db_scope.is_active() and identity is not None and not identity.is_system:
        with _dub_owners_lock:
            _dub_owners.setdefault(str(job_id), identity.sub)


def _saved_dub_owner(job_id: str) -> str | None:
    conn = store.connect()
    try:
        row = conn.execute(
            "SELECT owner FROM xelto_row_owner WHERE tbl = 'dub_history' AND row_id = ?",
            (str(job_id),),
        ).fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    return row[0] if row else None


def dub_job_visible(job_id: str) -> bool:
    """``get_job``: may the current identity see this dub job?"""
    if not db_scope.is_active():
        return True
    identity = current_identity.get()
    if identity is not None and identity.is_system:
        return True
    if identity is None:
        return False
    key = str(job_id)
    with _dub_owners_lock:
        owner = _dub_owners.get(key)
    if owner is None:
        owner = _saved_dub_owner(key)
        if owner is not None:
            with _dub_owners_lock:
                _dub_owners[key] = owner
    # An unknown job is either not there at all (get_job returns None either
    # way) or one nobody claimed, which no user may see.
    return owner == identity.sub


def forget_dub_owners() -> None:
    """Drop the cache (tests, and after ownership changes from the CLI)."""
    with _dub_owners_lock:
        _dub_owners.clear()
