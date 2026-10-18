"""The team domain keeps exactly one ``_use_fts``; copies drift apart."""

import ast
from pathlib import Path

DOMAIN = Path(__file__).resolve().parents[2] / "app" / "domain"

# Defined once in team/repositories.py; recruitment_repositories imports it
# (the reverse direction would be an import cycle).
TEAM_HOME = "team/repositories.py"

# Other domains keep their own copy on purpose and are out of scope here:
# questions/queries.py has a separate FTS tokenizer policy; tag/repositories.py
# is a different domain.
ALLOWED_ELSEWHERE = {"questions/queries.py", "tag/repositories.py"}


def _definitions(root: Path) -> list[str]:
    found: list[str] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(
            isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
            and n.name == "_use_fts"
            for n in ast.walk(tree)
        ):
            found.append(path.relative_to(root).as_posix())
    return found


def test_team_domain_defines_use_fts_once() -> None:
    team = [p for p in _definitions(DOMAIN) if p.startswith("team/")]
    assert team == [TEAM_HOME], (
        f"team domain must define _use_fts only in {TEAM_HOME}, found {team}"
    )


def test_other_copies_are_whitelisted() -> None:
    others = {p for p in _definitions(DOMAIN) if not p.startswith("team/")}
    assert others <= ALLOWED_ELSEWHERE, (
        f"unlisted _use_fts copies: {sorted(others - ALLOWED_ELSEWHERE)}"
    )
