"""Startup validation: multi-user mode refuses to run half-protected."""
import pytest

from xelto import install
from xelto.config import ConfigError, load_settings

from .conftest import PUBLIC_URL, build_app


def test_valid_configuration_loads(multiuser_env):
    settings = load_settings()
    assert settings.public_url == PUBLIC_URL
    assert settings.session_cookie == "__Host-xelto_sid"
    assert settings.redirect_uri == f"{PUBLIC_URL}/api/auth/oidc/callback"
    assert settings.api_audience == settings.client_id


@pytest.mark.parametrize("name", [
    "OIDC_ISSUER", "OIDC_CLIENT_ID", "OIDC_CLIENT_SECRET", "OMNIVOICE_PUBLIC_URL",
    "OMNIVOICE_MULTIUSER_SECRET",
])
def test_required_settings(multiuser_env, name):
    multiuser_env.setenv(name, "")
    with pytest.raises(ConfigError, match=name):
        load_settings()


@pytest.mark.parametrize("name, value", [
    ("OMNIVOICE_TRUSTED_NETWORKS", "10.0.0.0/8"),
    ("OMNIVOICE_TRUST_HOST_LOOPBACK", "1"),
])
def test_network_trust_shortcuts_are_refused(multiuser_env, name, value):
    multiuser_env.setenv(name, value)
    with pytest.raises(ConfigError, match=name):
        load_settings()


def test_server_mode_is_required(multiuser_env):
    multiuser_env.setenv("OMNIVOICE_SERVER_MODE", "")
    with pytest.raises(ConfigError, match="OMNIVOICE_SERVER_MODE"):
        load_settings()


def test_master_key_needs_explicit_opt_in(multiuser_env):
    multiuser_env.setenv("OMNIVOICE_API_KEY", "k" * 40)
    with pytest.raises(ConfigError, match="OMNIVOICE_API_KEY"):
        load_settings()
    multiuser_env.setenv("OMNIVOICE_MULTIUSER_ALLOW_MASTER_KEY", "1")
    assert load_settings().allow_master_key


def test_plain_http_needs_explicit_opt_in(multiuser_env):
    multiuser_env.setenv("OMNIVOICE_PUBLIC_URL", "http://studio.test")
    with pytest.raises(ConfigError, match="https"):
        load_settings()
    multiuser_env.setenv("OMNIVOICE_MULTIUSER_INSECURE_HTTP", "1")
    assert load_settings().session_cookie == "xelto_sid"


@pytest.mark.parametrize("url", ["studio.test", "https://studio.test/app", "ftp://studio.test"])
def test_public_url_must_be_an_origin(multiuser_env, url):
    multiuser_env.setenv("OMNIVOICE_PUBLIC_URL", url)
    with pytest.raises(ConfigError, match="OMNIVOICE_PUBLIC_URL"):
        load_settings()


def test_short_secret_is_refused(multiuser_env):
    multiuser_env.setenv("OMNIVOICE_MULTIUSER_SECRET", "short")
    with pytest.raises(ConfigError, match="32 characters"):
        load_settings()


def test_public_origin_must_be_allowed(multiuser_env):
    multiuser_env.setenv("OMNIVOICE_ALLOWED_ORIGINS", "https://elsewhere.test")
    with pytest.raises(ConfigError, match="OMNIVOICE_ALLOWED_ORIGINS"):
        install(build_app())
