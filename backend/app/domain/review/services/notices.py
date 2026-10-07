"""The notification tail this domain still owns: nudge dispatch and the
notes/wording sent to a room or a card."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import TYPE_CHECKING

from sqlalchemy import select

from app.core.sentences import say
from app.domain.review import (
    notes,
    pr_signals,
)
from app.domain.review.models import (
    AcceptCard,
)
from app.domain.review.nudges import (
    _ci_nudge as _ci_nudge_from_nudges,
)
from app.domain.review.nudges import (
    _conflict_nudge as _conflict_nudge_from_nudges,
)
from app.domain.review.nudges import (
    _review_nudge as _review_nudge_from_nudges,
)
from app.domain.review.room_notices import (
    _announce_filed as _announce_filed_from_room,
)
from app.domain.review.room_notices import (
    _announce_new_artifact as _announce_new_artifact_from_room,
)
from app.domain.review.room_notices import (
    _note_needs_human as _note_needs_human_from_room,
)
from app.domain.review.room_notices import (
    _note_outside_accept_txn as _note_outside_accept_txn_from_room,
)
from app.domain.review.room_notices import (
    _note_poll_failed as _note_poll_failed_from_room,
)
from app.domain.review.room_notices import (
    _notify_merge_result as _notify_merge_result_from_room,
)
from app.domain.review.room_notices import (
    _notify_ready as _notify_ready_from_room,
)
from app.domain.review.room_notices import (
    _record_task_nudge as _record_task_nudge_from_room,
)
from app.domain.review.room_notices import (
    _tell_the_reviewer as _tell_the_reviewer_from_room,
)
from app.domain.room_task.models import Task, TaskStatus
from app.domain.room_task.services import TaskService
from app.domain.topic.models import Topic

if TYPE_CHECKING:  # `github_pr` stays a lazy import at every call site
    pass

from app.domain.review import services as pkg
from app.domain.review.services._shared import (
    EVENT_MERGE_REFUSED,
    SEVERITY_ERROR,
    WHO_CHEESE,
    _GitHubCredentials,
    logger,
    notice,
)


async def _announce_filed(
    self: pkg.AcceptService,
    topic: Topic,
    card: AcceptCard,
    task: Task,
    *,
    artifact: str,
) -> None:
    return await _announce_filed_from_room(
        self._session, topic, card, task, artifact=artifact
    )


async def _announce_new_artifact(
    self: pkg.AcceptService, topic: Topic, name: str
) -> None:
    return await _announce_new_artifact_from_room(self._session, topic, name)


def _notify_merge_result(
    self: pkg.AcceptService,
    topic: Topic,
    content: str,
    *,
    task_id: uuid.UUID | None,
    meta: dict | None = None,
) -> None:
    return _notify_merge_result_from_room(
        pkg.async_session_factory, topic, content, task_id=task_id, meta=meta
    )


def _note_poll_failed(
    self: pkg.AcceptService, card: AcceptCard, exc: BaseException
) -> None:
    return _note_poll_failed_from_room(card, exc)


async def _tell_the_reviewer(
    self: pkg.AcceptService,
    card: AcceptCard,
    topic: Topic,
    content: str,
    *,
    meta: dict,
    also: Sequence[str] = (),
) -> None:
    return await _tell_the_reviewer_from_room(
        self._session, card, topic, content, meta=meta, also=also
    )


async def _notify_ready(
    self: pkg.AcceptService, card: AcceptCard, topic: Topic
) -> None:
    return await _notify_ready_from_room(self._session, card, topic)


async def _note_needs_human(
    self: pkg.AcceptService,
    *,
    card: AcceptCard,
    topic: Topic,
    reason: str,
    explain: str | None = None,
) -> None:
    return await _note_needs_human_from_room(
        self._session, card=card, topic=topic, reason=reason, explain=explain
    )


async def _record_task_nudge(
    self: pkg.AcceptService, *, topic, task, content, headline, meta
):
    return await _record_task_nudge_from_room(
        self._session,
        topic=topic,
        task=task,
        content=content,
        headline=headline,
        meta=meta,
    )


async def _note_merge_blocked(
    self: pkg.AcceptService,
    *,
    card: AcceptCard,
    topic: Topic,
    reason: str,
    chat_service,
    runner,
) -> None:
    """Put GitHub's merge refusal on the card's `note` AND wake 芝士 up.
    Before this existed a refusal left `note` empty, so a permanently-
    unmergeable PR looked exactly like a healthy one still waiting on CI.

    The note alone was still not enough (2026-08-11): a note is something
    you have to be looking at. The most common refusal — merge conflicts —
    is exactly the kind 芝士 can fix in its own workspace, so this summons
    it the same way `_ci_nudge` does for a red check. Without the
    summon nobody is working the card and the topic just sits there
    forever (真实案例: PR #242). Note that the conflict dispatch in
    `routes/accept.py` never covers this — that one only runs for the
    synchronous merge at the moment a human clicks 采纳, not for the poll.

    Three things the 60s poll makes mandatory:

    - **No spam.** The note is rewritten only when the text actually
      changes, so an unchanging reason costs one write, not one per poll.
      (Stricter than the nudge ledger's content signature, which cannot
      notice a 405 turning into a 409 — same reason, same string.)
    - **One summon per reason.** The dispatch hangs off that same "the note
      really changed" test, so a 405 that turns into a 409 gets a fresh
      nudge while an unchanging one stays quiet.
    """
    # GitHub 的原话是外部字符串，而它要被贴进芝士的终端（见
    # `pr_signals.sanitize_external`）。
    reason = pr_signals.sanitize_external(reason)
    note = f"PR #{card.pr_number} GitHub 拒绝合并：{reason}"
    current_note = await self._session.scalar(
        select(AcceptCard.note).where(AcceptCard.id == card.id).with_for_update()
    )
    if current_note == note:
        return
    notes.record(card, notes.NoteCode.merge_refused, note)
    logger.warning("PR merge refused for card %s: %s", card.id, reason)
    task = await TaskService(self._session).get(card.task_id) if card.task_id else None
    actionable = task is not None and task.status == TaskStatus.open
    action = (
        f'先执行 cd "$(cheese worktree {card.task_id})" 进入任务目录。'
        "检查 GitHub 返回的具体原因；若是冲突，确认 PR 当前目标分支后再合入，"
        "解决并验证后提交，用 cheese push-fix 更新原 PR。"
        "采纳由人决定；只有人已启用自动合并且项目条件满足时才会自动合并。\n"
        "如果原因不是冲突（比如仓库禁用了这种合并方式），工作区里改不动，"
        "请在话题里说清楚卡在哪、需要谁做什么。"
        if actionable
        else "原任务已关闭或不存在；如需继续修改，请由新任务承接。"
    )
    await self._record_task_nudge(
        topic=topic,
        task=task,
        content=(
            f"任务 {card.task_id} 的 PR #{card.pr_number}（{card.pr_url}）"
            "被 GitHub 拒绝合并：\n"
            f"```\n{reason[:1500]}\n```\n"
            f"{action}"
        ),
        headline=say("mergeRefused", pr=card.pr_number),
        meta=notice(
            EVENT_MERGE_REFUSED,
            severity=SEVERITY_ERROR,
            who=WHO_CHEESE,
            detail=reason[:1500],
            detail_label=say("labelGithubReason"),
        ),
    )


def _ci_nudge(
    self: pkg.AcceptService,
    *,
    card: AcceptCard,
    tail: str,
    stage: str,
    owner: str,
    repo: str,
) -> pr_signals.PendingNudge | None:
    return _ci_nudge_from_nudges(
        card=card, tail=tail, stage=stage, owner=owner, repo=repo
    )


async def _review_nudge(
    self: pkg.AcceptService,
    *,
    card: AcceptCard,
    owner: str,
    repo: str,
    creds: _GitHubCredentials,
    client,
    status,
) -> pr_signals.PendingNudge | None:
    return await _review_nudge_from_nudges(
        card=card,
        owner=owner,
        repo=repo,
        creds=creds,
        client=client,
        status=status,
    )


def _conflict_nudge(
    self: pkg.AcceptService, *, card: AcceptCard, status
) -> pr_signals.PendingNudge | None:
    return _conflict_nudge_from_nudges(card=card, status=status)


async def _dispatch_nudges(
    self: pkg.AcceptService,
    *,
    card: AcceptCard,
    topic: Topic,
    pending: list[pr_signals.PendingNudge],
    chat_service,
    runner,
) -> None:
    """Lock the card, then record each new event and parent intent.

    Signatures, task events and recipient intent commit together. A failed
    producer transaction leaves none of them; the next poll may recreate
    the event. Only committed intent can reach a native session.
    """
    saved_state = await self._session.scalar(
        select(AcceptCard.nudge_state).where(AcceptCard.id == card.id).with_for_update()
    )
    ledger = pr_signals.NudgeLedger.load(saved_state)
    fresh = [p for p in pending if not ledger.already_sent(p.kind, p.signature)]
    if not fresh:
        return
    task = await TaskService(self._session).get(card.task_id) if card.task_id else None
    actionable = task is not None and task.status == TaskStatus.open
    for nudge in fresh:
        if nudge.capped:
            continue
        await self._record_task_nudge(
            topic=topic,
            task=task,
            content=(
                f"任务 {card.task_id}："
                f'先执行 cd "$(cheese worktree {card.task_id})"。\n' + nudge.content
                if actionable
                else f"{nudge.event}。原任务已关闭或不存在；"
                "如需继续修改，请由新任务承接。"
            ),
            headline=nudge.event,
            meta=notice(
                nudge.event_type,
                severity=SEVERITY_ERROR,
                who=WHO_CHEESE,
                detail=nudge.detail or None,
                detail_label=nudge.detail_label or None,
            ),
        )
    loudest = max(fresh, key=lambda n: pr_signals.NOTE_PRIORITY[n.kind])
    if loudest.note:
        notes.record(card, loudest.note_code, loudest.note)
    for nudge in fresh:
        ledger.record(nudge.kind, nudge.signature)
    card.nudge_state = ledger.dump()


async def _note_outside_accept_txn(
    self: pkg.AcceptService, card_id: uuid.UUID, code: notes.NoteCode, note: str
) -> None:
    return await _note_outside_accept_txn_from_room(self._session, card_id, code, note)
