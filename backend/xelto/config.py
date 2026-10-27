"""Environment switches for multi-user mode, read in one place."""
from __future__ import annotations

import os

_TRUE = {"1", "true", "yes", "on"}


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _TRUE


def multiuser_enabled() -> bool:
    """``OMNIVOICE_MULTIUSER=1`` turns the mode on; anything else keeps upstream behaviour."""
    return _flag("OMNIVOICE_MULTIUSER")
