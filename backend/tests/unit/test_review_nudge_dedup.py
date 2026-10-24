"""PR 回流去重（nudge ledger）：同一个失败的重复轮询不重复叫芝士。"""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.domain.review import notes, pr_signals
from app.domain.review.notes import NoteCode
from app.domain.review.services import AcceptService


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
    session = MagicMock()
    session.scalar = AsyncMock(side_effect=lambda *_: card.nudge_state)
    service = AcceptService(session)
    service._record_task_nudge = AsyncMock()
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
    assert service._record_task_nudge.await_count == 1
    runner.submit.assert_not_called()

    await _tick()

    assert card.note == first_note
    assert service._record_task_nudge.await_count == 1
    runner.submit.assert_not_called()


@pytest.mark.anyio
async def test_a_cleared_card_face_comes_back_while_the_reason_still_stands():
    """CI 转绿时清掉的那句理由，只要挡着的事还在，卡面就要自己长回来。

    真实形状（独立复核 #3509 报的）：一张 PR 标题和卡片不一致的卡，同时 CI 红。
    第一轮两条都新，卡面写 CI 那句（优先级 3 > 2），标题那条的签名照样进了账本。
    此后 CI 转绿，`polling` 的 `notes.clear(card)` 把卡面清空；标题仍不一致，可它
    的签名已经在账本里、`fresh` 为空 —— 旧写法到这里直接 return，卡面就空着，而
    两条合并路径还挡着。卡面于是看起来是一张没人要处理的普通待办，正是这次改动
    要消灭的那种「停住了却看不出来」。
    """
    service, card, topic = _service_and_card()
    runner = MagicMock()
    title = pr_signals.PendingNudge(
        kind=pr_signals.NudgeKind.pr_title,
        signature="pr-title:the-card-line",
        event="prTitleDisagrees",
        content="PR 标题和卡片不是同一行，平台停下不合。",
        note="PR #7 的标题和卡片不一致，合并已暂停",
        note_code=NoteCode.pr_title_disagrees,
        event_type="merge_refused",
    )
    # 上一轮已经叫过这一条：签名在账本里，这一轮的 fresh 是空的。
    ledger = pr_signals.NudgeLedger()
    ledger.record(title.kind, title.signature)
    card.nudge_state = ledger.dump()
    # CI 转绿那一轮把卡面清掉了。
    notes.clear(card)

    await service._dispatch_nudges(
        card=card,
        topic=topic,
        pending=[title],
        chat_service=MagicMock(),
        runner=runner,
    )

    assert card.note_code is NoteCode.pr_title_disagrees
    assert card.note.strip()
    # 理由长回卡面，不等于再叫一次人。
    assert service._record_task_nudge.await_count == 0
    runner.submit.assert_not_called()


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
    call = service._record_task_nudge.call_args.kwargs
    # 活已经关了，就谁也没点到 —— 房间里照样看得见这一行，只是不会有人被叫起来。
    runner.submit.assert_not_called()
    assert "新任务" in call["content"]
    assert "cheese worktree" not in call["content"]
    assert "cancelled" in call["meta"]["detail"]


@pytest.mark.anyio
async def test_leaving_the_merge_queue_only_wakes_a_task_that_is_still_open():
    """出队这条的老账新账分开：任务还开着才说那份能照做的指令。

    `_note_left_merge_queue` 走的是 `_note_merge_stop_and_wake`，那里按任务
    开着没有挑一句 `action`。关了的活照做不了，写的是「请由新任务承接」，
    房间那行文字照旧看得见。
    """
    service, card, topic = _service_and_card()
    closed = SimpleNamespace(status="closed")
    with patch(
        "app.domain.review.services.TaskService.get", new=AsyncMock(return_value=closed)
    ):
        await service._note_left_merge_queue(card=card, topic=topic)
    call = service._record_task_nudge.call_args.kwargs
    assert "新任务" in call["content"]
    assert "cheese worktree" not in call["content"]

    # 还开着的活照旧点名，话也照旧是那份能照做的。
    service, card, topic = _service_and_card()
    opened = SimpleNamespace(status="open")
    with patch(
        "app.domain.review.services.TaskService.get", new=AsyncMock(return_value=opened)
    ):
        await service._note_left_merge_queue(card=card, topic=topic)
    call = service._record_task_nudge.call_args.kwargs
    assert "cheese worktree" in call["content"]
    assert call["content"].endswith("需要人重新采纳。")


