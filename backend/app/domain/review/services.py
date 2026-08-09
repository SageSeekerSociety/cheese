"""Accept-card / Review business logic — the 验收 state machine.

Spec §4.4 (AI 不能验收自己做的东西), §6.3 (采纳即归档/merge, 且可撤销).
This is deterministic platform code, not AI.
"""

import asyncio
import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import async_session_factory
from app.core.errors import ForbiddenError, NotFoundError, ValidationError
from app.domain.cx_task.repositories import TaskRepository, TaskTemplateRepository
from app.domain.membership.repositories import MemberRepository
from app.domain.project.models import AiMode, Project, ProjectRole
from app.domain.project.repositories import ProjectRepository
from app.domain.review.models import AcceptCard, AcceptStatus
from app.domain.review.repositories import AcceptCardRepository
from app.domain.review.schemas import AcceptCardOut
from app.domain.topic.models import Topic, TopicStatus
from app.domain.topic.repositories import TopicRepository
from app.domain.webhook import service as webhook_service

logger = logging.getLogger("cheesex.review")

_MERGE_FAILED_MESSAGE = (
    "Acceptance could not complete because the topic could not be merged. "
    "The card remains pending and the topic stays active; repair the workspace "
    "and retry."
)


def _pr_trailers(topic: Topic, decided_by: str) -> str:
    """两阶段采纳 (PR迭代式) 设计要点5: 标清芝士代表谁开的 PR. Requested-by = 话题
    发起人 (Topic.created_by)，Reviewed-by = 批准人 (AcceptCard.decided_by)。"""
    lines = []
    if topic.created_by:
        lines.append(f"Requested-by: {topic.created_by}")
    lines.append(f"Reviewed-by: {decided_by}")
    lines.append(f"Cheese-Topic: {topic.id}")
    return "\n".join(lines)


def _pr_body(topic: Topic, decided_by: str) -> str:
    return (
        f"由芝士代表 {decided_by} 通过 CheeseX 平台两阶段采纳流程开出。\n\n"
        f"{_pr_trailers(topic, decided_by)}"
    )


def _pr_merge_commit_message(topic: Topic, decided_by: str) -> str:
    return f"采纳 {topic.title}\n\n{_pr_trailers(topic, decided_by)}"


def approvals_required_of(project: Project | None) -> int:
    """主分支保护 (spec §4.4): distinct approvals an accept needs. Default 1 —
    the accepter's own accept counts, so unconfigured projects are unchanged."""
    if project is None:
        return 1
    try:
        return max(1, int((project.settings or {}).get("approvals_required") or 1))
    except (TypeError, ValueError):
        return 1


def check_command_of(project: Project | None) -> str | None:
    """机器闸门 (eval C2): the project's configured check command, or None."""
    if project is None:
        return None
    cmd = str((project.settings or {}).get("check_command") or "").strip()
    return cmd or None


