"""Accept-card / Review business logic — the 验收 state machine.

Spec §4.4 (AI 不能验收自己做的东西), §6.3 (采纳即归档/merge, 且可撤销).
This is deterministic platform code, not AI.
"""

# ruff: noqa: F401  # 过渡期的兼容门面：再导出却不被本文件用到的名字就是这一批，
# 它们仍要留在 `app.domain.review.services` 的名字空间里（P9 收敛成门面后删掉这行）。
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Final, NoReturn

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.db import async_session_factory
from app.core.errors import ForbiddenError, NotFoundError, ValidationError
from app.domain.block.about import EventAbout, landing
from app.domain.block.models import AuthorType, BlockKind
from app.domain.block.notice_text import NoticeText, say
from app.domain.identity.handles import looks_like_agent_handle
from app.domain.library import service as library
from app.domain.membership.services import MemberService
from app.domain.project import artifacts
from app.domain.project.models import AiMode, Project
from app.domain.repository import identity
from app.domain.review import (
    archive,
    commit_message,
    merge_state,
    notes,
    pr_publish,
    pr_signals,
    pr_text,
)
from app.domain.review import forge as forge_mod
from app.domain.review.merge_state import MergeVerdict, Who, whose_move
from app.domain.review.models import (
    AcceptCard,
    AcceptStatus,
    DeliverableKind,
    GateOutcome,
)
from app.domain.review.nudges import _NUDGE_TAIL_LIMIT as _NUDGE_TAIL_LIMIT

# 兼容门面：PR 上的三件事怎么说（CI 挂了、有人评审、和目标分支冲突）搬去了
# `nudges.py`。这里重新导出，`app.domain.review.services` 仍是既有调用点与测试的
# 导入路径；`AcceptService` 上留一行同名委托，调用方一格没动。`_NUDGE_TAIL_LIMIT`
# 是给 `tests/integration/test_platform_notices.py` 取 detail 上界的，所以留在原
# 路径上（`as` 是 ruff 认的那种「这是再导出，不是没用上」的写法）；两段措辞辅助
# `_ci_log_howto` / `_nudge_note_prefix` 随三条待发一起搬走，没有别的取用者。
from app.domain.review.nudges import (
    _ci_nudge,
    _conflict_nudge,
    _review_nudge,
)
from app.domain.review.repositories import AcceptCardRepository

# 兼容门面：一件事做完之后「怎么说出去」（房间里那一行、卡上那条 note、要不要点
# 名）搬去了 `room_notices.py`。这里重新导出，`app.domain.review.services` 仍是既
# 有调用点与测试的导入路径；`AcceptService` 上每个各留一行同名委托，调用方一格没
# 动。`test_review_nudge_dedup.py` 就是靠 `service._record_task_nudge = AsyncMock()`
# 拦下一次待发的，所以那条委托必须还是一个真方法、走 `self.` 调用。
#
# `_notify_merge_result` 的那条委托多给一个参数：房间通知自己开一条 session 写库，
# 工厂由调用方给（就是上面的 `async_session_factory`），而不是被搬过去的那段代码
# 伸手去拿模块级单例——集成测试正是在 `services` 这个名字上换掉它
# （`test_accept_pr.py`、`forgejo/test_live.py`），放在这里读，换法照旧有效。
from app.domain.review.room_notices import (
    _announce_filed,
    _announce_new_artifact,
    _note_needs_human,
    _note_outside_accept_txn,
    _note_poll_failed,
    _notify_merge_result,
    _notify_ready,
    _record_task_nudge,
    _tell_the_reviewer,
)
from app.domain.review.schemas import AcceptCardOut
from app.domain.room_task.checkouts import after_close
from app.domain.room_task.models import Task, TaskStatus
from app.domain.room_task.place import PlaceResolver
from app.domain.room_task.services import TaskService
from app.domain.topic.models import Topic, TopicStatus

if TYPE_CHECKING:  # `github_pr` stays a lazy import at every call site
    from app.domain.project.protection import BranchProtection
    from app.domain.review.github_pr import PullRequestStatus

