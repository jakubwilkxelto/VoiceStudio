"""Drive the real ``main.app`` in multi-user mode; run by test_real_app.py in a subprocess.

A subprocess because ``main`` installs the gate at import time from the
environment, and must not leak into the pytest process. Prints one JSON object.
"""
import functools
import json
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
alice = idp.add_user("alice", "voicestudio-user")
admin = idp.add_user("admin", "voicestudio-admin")

import dotenv  # noqa: E402

# main.py loads a .env found by walking up from backend/; keep the probe hermetic.
dotenv.load_dotenv = lambda *args, **kwargs: False

import xelto  # noqa: E402

xelto.install = functools.partial(xelto.install, oidc_transport=idp.transport)

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402

sys.path.insert(0, str(REPO / "scripts"))
from dump_api_routes import SNAPSHOT, route_lines  # noqa: E402


def bearer(user):
    return {"authorization": f"Bearer {idp.access_token(user)}"}


out = {}
snapshot = {ln.strip() for ln in SNAPSHOT.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.startswith("#")}
out["routes_unchanged"] = set(route_lines(main.app)) == snapshot

with TestClient(main.app, base_url=PUBLIC_URL) as client:
    deadline = time.monotonic() + 120
    while client.get("/health").status_code != 200 and time.monotonic() < deadline:
        time.sleep(0.5)  # deferred startup (upstream StartupGateMiddleware)
    out["health"] = client.get("/health").status_code
    out["anonymous_profiles"] = client.get("/profiles").status_code
    out["me"] = client.get("/api/auth/oidc/me").json()
    out["user_profiles"] = client.get("/profiles", headers=bearer(alice)).status_code
    out["user_system_info"] = client.get("/system/info", headers=bearer(alice)).status_code
    out["user_system_logs"] = client.get("/system/logs", headers=bearer(alice)).status_code
    out["user_engine_select"] = client.post("/engines/select", headers=bearer(alice),
                                            json={"engine_id": "omnivoice"}).status_code
    out["admin_system_logs"] = client.get("/system/logs", headers=bearer(admin)).status_code
    out["admin_settings_storage"] = client.get("/api/settings/storage",
                                               headers=bearer(admin)).status_code
    out["admin_network_enable"] = client.post("/system/network/enable",
                                              headers=bearer(admin)).status_code
    out["loopback_anonymous"] = TestClient(main.app, base_url=PUBLIC_URL,
                                           client=("127.0.0.1", 1)).get("/system/logs").status_code

print(json.dumps(out))
