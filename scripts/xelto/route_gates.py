#!/usr/bin/env python3
"""List the access gates upstream puts on each route, read from the router source.

For every route declared under ``backend/api/routers`` it prints the method and
path (snapshot spelling) and the gate dependencies found on the router, the
decorator, the signature or called in the body. Used by
``tests/xelto_fork/test_policy_vs_upstream_gates.py`` to keep the XELTO route
policy at least as strict as upstream.

    uv run python scripts/xelto/route_gates.py
"""
from __future__ import annotations

import ast
from pathlib import Path

ROUTERS = Path(__file__).resolve().parents[2] / "backend" / "api" / "routers"

GATES = frozenset({
    "require_admin", "require_admin_action", "require_desktop", "require_native_access",
    "require_loopback", "require_local", "require_consumer", "check_admin",
    "reject_cross_site_get",
})
# Gates that keep a route from ordinary remote consumers.
PRIVILEGED = frozenset({
    "require_admin", "require_admin_action", "check_admin", "require_desktop",
    "require_native_access", "require_loopback",
})
_VERBS = {"get", "post", "put", "delete", "patch", "websocket"}


def _gates(node: ast.AST) -> set[str]:
    found = set()
    for child in ast.walk(node):
        if not isinstance(child, ast.Call) or not isinstance(child.func, ast.Name):
            continue
        if child.func.id == "Depends" and child.args and isinstance(child.args[0], ast.Name):
            found.add(child.args[0].id)
        elif child.func.id in GATES:
            found.add(child.func.id)
    return found & GATES


def route_gates() -> dict[str, frozenset[str]]:
    """``{"METHOD /path": gates}`` for every route the routers declare."""
    result: dict[str, frozenset[str]] = {}
    for path in sorted(ROUTERS.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        prefix, router_gates = "", set()
        for node in tree.body:
            if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                    and any(isinstance(t, ast.Name) and t.id == "router" for t in node.targets)):
                for keyword in node.value.keywords:
                    if keyword.arg == "prefix" and isinstance(keyword.value, ast.Constant):
                        prefix = keyword.value.value
                router_gates = _gates(node.value)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for deco in node.decorator_list:
                if not (isinstance(deco, ast.Call) and isinstance(deco.func, ast.Attribute)
                        and deco.func.attr in _VERBS and deco.args
                        and isinstance(deco.args[0], ast.Constant)):
                    continue
                method = "WS" if deco.func.attr == "websocket" else deco.func.attr.upper()
                gates = router_gates | _gates(deco) | _gates(node.args)
                gates |= _gates(ast.Module(body=node.body, type_ignores=[]))
                result[f"{method} {prefix}{deco.args[0].value}"] = frozenset(gates)
    return result


if __name__ == "__main__":
    for route, gates in sorted(route_gates().items()):
        print(f"{route}\t{','.join(sorted(gates)) or '-'}")
