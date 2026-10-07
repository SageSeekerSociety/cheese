"""Every line of tests/slow_tests.txt names a test that still exists.

A test that was deleted or renamed leaves its line behind, and the line then
claims a fix is still owed for a test that no longer exists. This reads each
node id's file and finds the named function (and class) in it, without
collecting the suite.
"""

import ast
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
BASELINE = BACKEND / "tests" / "slow_tests.txt"


def _entries() -> list[str]:
    return [
        line.split()[0]
        for line in BASELINE.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]


def _defines(tree: ast.AST, names: list[str]) -> bool:
    scope = tree.body
    for name in names:
        found = next(
            (
                node
                for node in scope
                if isinstance(
                    node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
                )
                and node.name == name
            ),
            None,
        )
        if found is None:
            return False
        scope = getattr(found, "body", [])
    return True


@pytest.mark.parametrize("nodeid", _entries())
def test_a_listed_slow_test_still_exists(nodeid: str) -> None:
    path, *names = nodeid.split("::")
    file = BACKEND / path
    assert file.is_file(), f"{nodeid}: {path} no longer exists, so remove its line"
    names[-1] = names[-1].split("[")[0]
    tree = ast.parse(file.read_text())
    assert _defines(tree, names), (
        f"{nodeid}: {path} no longer defines {'::'.join(names)}, so remove its line"
    )


def test_no_test_is_listed_twice() -> None:
    entries = _entries()
    assert len(entries) == len(set(entries))