class AcceptService:
    def __init__(self, session: AsyncSession):
        self._session = session
        self._repo = AcceptCardRepository(session)
        self._topics = TopicRepository(session)
        self._projects = ProjectRepository(session)

    async def _topic_or_404(self, topic_id: uuid.UUID) -> Topic:
        topic = await self._topics.get(topic_id)
        if topic is None:
            raise NotFoundError("Topic not found")
        return topic

    async def _card_or_404(self, card_id: uuid.UUID) -> AcceptCard:
        card = await self._repo.get(card_id)
        if card is None:
            raise NotFoundError("Accept card not found")
        return card

    async def create_card(
        self,
        *,
        topic_id: uuid.UUID,
        reviewer_handle: str,
        routing_reason: str = "",
    ) -> AcceptCard:
        topic = await self._topic_or_404(topic_id)
        # 采纳是一次性交付 (spec §6.3): a frozen topic can't be re-submitted.
        if topic.status == TopicStatus.archived:
            raise ValidationError("话题已归档，不能再递验收卡")
        # One reviewer at a time, not a broadcast (spec §4.4): if a card is
        # already pending (or still behind the gate), re-route / wait instead
        # of stacking a new one.
        existing = await self._repo.list_for_topic(topic_id)
        if any(
            c.status in (AcceptStatus.pending, AcceptStatus.pending_gate)
            for c in existing
        ):
            raise ValidationError("已有待处理的验收卡，请改验收人而不是再递一张")
        # 机器闸门 (spec §4.4/§9, eval C2): with a check_command configured the
        # card is born pending_gate; the platform runs the check in the topic's
        # workspace and only a green result promotes it to pending. The check
        # runs in the background (it can take minutes) — see review/gate.py.
        project = await self._projects.get(topic.project_id)
        status = (
            AcceptStatus.pending_gate
            if check_command_of(project)
            else AcceptStatus.pending
        )
        return await self._repo.add(
            topic_id=topic_id,
            reviewer_handle=reviewer_handle,
            routing_reason=routing_reason,
            status=status,
        )

    async def gate_plan(self, topic_id: uuid.UUID) -> tuple[uuid.UUID, str | None]:
        """(project_id, check_command) for a topic — what the gate should run."""
        topic = await self._topic_or_404(topic_id)
        project = await self._projects.get(topic.project_id)
        return topic.project_id, check_command_of(project)

    async def finish_gate(
        self, *, card_id: uuid.UUID, passed: bool, output_tail: str
    ) -> AcceptCard:
        """Settle a pending_gate card: green → pending (卡片这才递到验收人手上),
        red → gate_failed (卡片作废，芝士被 nudge 去修)."""
        card = await self._card_or_404(card_id)
        if card.status != AcceptStatus.pending_gate:
            raise ValidationError("只有等待检查的验收卡能记录检查结果")
        card.gate_output = output_tail
        if passed:
            card.status = AcceptStatus.pending
            card.gate_passed_at = datetime.now(UTC)
        else:
            card.status = AcceptStatus.gate_failed
        await self._session.flush()
        await self._session.refresh(card)
        return card

    async def list_for_topic(self, topic_id: uuid.UUID) -> tuple[list[AcceptCard], int]:
        cards = await self._repo.list_for_topic(topic_id)
        return cards, len(cards)

    async def describe(self, card: AcceptCard) -> dict:
        """AcceptCardOut payload enriched with the vote state (approvals live in
        their own table; the requirement is a project setting)."""
        data = AcceptCardOut.model_validate(card).model_dump(mode="json")
        data["approvals"] = await self._repo.list_approver_handles(card.id)
        topic = await self._topics.get(card.topic_id)
        project = (
            await self._projects.get(topic.project_id) if topic is not None else None
        )
        data["approvals_required"] = approvals_required_of(project)
        return data

    async def _enforce_protocol(self, topic: Topic, decided_by: str) -> None:
        """Task Template conditions (spec §4.2/§4.4): if a linked template
        requires a topic like this to be accepted by a mentor, enforce it."""
        links = await self._projects.list_links(topic.project_id)
        if not links:
            return
        tasks = TaskRepository(self._session)
        templates = TaskTemplateRepository(self._session)
        conditions: list[dict] = []
        for link in links:
            task = await tasks.get(link.task_id)
            if task is None:
                continue
            tmpl = await templates.get(task.template_id)
            if tmpl is not None:
                conditions.extend(tmpl.conditions or [])
        # A condition applies only when its required_topic is non-empty AND
        # matches this topic. An empty required_topic must NOT match every topic
        # (that would force mentor review on the whole project).
        needs_mentor = any(
            c.get("reviewer_role") == "mentor"
            and (c.get("required_topic") or "").strip()
            and c["required_topic"].strip() in topic.title
            for c in conditions
        )
        if not needs_mentor:
            return
        members = await MemberRepository(self._session).list_for_project(
            topic.project_id
        )
        mentors = {m.user_handle for m in members if m.role == ProjectRole.mentor}
        if decided_by not in mentors:
            raise ValidationError("按机构协议，这个话题须由导师验收")

    async def reassign(
        self, *, card_id: uuid.UUID, reviewer_handle: str, reason: str = ""
    ) -> AcceptCard:
        """改验收人 (spec §4.4): anyone can re-route a pending accept card to a
        different reviewer."""
        card = await self._card_or_404(card_id)
        if card.status != AcceptStatus.pending:
            raise ValidationError("只有待处理的验收卡能改验收人")
        card.reviewer_handle = reviewer_handle
        if reason:
            card.routing_reason = reason
        await self._session.flush()
        await self._session.refresh(card)
        return card

    def _forbid_ai(self, project: Project | None, handle: str, action: str) -> None:
        """Hard rule (spec §4.4): in collaborative mode AI cannot accept (or
        vote for) its own work — a human must. Autonomous mode allows it."""
        if (
            project is not None
            and project.ai_mode == AiMode.collaborative
            and handle == "cheese"
        ):
            raise ValidationError(f"AI 不能{action}自己做的东西，必须有人来")

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
            raise ValidationError("验收卡已关闭，不能再批准")
        topic = await self._topic_or_404(card.topic_id)
        project = await self._projects.get(topic.project_id)
        self._forbid_ai(project, approver_handle, "批准")
        await self._repo.add_approval(card_id, approver_handle)
        return card

    def _notify_merge_result(self, topic: Topic, content: str) -> None:
        """merge 后结果回房间: post the accept's merge outcome into the topic
        timeline via the webhook primitive's internal function (卡1) — no HTTP
        hop, no token check, this call is trusted by construction. Uses its
        own session (async_session_factory), independent of self._session, so
        the notice lands even when the accept itself is about to be rolled
        back by a raised ValidationError.

        Fire-and-forget (asyncio.create_task, mirroring workspace/service.py's
        watch_dogfood_push): post_with_retries can sleep up to 35s across its
        retries, and the accepter's HTTP response must not wait on a room
        notification succeeding — only on the merge itself."""
        asyncio.get_running_loop().create_task(
            webhook_service.post_with_retries(
                async_session_factory,
                project_id=topic.project_id,
                topic_id=topic.id,
                content=content,
                source="accept",
            )
        )

    async def accept(self, *, card_id: uuid.UUID, decided_by: str) -> AcceptCard:
        card = await self._card_or_404(card_id)
        # 机器闸门 (eval C2): the card isn't in the reviewer's hands yet / died.
        if card.status == AcceptStatus.pending_gate:
            raise ValidationError("平台检查还在进行中，检查通过后才能采纳")
        if card.status == AcceptStatus.gate_failed:
            raise ValidationError("平台检查未通过，等芝士修复后重新递卡")
        # pending → first attempt; conflict → retry after 芝士 resolved.
        if card.status not in (AcceptStatus.pending, AcceptStatus.conflict):
            raise ValidationError("验收卡已处理，不能重复验收")
        # 递给某个具体的人 (spec §4.4): only the routed reviewer may accept —
        # decided_by is the caller's verified actor handle, never body-trusted.
        if decided_by != card.reviewer_handle:
            raise ForbiddenError("你不是这张验收卡指定的验收人，无权采纳")

        topic = await self._topic_or_404(card.topic_id)
        # 采纳一次性 (spec §6.3): can't re-accept an already-archived topic.
        if topic.status == TopicStatus.archived:
            raise ValidationError("话题已归档，不能重复采纳")
        project = await self._projects.get(topic.project_id)
        self._forbid_ai(project, decided_by, "验收")

        # Institution protocol from linked Task Templates (spec §4.2).
        await self._enforce_protocol(topic, decided_by)

        # 主分支保护 (spec §4.4): the accept itself counts as the accepter's
        # vote (default requirement of 1 ⇒ 现行为不变); short of votes the whole
        # transaction rolls back and nothing merges.
        approvers = await self._repo.list_approver_handles(card_id)
        # Count this decision as a vote without persisting it yet. A merge can
        # fail outside SQLAlchemy; delaying the write keeps even callers that
        # catch ValidationError from accidentally committing a failed accept.
        votes = len(set(approvers) | {decided_by})
        required = approvals_required_of(project)
        if votes < required:
            raise ValidationError(
                f"批准人数不足，还差 {required - votes} 票（{votes}/{required}）"
            )

        # PR-based accept (#188 §5.1): a card that ALREADY rides a real PR
        # (published fire-and-forget by pr_publish.py when the card turned
        # pending, behind settings.accept_via_pr — off by default) is accepted
        # by merging THAT PR via the API, never by opening a second one. Falls
        # through to the local path when GitHub is unreachable (availability
        # must never regress) — the merge commit landing on main closes the
        # PR anyway.
        if card.pr_number is not None:
            settled = await self._accept_via_pr(card, topic, decided_by)
            if settled is not None:
                return settled

        # 两阶段采纳 (PR迭代式, 2026-08-09): no PR yet — try opening a NEW one via
        # the approver's own connected GitHub token. Any missing prerequisite
        # (no connected token / no connected repo) or any GitHub-side failure
        # (push/API) degrades to the old direct-merge path below — a normal
        # degrade, never an accept failure (拍板 decision 2). Resolving the
        # prerequisites themselves must degrade the same way: a DB hiccup here
        # is exactly as "mechanism unavailable" as a missing token.
        try:
            pr_prereqs = await self._resolve_pr_prerequisites(topic, decided_by)
        except Exception as exc:  # noqa: BLE001 — degrade, don't fail the accept
            logger.warning(
                "could not resolve PR prerequisites for topic=%s, degrading to "
                "direct merge: %s",
                topic.id,
                exc,
            )
            pr_prereqs = None
        if pr_prereqs is not None:
            token, pr_owner, pr_repo = pr_prereqs
            try:
                return await self._open_pr_for_accept(
                    card=card,
                    topic=topic,
                    decided_by=decided_by,
                    token=token,
                    owner=pr_owner,
                    repo=pr_repo,
                )
            except Exception as exc:  # noqa: BLE001 — degrade, don't fail the accept
                logger.warning(
                    "PR-based accept unavailable for topic=%s, degrading to "
                    "direct merge: %s",
                    topic.id,
                    exc,
                )
        # 采纳 = merge (spec §6.3) — and the merge DECIDES the outcome. A
        # conflict must never silently archive the topic while the work is
        # stranded on its branch (that shipped a lie once): the card moves to
        # `conflict`, 芝士 gets dispatched to resolve, a human retries.
        from app.domain.workspace import service as ws

        try:
            merged = await asyncio.to_thread(ws.merge_topic, topic.project_id, topic.id)
        except Exception as exc:  # noqa: BLE001 — surface, don't invent success
            logger.exception(
                "accept merge raised for project=%s topic=%s",
                topic.project_id,
                topic.id,
            )
            self._notify_merge_result(
                topic, f"❌ 采纳未完成：合并出错，请检查工作区状态。（{exc}）"
            )
            raise ValidationError(_MERGE_FAILED_MESSAGE) from exc

        if not merged.get("merged"):
            # The conflicts key means an attempted merge failed. An empty list
            # is still a failure: Git can error before it identifies paths.
            if "conflicts" in merged and merged.get("conflicts"):
                await self._repo.add_approval(card_id, decided_by)
                card.status = AcceptStatus.conflict
                card.decided_by = decided_by
                card.decided_at = datetime.now(UTC)
                card.note = merged.get("reason", "")
                await self._session.flush()
                await self._session.refresh(card)
                conflict_msg = "❌ 采纳未完成：合并冲突，需要芝士处理后重试。"
                if card.note:
                    conflict_msg += f"\n{card.note}"
                self._notify_merge_result(topic, conflict_msg)
                return card

            # Discussion-only topics and a topic already on the base branch
            # intentionally have nothing to merge and remain acceptable.
            if merged.get("noop") is not True:
                logger.error(
                    "accept merge failed for project=%s topic=%s result=%r",
                    topic.project_id,
                    topic.id,
                    merged,
                )
                self._notify_merge_result(
                    topic, "❌ 采纳未完成：合并失败，请检查工作区状态。"
                )
                raise ValidationError(_MERGE_FAILED_MESSAGE)

        # Merged (or nothing to merge — e.g. a discussion topic with no branch
        # work): the accept completes as before.
        await self._repo.add_approval(card_id, decided_by)
        now = datetime.now(UTC)
        card.status = AcceptStatus.accepted
        card.decided_by = decided_by
        card.decided_at = now
        # 采纳即上线: propagate the merge to the upstream repo. The outcome is
        # RECORDED on the card — a push that only landed a side branch (or failed
        # outright) used to be swallowed here, so the accept looked complete while
        # nothing reached the upstream and no one could tell why.
        if merged.get("merged"):
            try:
                pushed = await asyncio.to_thread(
                    ws.push_back, topic.project_id, topic.id
                )
            except Exception as exc:  # noqa: BLE001 — never fail the accept itself
                card.note = f"上游回推失败：{exc}"[:2000]
            else:
                mode = pushed.get("mode")
                if mode == "upstream":
                    card.note = f"已合并并推送到上游 {pushed.get('target')}"
                elif mode == "branch":
                    why = (pushed.get("reason") or "").strip()
                    card.note = (
                        f"上游 {pushed.get('target')} 未能直接推送，"
                        f"已推分支 {pushed.get('branch')} 待合并"
                        + (f"（{why[-200:]}）" if why else "")
                    )[:2000]
                elif mode == "blocked":
                    card.note = str(pushed.get("reason") or "")[:2000]
                elif mode == "none":
                    card.note = str(pushed.get("reason") or "")[:2000]

        # Topic is done → free its long-lived sandbox container (it would be
        # recreated on demand if the archived topic is ever resumed).
        try:
            ws.stop_topic_container(topic.id)
        except Exception:  # noqa: BLE001 — best effort, never fatal
            pass

        # 采纳即归档 (spec §6.3).
        topic.status = TopicStatus.archived
        topic.accepted_by = decided_by
        topic.accepted_at = now
        topic.archived_at = now

        await self._session.flush()
        await self._session.refresh(card)
        success_msg = f"✅ 话题已被 {decided_by} 采纳并合并。"
        if card.note:
            success_msg += f"\n{card.note}"
        self._notify_merge_result(topic, success_msg)
        return card

    # ---- 两阶段采纳 (PR迭代式, 2026-08-09) ----------------------------------

    async def _resolve_pr_prerequisites(
        self, topic: Topic, decided_by: str
    ) -> tuple[str, str, str] | None:
        """(token, owner, repo) when the PR path is usable — a connected
        GitHub token for the approver AND a project connected to a repo
        (#192). Either missing → None, and the caller degrades to the old
        direct-merge path (拍板 decision 2: this is normal, not an error)."""
        from app.domain.oauth.services import get_github_user_token_for_handle
        from app.domain.project.repositories import ProjectGitInstallationRepository

        token = await get_github_user_token_for_handle(self._session, decided_by)
        if not token:
            return None
        installation = await ProjectGitInstallationRepository(
            self._session
        ).get_by_project(topic.project_id)
        if installation is None or "/" not in installation.repo:
            return None
        owner, _, repo_name = installation.repo.partition("/")
        return token, owner, repo_name

    async def _open_pr_for_accept(
        self,
        *,
        card: AcceptCard,
        topic: Topic,
        decided_by: str,
        token: str,
        owner: str,
        repo: str,
    ) -> AcceptCard:
        """Push the topic branch, open a NEW real PR, and hand the rest to the
        scheduler's PrPollRunner (SchedulerService.poll_open_prs /
        advance_pr_card) — this call does NOT wait for CI. Topic stays
        active; no merge_topic()/push_back()/archive here (拍板 decision 3:
        archive gates on the PR *and* its triggered deploy both succeeding).
        Distinct from `_accept_via_pr` below (#188 §5.1), which merges a PR
        that ALREADY exists on the card rather than opening a new one."""
        from app.domain.review import github_pr
        from app.domain.workspace import service as ws

        remote_branch = github_pr.pr_branch_name(topic.id)
        pushed = await asyncio.to_thread(
            ws.push_topic_branch_for_github_pr,
            topic.project_id,
            topic.id,
            owner=owner,
            repo=repo,
            remote_branch=remote_branch,
            token=token,
        )
        base = await asyncio.to_thread(ws.pr_base_branch, topic.project_id)
        client = github_pr.default_client()
        pr = await client.open_pull_request(
            owner=owner,
            repo=repo,
            head=remote_branch,
            base=base,
            title=f"[cheesex] {topic.title}"[:250],
            body=_pr_body(topic, decided_by),
            token=token,
        )

        await self._repo.add_approval(card.id, decided_by)
        now = datetime.now(UTC)
        card.status = AcceptStatus.pr_open
        card.decided_by = decided_by
        card.decided_at = now
        card.pr_number = pr.number
        card.pr_repo = f"{owner}/{repo}"
        card.pr_url = pr.url
        card.pr_head_sha = pushed["head_sha"]
        card.pr_merged_at = None
        card.note = f"已开 PR #{pr.number}，等 CI 转绿后自动合并：{pr.url}"
        await self._session.flush()
        await self._session.refresh(card)
        self._notify_merge_result(
            topic,
            f"🔁 {decided_by} 采纳了这个话题，已开 PR #{pr.number} 等待 CI：{pr.url}\n"
            "话题保持 active（容器不停），PR 合并且部署也成功后才会归档。",
        )
        return card

    async def advance_pr_card(
        self, card_id: uuid.UUID, *, chat_service, runner
    ) -> None:
        """One polling step for a pr_open card — check the PR's CI, merge
        when green, then check the deploy workflow the merge triggers, and
        only archive once THAT is green too (2026-08-09 拍板: merge alone
        doesn't count). Called by SchedulerService.poll_open_prs(); never
        raises for a transient GitHub hiccup — the next poll just retries."""
        card = await self._card_or_404(card_id)
        if card.status != AcceptStatus.pr_open:
            return
        if not card.pr_repo or card.pr_number is None or not card.pr_head_sha:
            logger.error("pr_open card %s missing PR fields, cannot poll", card.id)
            return
        topic = await self._topic_or_404(card.topic_id)
        owner, _, repo = card.pr_repo.partition("/")

        from app.domain.oauth.services import get_github_user_token_for_handle

        token = await get_github_user_token_for_handle(
            self._session, card.decided_by or ""
        )
        if not token:
            logger.warning(
                "pr_open card %s has no usable GitHub token anymore; skipping "
                "this poll (will retry next tick)",
                card.id,
            )
            return

        from app.domain.review import github_pr

        client = github_pr.default_client()
        try:
            if card.pr_merged_at is None:
                await self._advance_pr_checks(
                    card=card,
                    topic=topic,
                    owner=owner,
                    repo=repo,
                    token=token,
                    client=client,
                    chat_service=chat_service,
                    runner=runner,
                )
            else:
                await self._advance_deploy_checks(
                    card=card,
                    topic=topic,
                    owner=owner,
                    repo=repo,
                    token=token,
                    client=client,
                )
        except github_pr.GitHubPrError as exc:
            logger.warning(
                "GitHub API hiccup polling pr_open card %s: %s — retrying next tick",
                card.id,
                exc,
            )

    async def _advance_pr_checks(
        self,
        *,
        card: AcceptCard,
        topic: Topic,
        owner: str,
        repo: str,
        token: str,
        client,
        chat_service,
        runner,
    ) -> None:
        """Stage 1: the PR itself hasn't merged yet."""
        live_head = await client.pull_request_head_sha(
            owner=owner, repo=repo, number=card.pr_number, token=token
        )
        if live_head != card.pr_head_sha:
            # 芝士 pushed a new commit — track it, and clear any "already
            # nudged" marker so a fresh failure on the NEW commit still
            # notifies (see the note-based dedup in _nudge_pr_fix).
            card.pr_head_sha = live_head
            card.note = ""
            await self._session.flush()

        state, tail = await client.check_state(
            owner=owner, repo=repo, ref=card.pr_head_sha, token=token
        )
        if state == "pending":
            return
        if state == "failure":
            self._nudge_pr_fix(
                card=card,
                topic=topic,
                tail=tail,
                stage="CI",
                chat_service=chat_service,
                runner=runner,
            )
            await self._session.flush()
            return

        # Green → merge now. Trailers go on the merge commit too, not just
        # the PR description (2026-08-09 设计要点5: 标清芝士代表谁).
        message = _pr_merge_commit_message(topic, card.decided_by or "")
        merge_sha = await client.merge_pull_request(
            owner=owner,
            repo=repo,
            number=card.pr_number,
            token=token,
            commit_message=message,
        )
        if merge_sha is None:
            return  # not mergeable yet (behind base etc.) — retry next tick
        card.pr_merged_at = datetime.now(UTC)
        card.pr_head_sha = merge_sha  # now tracking the merge commit (stage 2)
        card.note = f"PR #{card.pr_number} 检查全绿，已自动合并，等部署也成功后才归档。"
        await self._session.flush()
        self._notify_merge_result(
            topic,
            f"✅ PR #{card.pr_number} 的检查全绿，已自动合并。"
            "等部署也成功后话题才会归档。",
        )

    async def _advance_deploy_checks(
        self,
        *,
        card: AcceptCard,
        topic: Topic,
        owner: str,
        repo: str,
        token: str,
        client,
    ) -> None:
        """Stage 2 (2026-08-09 拍板): the PR merged — now wait for the deploy
        workflow it triggered before the topic is allowed to archive."""
        state, tail = await client.workflow_run_state(
            owner=owner,
            repo=repo,
            workflow_file=settings.accept_deploy_workflow_file,
            head_sha=card.pr_head_sha,
            token=token,
        )
        if state == "pending":
            return
        if state == "failure":
            # wangchangxin 建议的默认值，评估后采纳为最终方案：topic 保持
            # active，复用卡5 webhook 通知房间，不自动重试，交给人判断。
            if not card.note.startswith("❌"):
                card.note = f"❌ 部署失败：{tail}"[:2000]
                await self._session.flush()
                self._notify_merge_result(
                    topic,
                    f"❌ PR #{card.pr_number} 已合并，但触发的部署失败：{tail}\n"
                    "话题保持 active，需要人判断下一步（不会自动重试）。",
                )
            return

        await self._finish_pr_accept(card=card, topic=topic)

    def _nudge_pr_fix(
        self,
        *,
        card: AcceptCard,
        topic: Topic,
        tail: str,
        stage: str,
        chat_service,
        runner,
    ) -> None:
        if card.note.startswith("⚠️"):
            return  # already nudged for this exact commit — don't spam every poll
        card.note = f"⚠️ {stage} 检查未通过：{tail}"[:2000]
        runner.submit(
            chat_service,
            topic.id,
            author="system",
            content=(
                f"PR #{card.pr_number}（{card.pr_url}）的{stage}检查没通过：\n"
                f"```\n{tail[:1500]}\n```\n"
                "请在这个话题的工作区里修复问题，提交后推送新 commit 到这个 PR 分支，"
                "检查会自动重新跑；转绿后平台会自动合并 PR。"
            ),
            summon=True,
        )

    async def _finish_pr_accept(self, *, card: AcceptCard, topic: Topic) -> None:
        from app.domain.workspace import service as ws

        now = datetime.now(UTC)
        card.status = AcceptStatus.accepted
        card.note = f"PR #{card.pr_number} 已合并且部署成功：{card.pr_url}"
        try:
            ws.stop_topic_container(topic.id)
        except Exception:  # noqa: BLE001 — best effort, never fatal
            pass
        topic.status = TopicStatus.archived
        topic.accepted_by = card.decided_by
        topic.accepted_at = now
        topic.archived_at = now
        await self._session.flush()
        await self._session.refresh(card)
        self._notify_merge_result(
            topic,
            f"✅ 话题已被 {card.decided_by} 采纳：PR #{card.pr_number} 合并且部署成功，"
            f"话题归档。\n{card.pr_url}",
        )

    async def _accept_via_pr(
        self, card: AcceptCard, topic: Topic, decided_by: str
    ) -> AcceptCard | None:
        """Accept by merging the card's EXISTING GitHub PR (#188 §5.1) —
        distinct from `_open_pr_for_accept` above (两阶段采纳), which opens a
        NEW PR rather than merging one already recorded on the card.

        Returns the settled card (accepted or conflict), or None to fall back
        to the local merge path — config drift and GitHub outages must leave
        accept exactly as available as before PR-based accept existed.
        """
        from app.domain.agent.github_app import github_app_tokens_for_project
        from app.domain.review.github_pr import (
            GitHubPRClient,
            GitHubPRMergeBlocked,
            parse_github_repo,
        )
        from app.domain.workspace import service as ws

        assert card.pr_number is not None
        number = card.pr_number
        # #192: resolve the installation from the card's project, not a global.
        tokens = await github_app_tokens_for_project(topic.project_id, self._session)
        upstream = await asyncio.to_thread(ws.get_upstream, topic.project_id)
        parsed = parse_github_repo(upstream)
        if tokens is None or parsed is None:
            return None  # App unconfigured / upstream changed since the PR opened
        client = GitHubPRClient(*parsed, tokens)
        branch = ws.branch_for_topic(topic.id)

        try:
            # Someone may have handled the PR on GitHub directly — respect it.
            view = await client.pr_view(number)
            if view.get("merged"):
                return await self._settle_pr_accept(
                    card, topic, decided_by, note=f"PR #{number} 已在 GitHub 合并"
                )
            if view.get("state") == "closed":
                logger.warning(
                    "PR #%s for card %s was closed unmerged — falling back "
                    "to the local merge path",
                    number,
                    card.id,
                )
                return None

            # Re-push first: last-minute worktree edits and conflict fixes must
            # be what actually merges.
            token, _ = await tokens.write_token()
            await asyncio.to_thread(
                ws.push_topic_branch, topic.project_id, topic.id, token
            )
            await client.merge_pr(
                number,
                title=f"采纳 {branch} → {view.get('base', {}).get('ref', 'main')} "
                f"(#{number})",
                message=f"验收人：{decided_by}\n\n{card.routing_reason}".strip(),
            )
        except GitHubPRMergeBlocked:
            # Same contract as a local merge conflict: card → conflict, 芝士 is
            # dispatched (routes/accept.py), human retries. Sync the local base
            # first so the materialized conflict matches what GitHub sees.
            try:
                await asyncio.to_thread(ws.sync_upstream, topic.project_id)
            except Exception:  # noqa: BLE001 — the conflict flow still works on a stale base
                logger.exception(
                    "sync_upstream after merge refusal failed for %s", topic.id
                )
            await self._repo.add_approval(card.id, decided_by)
            card.status = AcceptStatus.conflict
            card.decided_by = decided_by
            card.decided_at = datetime.now(UTC)
            card.note = f"PR #{number} 合并冲突，已派芝士解决"
            await self._session.flush()
            await self._session.refresh(card)
            return card
        except Exception as exc:  # noqa: BLE001 — any non-conflict failure falls back
            logger.exception(
                "PR accept failed for card %s (PR #%s): %s — falling back "
                "to the local merge path",
                card.id,
                number,
                exc,
            )
            return None

        return await self._settle_pr_accept(
            card, topic, decided_by, note=f"已通过 PR #{number} 合并到上游"
        )

    async def _settle_pr_accept(
        self, card: AcceptCard, topic: Topic, decided_by: str, *, note: str
    ) -> AcceptCard:
        """Post-merge bookkeeping shared by the PR path: sync the platform's
        main down from upstream (the merge happened THERE), then archive."""
        from app.domain.workspace import service as ws

        try:
            synced = await asyncio.to_thread(ws.sync_upstream, topic.project_id)
            if not synced.get("synced"):
                note += f"；本地同步待补：{synced.get('reason', '')}"
        except Exception as exc:  # noqa: BLE001 — never fail the accept itself
            note += f"；本地同步待补：{exc}"

        await self._repo.add_approval(card.id, decided_by)
        now = datetime.now(UTC)
        card.status = AcceptStatus.accepted
        card.decided_by = decided_by
        card.decided_at = now
        card.note = note[:2000]

        try:
            ws.stop_topic_container(topic.id)
        except Exception:  # noqa: BLE001 — best effort, never fatal
            pass

        # 采纳即归档 (spec §6.3).
        topic.status = TopicStatus.archived
        topic.accepted_by = decided_by
        topic.accepted_at = now
        topic.archived_at = now

        await self._session.flush()
        await self._session.refresh(card)
        return card

    async def reject(
        self, *, card_id: uuid.UUID, decided_by: str, note: str = ""
    ) -> AcceptCard:
        card = await self._card_or_404(card_id)
        if card.status != AcceptStatus.pending:
            raise ValidationError("验收卡已处理，不能重复决议")
        if decided_by != card.reviewer_handle:
            raise ForbiddenError("你不是这张验收卡指定的验收人，无权驳回")

        card.status = AcceptStatus.rejected
        card.decided_by = decided_by
        card.decided_at = datetime.now(UTC)
        card.note = note

        # Topic stays active on rejection.
        await self._session.flush()
        await self._session.refresh(card)
        return card

    async def revoke(self, *, card_id: uuid.UUID, decided_by: str) -> AcceptCard:
        card = await self._card_or_404(card_id)
        # Accept is revocable (spec §6.3): only an accepted card can be revoked.
        if card.status != AcceptStatus.accepted:
            raise ValidationError("只有已验收的卡才能撤销")

        # Only the person who accepted it, or the project owner/lead, may revoke
        # — not any arbitrary handle.
        topic = await self._topic_or_404(card.topic_id)
        project = await self._projects.get(topic.project_id)
        allowed = {card.decided_by}
        if project is not None and project.owner_handle:
            allowed.add(project.owner_handle)
        members = await MemberRepository(self._session).list_for_project(
            topic.project_id
        )
        allowed |= {m.user_handle for m in members if m.role == ProjectRole.lead}
        if decided_by not in allowed:
            raise ValidationError("只有原采纳人或项目组长能撤销采纳")

        card.status = AcceptStatus.revoked
        card.decided_by = decided_by
        card.decided_at = datetime.now(UTC)

        # Un-archive the topic: back to active, clear accept/archive markers.
        topic.status = TopicStatus.active
        topic.accepted_by = None
        topic.accepted_at = None
        topic.archived_at = None

        await self._session.flush()
        await self._session.refresh(card)
        return card
