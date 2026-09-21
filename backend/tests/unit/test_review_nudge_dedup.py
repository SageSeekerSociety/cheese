"""PR 回流去重（nudge ledger）：同一个失败的重复轮询不重复叫芝士。"""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.domain.delivery.addressing import NOBODY
from app.domain.review.notes import NoteCode
from app.domain.review.services import AcceptService

#: 名册在这些用例里是假的（session 是个 MagicMock），而这几条说的是去重和文案，不
#: 是「谁坐在这个房间里」。席位照样得答得出来，否则寻址连问都问不了。
_a_seat = patch(
    "app.domain.review.services.TopicMemberService.addressable_agent_handle",
    new=AsyncMock(return_value="cheese-seat"),
)


def _service_and_card():
    card = SimpleNamespace(
        id=uuid.uuid4(),
        topic_id=uuid.uuid4(),
        task_id=uuid.uuid4(),
        note="",
        note_code=None,
        nudge_state={},
        pr_number=7,
        pr_url="https://github.com/acme/widgets/pull/7",
        pr_head_sha="abc123",
    )
    topic = SimpleNamespace(id=card.topic_id, project_id=uuid.uuid4())
    service = AcceptService(MagicMock())
    return service, card, topic


@pytest.mark.anyio
async def test_nudge_dedup_still_suppresses_a_repeat_ci_failure():
    """A second poll tick on the same failing commit must not re-notify 芝士."""
    service, card, topic = _service_and_card()
    runner = MagicMock()

    async def _tick() -> None:
        nudge = service._ci_nudge(
            card=card, tail="pytest failed", stage="CI", owner="acme", repo="widgets"
        )
        with (
            patch(
                "app.domain.review.services.TaskService.get",
                new=AsyncMock(return_value=SimpleNamespace(status="open")),
            ),
            _a_seat,
        ):
            await service._dispatch_nudges(
                card=card,
                topic=topic,
                pending=[nudge] if nudge is not None else [],
                chat_service=MagicMock(),
                runner=runner,
            )

    await _tick()
    first_note = card.note
    assert card.note_code is NoteCode.checks_failed
    assert runner.submit.call_count == 1

    await _tick()

    assert card.note == first_note
    assert runner.submit.call_count == 1


@pytest.mark.anyio
async def test_closed_task_keeps_failure_notice_without_invalid_repair_command():
    service, card, topic = _service_and_card()
    runner = MagicMock()
    nudge = service._ci_nudge(
        card=card, tail="cancelled", stage="CI", owner="acme", repo="widgets"
    )
    assert nudge is not None
    with patch(
        "app.domain.review.services.TaskService.get",
        new=AsyncMock(return_value=SimpleNamespace(status="closed")),
    ):
        await service._dispatch_nudges(
            card=card,
            topic=topic,
            pending=[nudge],
            chat_service=MagicMock(),
            runner=runner,
        )
    call = runner.submit.call_args.kwargs
    # 活已经关了，就谁也没点到 —— 房间里照样看得见这一行，只是不会有人被叫起来。
    assert call["addressed"] == NOBODY
    assert "新任务" in call["content"]
    assert "cheese worktree" not in call["content"]
    assert "cancelled" in call["nudge_meta"]["detail"]


@pytest.mark.anyio
async def test_merge_refusal_does_not_claim_checks_are_green():
    service, card, topic = _service_and_card()
    runner = MagicMock()
    with (
        patch(
            "app.domain.review.services.TaskService.get",
            new=AsyncMock(return_value=SimpleNamespace(status="open")),
        ),
        _a_seat,
    ):
        await service._note_merge_blocked(
            card=card,
            topic=topic,
            reason="merge method disabled",
            chat_service=MagicMock(),
            runner=runner,
        )
    call = runner.submit.call_args.kwargs
    assert "全绿" not in card.note + call["content"] + call["nudge_event"]
    assert str(card.task_id) in call["content"]
    assert "merge method disabled" in call["content"]
