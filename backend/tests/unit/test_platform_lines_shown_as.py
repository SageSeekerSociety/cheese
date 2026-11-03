"""Which platform lines people read in the conversation, and drawn as what.

The room carries what people say, what an AI teammate hands over, and what the
platform says for a teammate that cannot speak. What the platform handles on
its own is not said (`notice_meta.shown_as`).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.domain.block.models import BlockKind
from app.domain.block.schemas import BlockOut
from app.domain.event_author import AuthorType
from app.domain.notice_meta import (
    EVENT_CARD_FILED,
    EVENT_CARD_REJECTED,
    EVENT_CI_FAILED,
    EVENT_MACHINE_PROVISIONING,
    EVENT_SKILL_PROPOSED,
    EVENT_TURN_FAILED,
    SEVERITY_INFO,
    SHOWN_CARD,
    SHOWN_FOR_AGENT,
    SHOWN_HAPPENING,
    SHOWN_NOT,
    SHOWN_PERSON,
    SHOWN_RESULT,
    WHO_CHEESE,
    WHO_HUMAN,
    notice,
    shaped,
    shown_as,
)


def test_what_the_platform_handles_itself_is_not_shown() -> None:
    preparing = notice(
        EVENT_MACHINE_PROVISIONING, severity=SEVERITY_INFO, who="platform"
    )
    assert shown_as(preparing, in_turn=True) == SHOWN_NOT
    cleaning = {"event_type": "platform_error", "code": "storage_exhausted"}
    assert shown_as(cleaning, in_turn=True) == SHOWN_NOT


def test_a_stop_that_needs_people_is_said_for_the_agent() -> None:
    failed = notice(EVENT_TURN_FAILED, severity=SEVERITY_INFO, who=WHO_HUMAN)
    assert shown_as(failed, in_turn=True) == SHOWN_FOR_AGENT
    credential = {
        "event_type": "platform_error",
        "code": "subscription_credential_expired",
    }
    assert shown_as(credential, in_turn=True) == SHOWN_FOR_AGENT


def test_a_review_step_is_said_only_when_it_waits_on_people() -> None:
    fixing = notice(EVENT_CI_FAILED, severity=SEVERITY_INFO, who=WHO_CHEESE)
    stuck = notice(EVENT_CI_FAILED, severity=SEVERITY_INFO, who=WHO_HUMAN)
    assert shown_as(fixing, in_turn=False) == SHOWN_NOT
    assert shown_as(stuck, in_turn=False) == SHOWN_FOR_AGENT
    assert shown_as({"event_type": EVENT_CARD_FILED}, in_turn=True) == SHOWN_NOT


def test_a_rejection_with_its_reason_is_that_persons_words() -> None:
    with_reason = {"event_type": EVENT_CARD_REJECTED, "detail": "登录失败的提示也要改"}
    assert shown_as(with_reason, in_turn=False) == SHOWN_PERSON
    assert (
        shown_as({"event_type": EVENT_CARD_REJECTED}, in_turn=False) == SHOWN_HAPPENING
    )


def test_what_a_teammate_hands_over_is_a_card_and_what_it_changed_a_result() -> None:
    assert shown_as({"event_type": EVENT_SKILL_PROPOSED}, in_turn=True) == SHOWN_CARD
    made = {"action": "doc", "document": {"id": "d"}, "doc_created": True}
    edited = {"action": "doc", "document": {"id": "d"}, "doc_created": False}
    assert shown_as(made, in_turn=True) == SHOWN_CARD
    assert shown_as(edited, in_turn=True) == SHOWN_RESULT
    # Nobody's turn: a person changed it, which is something that happened.
    assert shown_as(edited, in_turn=False) == SHOWN_HAPPENING
    assert shown_as({"changeset": {"files_total": 1}}, in_turn=True) == SHOWN_RESULT
    assert shown_as({"action": "decision"}, in_turn=True) == SHOWN_NOT


def test_a_line_kept_out_is_written_out_of_the_room() -> None:
    preparing = notice(
        EVENT_MACHINE_PROVISIONING, severity=SEVERITY_INFO, who="platform"
    )
    assert shaped(preparing, in_turn=True)["in_room"] is False
    failed = notice(EVENT_TURN_FAILED, severity=SEVERITY_INFO, who=WHO_HUMAN)
    assert "in_room" not in shaped(failed, in_turn=True)


def test_an_old_line_reads_through_the_same_table() -> None:
    # Written before the table: no `in_room`, no `shown_as` stored.
    old = BlockOut(
        id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        seq=1,
        kind=BlockKind.event,
        author_type=AuthorType.platform,
        author="system",
        content="PR #12 的 lint 检查没通过",
        reply_to=None,
        meta=notice(EVENT_CI_FAILED, severity=SEVERITY_INFO, who=WHO_CHEESE),
        created_at=datetime.now(UTC),
    )
    assert old.shown_as == SHOWN_NOT
    assert old.model_dump()["shown_as"] == SHOWN_NOT
    message = old.model_copy(update={"kind": BlockKind.message})
    assert message.shown_as is None
