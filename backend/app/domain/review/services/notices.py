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
from app.domain.review.nudges import (
    _title_nudge as _title_nudge_from_nudges,
)
from app.domain.review.ports import (
    Announce,
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
    _settle_the_card as _settle_the_card_from_room,
)
from app.domain.review.room_notices import (
    _tell_the_reviewer as _tell_the_reviewer_from_room,
)
from app.domain.room_task import heard
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


def _announce_of(self: pkg.AcceptService) -> Announce:
    """构造时递进来的那一个（`ports.Announce`）。

    只有只读路径的门不接线，`None` 就是那种；这里还要说话，说明有个门漏了递 ——
    当场炸掉，比静默不发好。
    """
    if self._announce is None:
        # i18n-exempt: developer miswiring, not a refusal a reader can act on
        raise RuntimeError(
            "AcceptService was built without an `announce`, and this path speaks: "
            "the door that built it must hand one in "
            "(app/domain/review/ports.py)"
        )
    return self._announce


async def _announce_filed(
    self: pkg.AcceptService,
    topic: Topic,
    card: AcceptCard,
    task: Task,
) -> None:
    return await _announce_filed_from_room(
        self._session, topic, card, task, announce=_announce_of(self)
    )


async def _announce_new_artifact(
    self: pkg.AcceptService, topic: Topic, name: str, *, task_id: uuid.UUID
) -> None:
    return await _announce_new_artifact_from_room(
        self._session, topic, name, task_id=task_id, announce=_announce_of(self)
    )


def _notify_merge_result(
    self: pkg.AcceptService,
    topic: Topic,
    content: str,
    *,
    task_id: uuid.UUID | None,
    meta: dict | None = None,
) -> None:
    return _notify_merge_result_from_room(
        pkg.async_session_factory,
        topic,
        content,
        task_id=task_id,
        meta=meta,
        announce=_announce_of(self),
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
        self._session,
        card,
        topic,
        content,
        meta=meta,
        also=also,
        announce=_announce_of(self),
    )


async def _settle_the_card(
    self: pkg.AcceptService, card: AcceptCard, outcome: str
) -> None:
    return await _settle_the_card_from_room(
        self._session, card, outcome, settle_card=self._settle_card
    )


async def _notify_ready(
    self: pkg.AcceptService, card: AcceptCard, topic: Topic
) -> None:
    return await _notify_ready_from_room(
        self._session,
        card,
        topic,
        announce=_announce_of(self),
    )


async def _note_needs_human(
    self: pkg.AcceptService,
    *,
    card: AcceptCard,
    topic: Topic,
    reason: str,
    explain: str | None = None,
) -> None:
    return await _note_needs_human_from_room(
        self._session,
        card=card,
        topic=topic,
        reason=reason,
        explain=explain,
        announce=_announce_of(self),
    )


