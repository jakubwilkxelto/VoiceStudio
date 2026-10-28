"""Drive the real ``main.app`` in multi-user mode; run by test_real_app.py in a subprocess.

A subprocess because ``main`` installs the gate at import time from the
environment, and must not leak into the pytest process. Prints one JSON object.
"""
import json
import sys

from real_app_setup import PUBLIC_URL, REPO, boot, wait_healthy

main, idp, users = boot({"alice": "voicestudio-user", "admin": "voicestudio-admin"})
alice, admin = users["alice"], users["admin"]

from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, str(REPO / "scripts"))
from dump_api_routes import SNAPSHOT, route_lines  # noqa: E402


def bearer(user):
    return {"authorization": f"Bearer {idp.access_token(user)}"}


def body_ids(app) -> dict[str, list[str]]:
    """``*_id`` fields of USER routes' JSON/form bodies and query strings."""
    import re

    from xelto.policy import RouteClass, classify

    spec = app.openapi()
    schemas = spec.get("components", {}).get("schemas", {})

    def fields(schema, depth=0) -> set[str]:
        if depth > 4 or not isinstance(schema, dict):
            return set()
        if "$ref" in schema:
            return fields(schemas.get(schema["$ref"].rsplit("/", 1)[-1], {}), depth + 1)
        names = set(schema.get("properties") or {})
        for key in ("allOf", "anyOf", "oneOf"):
            for child in schema.get(key, []):
                names |= fields(child, depth + 1)
        return names

    found = {}
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            if classify(method.upper(), path)[0] is not RouteClass.USER:
                continue
            names = {p["name"] for p in op.get("parameters", []) if p.get("in") == "query"}
            for content in op.get("requestBody", {}).get("content", {}).values():
                names |= fields(content.get("schema", {}))
            ids = sorted(n for n in names if re.search(r"(^|_)ids?$", n))
            if ids:
                found[f"{method.upper()} {path}"] = ids
    return found


out = {}
out["body_ids"] = body_ids(main.app)
snapshot = {ln.strip() for ln in SNAPSHOT.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.startswith("#")}
out["routes_unchanged"] = set(route_lines(main.app)) == snapshot

with TestClient(main.app, base_url=PUBLIC_URL) as client:
    out["health"] = wait_healthy(client)
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
