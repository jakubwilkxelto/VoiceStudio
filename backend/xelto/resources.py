"""Owners of what the database views cannot see: files and jobs kept outside SQLite.

Persistent claims live in ``xelto.xelto_file_owner`` under namespaced keys:

- ``audio/<rel>``, ``voice_audio/<rel>``, ``preview/<name>`` — files served
  from ``OUTPUTS_DIR``, ``VOICES_DIR`` and ``PREVIEW_DIR``;
- ``bundle/<uuid>`` — project bundles under ``outputs/project_handoff``.

Batch jobs live only in upstream's memory (``api.routers.batch._jobs``), so
their owners do too.

Claims are first-come: a key that already has an owner is never reassigned.
"""
from __future__ import annotations

import logging
import os
import re
import sqlite3
import threading
import time
from urllib.parse import unquote

from xelto import store
from xelto.identity import Identity, current_identity

logger = logging.getLogger("omnivoice.xelto")

FILE_PREFIXES = ("audio", "voice_audio", "preview")


def acting_user() -> Identity | None:
    """The signed-in user the current work runs for (not SYSTEM, not nobody)."""
    identity = current_identity.get()
    if identity is None or identity.is_system:
        return None
    return identity


# ── Persistent claims ───────────────────────────────────────────────────────


def claim(key: str, owner: str) -> None:
    conn = store.connect()
    try:
        with conn:
            conn.execute(
                "INSERT OR IGNORE INTO xelto_file_owner(path, owner, created_at) VALUES (?, ?, ?)",
                (key, owner, time.time()),
            )
    finally:
        conn.close()


def owner_of(key: str) -> str | None:
    conn = store.connect()
    try:
        row = conn.execute("SELECT owner FROM xelto_file_owner WHERE path = ?", (key,)).fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    return row[0] if row else None


def owned_keys(owner: str, prefix: str) -> set[str]:
    """Keys under ``prefix/`` that ``owner`` holds, without the prefix."""
    conn = store.connect()
    try:
        rows = conn.execute(
            "SELECT substr(path, ?) FROM xelto_file_owner WHERE owner = ? AND path LIKE ? ESCAPE '\\'",
            (len(prefix) + 2, owner, _like_prefix(prefix + "/")),
        ).fetchall()
    finally:
        conn.close()
    return {r[0] for r in rows}


def row_owner(table: str, row_id: str) -> str | None:
    """Who owns row ``row_id`` of a per-user table (``xelto_row_owner``)."""
    conn = store.connect()
    try:
        row = conn.execute(
            "SELECT owner FROM xelto_row_owner WHERE tbl = ? AND row_id = ?", (table, str(row_id))
        ).fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    return row[0] if row else None


def owned_rows(table: str, owner: str) -> set[str]:
    conn = store.connect()
    try:
        rows = conn.execute(
            "SELECT row_id FROM xelto_row_owner WHERE tbl = ? AND owner = ?", (table, owner)
        ).fetchall()
    finally:
        conn.close()
    return {r[0] for r in rows}


