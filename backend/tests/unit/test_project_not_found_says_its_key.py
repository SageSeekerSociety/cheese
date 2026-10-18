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
SAYING = [
    "domain/project/environment.py",
    "domain/agent_credential/services.py",
    "api/routes/agent_credential.py",
    "api/routes/projects_run_config.py",
]


def count_legacy(source: str) -> int:
    return sum(
        1
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        for arg in node.args
        if isinstance(arg, ast.Constant) and arg.value == LEGACY
    )


@pytest.mark.parametrize("name", SAYING)
def test_the_file_says_project_not_found_with_a_key(name):
    source = (APP / name).read_text(encoding="utf-8")

    assert count_legacy(source) == 0, f'{name}: use say("projectNotFound")'
    assert 'NotFoundError(say("projectNotFound"))' in source


def test_the_counter_sees_a_string_and_ignores_prose():
    assert count_legacy('raise NotFoundError("Project not found")') == 1
    assert count_legacy('# "Project not found"\nx = 1') == 0
    assert count_legacy('raise NotFoundError(say("projectNotFound"))') == 0


def test_the_refusal_goes_out_with_its_key():
    body = NotFoundError(say("projectNotFound")).to_response_body()

    assert body["error"]["i18n"]["key"] == "projectNotFound"