async def _record_task_nudge(
    self: pkg.AcceptService, *, topic, task, content, headline, meta, replaces=None
):
    """Wake the task's teammate with ``content`` and say ``headline`` where
    people read it (`room_task.heard.act`)."""
    return await heard.act(
        self._session,
        topic=topic,
        task=task,
        said=content,
        line=headline,
        meta=meta,
        replaces=replaces,
        announce=_announce_of(self),
        record_instruction=self._record_instruction,
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

    The dispatch tail is `_note_merge_stop_and_wake`, shared with
    `_note_left_merge_queue`: a refusal is not the only way an accepted PR
    stops.
    """
    # GitHub 的原话是外部字符串，而它要被贴进芝士的终端（见
    # `pr_signals.sanitize_external`）。
    reason = pr_signals.sanitize_external(reason)
    await _note_merge_stop_and_wake(
        self,
        card=card,
        topic=topic,
        note=f"PR #{card.pr_number} GitHub 拒绝合并：{reason}",
        log=f"PR merge refused for card {card.id}: {reason}",
        lead=(
            f"任务 {card.task_id} 的 PR #{card.pr_number}（{card.pr_url}）"
            "被 GitHub 拒绝合并：\n"
            f"```\n{reason[:1500]}\n```\n"
        ),
        action=(
            f'先执行 cd "$(cheese worktree {card.task_id})" 进入任务目录。'
            "检查 GitHub 返回的具体原因；若是冲突，确认 PR 当前目标分支后再合入，"
            "解决并验证后提交，用 cheese push-fix 更新原 PR。"
            "采纳由人决定；只有人已启用自动合并且项目条件满足时才会自动合并。\n"
            "如果原因不是冲突（比如仓库禁用了这种合并方式），工作区里改不动，"
            "请在频道里说清楚卡在哪、需要谁做什么。"
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


async def _note_merge_stop_and_wake(
    self: pkg.AcceptService,
    *,
    card: AcceptCard,
    topic: Topic,
    note: str,
    log: str,
    lead: str,
    action: str,
    headline,
    meta,
) -> None:
    """Put `note` on the card and, when it is new, wake the task's 芝士.

    The tail `_note_merge_blocked` and `_note_left_merge_queue` share. An
    accepted PR stops being a live accept in more than one way, and every way
    has to summon whoever can work the card rather than leaving a line nobody
    is looking at. The 60s poll re-derives the same `note`, so both the
    rewrite and the summon hang off "the text actually changed" — one write
    and one nudge per distinct stop, never one per poll.
    """
    current_note = await self._session.scalar(
        select(AcceptCard.note).where(AcceptCard.id == card.id).with_for_update()
    )
    if current_note == note:
        return
    notes.record(card, notes.NoteCode.merge_refused, note)
    logger.warning(log)
    task = await TaskService(self._session).get(card.task_id) if card.task_id else None
    actionable = task is not None and task.status == TaskStatus.open
    await self._record_task_nudge(
        topic=topic,
        task=task,
        content=f"{lead}{action if actionable else heard.TASK_CLOSED}",
        headline=headline,
        meta=meta,
    )


async def _note_left_merge_queue(
    self: pkg.AcceptService,
    *,
    card: AcceptCard,
    topic: Topic,
) -> None:
    """An accepted PR left GitHub's merge queue without merging: note it and
    wake the task's 芝士.

    GitHub drops an entry when a check fails or a person takes it out, and
    the card keeps reading 「已采纳」 either way — so before this the topic sat
    silent until somebody happened to look (真实案例: PR #3496 was dropped at
    12:26 and went unnoticed for 26 minutes). Like a refusal it is something
    芝士 can act on — a failed check is fixable in its own workspace — so it
    summons it the same way `_note_merge_blocked` does. The platform never
    re-enqueues the PR itself; a fix still needs a person to accept again.
    """
    lead = (
        f"任务 {card.task_id} 的 PR #{card.pr_number}（{card.pr_url}）"
        "已离开合并队列但尚未确认合并，可能是检查失败或被人取出：\n"
    )
    action = (
        f'先执行 cd "$(cheese worktree {card.task_id})" 进入任务目录。'
        "到 GitHub 上看 PR 为什么离开合并队列：若是检查失败，在任务目录里修好、"
        "验证后提交，用 cheese push-fix 更新原 PR。"
        "平台不会自动重新入队；修好后需要人重新采纳。"
    )
    await _note_merge_stop_and_wake(
        self,
        card=card,
        topic=topic,
        note=(
            f"PR #{card.pr_number} 已离开合并队列但尚未确认合并，"
            "请检查 GitHub 后重新采纳。"
        ),
        log=f"PR left the merge queue unmerged for card {card.id}",
        lead=lead,
        action=action,
        headline=say("mergeQueueLeft", pr=card.pr_number),
        meta=notice(
            EVENT_MERGE_REFUSED,
            severity=SEVERITY_ERROR,
            who=WHO_CHEESE,
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


def _title_nudge(
    self: pkg.AcceptService, *, card: AcceptCard, topic: Topic, status
) -> pr_signals.PendingNudge | None:
    return _title_nudge_from_nudges(card=card, topic=topic, status=status)


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
    # 卡面那一行说的是「这张卡此刻停在哪」（`notes` 的码），不是「这一轮又新发现
    # 了什么」。所以**卡面空着**时它取全部待发里最重的那条，不取 `fresh`：一条已经
    # 叫过人的理由（签名进了账本，`fresh` 里再也不会出现）在卡面被别的分支清掉之后
    # 就再也写不回来 —— CI 从红转绿时 `notes.clear(card)` 正是这么清的。理由还挡着
    # 的那件事（标题分家，两条合并路径都不放行）没走，卡面却成了一行空白的普通待办，
    # 验收人的黄灯不亮：这正是这次改动要消灭的那种「停住了却看不出来」。
    #
    # 卡面上已经有字时照旧只按 `fresh` 写，不按 `pending` 重写一遍：`_note_needs_human`
    # 在本函数之后才跑，它写的 `merge_withheld` 说的是「机器没招了、要人来」。一个
    # 每拍都刷新卡面的写入口会把它盖掉，下一拍它再盖回来 —— 卡面每拍翻一次，而它按
    # 整行文字去重，于是验收人每 60 秒被重新叫一次，直到那张卡不再等为止。
    #
    # 不担心盖掉队列/合并态那几句：进队列的卡在 `_poll_pr_card` 更早处就返回了，
    # 走不到这里（`waiting_merge_queue` 那条分支）。
    source = fresh if (card.note or "").strip() else pending
    loudest = (
        max(source, key=lambda n: pr_signals.NOTE_PRIORITY[n.kind]) if source else None
    )
    if loudest is not None and loudest.note:
        notes.record(card, loudest.note_code, loudest.note)
    if not fresh:
        return
    task = await TaskService(self._session).get(card.task_id) if card.task_id else None
    actionable = task is not None and task.status == TaskStatus.open
    for nudge in fresh:
        if nudge.capped:
            continue
        # 「检查通过」不要谁动手：活已经关了就不说，说的时候也不带进工作区的命令。
        informational = nudge.severity != SEVERITY_ERROR
        if informational and not actionable:
            continue
        await self._record_task_nudge(
            topic=topic,
            task=task,
            content=(
                f"任务 {card.task_id}：" + nudge.content
                if informational
                else f"任务 {card.task_id}："
                f'先执行 cd "$(cheese worktree {card.task_id})"。\n' + nudge.content
                if actionable
                else f"{nudge.event}。{heard.TASK_CLOSED}"
            ),
            headline=nudge.event,
            meta=notice(
                nudge.event_type,
                severity=nudge.severity,
                who=WHO_CHEESE,
                detail=nudge.detail or None,
                detail_label=nudge.detail_label or None,
            ),
            # Each kind's newest nudge restates that kind's whole current state
            # on this PR, so it replaces one of its kind still waiting for the
            # agent (`delivery.agent.record_task_instruction`).
            replaces=f"pr:{card.pr_number}:{nudge.kind.value}",
        )
    for nudge in fresh:
        ledger.record(nudge.kind, nudge.signature)
    card.nudge_state = ledger.dump()


async def _note_outside_accept_txn(
    self: pkg.AcceptService, card_id: uuid.UUID, code: notes.NoteCode, note: str
) -> None:
    return await _note_outside_accept_txn_from_room(self._session, card_id, code, note)