def _like_prefix(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


# ── Batch jobs (in memory, like upstream's) ─────────────────────────────────

_batch_owners: dict[str, Identity] = {}
_batch_lock = threading.Lock()


class BatchJobs(dict):
    """Stand-in for ``batch._jobs`` that remembers who created each job."""

    def __setitem__(self, job_id, job) -> None:
        user = acting_user()
        if user is not None:
            with _batch_lock:
                _batch_owners.setdefault(str(job_id), user)
        super().__setitem__(job_id, job)


def batch_owner(job_id: str) -> Identity | None:
    with _batch_lock:
        return _batch_owners.get(str(job_id))


def forget_batch_owners() -> None:
    with _batch_lock:
        _batch_owners.clear()


# ── Learning file owners from responses ─────────────────────────────────────

# A served file in a response: "/audio/x.wav", "/voice_audio/p.wav", "/preview/u.mp4".
_URL = re.compile(r"/(audio|voice_audio|preview)/([A-Za-z0-9._~%\-/]+)")
# Longform renders report their output relative to OUTPUTS_DIR (served at /audio).
_OUTPUT = re.compile(r'"output"\s*:\s*"([^"\\]+)"')
_CARRY = 512
_TEXT_TYPES = (b"application/json", b"text/event-stream")


def _roots() -> dict[str, str]:
    from core.config import OUTPUTS_DIR, PREVIEW_DIR, VOICES_DIR

    return {"audio": OUTPUTS_DIR, "voice_audio": VOICES_DIR, "preview": PREVIEW_DIR}


def file_key(prefix: str, rel: str) -> str | None:
    """Normalise a served path to its key; None if it escapes its root."""
    rel = unquote(rel).replace("\\", "/").lstrip("/")
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    if not parts or ".." in parts or prefix not in FILE_PREFIXES:
        return None
    return f"{prefix}/{'/'.join(parts)}"


def existing_file_key(prefix: str, rel: str, *, since: float | None = None) -> str | None:
    """The key of an existing served file; with ``since``, only one written after it."""
    key = file_key(prefix, rel)
    if key is None:
        return None
    root = os.path.realpath(_roots()[prefix])
    path = os.path.realpath(os.path.join(root, key[len(prefix) + 1:]))
    if not path.startswith(root + os.sep) or not os.path.isfile(path):
        return None
    if since is not None:
        try:
            if os.path.getmtime(path) < since:
                return None
        except OSError:
            return None
    return key


# How far before the request a file may have been written and still count as
# its output (coarse filesystem timestamps, clock steps).
CLAIM_SLACK_SECONDS = 2.0


class ResponseTap:
    """Claims, for the caller, the files a response points them to.

    Reads ``X-Audio-Path`` / ``X-Audio-Id`` and scans JSON and SSE bodies chunk
    by chunk (keeping a tail so a URL split across chunks is still seen). Only
    files that exist are claimed, so API paths that look like file URLs
    (``/audio/voices``) are not.

    A response may echo what the client stored (a project's state), so a path
    in it proves nothing by itself. Only files written while the request ran
    are claimed, and never voice files, whose owners come from their profiles.
    The database wins over a claim either way (:func:`xelto.file_guard.verdict`).
    """

    def __init__(self, owner: str, started: float | None = None) -> None:
        self.owner = owner
        self.since = (time.time() if started is None else started) - CLAIM_SLACK_SECONDS
        self.scan = False
        self._tail = ""
        self._seen: set[str] = set()

    def start(self, message) -> None:
        headers = {k.lower(): v for k, v in message.get("headers", [])}
        if 200 <= message["status"] < 300:
            path = headers.get(b"x-audio-path")
            audio_id = headers.get(b"x-audio-id")
            if path:
                self._claim(existing_file_key("audio", path.decode("latin-1"), since=self.since))
            elif audio_id:
                self._claim(existing_file_key("audio", audio_id.decode("latin-1") + ".wav",
                                              since=self.since))
            ctype = headers.get(b"content-type", b"")
            self.scan = any(ctype.startswith(t) for t in _TEXT_TYPES)

    def body(self, chunk: bytes) -> None:
        if not self.scan or not chunk:
            return
        text = self._tail + chunk.decode("utf-8", "ignore")
        for prefix, rel in _URL.findall(text):
            if prefix != "voice_audio":
                self._claim(existing_file_key(prefix, rel, since=self.since))
        for rel in _OUTPUT.findall(text):
            self._claim(existing_file_key("audio", rel, since=self.since))
        self._tail = text[-_CARRY:]

    def _claim(self, key: str | None) -> None:
        if key is None or key in self._seen:
            return
        self._seen.add(key)
        try:
            claim(key, self.owner)
        except sqlite3.Error:
            logger.warning("xelto: could not record the owner of %s", key, exc_info=True)
