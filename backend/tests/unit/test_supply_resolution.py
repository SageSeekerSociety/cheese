"""Which pool a project runs on (issue #243, 结论 46).

The decision lives in the backend so a project can change supply without
touching the proxy or restarting the sandbox — the previous design pinned the
pool into the sandbox's launch environment, and there is no launch environment
left to pin it into.
"""

import ast
from pathlib import Path

from app.domain.agent.supply import GATEWAY, SUBSCRIPTION, resolve_pool

_APP = Path(__file__).resolve().parents[2] / "app"
_DEFINED_IN = "domain/agent/supply.py"
_CALLED_FROM = "domain/agent_instance/configuration.py"


def test_an_explicit_setting_wins():
    assert resolve_pool({"supply": GATEWAY}) == GATEWAY
    assert resolve_pool({"supply": SUBSCRIPTION}) == SUBSCRIPTION


def test_a_project_that_says_nothing_runs_on_the_subscription():
    """A machine is launched in the subscription shape and nothing else, so a
    project that has never chosen runs on the pool that shape reaches first."""
    assert resolve_pool(None) == SUBSCRIPTION
    assert resolve_pool({}) == SUBSCRIPTION
    assert resolve_pool({"other": "x"}) == SUBSCRIPTION


def test_an_unrecognised_value_falls_back_rather_than_failing():
    """A typo in a settings blob must not take a project offline."""
    for junk in ("gatway", "", None, 5, ["gateway"]):
        assert resolve_pool({"supply": junk}) == SUBSCRIPTION


# —— 守卫：「这一轮走哪条供给」全仓只有一处回答 ————————————————————


def _references(name: str) -> dict[str, list[str]]:
    """Every module under ``app`` that defines or names ``name``, and how."""
    found: dict[str, list[str]] = {}
    for path in sorted(_APP.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        kinds = []
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                if node.name == name:
                    kinds.append("defines")
            elif isinstance(node, ast.ImportFrom):
                if any(a.name == name for a in node.names):
                    kinds.append("imports")
            elif isinstance(node, ast.Name) and node.id == name:
                kinds.append("names")
            elif isinstance(node, ast.Attribute) and node.attr == name:
                kinds.append("names")
        if kinds:
            found[path.relative_to(_APP).as_posix()] = kinds
    return found


def test_resolve_pool_is_the_only_answer_to_which_supply_a_turn_takes():
    """P34 的验收守卫，写成它立得住的那个形状。

    The acceptance asks that "which supply does this turn take" be answered in
    exactly one place. Written literally — the two pool constants take part in
    no `if` outside `supply.py` — it is red on any correct implementation, and
    was red before this change too: the constants are also how the catalogue
    records which pool serves a model, how the proxy carries out the answer it
    was given, and how a short alias is translated to an upstream name. Those
    act on the answer; none of them produces one.

    What does hold, and what this guards, is the producer: `resolve_pool` is
    defined once and reached from one place. A second definition, or a second
    module reaching for it, is the shape the acceptance was written against —
    two answers to one question, with nothing saying which wins (I4a)."""
    where = _references("resolve_pool")
    assert set(where) == {_DEFINED_IN, _CALLED_FROM}, where
    assert where[_DEFINED_IN].count("defines") == 1, where
    assert "defines" not in where[_CALLED_FROM], where
