"""Merge guard: SQL on per-user tables that the scoping cannot serve as-is.

On a scoped connection the per-user tables are TEMP views for reads, and every
write is retargeted to ``main.<t>`` by ``xelto.db_scope.rewrite``, which only
recognises a write at the start of a statement. A write it would miss (after
a ``WITH`` clause or a comment) hits the read-only view and fails. The rest
needs a look: ``RETURNING`` and ``lastrowid`` (correct on the base table, but
worth checking), an UPSERT (its DO UPDATE runs on the base table),
``CREATE VIRTUAL TABLE`` (cannot be shadowed) and ``executescript`` (runs on a
system connection). A new upstream use of any of these fails here until
someone checks it and adds it to ``ALLOWED`` with a reason.
"""
import ast
import re
from pathlib import Path

from xelto.db_scope import rewrite
from xelto.db_views import USER_TABLES

BACKEND = Path(__file__).resolve().parents[2] / "backend"
_TABLE = re.compile(r"\b(" + "|".join(USER_TABLES) + r")\b")
_RISKY = {
    "returning": re.compile(r"\bRETURNING\b", re.IGNORECASE),
    "upsert": re.compile(r"\bON\s+CONFLICT\b.*\bDO\s+UPDATE\b", re.IGNORECASE | re.DOTALL),
    "virtual": re.compile(r"\bCREATE\s+VIRTUAL\s+TABLE\b", re.IGNORECASE),
}
_INSERT = re.compile(r"\bINSERT\b", re.IGNORECASE)
_WRITE = re.compile(
    r"\b(?:INSERT(?:\s+OR\s+\w+)?\s+INTO|REPLACE\s+INTO|UPDATE(?:\s+OR\s+\w+)?|DELETE\s+FROM)\s+("
    + "|".join(USER_TABLES) + r")\b(?!\s*\.)",
    re.IGNORECASE,
)

# (file, function, kind): why it is safe.
ALLOWED = {
    # xelto.db_scope.rewrite sends it to main.dub_history, guarded by the
    # base-table triggers (test_db_scope.test_upstream_upsert_is_rewritten_and_still_guarded).
    ("services/dub_pipeline.py", "_persist_job", "upsert"): "rewritten to the base table",
    # ensure_schema/init_db: schema DDL only; db_scope runs it on a system connection.
    ("core/db.py", "ensure_schema", "executescript"): "schema DDL",
    ("core/db.py", "init_db", "executescript"): "schema DDL",
    ("core/db.py", "_reconcile_additive_columns", "executescript"): "in-memory canonical schema",
}


def _strings(node: ast.AST) -> list[str]:
    out = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            out.append(sub.value)
        elif isinstance(sub, ast.JoinedStr):
            out.append("".join(v.value for v in sub.values
                               if isinstance(v, ast.Constant) and isinstance(v.value, str)))
    return out


def findings() -> set[tuple[str, str, str]]:
    found = set()
    for path in BACKEND.rglob("*.py"):
        rel = path.relative_to(BACKEND).as_posix()
        if rel.startswith(("xelto/", "tests/")) or "/tests/" in rel:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        for func in ast.walk(tree):
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            # Nested functions are scanned on their own too; report the innermost.
            body = ast.Module(body=[n for n in func.body
                                    if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))],
                              type_ignores=[])
            sql = [s for s in _strings(body) if _TABLE.search(s)]
            for kind, pattern in _RISKY.items():
                if any(pattern.search(s) for s in sql):
                    found.add((rel, func.name, kind))
            if any(_WRITE.search(s) and rewrite(s) == s for s in sql):
                found.add((rel, func.name, "unretargeted-write"))
            attrs = {n.attr for n in ast.walk(body) if isinstance(n, ast.Attribute)}
            if "lastrowid" in attrs and any(_INSERT.search(s) for s in sql):
                found.add((rel, func.name, "lastrowid"))
            if "executescript" in attrs:
                found.add((rel, func.name, "executescript"))
    return found


def test_scan_sees_the_known_upsert():
    assert ("services/dub_pipeline.py", "_persist_job", "upsert") in findings()


def test_no_unreviewed_sql_on_user_tables():
    new = sorted(findings() - set(ALLOWED))
    assert not new, (
        "SQL on per-user tables that the scoping views cannot serve as-is; check each "
        f"against xelto.db_scope and add it to ALLOWED with the reason: {new}"
    )


def test_allowlist_has_no_stale_entries():
    stale = sorted(set(ALLOWED) - findings())
    assert not stale, f"ALLOWED entries no longer found upstream; remove them: {stale}"


def test_the_scan_reports_a_write_the_rewrite_misses():
    sql = "WITH x AS (SELECT 1) UPDATE voice_profiles SET name = 'n'"
    assert _WRITE.search(sql) and rewrite(sql) == sql
