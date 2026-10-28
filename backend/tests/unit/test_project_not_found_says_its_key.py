"""A missing project is refused with its catalog key, not with English text.

``raise NotFoundError("Project not found")`` reaches the screen as the English
words, without ``error.i18n``. The catalog (``apiError``) has
``projectNotFound`` for it, in both languages. These files already say it; the
scan keeps them saying it.
"""

import ast
from pathlib import Path

import pytest

from app.core.errors import NotFoundError
from app.core.sentences import say

APP = Path(__file__).resolve().parents[2] / "app"
LEGACY = "Project not found"
#: How many missing-project refusals each file raises, each said with the key.
SAYING = {
    "domain/project/environment.py": 3,
    "domain/project/reads.py": 1,
    "api/routes/agent_credential.py": 1,
    "api/routes/projects_run_config.py": 2,
}


def count_legacy(source: str) -> int:
    return sum(
        1
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        for arg in node.args
        if isinstance(arg, ast.Constant) and arg.value == LEGACY
    )


def count_keyed(source: str) -> int:
    return sum(
        1
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "NotFoundError"
        for arg in node.args
        if isinstance(arg, ast.Call)
        and isinstance(arg.func, ast.Name)
        and arg.func.id == "say"
        and [a.value for a in arg.args if isinstance(a, ast.Constant)]
        == ["projectNotFound"]
    )


@pytest.mark.parametrize(("name", "raised"), SAYING.items())
def test_every_refusal_in_the_file_says_project_not_found_with_a_key(name, raised):
    source = (APP / name).read_text(encoding="utf-8")

    assert count_legacy(source) == 0, f'{name}: use say("projectNotFound")'
    assert count_keyed(source) == raised, f"{name}: each refusal says the key"


def test_the_counter_sees_a_string_and_ignores_prose():
    assert count_legacy('raise NotFoundError("Project not found")') == 1
    assert count_legacy('# "Project not found"\nx = 1') == 0
    assert count_legacy('raise NotFoundError(say("projectNotFound"))') == 0


def test_the_key_counter_sees_the_right_key_only():
    assert count_keyed('raise NotFoundError(say("projectNotFound"))') == 1
    assert count_keyed('raise NotFoundError(say("topicNotFound"))') == 0
    assert count_keyed('raise NotFoundError("Project not found")') == 0


def test_the_refusal_goes_out_with_its_key():
    body = NotFoundError(say("projectNotFound")).to_response_body()

    assert body["error"]["i18n"]["key"] == "projectNotFound"
