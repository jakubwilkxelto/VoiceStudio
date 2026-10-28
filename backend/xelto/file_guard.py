"""Who may fetch a file under ``/audio``, ``/voice_audio`` and ``/preview``.

Upstream serves these straight from disk to anyone allowed in. In multi-user
mode a file is served when, in this order:

1. it lies in a shared library subtree (gallery, archetype previews, marketplace);
2. the database ties it to the caller: a generation's ``audio_path``, a
   longform render's job, or a voice profile's reference audio (shared demo
   voices included);
3. failing that, its claim in ``xelto_file_owner`` names the caller (see
   :mod:`xelto.resources`).

A file owned by someone else is a 404. A file nothing accounts for is served
and logged in ``lenient`` mode and refused in ``strict`` mode
(``OMNIVOICE_MULTIUSER_FILES``).
"""
from __future__ import annotations

import logging
import os
import re
import sqlite3
from enum import Enum

from xelto import resources, store

logger = logging.getLogger("omnivoice.xelto")

SHARED_SUBTREES = {"audio": ("voice_gallery/", "archetype_previews/", "marketplace/")}
# Longform renders: "<type>_<job id>.<ext>" or the work dir "<type>_<job id>/…".
_LONGFORM = re.compile(r"^(audiobook|story)_([A-Za-z0-9_-]+?)(?:\.[A-Za-z0-9]+|/.*)$")
_PROFILE_SEPARATORS = (".", "-", "_")


class Verdict(str, Enum):
    MINE = "mine"
    SHARED = "shared"
    OTHER = "other"
    UNKNOWN = "unknown"


def verdict(key: str, sub: str) -> Verdict:
    prefix, rel = key.split("/", 1)
    if rel.startswith(SHARED_SUBTREES.get(prefix, ())):
        return Verdict.SHARED
    # The database first: a claim learned from a response can be wrong (an
    # echoed path), a row's owner cannot.
    try:
        known = _from_database(prefix, rel, sub)
    except sqlite3.Error:
        logger.warning("xelto file guard: lookup failed for %s", key, exc_info=True)
        known = Verdict.UNKNOWN
    if known is not Verdict.UNKNOWN:
        return known
    owner = resources.owner_of(key)
    if owner is None:
        return Verdict.UNKNOWN
    return Verdict.MINE if owner == sub else Verdict.OTHER


def allowed(key: str | None, sub: str, mode: str) -> bool:
    if key is None:
        return False
    result = verdict(key, sub)
    if result is Verdict.UNKNOWN:
        if mode == "strict":
            return False
        logger.warning("xelto file guard (lenient): serving unowned %s", key)
        return True
    return result is not Verdict.OTHER


# The mode install() configured, for checks that run outside the gate.
configured_mode = "strict"


def path_allowed(path, sub: str, mode: str | None = None) -> bool:
    """May ``sub`` use the file at ``path`` (any data folder, not only served ones)?

    For upstream code that opens files a client names, such as a project bundle
    export collecting a project's assets.
    """
    from core.config import DUB_DIR, OUTPUTS_DIR, VOICES_DIR

    from xelto import hooks

    mode = mode or configured_mode
    real = os.path.realpath(str(path))

    def under(root: str) -> str | None:
        root = os.path.realpath(root)
        return os.path.relpath(real, root).replace(os.sep, "/") if real.startswith(root + os.sep) else None

    if (rel := under(DUB_DIR)) is not None:
        return hooks.dub_job_owner(rel.split("/", 1)[0]) == sub
    if (rel := under(VOICES_DIR)) is not None:
        return allowed(resources.file_key("voice_audio", rel), sub, mode)
    if (rel := under(OUTPUTS_DIR)) is not None:
        if rel.startswith("project_handoff/"):
            bundle = rel.split("/", 2)[1]
            return resources.owner_of(f"bundle/{bundle}") == sub
        return allowed(resources.file_key("audio", rel), sub, mode)
    return False


def _mine(owner: str | None, sub: str) -> Verdict:
    if owner is None:
        return Verdict.UNKNOWN
    return Verdict.MINE if owner == sub else Verdict.OTHER


def _from_database(prefix: str, rel: str, sub: str) -> Verdict:
    if prefix == "voice_audio":
        return _voice_file(rel, sub)
    if prefix != "audio":
        return Verdict.UNKNOWN
    longform = _LONGFORM.match(rel)
    if longform:
        return _mine(resources.row_owner("jobs", longform.group(2)), sub)
    conn = store.connect_both()
    try:
        row = conn.execute(
            "SELECT o.owner FROM main.generation_history AS g "
            "JOIN xelto.xelto_row_owner AS o ON o.tbl = 'generation_history' AND o.row_id = g.id "
            "WHERE g.audio_path = ? LIMIT 1",
            (rel,),
        ).fetchone()
    finally:
        conn.close()
    return _mine(row[0] if row else None, sub)


def _voice_file(name: str, sub: str) -> Verdict:
    """A file in VOICES_DIR belongs to the profile that names it or prefixes it."""
    if "/" in name:
        return Verdict.UNKNOWN
    conn = store.connect_both()
    try:
        rows = conn.execute(
            "SELECT p.id, COALESCE(p.is_demo, 0), o.owner, "
            "? IN (p.ref_audio_path, p.locked_audio_path, p.consent_audio_path) "
            "FROM main.voice_profiles AS p "
            "LEFT JOIN xelto.xelto_row_owner AS o ON o.tbl = 'voice_profiles' AND o.row_id = p.id "
            "WHERE ? IN (p.ref_audio_path, p.locked_audio_path, p.consent_audio_path) "
            "OR (length(p.id) > 0 AND substr(?, 1, length(p.id)) = p.id)",
            (name, name, name),
        ).fetchall()
    finally:
        conn.close()
    for profile_id, is_demo, owner, named_by_path in rows:
        prefixed = name[len(profile_id):len(profile_id) + 1] in _PROFILE_SEPARATORS
        if not (named_by_path or prefixed):
            continue
        if is_demo:
            return Verdict.SHARED
        return _mine(owner, sub)
    return Verdict.UNKNOWN
