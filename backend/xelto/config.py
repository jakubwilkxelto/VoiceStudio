"""Environment switches for multi-user mode, read and validated in one place."""
from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlsplit

_TRUE = {"1", "true", "yes", "on"}

# Synthetic peers the gate presents to upstream code in place of the real
# client (RFC 5737 TEST-NET-1, never a real peer). Users come from a network
# multi-user mode marks as trusted, so upstream's consumption checks treat
# them like LAN clients; admins come from outside it and carry an admin
# session principal instead.
USER_PEER = "192.0.2.10"
ADMIN_PEER = "192.0.2.20"
USER_PEER_NETWORK = f"{USER_PEER}/32"

FILES_MODES = ("lenient", "strict")

ADMIN_ROLE = "voicestudio-admin"
USER_ROLE = "voicestudio-user"


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _TRUE


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def multiuser_enabled() -> bool:
    """``OMNIVOICE_MULTIUSER=1`` turns the mode on; anything else keeps upstream behaviour."""
    return _flag("OMNIVOICE_MULTIUSER")


class ConfigError(RuntimeError):
    """Multi-user mode is on but cannot run safely; startup must stop."""


@dataclass(frozen=True)
class Settings:
    issuer: str  # as it appears in tokens ("iss")
    discovery_url: str  # where the backend fetches the OIDC metadata
    client_id: str
    client_secret: str
    api_audience: str
    public_url: str  # the browser-facing origin of VoiceStudio, no trailing slash
    secret: str  # signs/encrypts cookies and refresh tokens
    allow_master_key: bool
    insecure_http: bool
    bootstrap_owner: str
    files_mode: str = "strict"  # files nothing accounts for: "strict" refuses, "lenient" serves

    @property
    def secure_cookies(self) -> bool:
        return self.public_url.startswith("https://")

    @property
    def session_cookie(self) -> str:
        return "__Host-xelto_sid" if self.secure_cookies else "xelto_sid"

    @property
    def tx_cookie(self) -> str:
        return "__Host-xelto_tx" if self.secure_cookies else "xelto_tx"

    @property
    def redirect_uri(self) -> str:
        return f"{self.public_url}/api/auth/oidc/callback"

    @property
    def public_host(self) -> str:
        return urlsplit(self.public_url).hostname or ""


_REQUIRED = ("OIDC_ISSUER", "OIDC_CLIENT_ID", "OIDC_CLIENT_SECRET", "OMNIVOICE_PUBLIC_URL",
             "OMNIVOICE_MULTIUSER_SECRET")
# Upstream trust shortcuts that would let a peer skip sign-in.
_FORBIDDEN = ("OMNIVOICE_TRUSTED_NETWORKS", "OMNIVOICE_TRUST_HOST_LOOPBACK")


def load_settings() -> Settings:
    """Read and validate the multi-user configuration; raise :class:`ConfigError` if unsafe."""
    problems = [f"{name} is not set" for name in _REQUIRED if not _env(name)]
    problems += [
        f"{name} must be unset: sign-in replaces network trust in multi-user mode"
        for name in _FORBIDDEN
        # USER_PEER_NETWORK is the value install() itself sets.
        if _env(name) and _env(name) != USER_PEER_NETWORK
    ]
    if not _flag("OMNIVOICE_SERVER_MODE"):
        problems.append("OMNIVOICE_SERVER_MODE=1 is required (multi-user mode is a server deployment)")
    allow_master = _flag("OMNIVOICE_MULTIUSER_ALLOW_MASTER_KEY")
    if _env("OMNIVOICE_API_KEY") and not allow_master:
        problems.append(
            "OMNIVOICE_API_KEY is set but ignored in multi-user mode; unset it, or set "
            "OMNIVOICE_MULTIUSER_ALLOW_MASTER_KEY=1 for emergency admin access"
        )

    public_url = _env("OMNIVOICE_PUBLIC_URL").rstrip("/")
    insecure = _flag("OMNIVOICE_MULTIUSER_INSECURE_HTTP")
    parts = urlsplit(public_url)
    if public_url and (parts.scheme not in ("https", "http") or not parts.hostname
                       or parts.path not in ("", "/") or parts.query):
        problems.append("OMNIVOICE_PUBLIC_URL must be an origin such as https://studio.example.com")
    elif public_url.startswith("http://") and not insecure:
        problems.append(
            "OMNIVOICE_PUBLIC_URL must use https (set OMNIVOICE_MULTIUSER_INSECURE_HTTP=1 "
            "only for local testing)"
        )
    files_mode = _env("OMNIVOICE_MULTIUSER_FILES").lower() or "strict"
    if files_mode not in FILES_MODES:
        problems.append("OMNIVOICE_MULTIUSER_FILES must be one of: " + ", ".join(FILES_MODES))
    secret = _env("OMNIVOICE_MULTIUSER_SECRET")
    if secret and len(secret) < 32:
        problems.append("OMNIVOICE_MULTIUSER_SECRET must be at least 32 characters")

    if problems:
        raise ConfigError("multi-user mode cannot start:\n  - " + "\n  - ".join(problems))

    issuer = _env("OIDC_ISSUER").rstrip("/")
    client_id = _env("OIDC_CLIENT_ID")
    return Settings(
        issuer=issuer,
        discovery_url=_env("OIDC_DISCOVERY_URL") or f"{issuer}/.well-known/openid-configuration",
        client_id=client_id,
        client_secret=_env("OIDC_CLIENT_SECRET"),
        api_audience=_env("OIDC_API_AUDIENCE") or client_id,
        public_url=public_url,
        secret=secret,
        allow_master_key=allow_master,
        insecure_http=insecure,
        bootstrap_owner=_env("OMNIVOICE_MULTIUSER_BOOTSTRAP_OWNER"),
        files_mode=files_mode,
    )