@pytest.mark.anyio
async def test_merge_refusal_does_not_claim_checks_are_green():
    service, card, topic = _service_and_card()
    runner = MagicMock()
    with (
        patch(
            "app.domain.review.services.TaskService.get",
            new=AsyncMock(return_value=SimpleNamespace(status="open")),
        ),
    ):
        await service._note_merge_blocked(
            card=card,
            topic=topic,
            reason="merge method disabled",
            chat_service=MagicMock(),
            runner=runner,
        )
    call = service._record_task_nudge.call_args.kwargs
    assert "全绿" not in card.note + call["content"] + call["headline"]
    assert str(card.task_id) in call["content"]
    assert "merge method disabled" in call["content"]


@pytest.mark.anyio
async def test_leaving_the_merge_queue_wakes_cheese_and_says_what_to_do():
    """出队是「已采纳的 PR 停住了」，和 GitHub 拒绝合并一样要叫人，而且只叫一次。

    卡仍写着「已采纳」，没人被叫醒这条活就没人管（真实事故 PR #3496：12:26 出队，
    26 分钟后巡检才发现）。平台不自动重新入队，修好后要人重新采纳。
    """
    service, card, topic = _service_and_card()
    # 这条路径的去重靠卡上的 note 文本，`_service_and_card` 的 scalar 桩返回的是
    # nudge ledger，所以这里改读 card.note。
    service._session.scalar = AsyncMock(side_effect=lambda *_: card.note)
    with patch(
        "app.domain.review.services.TaskService.get",
        new=AsyncMock(return_value=SimpleNamespace(status="open")),
    ):
        await service._note_left_merge_queue(card=card, topic=topic)
        assert service._record_task_nudge.await_count == 1
        first_note = card.note
        # 轮询会再走一遍同一张卡：note 没变就不该再叫一次。
        await service._note_left_merge_queue(card=card, topic=topic)

    assert card.note_code is NoteCode.merge_refused
    assert card.note == first_note
    assert service._record_task_nudge.await_count == 1
    call = service._record_task_nudge.call_args.kwargs
    assert call["meta"]["event_type"] == "merge_refused"
    assert call["meta"]["severity"] == "error"
    assert call["meta"]["who"] == "cheese"
    assert "合并队列" in call["headline"]
    assert "cheese push-fix" in call["content"]
    assert "重新入队" in call["content"]


async def _dispatch_passed(service, card, topic, *, task_status: str) -> None:
    from app.domain.review.nudges import _ci_passed_nudge

    nudge = _ci_passed_nudge(card=card)
    assert nudge is not None
    with patch(
        "app.domain.review.services.TaskService.get",
        new=AsyncMock(return_value=SimpleNamespace(status=task_status)),
    ):
        await service._dispatch_nudges(
            card=card,
            topic=topic,
            pending=[nudge],
            chat_service=MagicMock(),
            runner=MagicMock(),
        )


@pytest.mark.anyio
async def test_green_checks_reach_the_agent_once_per_head_without_touching_the_card():
    """检查全绿回去找做活的芝士：同一个 head 只说一次，是 info 不是 error，不带
    进工作区的命令，也不写卡面（不要谁动手）。换了 head 绿了再说一次。"""
    service, card, topic = _service_and_card()

    await _dispatch_passed(service, card, topic, task_status="open")
    await _dispatch_passed(service, card, topic, task_status="open")

    assert service._record_task_nudge.await_count == 1
    call = service._record_task_nudge.call_args.kwargs
    assert call["meta"]["event_type"] == "ci_passed"
    assert call["meta"]["severity"] == "info"
    assert "cheese worktree" not in call["content"]
    assert "#7" in call["content"]
    assert card.note == ""
    assert card.note_code is None

    card.pr_head_sha = "def456"
    await _dispatch_passed(service, card, topic, task_status="open")
    assert service._record_task_nudge.await_count == 2


@pytest.mark.anyio
async def test_green_checks_on_a_closed_task_say_nothing():
    """活已经关了，绿了也没有下一步：不发，不留一句「原任务已关闭」。"""
    service, card, topic = _service_and_card()

    await _dispatch_passed(service, card, topic, task_status="closed")

    service._record_task_nudge.assert_not_called()
