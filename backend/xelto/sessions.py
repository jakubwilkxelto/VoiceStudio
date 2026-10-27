"""Browser sessions and known users, stored in the ``xelto_*`` tables.

The cookie carries a random token; only its SHA-256 is stored. The Keycloak
refresh token is kept Fernet-encrypted so roles can be re-checked (and a user
disabled in Keycloak loses access) without another sign-in.

Calls are synchronous SQLite; the middleware runs them in a worker thread.
"""
from __future__ import annotations

import json
import secrets
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass

from xelto.config import ADMIN_ROLE
from xelto.crypto import InvalidToken, fernet, token_hash
from xelto.identity import Identity

IDLE_TIMEOUT = 2 * 3600.0
ABSOLUTE_TIMEOUT = 12 * 3600.0
ROLE_RECHECK = 300.0
_TOUCH_EVERY = 60.0


@dataclass(frozen=True)
class Session:
    id_hash: str
    identity: Identity
    refresh_token: str | None
    roles_checked_at: float
    last_seen_at: float

    @property
    def roles_stale(self) -> bool:
        return time.time() - self.roles_checked_at > ROLE_RECHECK


class SessionStore:
    def __init__(self, db_path: Callable[[], str], secret: str):
        self._db_path = db_path
        self._fernet = fernet(secret, "refresh-token")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path())
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _seal(self, refresh_token: str | None) -> bytes | None:
        return self._fernet.encrypt(refresh_token.encode()) if refresh_token else None

    def _open(self, blob: bytes | None) -> str | None:
        if not blob:
            return None
        try:
            return self._fernet.decrypt(bytes(blob)).decode()
        except InvalidToken:
            return None

    # ── Users ────────────────────────────────────────────────────────────

    def upsert_user(self, identity: Identity) -> bool:
        """Record the sign-in; False when the user is disabled locally."""
        now = time.time()
        conn = self._connect()
        try:
            with conn:
                conn.execute(
                    """INSERT INTO xelto_users(sub, username, name, email, roles, created_at, last_login_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(sub) DO UPDATE SET username = excluded.username,
                         name = excluded.name, email = excluded.email,
                         roles = excluded.roles, last_login_at = excluded.last_login_at""",
                    (identity.sub, identity.username, identity.name, identity.email,
                     json.dumps(sorted(identity.roles)), now, now),
                )
                row = conn.execute("SELECT disabled FROM xelto_users WHERE sub = ?",
                                   (identity.sub,)).fetchone()
            return not row["disabled"]
        finally:
            conn.close()

    # ── Sessions ─────────────────────────────────────────────────────────

    def create(self, identity: Identity, refresh_token: str | None, user_agent: str = "") -> str:
        raw = secrets.token_urlsafe(32)
        now = time.time()
        conn = self._connect()
        try:
            with conn:
                conn.execute(
                    """INSERT INTO xelto_sessions(id_hash, sub, refresh_token_enc, roles, created_at,
                         last_seen_at, expires_at, roles_checked_at, user_agent)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (token_hash(raw), identity.sub, self._seal(refresh_token),
                     json.dumps(sorted(identity.roles)), now, now, now + ABSOLUTE_TIMEOUT, now,
                     user_agent[:300]),
                )
        finally:
            conn.close()
        return raw

    def get(self, raw: str) -> Session | None:
        """The live session for a cookie token; expired or disabled ones are removed."""
        if not raw:
            return None
        id_hash = token_hash(raw)
        now = time.time()
        conn = self._connect()
        try:
            row = conn.execute(
                """SELECT s.*, u.username, u.name, u.email, u.disabled
                   FROM xelto_sessions s JOIN xelto_users u ON u.sub = s.sub
                   WHERE s.id_hash = ?""",
                (id_hash,),
            ).fetchone()
            if row is None:
                return None
            if row["disabled"] or now > row["expires_at"] or now - row["last_seen_at"] > IDLE_TIMEOUT:
                with conn:
                    conn.execute("DELETE FROM xelto_sessions WHERE id_hash = ?", (id_hash,))
                return None
            if now - row["last_seen_at"] > _TOUCH_EVERY:
                with conn:
                    conn.execute("UPDATE xelto_sessions SET last_seen_at = ? WHERE id_hash = ?",
                                 (now, id_hash))
            roles = frozenset(json.loads(row["roles"]))
            identity = Identity(
                sub=row["sub"], username=row["username"], name=row["name"], email=row["email"],
                roles=roles, is_admin=ADMIN_ROLE in roles,
            )
            return Session(id_hash, identity, self._open(row["refresh_token_enc"]),
                           row["roles_checked_at"], row["last_seen_at"])
        finally:
            conn.close()

    def update_roles(self, id_hash: str, identity: Identity, refresh_token: str | None) -> None:
        conn = self._connect()
        try:
            with conn:
                conn.execute(
                    """UPDATE xelto_sessions SET roles = ?, roles_checked_at = ?,
                         refresh_token_enc = COALESCE(?, refresh_token_enc) WHERE id_hash = ?""",
                    (json.dumps(sorted(identity.roles)), time.time(), self._seal(refresh_token),
                     id_hash),
                )
        finally:
            conn.close()

    def delete(self, id_hash: str) -> None:
        conn = self._connect()
        try:
            with conn:
                conn.execute("DELETE FROM xelto_sessions WHERE id_hash = ?", (id_hash,))
        finally:
            conn.close()

    def delete_raw(self, raw: str) -> None:
        if raw:
            self.delete(token_hash(raw))

    def delete_user_sessions(self, sub: str) -> None:
        conn = self._connect()
        try:
            with conn:
                conn.execute("DELETE FROM xelto_sessions WHERE sub = ?", (sub,))
        finally:
            conn.close()
