"""XELTO fork: opt-in multi-user mode (Keycloak accounts + per-user data).

Everything for the feature lives in this package; upstream files carry only
thin ``XELTO:`` hooks (see ``CLAUDE.local.md``). With ``OMNIVOICE_MULTIUSER``
unset, :func:`install` is a no-op and the app behaves exactly like upstream.
"""
from xelto.install import install

__all__ = ["install"]
