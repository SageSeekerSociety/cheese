"""A missing channel is refused with its catalog key, not with English text.

``raise NotFoundError("Topic not found")`` reaches the screen as the English
words, without ``error.i18n``, and with 「Topic」, a word the product no longer
uses. The catalog (``apiError``) has ``topicNotFound`` for it; every new
refusal says that.

The files below still carry the old line: they only come down. A route that
answers for an id that exists but is not shown must word it like a channel that
was never made (the other 404s in this list), so those move together, not one
by one.
"""

import ast
from pathlib import Path

from app.core.errors import NotFoundError
from app.core.sentences import say

APP = Path(__file__).resolve().parents[2] / "app"
LEGACY = "Topic not found"

# Occurrences still left, per file relative to ``backend/app``. A count may fall
# (delete the entry at zero); it may not rise, and no other file may appear.
BASELINE = {
    # Says what a channel that was never made says, so a private channel is not
    # given away by a different answer (test_private_channels.py).
    "api/auth/resolver.py": 1,
    # The same goes for the routes that answer for an id that exists but is not
    # a channel here, and for tags, whose text says 话题 on purpose.
    "api/routes/alerts.py": 1,
    "api/routes/project_channels.py": 1,
    "api/routes/topics_channel.py": 1,
    "api/routes/topics_pins.py": 1,
    "domain/tag/services.py": 1,
    "api/routes/execution.py": 1,
    "api/routes/project_context.py": 1,
    "api/routes/project_skills.py": 1,
    "api/routes/routines.py": 2,
    "api/routes/topics.py": 1,
    "domain/questions/services.py": 1,
    "domain/review/services/cards.py": 1,
    "domain/room_task/place.py": 1,
    "domain/topic/services.py": 4,
    "domain/topic_membership/services/__init__.py": 10,
}


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


def test_no_file_says_topic_not_found_in_english_beyond_its_baseline():
    over = {
        name: (n, BASELINE.get(name, 0))
        for name, n in counts().items()
        if n > BASELINE.get(name, 0)
    }
    assert not over, (
        f'{over} (found, allowed): say it with say("topicNotFound") instead of '
        f'the string "{LEGACY}"'
    )


def test_the_counter_sees_a_string_and_ignores_prose():
    assert count_legacy('raise NotFoundError("Topic not found")') == 1
    assert count_legacy('# "Topic not found"\nx = 1') == 0
    assert count_legacy('"""Topic not found"""\nx = 1') == 0


def test_the_refusal_goes_out_with_its_key():
    body = NotFoundError(say("topicNotFound")).to_response_body()

    assert body["error"]["i18n"]["key"] == "topicNotFound"
