"""Topic membership business logic (fusion-design §3).

The roster is a topic's group-room membership: who is in the room, their role,
and who @all/@here reaches. Roles are owner/admin/member (distinct from the
project's lead/member/mentor). 芝士 is a member too, handle `cheese`.

No auth layer exists yet (agent-as-user is P1, fusion-design §2): the acting
user's handle is passed in and authorized against their topic role — owner and
admin may manage the roster, plain members may not.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ForbiddenError, NotFoundError, ValidationError
from app.domain.topic.models import TopicMembership, TopicRole
from app.domain.topic.repositories import TopicRepository
from app.domain.topic_membership.repositories import TopicMembershipRepository

# 芝士 is a member by default — the agent lives in every topic's room. Still a
# string handle here (agent-as-user is P1); no user table is introduced.
CHEESE_HANDLE = "cheese"

# Roles allowed to manage a topic's roster (add / remove / change roles).
_MANAGER_ROLES = frozenset({TopicRole.owner, TopicRole.admin})


class TopicMemberService:
    def __init__(self, session: AsyncSession):
        self._session = session
        self._repo = TopicMembershipRepository(session)
        self._topics = TopicRepository(session)

    async def _ensure_topic(self, topic_id: uuid.UUID) -> None:
        if await self._topics.get(topic_id) is None:
            raise NotFoundError("Topic not found")

    async def _require_manager(self, topic_id: uuid.UUID, actor: str) -> None:
        """Only an owner/admin of THIS topic may mutate its roster."""
        member = await self._repo.get(topic_id=topic_id, member_handle=actor)
        if member is None or member.role not in _MANAGER_ROLES:
            raise ForbiddenError("只有话题的 owner / admin 能管理成员")

    async def seed(self, topic_id: uuid.UUID, *, owner_handle: str | None) -> None:
        """Seed a newborn topic's roster: the creator becomes owner and 芝士
        joins as a member (fusion-design §3). Idempotent — re-seeding never
        duplicates a row. Called at topic-create time, outside the actor check
        (the platform, not a user, seeds)."""
        if owner_handle and owner_handle != CHEESE_HANDLE:
            await self._ensure_member(topic_id, owner_handle, role=TopicRole.owner)
        await self._ensure_member(topic_id, CHEESE_HANDLE, role=TopicRole.member)

    async def seed_root(
        self,
        topic_id: uuid.UUID,
        *,
        owner_handle: str | None,
        member_handles: list[str],
    ) -> None:
        """Seed a project's ROOT topic (总览/项目本体) roster: EVERY project
        member joins the room, so 总览 mirrors the whole project (fusion-design
        §3). The project owner is the topic owner; other members join as members;
        芝士 joins as a member. Idempotent — re-seeding never duplicates a row."""
        await self.seed(topic_id, owner_handle=owner_handle)
        for handle in member_handles:
            if not handle or handle == CHEESE_HANDLE or handle == owner_handle:
                continue
            await self._ensure_member(topic_id, handle, role=TopicRole.member)

    async def _ensure_member(
        self, topic_id: uuid.UUID, handle: str, *, role: TopicRole
    ) -> None:
        if await self._repo.get(topic_id=topic_id, member_handle=handle) is None:
            await self._repo.add(topic_id=topic_id, member_handle=handle, role=role)

    async def list_for_topic(
        self, topic_id: uuid.UUID
    ) -> tuple[list[TopicMembership], int]:
        await self._ensure_topic(topic_id)
        return (
            await self._repo.list_for_topic(topic_id),
            await self._repo.count_for_topic(topic_id),
        )

    async def agent_handles(self, topic_id: uuid.UUID) -> list[str]:
        """Which of this topic's members are agents, in roster order.

        Derived from the execution binding, never a hard-coded handle check: a
        member is an agent iff it carries an ``AgentBinding``. Returns every
        such member, because a room may host more than one 芝士 — callers that
        need "the agent acting here" want :meth:`resolve_agent_handle`.
        """
        from app.domain.identity.repositories import AgentBindingRepository
        from app.domain.user.repositories import UserRepository

        members = await self._repo.list_for_topic(topic_id)
        if not members:
            return []
        by_handle = await UserRepository(self._session).get_by_handles(
            [m.member_handle for m in members]
        )
        agent_ids = await AgentBindingRepository(self._session).agent_user_ids(
            [u.id for u in by_handle.values()]
        )
        return [
            m.member_handle
            for m in members
            if (user := by_handle.get(m.member_handle)) is not None
            and user.id in agent_ids
        ]

    async def resolve_agent_handle(self, topic_id: uuid.UUID) -> str:
        """The handle 芝士 acts under in this topic — for authoring blocks and
        keying its memory.

        Falls back to :data:`CHEESE_HANDLE` when the roster resolves to no bound
        agent. That is the pre-agent-as-user state: rooms seeded before bindings
        existed carry a plain ``cheese`` member with no user row behind it, and
        an agent turn still has to answer "who am I". The fallback keeps those
        rooms working instead of writing an empty author.
        """
        handles = await self.agent_handles(topic_id)
        return handles[0] if handles else CHEESE_HANDLE

    async def add(
        self, *, topic_id: uuid.UUID, handle: str, role: TopicRole, actor: str
    ) -> TopicMembership:
        await self._ensure_topic(topic_id)
        await self._require_manager(topic_id, actor)
        existing = await self._repo.get(topic_id=topic_id, member_handle=handle)
        if existing is not None:
            raise ValidationError("该成员已在话题里")
        return await self._repo.add(topic_id=topic_id, member_handle=handle, role=role)

    async def update_role(
        self, *, topic_id: uuid.UUID, handle: str, role: TopicRole, actor: str
    ) -> TopicMembership:
        await self._ensure_topic(topic_id)
        await self._require_manager(topic_id, actor)
        member = await self._repo.get(topic_id=topic_id, member_handle=handle)
        if member is None:
            raise NotFoundError("成员不存在")
        # Demoting the last owner would orphan the room — block it.
        if (
            member.role == TopicRole.owner
            and role != TopicRole.owner
            and await self._repo.count_owners(topic_id) <= 1
        ):
            raise ValidationError("不能把最后一个 owner 降级")
        return await self._repo.update_role(member, role=role)

    async def remove(self, *, topic_id: uuid.UUID, handle: str, actor: str) -> None:
        await self._ensure_topic(topic_id)
        await self._require_manager(topic_id, actor)
        member = await self._repo.get(topic_id=topic_id, member_handle=handle)
        if member is None:
            raise NotFoundError("成员不存在")
        # Never remove the last owner — a topic must always have one.
        if (
            member.role == TopicRole.owner
            and await self._repo.count_owners(topic_id) <= 1
        ):
            raise ValidationError("不能移除最后一个 owner")
        await self._repo.delete(member)
