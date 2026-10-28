"""Boot the real ``main.app`` in multi-user mode against a fake Keycloak.

For the probe scripts that test_real_app.py and test_isolation_db.py run in a
subprocess: ``main`` installs the gate at import time from the environment,
which must not leak into the pytest process.
"""
import functools
import os
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_idp import CLIENT_ID, CLIENT_SECRET, ISSUER, FakeIdP  # noqa: E402

PUBLIC_URL = "https://studio.test"


def boot(users: dict[str, str]):
    """Import ``main`` in multi-user mode; returns ``(main, idp, {name: user})``."""
    os.environ.update({
        "OMNIVOICE_MULTIUSER": "1",
        "OMNIVOICE_SERVER_MODE": "1",
        "OIDC_ISSUER": ISSUER,
        "OIDC_CLIENT_ID": CLIENT_ID,
        "OIDC_CLIENT_SECRET": CLIENT_SECRET,
        "OMNIVOICE_PUBLIC_URL": PUBLIC_URL,
        "OMNIVOICE_ALLOWED_ORIGINS": PUBLIC_URL,
        "OMNIVOICE_MULTIUSER_SECRET": "x" * 40,
    })
    for name in ("OMNIVOICE_TRUSTED_NETWORKS", "OMNIVOICE_TRUST_HOST_LOOPBACK", "OMNIVOICE_API_KEY"):
        os.environ.pop(name, None)
    os.environ["OMNIVOICE_DATA_DIR"] = tempfile.mkdtemp(prefix="xelto-real-app-")
    os.environ["OMNIVOICE_MODEL"] = "test"
    os.environ["OMNIVOICE_DISABLE_FILE_LOG"] = "1"
    os.environ["OMNIVOICE_MCP_DISABLE"] = "1"
    # Build every router at import, as scripts/dump_api_routes.py does.
    os.environ["OMNIVOICE_EAGER_INIT"] = "1"

    idp = FakeIdP()
    people = {name: idp.add_user(name, role) for name, role in users.items()}

    import dotenv

    # main.py loads a .env found by walking up from backend/; keep the probe hermetic.
    dotenv.load_dotenv = lambda *args, **kwargs: False

    import xelto

    xelto.install = functools.partial(xelto.install, oidc_transport=idp.transport)

    import main

    return main, idp, people


def wait_healthy(client) -> int:
    deadline = time.monotonic() + 120
    while client.get("/health").status_code != 200 and time.monotonic() < deadline:
        time.sleep(0.5)  # deferred startup (upstream StartupGateMiddleware)
    return client.get("/health").status_code
