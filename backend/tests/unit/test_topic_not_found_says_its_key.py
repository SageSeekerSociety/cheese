"""A missing channel is refused with its catalog key, not with English text.

``raise NotFoundError("Topic not found")`` reaches the screen as the English
words, without ``error.i18n``, and with 「Topic」, a word the product no longer
uses. The catalog (``apiError``) has ``topicNotFound`` for it; every
refusal says that.

A route that answers for an id that exists but is not shown words it like a
channel that was never made, so every one of them says the same key.
"""

import ast
from pathlib import Path

from app.core.errors import NotFoundError
from app.core.sentences import say

APP = Path(__file__).resolve().parents[2] / "app"
LEGACY = "Topic not found"


def count_legacy(source: str) -> int:
    return sum(
        1
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        for arg in node.args
        if isinstance(arg, ast.Constant) and arg.value == LEGACY
    )


def counts() -> dict[str, int]:
    found = {}
    for path in sorted(APP.rglob("*.py")):
        n = count_legacy(path.read_text(encoding="utf-8"))
        if n:
            found[path.relative_to(APP).as_posix()] = n
    return found


def test_no_file_says_topic_not_found_in_english():
    found = counts()
    assert not found, (
        f'{found}: say it with say("topicNotFound") instead of the string "{LEGACY}"'
    )


def test_the_counter_sees_a_string_and_ignores_prose():
    assert count_legacy('raise NotFoundError("Topic not found")') == 1
    assert count_legacy('# "Topic not found"\nx = 1') == 0
    assert count_legacy('"""Topic not found"""\nx = 1') == 0


def test_the_refusal_goes_out_with_its_key():
    body = NotFoundError(say("topicNotFound")).to_response_body()

    assert body["error"]["i18n"]["key"] == "topicNotFound"
