"""Symmetric protection for cookies and stored refresh tokens."""
from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken


def fernet(secret: str, purpose: str) -> Fernet:
    """A Fernet key derived from the deployment secret, separated per purpose."""
    digest = hashlib.sha256(f"xelto:{purpose}:{secret}".encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def token_hash(raw: str) -> str:
    """What is stored for a session token: never the token itself."""
    return hashlib.sha256(raw.encode()).hexdigest()


__all__ = ["InvalidToken", "fernet", "token_hash"]
