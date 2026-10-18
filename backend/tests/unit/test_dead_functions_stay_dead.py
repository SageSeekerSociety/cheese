"""Functions whose batch or inline successor replaced them stay deleted.

Each name below was a single-item wrapper or an older entry point that nothing
called any more (``effective_shells`` replaced ``effective_shell``, the page's
``viewer_favorited`` replaced ``is_favorited``, ``ledger.record`` replaced
``charge_spent`` ...). The repo has no reference-count gate, so this table is
the gate: no definition and no reference (call, attribute, import, or string
such as ``getattr(obj, "name")``) may come back under these names.
"""

from __future__ import annotations

import ast
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
ROOTS = (BACKEND / "app", BACKEND / "tests")

DEAD = {
    "is_favorited",
    "running_notices",
    "charge_spent",
    "attachment_exists",
    "effective_shell",
    "seed_existing",
    "scope_names",
    "project_of_scope",
    "waiting_in_room",
    "speaks_for_this_rooms_turn",
}


def scan(source: str, names: set[str]) -> dict[str, list[str]]:
    """Where `names` are defined ("def") or mentioned ("ref") in `source`."""
    found: dict[str, list[str]] = {}
    for node in ast.walk(ast.parse(source)):
        hit: tuple[str, str] | None = None
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            hit = (node.name, "def")
        elif isinstance(node, ast.Name):
            hit = (node.id, "ref")
        elif isinstance(node, ast.Attribute):
            hit = (node.attr, "ref")
        elif isinstance(node, ast.alias):
            hit = (node.name.split(".")[-1], "ref")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            hit = (node.value, "ref")
        if hit and hit[0] in names:
            found.setdefault(hit[0], []).append(hit[1])
    return found


def test_the_dead_names_have_no_definition_and_no_reference():
    this_file = Path(__file__).resolve()
    left: dict[str, list[str]] = {}
    for root in ROOTS:
        for path in root.rglob("*.py"):
            if path.resolve() == this_file:
                continue
            for name, kinds in scan(path.read_text(encoding="utf-8"), DEAD).items():
                left.setdefault(name, []).append(
                    f"{path.relative_to(BACKEND)} ({', '.join(kinds)})"
                )
    assert not left, f"deleted functions are back: {left}"


def test_the_scanner_sees_a_definition_a_call_an_import_and_a_string():
    probe = (
        "from m import charge_spent\n"
        "async def waiting_in_room(): ...\n"
        "x.effective_shell()\n"
        "getattr(x, 'scope_names')\n"
    )
    found = scan(probe, DEAD)
    assert found["charge_spent"] == ["ref"]
    assert found["waiting_in_room"] == ["def"]
    assert found["effective_shell"] == ["ref"]
    assert found["scope_names"] == ["ref"]