from app.domain.review.services import accept as _accept
from app.domain.review.services import cards, decisions, merge_queue, polling, reviewers
from app.domain.review.services._shared import (
    _ACCEPT_NO_BRANCH_PREFIX,
    _ACCEPT_PR_OPEN_FAILED_PREFIX,
    _ACCEPT_PR_STALLED_PREFIX,
    _ALEMBIC_VERSIONS_DIR,
    _ARTIFACT_ACTION_BOTH,
    _ARTIFACT_ACTION_MISSING,
    _ARTIFACT_ACTION_UNWANTED,
    _BLOCKED_BY_CARD_MESSAGES,
    _CARD_BLOCKS_NEW_CARD,
    _DELIVERABLE_BOTH,
    _DELIVERABLE_MAX_BYTES,
    _FORCE_MERGE_VERDICTS,
    _GITHUB_ENFORCES_TTL_S,
    _MERGE_FAILED_MESSAGE,
    _MISSING_SUBJECT,
    _NOT_THIS_ROOMS_WORK,
    _NOTHING_TO_DELIVER,
    _REQUIRED_CHECK_GRACE_MINUTES,
    EVENT_ACCEPT_DISMISSED,
    EVENT_ACCEPT_DONE,
    EVENT_ACCEPT_STOPPED,
    EVENT_CARD_REDESCRIBED,
    EVENT_CARD_VOIDED,
    EVENT_FORCE_MERGED,
    EVENT_MERGE_REFUSED,
    EVENT_MIGRATION_COLLISION,
    EVENT_PR_CLOSED,
    FORCE_MERGED_PREFIX,
    GATE_ABANDONED_PREFIX,
    SEVERITY_ERROR,
    SEVERITY_INFO,
    SEVERITY_WARN,
    VOIDED_PREFIX,
    WHO_CHEESE,
    WHO_HUMAN,
    WHO_PLATFORM,
    BlockRepository,
    ProjectRepository,
    ReviewerAdmission,
    TopicRepository,
    _capped,
    _force_merge_verdict,
    _github_enforces,
    _github_enforces_cache,
    _GitHubCredentials,
    _never_shown_message,
    _no_artifact_action,
    _one_artifact_action,
    _one_deliverable,
    _read_deliverable,
    _stale_view_message,
    approvals_required_of,
    logger,
    notice,
)


