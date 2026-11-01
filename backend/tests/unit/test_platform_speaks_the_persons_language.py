"""The platform's own words to the model follow the person being answered.

The model is held to that rule — the line it writes beside every step is shown
to the room verbatim — and the platform holds to it the same way: a person
reading Chinese is spoken to in Chinese, a person reading English in English.
Which one it is comes from `user.language`, that person's own pick
(`app.core.sentences.LOCALES`); None until they pick one, and then the stored
Chinese, like every other sentence the platform keeps.

A room writing Chinese flipped to English on the turn that began right after its
machine came back from an outage; the only English the platform had put in front
of that session was these — the publish contract at the top of every turn and
the reminders a stalled turn wakes up to.
"""

import re

import pytest

from app.domain.agent.harness.notices import blocked_report_reminder
from app.domain.agent.harness.prompt import (
    PLATFORM_NOTICE,
    publication_prompt,
    silence_reminder,
    stale_checklist_reminder,
    still_working_reminder,
)

# Tool names and the statuses it passes them are English and are read literally:
# the model calls one by name and types the other into the argument. Everything
# else the platform says is written for the person to read.
TOOL_NAMES = {
    "chat_send",
    "todo_write",
    "cheese_task",
    "action",
    "state",
    "completed",
    "in_progress",
    "pending",
    "working",
    "waiting",
    "blocked",
    "note",
}


def _english_words(text: str) -> list[str]:
    return [
        word
        for word in re.findall(r"[A-Za-z][A-Za-z_]{2,}", text)
        if word not in TOOL_NAMES
    ]


def _chinese_characters(text: str) -> list[str]:
    return [character for character in text if "一" <= character <= "鿿"]


def _said(text: str) -> str:
    """What the platform says, minus the marker that delimits it.

    `PLATFORM_NOTICE` is a protocol token, not prose: the harness prompt teaches
    the model to recognize exactly this literal, and human text is neutralized
    against the same one (`strip_platform_notice`). It is one fixed string in
    every room, which is why the assertions below look past it.
    """
    return text.replace(PLATFORM_NOTICE, "")


@pytest.mark.parametrize("language", [None, "zh-CN"])
def test_a_person_reading_chinese_is_spoken_to_in_chinese(
    language: str | None,
) -> None:
    said = (
        _said(publication_prompt("用户问了一件事", language))
        + _said(silence_reminder(10, language))
        + _said(still_working_reminder(10, language))
        + _said(stale_checklist_reminder(10, language))
        + _said(blocked_report_reminder(language))
    )
    assert _chinese_characters(said)
    assert _english_words(said) == []


def test_a_person_reading_english_is_spoken_to_in_english() -> None:
    contract = _said(publication_prompt("Somebody asked something", "en"))
    reminder = _said(silence_reminder(10, "en")) + _said(
        still_working_reminder(10, "en")
    )
    checklist = _said(stale_checklist_reminder(10, "en"))
    blocked = _said(blocked_report_reminder("en"))
    # The tool name survives the translation: it is how the model calls it.
    assert "chat_send" in contract
    assert "chat_send" in reminder
    assert "todo_write" in checklist
    assert "cheese_task(action=state)" in blocked
    assert _chinese_characters(contract + reminder + checklist + blocked) == []
