"""Accept-card / Review business logic — the 验收 state machine.

Spec §4.4 (AI 不能验收自己做的东西), §6.3 (采纳即归档/merge, 且可撤销).
This is deterministic platform code, not AI.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationError
from app.domain.membership.repositories import MemberRepository
from app.domain.project.models import AiMode, ProjectRole
from app.domain.project.repositories import ProjectRepository
from app.domain.review.models import AcceptCard, AcceptStatus
from app.domain.review.repositories import AcceptCardRepository
from app.domain.task.repositories import TaskRepository, TaskTemplateRepository
from app.domain.topic.models import Topic, TopicStatus
from app.domain.topic.repositories import TopicRepository


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
        # already pending, re-route it (改验收人) instead of stacking a new one.
        existing = await self._repo.list_for_topic(topic_id)
        if any(c.status == AcceptStatus.pending for c in existing):
            raise ValidationError("已有待处理的验收卡，请改验收人而不是再递一张")
        return await self._repo.add(
            topic_id=topic_id,
            reviewer_handle=reviewer_handle,
            routing_reason=routing_reason,
        )

    async def list_for_topic(self, topic_id: uuid.UUID) -> tuple[list[AcceptCard], int]:
        cards = await self._repo.list_for_topic(topic_id)
        return cards, len(cards)

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

    async def accept(self, *, card_id: uuid.UUID, decided_by: str) -> AcceptCard:
        card = await self._card_or_404(card_id)
        if card.status != AcceptStatus.pending:
            raise ValidationError("验收卡已处理，不能重复验收")

        topic = await self._topic_or_404(card.topic_id)
        # 采纳一次性 (spec §6.3): can't re-accept an already-archived topic.
        if topic.status == TopicStatus.archived:
            raise ValidationError("话题已归档，不能重复采纳")
        project = await self._projects.get(topic.project_id)
        # Hard rule (spec §4.4): in collaborative mode AI cannot accept its
        # own work — a human must. Autonomous mode allows it.
        if (
            project is not None
            and project.ai_mode == AiMode.collaborative
            and decided_by == "cheese"
        ):
            raise ValidationError("AI 不能验收自己做的东西，必须有人来")

        # Institution protocol from linked Task Templates (spec §4.2).
        await self._enforce_protocol(topic, decided_by)

        now = datetime.now(UTC)
        card.status = AcceptStatus.accepted
        card.decided_by = decided_by
        card.decided_at = now

        # 采纳 = merge (spec §6.3): merge the topic's branch into the base. Best
        # effort — a git conflict / missing branch must not block the archival.
        from app.domain.workspace import service as ws

        try:
            ws.merge_topic(topic.project_id, topic.id)
        except Exception:  # noqa: BLE001 — git is a side channel, never fatal here
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
