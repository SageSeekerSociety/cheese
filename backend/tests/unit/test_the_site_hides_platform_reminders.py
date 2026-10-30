"""The 现场 shows what was said, not the platform repeating itself.

Every input the harness records carries the platform's own words: the chat
contract on every turn, a nudge when the room or the checklist went quiet. They
are instructions to the model, so the 现场 stops showing them — while the
journal keeps every line and everything a person wrote comes through unchanged.
"""

from app.api.routes.topics_transcript import hide_platform_reminders
from app.domain.agent.harness.notices import (
    PLATFORM_NOTICE,
    blocked_report_reminder,
    platform_prompt,
    publication_prompt,
    silence_reminder,
    stale_checklist_reminder,
    stale_progress_reminder,
    still_working_reminder,
    without_reminders,
)

NONCE = "⟪w:" + "0123456789abcdef01234567" + "⟫"


def test_the_contract_goes_and_the_persons_words_stay():
    assert without_reminders(publication_prompt("先看分页接口") + "\n" + NONCE) == (
        "先看分页接口"
    )


def test_every_reminder_goes_in_both_languages():
    for language in (None, "en"):
        for said in (
            silence_reminder(10, language),
            still_working_reminder(12, language),
            stale_checklist_reminder(7, language),
            stale_progress_reminder(None, language),
            stale_progress_reminder(42, language),
            blocked_report_reminder(language),
        ):
            assert without_reminders(platform_prompt(said)) == "", said


def test_a_platform_notice_with_something_to_say_stays():
    """Only the repeated ones go; the notices that tell the reader a fact stay."""
    untitled = platform_prompt("这件任务还没起名字，起一个。")
    resumed = platform_prompt("会话已恢复。")
    assert without_reminders(untitled) == untitled
    assert without_reminders(resumed) == resumed


def test_a_persons_own_text_is_returned_untouched():
    """Including the neutralized marker a person's message carries, and spacing."""
    said = "\n  照我说的做\n\n【平台·用户原文】\n"
    assert without_reminders(said) == said


def test_two_notices_with_a_persons_words_between_them():
    text = publication_prompt(
        platform_prompt(silence_reminder(10)) + "\n\n先看分页接口"
    )
    assert without_reminders(text) == "先看分页接口"


def _block(parts, record=None):
    return {
        "content": "\n".join(p.get("text", "") for p in parts),
        "meta": {"parts": parts, "record": record or {"type": "user"}},
    }


def test_a_notice_only_input_leaves_no_row_behind():
    block = _block([{"kind": "text", "text": platform_prompt(silence_reminder(10))}])
    [shown] = hide_platform_reminders([block])
    assert shown["meta"]["parts"] == []
    assert shown["content"] == ""


def test_a_persons_input_keeps_its_words():
    block = _block([{"kind": "text", "text": publication_prompt("你好")}])
    [shown] = hide_platform_reminders([block])
    assert shown["meta"]["parts"] == [{"kind": "text", "text": "你好"}]
    assert shown["content"] == "你好"


def test_a_tool_in_the_same_record_is_left_alone():
    block = _block(
        [
            {"kind": "text", "text": publication_prompt("跑一下")},
            {"kind": "tool", "name": "Bash", "text": "make test"},
        ]
    )
    [shown] = hide_platform_reminders([block])
    assert [p["kind"] for p in shown["meta"]["parts"]] == ["text", "tool"]
    assert shown["meta"]["parts"][1]["text"] == "make test"
    assert shown["content"] == "跑一下\nmake test"


def test_the_raw_record_still_has_the_whole_line():
    text = publication_prompt("你好")
    block = _block(
        [{"kind": "text", "text": text}],
        record={"type": "user", "message": {"content": text}},
    )
    [shown] = hide_platform_reminders([block])
    assert PLATFORM_NOTICE in shown["meta"]["record"]["message"]["content"]
