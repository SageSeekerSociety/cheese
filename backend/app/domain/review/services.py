"""Accept-card / Review business logic — the 验收 state machine.

Spec §4.4 (AI 不能验收自己做的东西), §6.3 (采纳即归档/merge, 且可撤销).
This is deterministic platform code, not AI.
"""

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

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

logger = logging.getLogger("cheesex.review")

_MERGE_FAILED_MESSAGE = (
    "Acceptance could not complete because the topic could not be merged. "
    "The card remains pending and the topic stays active; repair the workspace "
    "and retry."
)


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

        # 采纳 = merge (spec §6.3) — and the merge DECIDES the outcome. A
        # conflict must never silently archive the topic while the work is
        # stranded on its branch (that shipped a lie once): the card moves to
        # `conflict`, 芝士 gets dispatched to resolve, a human retries.
        from app.domain.workspace import service as ws

        try:
            merged = ws.merge_topic(topic.project_id, topic.id)
        except Exception as exc:  # noqa: BLE001 — surface, don't invent success
            logger.exception(
                "accept merge raised for project=%s topic=%s",
                topic.project_id,
                topic.id,
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
                pushed = ws.push_back(topic.project_id, topic.id)
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
