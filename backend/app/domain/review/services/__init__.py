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
from app.domain.review.services import cards, merge_queue, polling, reviewers
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
        """主分支保护 (spec §4.4): record one vote toward this card's accept.
        Idempotent per (card, approver); AI cannot vote in collaborative mode."""
        card = await self._card_or_404(card_id)
        # Votable while the card is still live (incl. behind the gate / in a
        # merge-conflict retry); decided or gate-failed cards are closed.
        if card.status not in (
            AcceptStatus.pending,
            AcceptStatus.pending_gate,
            AcceptStatus.conflict,
        ):
            raise ValidationError(say("reviewEndedNoApprove"))
        topic = await self._topic_or_404(card.topic_id)
        project = await self._projects.get(topic.project_id)
        self._forbid_ai(project, approver_handle, "批准")
        await self._repo.add_approval(card_id, approver_handle)
        return card

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
        """Mark this task's PR ready; acceptance remains a separate human action."""
        task = await TaskService(self._session).require_in_room(room_id, task_id)
        if task.status != TaskStatus.open or not task.branch_name:
            raise ValidationError("这条任务已结束或没有工作分支")
        topic = await self._topic_or_404(room_id)
        if topic.status == TopicStatus.archived:
            raise ValidationError("话题已归档，不能提交验收")
        if task.pr_number is None:
            return {"ready": False, "reason": "任务还没有 PR，请先提交并同步代码"}
        client = await self._app_pr_client(topic)
        if client is None:
            return {"ready": False, "reason": "这个项目没有绑定 GitHub"}
        from app.domain.review.github_pr import GitHubPRRateLimited

        try:
            view = await client.pr_view(task.pr_number)
        except GitHubPRRateLimited:
            return {
                "ready": False,
                "reason": (
                    "GitHub API 请求额度暂时用尽，PR 尚未标记为可评审；"
                    "额度恢复后请重试。"
                ),
            }
        if view.get("draft"):
            node_id = str(view.get("node_id") or "")
            if not node_id:
                raise ValidationError("GitHub 未返回 PR 的 node_id")
            await client.mark_ready_for_review(node_id)
        return {
            "ready": True,
            "already": not bool(view.get("draft")),
            "pr_number": task.pr_number,
            "pr_url": task.pr_url,
        }

    async def redescribe(
        self,
        place_id: uuid.UUID,
        *,
        actor: str,
        change_subject: str | None = None,
        change_body: str | None = None,
    ) -> AcceptCard:
        """更正这张卡的描述 —— and rewrite the PR from it in the same breath.

        **Why this may be corrected while `Cheese-Task:` may not.** A delivery
        claim is an ASSERTION OF FACT about who wrote the code; letting it be
        edited after filing is letting somebody put another agent's name on a
        change, and the wrong name in permanent history reads exactly like the
        right one. A description is an EXPLANATION of the change, and having a
        reviewer say "that reasoning is wrong" is what review IS. Refusing to
        correct it does not protect history — it guarantees the correction
        happens on the PR page only, and main receives the sentence everyone
        already agreed was false. PR #735 是活例子：评审把 PR 正文改对了，
        `1c298199a` 里留下的仍是递卡那一刻的快照。

        The PR is rewritten from the card, never read back into it. The card is
        the single source of both texts, so they cannot disagree — and the
        trailers (`Cheese-Task`, `Requested-by`, `Cheese-Agent`) stay something
        the platform asserts rather than something anybody can retype in a
        GitHub textarea.

        Only while the card is `pending`. Once it is accepted the commit is
        already in main and there is nothing left to correct here; that case
        belongs in a correction the room records, not in a row nobody reads
        again.
        """
        cards = await self._repo.list_live_for_places(
            [place_id], statuses=(AcceptStatus.pending,)
        )
        if not cards:
            raise ValidationError("这个话题手上没有待处理的验收卡，没有描述可以改")
        card = cards[0]
        topic = await self._topic_or_404(card.topic_id)
        before_subject, before_body = card.change_subject, card.change_body or ""

        subject = (change_subject or "").strip()
        if subject:
            try:
                card.change_subject = commit_message.check_subject(subject)
            except commit_message.InvalidSubject as exc:
                raise ValidationError(str(exc)) from exc
        if change_body is not None:
            card.change_body = change_body.strip() or None
        if (card.change_subject, card.change_body or "") == (
            before_subject,
            before_body,
        ):
            return card
        await self._session.flush()

        if card.pr_number is not None:
            client = await self._app_pr_client(topic)
            if client is not None:
                who = await identity.attribution(self._session, topic, card=card)
                view = await client.pr_view(card.pr_number)
                await pr_publish.sync_pr_text(
                    client,
                    view,
                    title=pr_text.change_subject(card, topic),
                    body=pr_text.pr_body(topic, "", card, who),
                )
        # 留痕：谁在什么时候把描述从什么改成了什么。这条入口的存在本身需要可追溯，
        # 否则它就是一条能悄悄改「这次改动会在历史里说什么」的路。
        self._notify_merge_result(
            topic,
            say("cardRedescribed", actor=actor),
            meta=notice(
                EVENT_CARD_REDESCRIBED,
                severity=SEVERITY_INFO,
                who=WHO_CHEESE,
                detail=say(
                    "cardRedescribedDetail",
                    beforeSubject=before_subject or say("emptyValue"),
                    afterSubject=card.change_subject or say("emptyValue"),
                    beforeBody=before_body or say("emptyValue"),
                    afterBody=card.change_body or say("emptyValue"),
                ),
                detail_label=say("labelWhatChanged"),
            ),
        )
        await self._session.flush()
        await self._session.refresh(card)
        return card

    async def push_fix(
        self, place_id: uuid.UUID, *, drop_dependency: bool = False
    ) -> dict:
        """Observe the branch the CLI already pushed directly to the forge."""
        cards = await self._repo.list_live_for_places(
            [place_id], statuses=(AcceptStatus.pending, AcceptStatus.conflict)
        )
        cards = [c for c in cards if c.pr_number is not None]
        if drop_dependency:
            from app.domain.project.forge import default_branch
            from app.domain.review import github_pr

            task = await TaskService(self._session).get(place_id)
            if task is None or task.pr_number is None:
                raise ValidationError("任务尚无 PR，请先同步提交后再移除依赖")
            topic = await self._topic_or_404(task.room_id)
            for card in cards:
                await self._pr_repo_of(card, topic)
            publisher = await self._app_pr_client(topic)
            if publisher is None:
                raise ValidationError("项目的代码仓库暂时不可用，无法移除依赖")
            base = await default_branch(task.project_id, self._session)
            try:
                await publisher.update_pr(task.pr_number, base=base)
                status = await publisher.pr_status(task.pr_number)
            except (github_pr.GitHubPrError, github_pr.GitHubPRError) as exc:
                raise ValidationError(f"暂时无法更新 PR 的目标分支：{exc}") from exc
            if status.base_ref != base:
                raise ValidationError("仓库尚未确认新的目标分支，请稍后重试")
            pushed = await self._sync_task_dependency_target(
                task, status, drop_dependency=True
            )
            for card in cards:
                if pushed or card.pr_head_sha != status.head_sha:
                    pushed = True
                    await self._dismiss_stale_accept(card=card, topic=topic)
                    card.pr_head_sha = status.head_sha
                    card.merge_state = None
                    card.rebase_count = 0
                    notes.clear(card)
            await self._session.flush()
            return {
                "pushed": pushed,
                "pr_number": task.pr_number,
                "pr_url": task.pr_url,
                "reason": "" if pushed else "任务已无依赖，PR 没有新提交",
            }
        if not cards:
            return {"pushed": False, "reason": "这个话题手上没有骑着 PR 的验收卡"}
        card = cards[0]
        assert card.pr_number is not None  # filtered above; for the type checker
        topic = await self._topic_or_404(card.topic_id)
        try:
            owner, repo = await self._pr_repo_of(card, topic)
        except ValidationError as exc:
            return {"pushed": False, "reason": str(exc)}

        creds, reason = await self._pr_poll_credentials(card, topic)
        if creds is None:
            return {"pushed": False, "reason": f"拿不到可用的 GitHub 凭据：{reason}"}

        from app.domain.review import github_pr

        client = await self._status_client(topic.project_id)
        try:
            status = await client.pull_request_status(
                owner=owner, repo=repo, number=card.pr_number, token=creds.read
            )
            retargeted = await self._sync_dependency_target(card, status)
            pushed = retargeted or status.head_sha != card.pr_head_sha
            if pushed:
                await self._dismiss_stale_accept(card=card, topic=topic)
                card.pr_head_sha = status.head_sha
                card.merge_state = None
                card.rebase_count = 0
                notes.clear(card)
        except (github_pr.GitHubPrError, github_pr.GitHubPRError) as exc:
            # Same reasoning as the poll path: say it on the card, because the
            # person waiting is looking at the card and not at a log file.
            self._note_poll_failed(card, exc)
            await self._session.flush()
            return {"pushed": False, "reason": f"GitHub 暂时不通：{exc}"}
        await self._session.flush()
        return {
            "pushed": pushed,
            "pr_number": card.pr_number,
            "pr_url": card.pr_url,
            "reason": "" if pushed else "分支上没有 PR 还不知道的提交",
        }

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
        card = await self._card_or_404(card_id)
        if card.status != AcceptStatus.pending:
            raise ValidationError(say("reviewEndedNoReturn"))
        if decided_by != card.reviewer_handle:
            raise ForbiddenError(say("returnReviewerOnly"))

        await self._cancel_queued_accept(card)
        card.status = AcceptStatus.rejected
        card.decided_by = decided_by
        card.decided_at = datetime.now(UTC)
        notes.record(card, None, note)

        # Topic stays active on rejection.
        await self._session.flush()
        await self._session.refresh(card)
        return card

    async def revoke(self, *, card_id: uuid.UUID, decided_by: str) -> AcceptCard:
        card = await self._card_or_404(card_id)
        # Accept is revocable (spec §6.3): only an accepted card can be revoked.
        if card.status != AcceptStatus.accepted:
            raise ValidationError(say("revokeAcceptedOnly"))

        # Only the person who accepted it, or someone who manages the project,
        # may revoke — not any arbitrary handle.
        topic = await self._topic_or_404(card.topic_id)
        if decided_by != card.decided_by and not await MemberService(
            self._session
        ).manages(topic.project_id, decided_by):
            raise ValidationError(say("revokeAcceptForbidden"))

        card.status = AcceptStatus.revoked
        card.decided_by = decided_by
        card.decided_at = datetime.now(UTC)

        # 撤销的是这次**验收记录**，不是这次合并 —— PR 已经在 main 上了，git 层面
        # revoke 什么都没撤。只清采纳标记；任务仍然关闭，后续修改用新任务交付。
        #
        # 归档状态一律不动，这是 2026-08-17 的对称面：`TopicService.unarchive` 的
        # docstring 说「取消归档不改写采纳记录，那要用撤回采纳」；反过来同理——
        # 撤回采纳不改写归档状态，那是人的决定（取消归档）。以前这里要把话题拉回
        # active，是因为采纳会顺手归档；采纳不再归档之后，一张卡的撤销没有理由
        # 覆盖某个人「把这个话题收起来」的动作。
        await self._stamp_delivery(card, topic, by=None, at=None)

        await self._session.flush()
        await self._session.refresh(card)
        return card

    async def merge_despite_checks(
        self,
        *,
        card_id: uuid.UUID,
        decided_by: str,
        reason: str = "",
        head_sha: str | None = None,
    ) -> AcceptCard:
        """人明知规则没满足，仍然决定合并——**署名的**显式出口（人工放行）。

        为什么必须有：红着合有时候是对的。CI 基础设施抽风、与本次改动无关的既有
        失败、赶时间的热修——真正不能接受的不是「红着合」，而是**没有人做过这个
        决定**。平台自己的默认因此是拒绝（`_merge_pr_for_accept`：合并态不是
        clean/unstable 就不合），而这条出口是另一半：**显式放行，且放行必须
        签字**。

        平台不重算「这段代码好不好」：它只把 forge 的结论如实呈上，然后让一个
        **具名的人**在上面按手印。它记什么：谁、什么时候、**当时的检查到底是
        什么状态**（现读一次，读不到就如实写读不到——但绝不因此拒绝放行，凭据坏
        了不该把人锁在门外）、以及人自己写的理由。

        谁能点 (#718)：项目分支保护的人工放行名单（`override_handles`，没配置
        = 项目所有者 + 团队的所有者和管理员）。芝士被 `_forbid_ai` 挡在外面
        （跟 accept/approve/void 同一条线），路由也**故意不进** `app/main.py`
        的 `_CHEESE_WRITE_PATHS`——
        照 `void` 的先例：不进白名单本身拦不住任何东西（没列进去的写路由压根不
        过那个中间件），真正拦住芝士的是这里的 `_forbid_ai` 加路由上的登录校验。

        放行**放的是规则，不是眼睛**：它跟采纳一样要声明「我看的是哪一版」
        （`_seen_head_or_refresh`）。签字的人要为一段具体的代码背书，屏幕上那版
        已经不在了、或者卡面压根没显示过任何版本的时候，这个签名就落到了别的东西
        上。
        """
        from app.domain.project.protection import branch_protection_of

        card = await self._card_or_404(card_id)
        if card.status != AcceptStatus.pending or card.pr_merged_at is not None:
            raise ValidationError(say("overridePendingOnly"))
        if card.pr_number is None:
            raise ValidationError(say("noMergeablePr"))

        topic = await self._topic_or_404(card.topic_id)
        project = await self._projects.get(topic.project_id)
        self._forbid_ai(project, decided_by, "人工放行")

        protection = branch_protection_of(project)
        if protection.override_handles is not None:
            allowed = set(protection.override_handles)
        else:
            allowed = (
                {decided_by}
                if await MemberService(self._session).manages(
                    topic.project_id, decided_by
                )
                else set()
            )
        if decided_by not in allowed:
            raise ForbiddenError(say("overrideListOnly"))
        # 骑着 PR 的卡在这里必然带着一个被展示过的 sha：卡面从没显示过 head 的
        # （刚递、轮询器还没镜像）会被刷新并要求重看，而不是拿现读的 head 去合。
        seen_head = await self._seen_head_or_refresh(
            card, topic, head_sha, say("verbMergeAnyway")
        )
        assert seen_head is not None  # PR lane; the guard above rules None out

        forge = await self._resolve_forge(topic.project_id, card=card)
        return await forge.merge_despite_checks(
            self, card, topic, decided_by, seen_head=seen_head, reason=reason
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
        assert card.pr_number is not None  # The shared override entry requires a PR.
        creds, why = await self._pr_poll_credentials(card, topic)
        if creds is None:
            raise ValidationError(say("overrideNoGithubCredentials", why=why))

        owner, repo = await self._pr_repo_of(card, topic)
        client = await self._status_client(topic.project_id)
        status = await client.pull_request_status(
            owner=owner, repo=repo, number=card.pr_number, token=creds.read
        )
        if not status.merged:
            dependency = await self._dependency_block_reason(card, status)
            if dependency:
                raise ValidationError(dependency)
        # 留痕用，不是门禁：读一次「此刻检查是什么状态」，读不到也照样放行。
        state: str | None = None
        try:
            state, tail = await client.check_state(
                owner=owner, repo=repo, ref=seen_head, token=creds.read
            )
            checks_at_merge = say(
                "forceMergedChecks",
                state=state,
                line=tail.splitlines()[0] if tail else "",
            )
        except Exception as exc:  # noqa: BLE001 — a broken read must not lock a human out
            logger.warning(
                "force-merge check read failed for card %s: %s", card.id, exc
            )
            state = None
            checks_at_merge = say("forceMergedChecksUnread")
        # 读到的状态决定这句话怎么写：全绿时说「明知未全绿」是往历史里写一条从没
        # 发生过的决定（PR #520 真的这么记了一条）。
        verdict = _force_merge_verdict(state)

        number = card.pr_number
        who = await identity.attribution(
            self._session, topic, card=card, decided_by=decided_by
        )
        from app.domain.project.forge import ensure_author_email

        if who.author:
            await ensure_author_email(topic.project_id, self._session, who.author.email)
        result = await client.merge_pull_request(
            owner=owner,
            repo=repo,
            number=number,
            token=creds.write,
            commit_title=pr_text.merge_commit_title(card, topic, number),
            commit_message=pr_text.merge_commit_message(topic, decided_by, card, who),
            # 放行合的也是人看到的那个 commit：head 变了 GitHub 409，卡刷新。
            sha=seen_head,
        )
        if result.stale_head:
            await self._refresh_stale_card(
                card, topic, live_head="", action=say("verbMergeAnyway")
            )
            raise ValidationError(say("prNewCommitsDuringOverride", number=number))
        if result.queued:
            await self._record_queue_entry(card, decided_by)
            return card
        if result.sha is None:
            raise ValidationError(
                say(
                    "githubMergeRefused",
                    pr=number,
                    reason=result.blocked_reason or "未说明原因",
                )
            )

        now = datetime.now(UTC)
        stamp = now.strftime("%Y-%m-%d %H:%M UTC")
        tail_reason = (
            say("forceMergedReason", reason=reason.strip()) if reason.strip() else ""
        )
        headline = (
            f"{FORCE_MERGED_PREFIX}：<@{decided_by}> 于 {stamp} 人工放行合并"
            f"（{verdict}；合并时检查状态：{checks_at_merge}）{tail_reason}"
        )
        card.pr_merged_at = now
        await self._mark_task_merged(card, delivered_head=seen_head)
        card.pr_head_sha = result.sha
        await self._repo.add_approval(card.id, decided_by)
        card.decided_by = decided_by
        card.decided_at = now
        await self._finish_pr_accept(card=card, topic=topic, headline=headline)
        logger.warning(
            "card %s: PR #%s force-merged by %s (checks: %s)",
            card.id,
            number,
            decided_by,
            checks_at_merge,
        )
        self._notify_merge_result(
            topic,
            say("forceMerged", actor=f"<@{decided_by}>", pr=number),
            meta=notice(
                EVENT_FORCE_MERGED,
                severity=SEVERITY_WARN,
                who=WHO_HUMAN,
                detail=say(
                    "forceMergedDetail",
                    verdict=verdict,
                    checks=checks_at_merge,
                    reason=tail_reason,
                    url=card.pr_url or "",
                ),
                detail_label=say("labelForceMergeRecord"),
            ),
        )
        return card

    async def void(
        self, *, card_id: uuid.UUID, decided_by: str, note: str = ""
    ) -> AcceptCard:
        """人工作废一张未决的验收卡 (pending_gate 孤儿卡出口, 2026-08-11).

        这是**唯一**能把非终态卡强制收尾的人工动作。它存在的理由是 `create_card`
        的互斥：一张卡卡在 `pending_gate` / `conflict` 上，整个话题就再也递不出
        第二张卡，而 accept/reject/revoke/reassign 四条路由对这些状态全部是拒绝
        的——出口是零。

        ⚠️ **它把卡置为终态，不是"放行到 pending"**。放行等于让卡面的绿勾替一段
        从没被检查过的代码背书；作废 + 重递效果一样而且安全，这条区别是本功能的
        设计前提，不要"优化"掉。

        授权：卡上的验收人、项目所有者、团队的所有者和管理员。它是授权类动作，所以芝士在
        collaborative 模式下被 `_forbid_ai` 挡住（跟 accept/approve 同一条线）
        —— 路由也**故意不进** `app/main.py` 的 `_CHEESE_WRITE_PATHS`。
        """
        card = await self._card_or_404(card_id)
        if card.status not in archive.OPEN_CARD_STATUSES:
            raise ValidationError(say("reviewEndedNoVoid"))

        topic = await self._topic_or_404(card.topic_id)
        project = await self._projects.get(topic.project_id)
        self._forbid_ai(project, decided_by, "作废")

        if decided_by != card.reviewer_handle and not await MemberService(
            self._session
        ).manages(topic.project_id, decided_by):
            raise ForbiddenError(say("voidForbidden"))

        await self._cancel_queued_accept(card)
        was = card.status
        reason = say("cardVoidedReason", note=note.strip()) if note.strip() else ""
        headline = (
            f"{VOIDED_PREFIX}：<@{decided_by}> 作废于状态「{was}」。"
            f"话题可以重新提交审阅。{reason}"
        )
        if archive.pr_left_open(card):
            # 跟归档收敛同一条产品判断 (review/archive.py 的模块 docstring)：平台
            # 不拿别人的 token 去关别人名下的 PR。停止跟进 + 留痕。
            headline = (
                f"{VOIDED_PREFIX}：<@{decided_by}> 作废了这次审阅，平台已停止跟进 "
                f"PR #{card.pr_number}。PR 未合并，仍在 GitHub 上打开，需要人决定"
                f"合并还是关闭：{card.pr_url or '（无链接）'}{reason}"
            )
        card.status = AcceptStatus.revoked
        notes.record(
            card, notes.NoteCode.voided, archive.prefix_note(card.note, headline)
        )
        # 只在空的时候补：作废人始终写在 note 里，已有的决议痕迹不覆盖。
        if card.decided_by is None:
            card.decided_by = decided_by
        if card.decided_at is None:
            card.decided_at = datetime.now(UTC)

        await self._session.flush()
        await self._session.refresh(card)

        # 作废的是这张卡，落点就是这张卡（结论 14）：房间主线那一档只留给为房间
        # 本身递的卡（`task_id` 空）。
        landed = landing(
            EventAbout.task if card.task_id is not None else EventAbout.room,
            project_id=topic.project_id,
            room_id=topic.id,
            task_id=card.task_id,
        )
        await BlockRepository(self._session).add(
            project_id=landed.project_id,
            topic_id=landed.topic_id,
            task_id=landed.task_id,
            author="cheese",
            author_type=AuthorType.platform,
            content=say("cardVoided", actor=f"<@{decided_by}>"),
            kind=BlockKind.event,
            meta={
                "platform": True,
                **notice(
                    EVENT_CARD_VOIDED,
                    severity=SEVERITY_INFO,
                    who=WHO_CHEESE,
                    detail=say("cardVoidedDetail", state=f"{was}", reason=reason),
                    detail_label=say("labelVoidNote"),
                ),
            },
        )
        return card
