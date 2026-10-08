"""`AcceptService` — the 验收 state machine.

State (`_session` / `_repo` / `_topics` / `_projects`) lives here; every
method is a one-line delegation to the module function of the same name in
the sibling module, so behaviour and patch points are unchanged.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import TYPE_CHECKING, NoReturn

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.project.models import Project
from app.domain.review import forge as forge_mod
from app.domain.review import (
    merge_state,
    notes,
    pr_signals,
)
from app.domain.review.merge_state import MergeVerdict, Who
from app.domain.review.models import (
    AcceptCard,
    GateOutcome,
)
from app.domain.review.repositories import AcceptCardRepository
from app.domain.review.services._shared import (
    ProjectRepository,
    ReviewerAdmission,
    TopicRepository,
    _GitHubCredentials,
)
from app.domain.room_task.models import Task
from app.domain.topic.models import Topic

# 同级子模块从**定义它们的模块**取，不经过包 `__init__` 的名字空间：
# `from . import x` 在名字尚未绑定时会回落去导入子模块本身，所以这里的
# 导入顺序不依赖 `__init__.py` 把 `cards` 等名字先绑定好。
from . import accept as _accept
from . import cards, decisions, merge_queue, notices, polling, reviewers

if TYPE_CHECKING:  # `github_pr` stays a lazy import at every call site
    from app.domain.project.protection import BranchProtection
    from app.domain.review.github_pr import PullRequestStatus


class AcceptService:
    def __init__(self, session: AsyncSession):

        self._session = session
        # Set only while a claimed poll runs (): forge clients
        # then give the connection back before each call.
        self._commits_before_remote = False
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
        focus: str = "",
        change_subject: str | None = None,
        change_body: str | None = None,
        artifact: str | None = None,
        new_artifact: str | None = None,
        about: str | None = None,
        deliver: str | None = None,
        deliver_url: str | None = None,
        completes_task: bool = True,
        admits_reviewer: ReviewerAdmission,
    ) -> AcceptCard:
        return await cards.create_card(
            self,
            topic_id=topic_id,
            task_id=task_id,
            reviewer_handle=reviewer_handle,
            focus=focus,
            change_subject=change_subject,
            change_body=change_body,
            artifact=artifact,
            new_artifact=new_artifact,
            about=about,
            deliver=deliver,
            deliver_url=deliver_url,
            completes_task=completes_task,
            admits_reviewer=admits_reviewer,
        )

    async def _announce_filed(
        self, topic: Topic, card: AcceptCard, task: Task, *, artifact: str
    ) -> None:
        return await notices._announce_filed(
            self, topic=topic, card=card, task=task, artifact=artifact
        )

    async def _announce_new_artifact(
        self, topic: Topic, name: str, *, task_id: uuid.UUID
    ) -> None:
        return await notices._announce_new_artifact(
            self, topic=topic, name=name, task_id=task_id
        )

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

    async def describe_many(self, cards_: Sequence[AcceptCard]) -> list[dict]:
        return await cards.describe_many(self, cards=cards_)

    async def _enforce_protocol(self, topic: Topic, decided_by: str) -> None:
        return await cards._enforce_protocol(self, topic=topic, decided_by=decided_by)

    async def reassign(
        self,
        *,
        card_id: uuid.UUID,
        reviewer_handle: str | None = None,
        admits_reviewer: ReviewerAdmission,
    ) -> AcceptCard:
        return await reviewers.reassign(
            self,
            card_id=card_id,
            reviewer_handle=reviewer_handle,
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
        self,
        topic: Topic,
        content: str,
        *,
        task_id: uuid.UUID | None,
        meta: dict | None = None,
    ) -> None:
        return notices._notify_merge_result(
            self, topic=topic, content=content, task_id=task_id, meta=meta
        )

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
        return notices._note_poll_failed(self, card=card, exc=exc)

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
        return await notices._tell_the_reviewer(
            self, card=card, topic=topic, content=content, meta=meta, also=also
        )

    async def _notify_ready(self, card: AcceptCard, topic: Topic) -> None:
        return await notices._notify_ready(self, card=card, topic=topic)

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
        return await notices._note_needs_human(
            self, card=card, topic=topic, reason=reason, explain=explain
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
        return await notices._record_task_nudge(
            self, topic=topic, task=task, content=content, headline=headline, meta=meta
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
        return await notices._note_merge_blocked(
            self,
            card=card,
            topic=topic,
            reason=reason,
            chat_service=chat_service,
            runner=runner,
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
        return notices._ci_nudge(
            self, card=card, tail=tail, stage=stage, owner=owner, repo=repo
        )

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
        return await notices._review_nudge(
            self,
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
        return notices._conflict_nudge(self, card=card, status=status)

    async def _dispatch_nudges(
        self,
        *,
        card: AcceptCard,
        topic: Topic,
        pending: list[pr_signals.PendingNudge],
        chat_service,
        runner,
    ) -> None:
        return await notices._dispatch_nudges(
            self,
            card=card,
            topic=topic,
            pending=pending,
            chat_service=chat_service,
            runner=runner,
        )

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
        return await notices._note_outside_accept_txn(
            self, card_id=card_id, code=code, note=note
        )

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