class AcceptService:
    def __init__(self, session: AsyncSession):

        self._session = session
        self._repo = AcceptCardRepository(session)
        self._topics = TopicRepository(session)
        self._projects = ProjectRepository(session)

    async def _topic_or_404(self, topic_id: uuid.UUID) -> Topic:
        return await cards._topic_or_404(self, topic_id=topic_id)

    async def _stamp_delivery(
        self, card: AcceptCard, topic: Topic, *, by: str | None, at: datetime | None
    ) -> None:
        return await cards._stamp_delivery(self, card=card, topic=topic, by=by, at=at)

    async def _card_or_404(self, card_id: uuid.UUID) -> AcceptCard:
        return await cards._card_or_404(self, card_id=card_id)

    async def _reviewer_or_project_default(
        self,
        project: Project | None,
        reviewer_handle: str | None,
        *,
        from_work: list[Task] | None = None,
    ) -> str:
        return await reviewers._reviewer_or_project_default(
            self, project=project, reviewer_handle=reviewer_handle, from_work=from_work
        )

    async def _require_reviewer_in_room(
        self, topic: Topic, handle: str, admits: ReviewerAdmission
    ) -> None:
        return await reviewers._require_reviewer_in_room(
            self, topic=topic, handle=handle, admits=admits
        )

    async def create_card(
        self,
        *,
        topic_id: uuid.UUID,
        task_id: uuid.UUID,
        reviewer_handle: str | None = None,
        routing_reason: str = "",
        change_subject: str | None = None,
        change_body: str | None = None,
        artifact: str | None = None,
        new_artifact: str | None = None,
        about: str | None = None,
        deliver: str | None = None,
        deliver_url: str | None = None,
        admits_reviewer: ReviewerAdmission,
    ) -> AcceptCard:
        return await cards.create_card(
            self,
            topic_id=topic_id,
            task_id=task_id,
            reviewer_handle=reviewer_handle,
            routing_reason=routing_reason,
            change_subject=change_subject,
            change_body=change_body,
            artifact=artifact,
            new_artifact=new_artifact,
            about=about,
            deliver=deliver,
            deliver_url=deliver_url,
            admits_reviewer=admits_reviewer,
        )

    async def _announce_filed(
        self, topic: Topic, card: AcceptCard, task: Task, *, artifact: str
    ) -> None:
        return await _announce_filed(
            self._session, topic, card, task, artifact=artifact
        )

    async def _announce_new_artifact(self, topic: Topic, name: str) -> None:
        return await _announce_new_artifact(self._session, topic, name)

    async def _warn_about_a_second_pending_migration(
        self, topic: Topic, task_id: uuid.UUID
    ) -> None:
        return await cards._warn_about_a_second_pending_migration(
            self, topic=topic, task_id=task_id
        )

    async def project_id_for_topic(self, topic_id: uuid.UUID) -> uuid.UUID:
        return await cards.project_id_for_topic(self, topic_id=topic_id)

    async def mark_gate_started(self, *, card_id: uuid.UUID) -> AcceptCard:
        return await cards.mark_gate_started(self, card_id=card_id)

    async def finish_gate(
        self, *, card_id: uuid.UUID, outcome: GateOutcome, output_tail: str
    ) -> AcceptCard:
        return await cards.finish_gate(
            self, card_id=card_id, outcome=outcome, output_tail=output_tail
        )

    async def list_for_topic(self, topic_id: uuid.UUID) -> tuple[list[AcceptCard], int]:
        return await cards.list_for_topic(self, topic_id=topic_id)

    async def open_pr_card_ids(
        self, project_id: uuid.UUID | None = None
    ) -> list[uuid.UUID]:
        return await cards.open_pr_card_ids(self, project_id=project_id)

    async def anybody_still_waiting(self, place_ids: list[uuid.UUID]) -> bool:
        return await cards.anybody_still_waiting(self, place_ids=place_ids)

    async def latest_decision_at(self, place_ids: list[uuid.UUID]) -> datetime | None:
        return await cards.latest_decision_at(self, place_ids=place_ids)

    async def reviewer_topic_ids(
        self, topic_ids: list[uuid.UUID], reviewer_handle: str
    ) -> dict[uuid.UUID, bool]:
        return await cards.reviewer_topic_ids(
            self, topic_ids=topic_ids, reviewer_handle=reviewer_handle
        )

    async def describe(self, card: AcceptCard) -> dict:
        return await cards.describe(self, card=card)

    async def _enforce_protocol(self, topic: Topic, decided_by: str) -> None:
        return await cards._enforce_protocol(self, topic=topic, decided_by=decided_by)

    async def reassign(
        self,
        *,
        card_id: uuid.UUID,
        reviewer_handle: str | None = None,
        reason: str = "",
        admits_reviewer: ReviewerAdmission,
    ) -> AcceptCard:
        return await reviewers.reassign(
            self,
            card_id=card_id,
            reviewer_handle=reviewer_handle,
            reason=reason,
            admits_reviewer=admits_reviewer,
        )

    def _forbid_ai(self, project: Project | None, handle: str, action: str) -> None:
        return reviewers._forbid_ai(self, project=project, handle=handle, action=action)

    async def approve(self, *, card_id: uuid.UUID, approver_handle: str) -> AcceptCard:
        return await decisions.approve(
            self, card_id=card_id, approver_handle=approver_handle
        )

    async def arm_auto_merge(
        self,
        *,
        card_id: uuid.UUID,
        decided_by: str,
        enabled: bool,
        head_sha: str | None = None,
    ) -> AcceptCard:
        return await merge_queue.arm_auto_merge(
            self,
            card_id=card_id,
            decided_by=decided_by,
            enabled=enabled,
            head_sha=head_sha,
        )

    def _notify_merge_result(
        self, topic: Topic, content: str, *, meta: dict | None = None
    ) -> None:
        return _notify_merge_result(async_session_factory, topic, content, meta=meta)

    @staticmethod
    def _seen_head(card: AcceptCard, head_sha: str | None, action: str) -> str | None:
        return polling._seen_head(card, head_sha, action)

    async def _seen_head_or_refresh(
        self, card: AcceptCard, topic: Topic, head_sha: str | None, action: str
    ) -> str | None:
        return await polling._seen_head_or_refresh(
            self, card=card, topic=topic, head_sha=head_sha, action=action
        )

    async def _refresh_never_shown_card(
        self, card: AcceptCard, topic: Topic, action: str
    ) -> NoReturn:
        return await polling._refresh_never_shown_card(
            self, card=card, topic=topic, action=action
        )

    async def _refresh_github_unseen_head(
        self, card: AcceptCard, topic: Topic, action: str
    ) -> NoReturn:
        return await polling._refresh_github_unseen_head(
            self, card=card, topic=topic, action=action
        )

    async def accept(
        self, *, card_id: uuid.UUID, decided_by: str, head_sha: str | None = None
    ) -> AcceptCard:
        return await _accept.accept(
            self, card_id=card_id, decided_by=decided_by, head_sha=head_sha
        )

    async def _accept_github(
        self,
        card: AcceptCard,
        topic: Topic,
        decided_by: str,
        *,
        seen_head: str | None,
    ) -> AcceptCard:
        return await _accept._accept_github(
            self, card=card, topic=topic, decided_by=decided_by, seen_head=seen_head
        )

    async def _accept_discussion(
        self, card: AcceptCard, topic: Topic, decided_by: str
    ) -> AcceptCard:
        return await _accept._accept_discussion(
            self, card=card, topic=topic, decided_by=decided_by
        )

    async def _pr_repo_of(self, card: AcceptCard, topic: Topic) -> tuple[str, str]:
        return await _accept._pr_repo_of(self, card=card, topic=topic)

    async def _status_client(self, project_id: uuid.UUID):
        return await polling._status_client(self, project_id=project_id)

    async def _dependency_block_reason(
        self, card: AcceptCard, status: PullRequestStatus
    ) -> str | None:
        return await _accept._dependency_block_reason(self, card=card, status=status)

    async def _sync_dependency_target(
        self,
        card: AcceptCard,
        status: PullRequestStatus,
    ) -> bool:
        return await _accept._sync_dependency_target(self, card=card, status=status)

    async def _sync_task_dependency_target(
        self, task, status: PullRequestStatus, *, drop_dependency: bool = False
    ) -> bool:
        return await _accept._sync_task_dependency_target(
            self, task=task, status=status, drop_dependency=drop_dependency
        )

    async def _pr_verdict(
        self,
        *,
        card: AcceptCard,
        topic: Topic,
        owner: str,
        repo: str,
        creds: _GitHubCredentials,
        client,
        status: PullRequestStatus,
        ref: str,
    ) -> tuple[MergeVerdict, Who, BranchProtection, bool, list[merge_state.CheckRun]]:
        return await _accept._pr_verdict(
            self,
            card=card,
            topic=topic,
            owner=owner,
            repo=repo,
            creds=creds,
            client=client,
            status=status,
            ref=ref,
        )

    async def _mirror_pr_verdict(
        self,
        card: AcceptCard,
        *,
        topic: Topic,
        owner: str,
        repo: str,
        creds: _GitHubCredentials,
        client,
        status: PullRequestStatus,
        ref: str,
    ) -> tuple[MergeVerdict, Who, BranchProtection, bool, list[merge_state.CheckRun]]:
        return await _accept._mirror_pr_verdict(
            self,
            card=card,
            topic=topic,
            owner=owner,
            repo=repo,
            creds=creds,
            client=client,
            status=status,
            ref=ref,
        )

    def _write_merge_mirror(
        self, card: AcceptCard, verdict: MergeVerdict, who: Who, head_sha: str
    ) -> None:
        return merge_queue._write_merge_mirror(
            self, card=card, verdict=verdict, who=who, head_sha=head_sha
        )

    async def _merge_pr_for_accept(
        self,
        card: AcceptCard,
        topic: Topic,
        decided_by: str,
        *,
        seen_head: str,
    ) -> AcceptCard:
        return await merge_queue._merge_pr_for_accept(
            self, card=card, topic=topic, decided_by=decided_by, seen_head=seen_head
        )

    async def _record_queue_entry(self, card: AcceptCard, decided_by: str) -> None:
        return await merge_queue._record_queue_entry(
            self, card=card, decided_by=decided_by
        )

    async def _conclude_pr_accept(
        self,
        card: AcceptCard,
        topic: Topic,
        decided_by: str,
        *,
        merged_externally: bool = False,
    ) -> AcceptCard:
        return await merge_queue._conclude_pr_accept(
            self,
            card=card,
            topic=topic,
            decided_by=decided_by,
            merged_externally=merged_externally,
        )

    async def _refresh_stale_card(
        self,
        card: AcceptCard,
        topic: Topic,
        *,
        live_head: str,
        action: str = "采纳",
        headline: str | None = None,
    ) -> None:
        return await polling._refresh_stale_card(
            self,
            card=card,
            topic=topic,
            live_head=live_head,
            action=action,
            headline=headline,
        )

    async def _pr_poll_credentials(
        self, card: AcceptCard, topic: Topic
    ) -> tuple[_GitHubCredentials | None, str]:
        return await polling._pr_poll_credentials(self, card=card, topic=topic)

    async def _app_credentials(
        self, topic: Topic
    ) -> tuple[_GitHubCredentials | None, str]:
        return await polling._app_credentials(self, topic=topic)

    async def advance_pr_card(
        self, card_id: uuid.UUID, *, chat_service, runner
    ) -> None:
        return await polling.advance_pr_card(
            self, card_id=card_id, chat_service=chat_service, runner=runner
        )

    async def refresh_stale_pr_snapshots(self, cards: Sequence[AcceptCard]) -> None:
        return await polling.refresh_stale_pr_snapshots(self, cards=cards)

    def _merge_snapshot_age_s(self, card: AcceptCard) -> float | None:
        return polling._merge_snapshot_age_s(self, card=card)

    async def _refresh_stale_pr_snapshot(self, card: AcceptCard) -> None:
        return await polling._refresh_stale_pr_snapshot(self, card=card)

    async def _refresh_github_snapshot(self, card: AcceptCard, topic: Topic) -> None:
        return await polling._refresh_github_snapshot(self, card=card, topic=topic)

    async def _advance_github_card(
        self,
        card: AcceptCard,
        topic: Topic,
        *,
        chat_service,
        runner,
    ) -> None:
        return await polling._advance_github_card(
            self, card=card, topic=topic, chat_service=chat_service, runner=runner
        )

    async def _mark_task_merged(
        self, card: AcceptCard, *, delivered_head: str | None = None
    ) -> None:
        return await polling._mark_task_merged(
            self, card=card, delivered_head=delivered_head
        )

    async def _app_pr_client(self, topic: Topic):  # noqa: ANN202 — GitHubPRClient
        return await polling._app_pr_client(self, topic=topic)

    async def mark_ready(self, room_id: uuid.UUID, task_id: uuid.UUID) -> dict:
        return await decisions.mark_ready(self, room_id=room_id, task_id=task_id)

    async def redescribe(
        self,
        place_id: uuid.UUID,
        *,
        actor: str,
        change_subject: str | None = None,
        change_body: str | None = None,
    ) -> AcceptCard:
        return await decisions.redescribe(
            self,
            place_id=place_id,
            actor=actor,
            change_subject=change_subject,
            change_body=change_body,
        )

    async def push_fix(
        self, place_id: uuid.UUID, *, drop_dependency: bool = False
    ) -> dict:
        return await decisions.push_fix(
            self, place_id=place_id, drop_dependency=drop_dependency
        )

    async def note_poll_crashed(self, card_id: uuid.UUID, exc: BaseException) -> None:
        return await polling.note_poll_crashed(self, card_id=card_id, exc=exc)

    def _note_poll_failed(self, card: AcceptCard, exc: BaseException) -> None:
        return _note_poll_failed(card, exc)

    async def _poll_pr_card(
        self,
        *,
        card: AcceptCard,
        topic: Topic,
        owner: str,
        repo: str,
        creds: _GitHubCredentials,
        client,
        chat_service,
        runner,
    ) -> None:
        return await polling._poll_pr_card(
            self,
            card=card,
            topic=topic,
            owner=owner,
            repo=repo,
            creds=creds,
            client=client,
            chat_service=chat_service,
            runner=runner,
        )

    async def _update_behind_branch(
        self,
        *,
        card: AcceptCard,
        topic: Topic,
        owner: str,
        repo: str,
        creds: _GitHubCredentials,
        client,
    ) -> None:
        return await polling._update_behind_branch(
            self,
            card=card,
            topic=topic,
            owner=owner,
            repo=repo,
            creds=creds,
            client=client,
        )

    async def _dismiss_stale_accept(self, *, card: AcceptCard, topic: Topic) -> None:
        return await _accept._dismiss_stale_accept(self, card=card, topic=topic)

    async def _tell_the_reviewer(
        self,
        card: AcceptCard,
        topic: Topic,
        content: str,
        *,
        meta: dict,
        also: Sequence[str] = (),
    ) -> None:
        return await _tell_the_reviewer(
            self._session, card, topic, content, meta=meta, also=also
        )

    async def _notify_ready(self, card: AcceptCard, topic: Topic) -> None:
        return await _notify_ready(self._session, card, topic)

    def _required_absence_overdue(self, card: AcceptCard) -> bool:
        return reviewers._required_absence_overdue(self, card=card)

    async def _merge_armed_card(
        self,
        *,
        card: AcceptCard,
        topic: Topic,
        owner: str,
        repo: str,
        creds: _GitHubCredentials,
        client,
        protection: BranchProtection,
        chat_service,
        runner,
    ) -> None:
        return await merge_queue._merge_armed_card(
            self,
            card=card,
            topic=topic,
            owner=owner,
            repo=repo,
            creds=creds,
            client=client,
            protection=protection,
            chat_service=chat_service,
            runner=runner,
        )

    async def _note_needs_human(
        self,
        *,
        card: AcceptCard,
        topic: Topic,
        reason: str,
        explain: str | None = None,
    ) -> None:
        return await _note_needs_human(
            self._session, card=card, topic=topic, reason=reason, explain=explain
        )

    async def _settle_external_merge(
        self, *, card: AcceptCard, topic: Topic, status: PullRequestStatus
    ) -> None:
        return await _accept._settle_external_merge(
            self, card=card, topic=topic, status=status
        )

    async def _void_closed_pr_card(self, *, card: AcceptCard, topic: Topic) -> None:
        return await _accept._void_closed_pr_card(self, card=card, topic=topic)

    async def _record_task_nudge(self, *, topic, task, content, headline, meta):
        return await _record_task_nudge(
            self._session,
            topic=topic,
            task=task,
            content=content,
            headline=headline,
            meta=meta,
        )

    async def _note_merge_blocked(
        self,
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
        task = (
            await TaskService(self._session).get(card.task_id) if card.task_id else None
        )
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
        self,
        *,
        card: AcceptCard,
        tail: str,
        stage: str,
        owner: str,
        repo: str,
    ) -> pr_signals.PendingNudge | None:
        return _ci_nudge(card=card, tail=tail, stage=stage, owner=owner, repo=repo)

    async def _review_nudge(
        self,
        *,
        card: AcceptCard,
        owner: str,
        repo: str,
        creds: _GitHubCredentials,
        client,
        status,
    ) -> pr_signals.PendingNudge | None:
        return await _review_nudge(
            card=card,
            owner=owner,
            repo=repo,
            creds=creds,
            client=client,
            status=status,
        )

    def _conflict_nudge(
        self, *, card: AcceptCard, status
    ) -> pr_signals.PendingNudge | None:
        return _conflict_nudge(card=card, status=status)

    async def _dispatch_nudges(
        self,
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
            select(AcceptCard.nudge_state)
            .where(AcceptCard.id == card.id)
            .with_for_update()
        )
        ledger = pr_signals.NudgeLedger.load(saved_state)
        fresh = [p for p in pending if not ledger.already_sent(p.kind, p.signature)]
        if not fresh:
            return
        task = (
            await TaskService(self._session).get(card.task_id) if card.task_id else None
        )
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

    async def _finish_pr_accept(
        self,
        *,
        card: AcceptCard,
        topic: Topic,
        merged_externally: bool = False,
        headline: str = "",
    ) -> None:
        return await _accept._finish_pr_accept(
            self,
            card=card,
            topic=topic,
            merged_externally=merged_externally,
            headline=headline,
        )

    async def _resolve_forge(
        self, project_id: uuid.UUID, *, card: AcceptCard | None = None
    ) -> forge_mod.Forge:
        return await _accept._resolve_forge(self, project_id=project_id, card=card)

    async def _stop_accept_pr_unavailable(
        self, card: AcceptCard, topic: Topic, reason: str
    ) -> NoReturn:
        return await _accept._stop_accept_pr_unavailable(
            self, card=card, topic=topic, reason=reason
        )

    async def _stop_accept_no_branch(self, card: AcceptCard, topic: Topic) -> NoReturn:
        return await _accept._stop_accept_no_branch(self, card=card, topic=topic)

    async def _publish_pr_for_accept(self, card: AcceptCard, topic: Topic) -> None:
        return await _accept._publish_pr_for_accept(self, card=card, topic=topic)

    async def _note_outside_accept_txn(
        self, card_id: uuid.UUID, code: notes.NoteCode, note: str
    ) -> None:
        return await _note_outside_accept_txn(self._session, card_id, code, note)

    async def merge_queued_pr(self, task_id: uuid.UUID) -> int | None:
        return await merge_queue.merge_queued_pr(self, task_id=task_id)

    async def _cancel_queued_accept(self, card: AcceptCard) -> None:
        return await merge_queue._cancel_queued_accept(self, card=card)

    async def reject(
        self, *, card_id: uuid.UUID, decided_by: str, note: str = ""
    ) -> AcceptCard:
        return await decisions.reject(
            self, card_id=card_id, decided_by=decided_by, note=note
        )

    async def revoke(self, *, card_id: uuid.UUID, decided_by: str) -> AcceptCard:
        return await decisions.revoke(self, card_id=card_id, decided_by=decided_by)

    async def merge_despite_checks(
        self,
        *,
        card_id: uuid.UUID,
        decided_by: str,
        reason: str = "",
        head_sha: str | None = None,
    ) -> AcceptCard:
        return await decisions.merge_despite_checks(
            self,
            card_id=card_id,
            decided_by=decided_by,
            reason=reason,
            head_sha=head_sha,
        )

    async def _override_github_checks(
        self,
        card: AcceptCard,
        topic: Topic,
        decided_by: str,
        *,
        seen_head: str,
        reason: str,
    ) -> AcceptCard:
        return await decisions._override_github_checks(
            self,
            card=card,
            topic=topic,
            decided_by=decided_by,
            seen_head=seen_head,
            reason=reason,
        )

    async def void(
        self, *, card_id: uuid.UUID, decided_by: str, note: str = ""
    ) -> AcceptCard:
        return await decisions.void(
            self, card_id=card_id, decided_by=decided_by, note=note
        )
