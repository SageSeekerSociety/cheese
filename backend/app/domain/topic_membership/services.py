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
from app.domain.identity.handles import (
    AGENT_HANDLE_PREFIX,
    CHEESE_HANDLE,
    agent_instance_handle,
    looks_like_agent_handle,
)
from app.domain.project.repositories import ProjectRepository
from app.domain.topic.models import Topic, TopicMembership, TopicRole
from app.domain.topic.repositories import TopicRepository
from app.domain.topic_membership.repositories import TopicMembershipRepository

__all__ = ["CHEESE_HANDLE", "TopicMemberService"]

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
        """Only an owner/admin of THIS topic may mutate its roster — unless the
        room has NO manager at all, in which case the project's owner/lead may
        step in.

        Without that escape hatch an ownerless topic is a dead end with no way
        out of the product: only an owner may appoint one, and there is no
        owner. That state is not hypothetical — 96 of this project's 149 topics
        were in it (see TopicService._resolve_owner for how they got there), and
        repairing them meant writing the database by hand. The hatch is
        deliberately narrow: it opens only while the room has nobody who can
        manage it, so a healthy room's owner is never overridden.
        """
        member = await self._repo.get(topic_id=topic_id, member_handle=actor)
        if member is not None and member.role in _MANAGER_ROLES:
            return
        if not await self._has_manager(topic_id) and await self._is_project_steward(
            topic_id, actor
        ):
            return
        raise ForbiddenError("只有话题的 owner / admin 能管理成员")

    async def _has_manager(self, topic_id: uuid.UUID) -> bool:
        return any(
            m.role in _MANAGER_ROLES for m in await self._repo.list_for_topic(topic_id)
        )

    async def _is_project_steward(self, topic_id: uuid.UUID, actor: str) -> bool:
        """Who may step into a room that has lost its owner.

        Normally the project's owner or a lead — they answer for the whole
        project, and so for a room inside it.

        But that alone leaves a project with NEITHER a dead end, and such a
        project is reachable: ``ProjectService.create`` accepts
        ``owner_handle=None`` and seeds no member rows, so the row can have a
        NULL owner and no lead at all. Its ownerless topics then have no way out
        of the product in either direction — only a topic manager may appoint
        one and there is none, only a steward may step in and there is none, and
        appointing a project owner needs a steward too. Measured on dev
        (2026-08-13): 5 of 18 active topics in `cheese 自建` sit with no owner
        while the project's own ``owner_handle`` is NULL; that project happens to
        have a lead, which is the only reason it is recoverable.

        So when a project has nobody in charge at all, any of its members may.
        The widening is deliberately the narrowest one that removes the dead
        end: it opens only while BOTH the room and the project have no manager,
        so a project with a lead never has its authority diluted.
        """
        from app.domain.membership.repositories import MemberRepository
        from app.domain.project.models import ProjectRole
        from app.domain.project.repositories import ProjectRepository

        topic = await self._topics.get(topic_id)
        if topic is None:
            return False
        project = await ProjectRepository(self._session).get(topic.project_id)
        if project is not None and project.owner_handle == actor:
            return True
        members = await MemberRepository(self._session).list_for_project(
            topic.project_id
        )
        if any(m.role == ProjectRole.lead for m in members if m.user_handle == actor):
            return True
        # Last resort: nobody is in charge of this project either.
        #
        # "Nobody" is strictly an ABSENT owner — NULL or empty. Not `anonymous`,
        # even though that is what an unidentified caller resolves to and what
        # `projects.py::_is_a_real_person` (advisorily) discounts: nothing
        # reserves that username, so a real account could hold it, and dissolving
        # a real owner's authority on a name is not a trade this may make. Same
        # reason the agent-handle SHAPE heuristic stays out of here.
        if project is None or project.owner_handle:
            return False
        if any(m.role == ProjectRole.lead for m in members):
            return False
        return any(m.user_handle == actor for m in members)

    async def seed(
        self,
        topic_id: uuid.UUID,
        *,
        owner_handle: str | None,
        agent_handle: str | None = None,
    ) -> None:
        """Seed a newborn topic's roster: the creator becomes owner and 芝士
        joins as a member (fusion-design §3). Idempotent — re-seeding never
        duplicates a row. Called at topic-create time, outside the actor check
        (the platform, not a user, seeds).

        芝士 joins under its own handle, not the shared platform account — that
        seat is what makes the room's agent attributable and individually
        revocable. Unnamed, it is the project's own 芝士."""
        if owner_handle and not self._is_agent_handle(owner_handle):
            await self._ensure_member(topic_id, owner_handle, role=TopicRole.owner)
        seat = agent_handle or await self._project_agent_seat(topic_id)
        if seat is not None:
            await self.ensure_agent_seat(topic_id, seat)

    async def seed_root(
        self,
        topic_id: uuid.UUID,
        *,
        owner_handle: str | None,
        member_handles: list[str],
    ) -> None:
        """Seed a project's ROOT topic (总览/项目本体) roster: EVERY project
        member joins the room, so 总览 mirrors the whole project (fusion-design
        §3). The project owner is the topic owner; other members join as members.
        Idempotent — re-seeding never duplicates a row.

        People only. 芝士 is seated where the project's own agent is seeded
        (`AgentInstanceService.materialize_default`), because that is where
        「这个项目的芝士是谁」 is decided. Seating a room-derived stand-in here
        would give 总览 a second agent nobody created.
        """
        if owner_handle and not self._is_agent_handle(owner_handle):
            await self._ensure_member(topic_id, owner_handle, role=TopicRole.owner)
        await self._ensure_people(
            topic_id, owner_handle=owner_handle, member_handles=member_handles
        )

    async def seed_private(
        self, topic_id: uuid.UUID, *, owner_handle: str, peer_handle: str
    ) -> None:
        """Seed exactly the two seats a private conversation contains: the
        owner, and the peer — a person's handle or a teammate's seat, the same
        row either way. Idempotency also repairs private topics created before
        rosters existed.
        """
        await self._ensure_member(topic_id, owner_handle, role=TopicRole.owner)
        await self._ensure_member(topic_id, peer_handle, role=TopicRole.member)

    async def seed_split(
        self,
        topic_id: uuid.UUID,
        *,
        owner_handle: str | None,
        member_handles: list[str],
    ) -> None:
        """Seed a split-off sub-topic's roster: whoever the caller resolved as the
        person driving this work becomes owner (`TopicService.dispatch_task`
        walks that ladder — the splitter, else the human whose turn the split came
        out of, else inherited), and the parent topic's members (typically its
        human roster) join as plain members — otherwise a 分身-initiated split
        (owner_handle "cheese", skipped by seed()) leaves every human silently off
        the new topic's roster, the bug this exists to close. The parent's own
        owner arrives through that member list, so a room that changed hands keeps
        the original requester on the roster instead of dropping them. Role nuance
        (owner/admin on the parent) is deliberately NOT preserved: importing
        everyone as a plain member is simple and correct enough — the owner can
        promote people afterward if the child needs its own owner/admin split.
        Idempotent, same as seed()."""
        await self.seed(topic_id, owner_handle=owner_handle)
        await self._ensure_people(
            topic_id, owner_handle=owner_handle, member_handles=member_handles
        )

    async def _ensure_people(
        self,
        topic_id: uuid.UUID,
        *,
        owner_handle: str | None,
        member_handles: list[str],
    ) -> None:
        for handle in member_handles:
            if not handle or self._is_agent_handle(handle) or handle == owner_handle:
                continue
            await self._ensure_member(topic_id, handle, role=TopicRole.member)

    @staticmethod
    def _is_agent_handle(handle: str) -> bool:
        """Seeding-time check only: a 分身 never joins a room as a *human* member
        (it gets its own seat via :meth:`resolve_agent_handle`), and a room whose
        "owner" is 芝士 has no human owner. Both the shared platform handle and a
        per-topic 分身 handle count."""
        return looks_like_agent_handle(handle)

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

    async def require_archive_manager(self, topic_id: uuid.UUID, actor: str) -> None:
        member = await self._repo.get(topic_id=topic_id, member_handle=actor)
        if member is None or member.role not in _MANAGER_ROLES:
            raise ForbiddenError("只有房间的 owner / admin 能归档或取消归档")

    async def managed_topic_ids(
        self, topic_ids: list[uuid.UUID], actor: str
    ) -> set[uuid.UUID]:
        return await self._repo.topic_ids_for_member(
            topic_ids, actor, roles=_MANAGER_ROLES
        )

    async def owner_of(self, topic_id: uuid.UUID) -> str | None:
        """The human this room belongs to — its ``owner`` member, or None.

        The roster is the ONLY place that answer is reliably recorded.
        ``Topic.created_by`` is not: a 分身 splitting a sub-topic creates it
        under its own ``cheese-<hex12>`` handle, so on every split topic
        ``created_by`` names a robot. Seeding already walked the ladder that
        finds the real human — :meth:`seed`/:meth:`seed_split` skip 芝士 as owner,
        and ``TopicService.dispatch_task`` falls back to the person driving
        the turn the split came out of, then the parent room's owner, then the
        project's — so this just reads what that ladder wrote.

        Cheap and unauthorized on purpose: attribution paths (who a PR and its
        commits belong to) call it on every merge, and they are read-only.
        """
        member = next(
            (
                m
                for m in await self._repo.list_for_topic(topic_id)
                if m.role == TopicRole.owner
            ),
            None,
        )
        return member.member_handle if member is not None else None

    async def topic_ids_for_member(
        self, topic_ids: list[uuid.UUID], member_handle: str
    ) -> set[uuid.UUID]:
        """Which of these topics this handle is in the roster of, in ONE query.

        A read, so it carries no roster-management authorization: the caller
        (与我的相关性 on the topic list) is asking about ITSELF, and every
        answer it gets back is about topics it was already allowed to list.
        """
        return await self._repo.topic_ids_for_member(topic_ids, member_handle)

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

    async def holds_an_agent_seat(self, room: Topic, handle: str) -> bool:
        """Does ``handle`` answer THIS room as one of its agents?

        The question every caller used to ask of the actor itself ("is this an
        agent?") and answer away from the room it was acting in. A participant
        is not typed; it holds a seat, and the seat is what says an agent
        answers here — so a teammate seated in some OTHER room, this project's
        rooms included, is in this one simply not one of its agents. That is
        also the capability the whole change exists to provide: revoking a
        room's seat revokes the authorization, which only holds while the
        question stays 「这个房间认不认它」 and does not widen to
        「这个项目认不认它」.

        One handle answers without a seat here, and it is one handle rather than
        a second roster: ``ProjectAgentCredentialService.agent_handle`` — i.e.
        the project's own 芝士 — is what a project credential authenticates as,
        and it borrows no room's seat by definition. An off-platform 芝士 (local
        agent, bot, CI) holds exactly that credential and acts in every room of
        its project, so asking only the destination room's roster would answer
        "not an agent" for it in every room but 总览: a 403 instead of a
        published message, and a turn that reads its own question back as unread
        input. Reading 总览's whole roster instead of this one handle is what
        would widen the check back to the project — 总览's roster is every
        project member, so any agent seated there would pass everywhere.

        In 总览 itself the roster is the whole answer: that is where the
        project's 芝士 holds its seat, so revoking it there revokes it there.

        Pass the ROOM: threads have no roster of their own, and the project the
        room belongs to is where that credential handle is read from.
        """
        if handle in await self.agent_handles(room.id):
            return True
        project = await ProjectRepository(self._session).get(room.project_id)
        if project is None or project.root_topic_id == room.id:
            return False
        if project.default_agent_instance_id is None:
            return False
        return handle == agent_instance_handle(project.default_agent_instance_id)

    async def ensure_agent_seat(self, topic_id: uuid.UUID, handle: str) -> str:
        """Seat THIS agent in this room, and return the handle it acts under.

        The seat is the grant: an action is attributable to the agent that holds
        one, and de-authorizing it is a single row delete rather than waiting out
        a token's TTL. What the seat does not do is tell the room who its agent
        is — a room is a collaboration space and may seat several, so the caller
        names the agent it means.

        Idempotent, and cheap on the fast path (one indexed lookup)."""
        if await self._repo.get(topic_id=topic_id, member_handle=handle) is None:
            await self._repo.add(
                topic_id=topic_id, member_handle=handle, role=TopicRole.member
            )
        return handle

    async def _project_agent_seat(self, topic_id: uuid.UUID) -> str | None:
        """这间房所属项目的芝士，在名册上的那一行 handle。

        身份从 agent 自己派生（``agent_instance_handle``），不从房间派生：一间房
        可以坐好几个 agent，从房间派生会给它们同一个名字，也会给同一个 agent 在
        两间房里两个名字。项目还没有芝士时为 None——建项目就播种它，所以这只在
        一个拼出来的、没有芝士的 Project 上成立。"""
        topic = await self._topics.get(topic_id)
        if topic is None:
            return None
        project = await ProjectRepository(self._session).get(topic.project_id)
        if project is None or project.default_agent_instance_id is None:
            return None
        return agent_instance_handle(project.default_agent_instance_id)

    async def migrate_shared_agent_seat(self, topic_id: uuid.UUID) -> None:
        """Retire a pre-分身独立身份 room's shared ``cheese`` seat in favour of its
        own 分身. Called when the room's agent is about to act, so rooms migrate
        themselves — no data migration, hence no alembic chain to fork.

        Deliberately narrow: it fires ONLY when the shared seat is actually there.
        A room whose agent seat was **revoked** (no agent at all), or replaced by
        another agent, is left exactly as it is — re-adding a seat here would
        silently undo a revocation, i.e. break the one capability this whole
        change exists to provide. Blocks already authored under the shared handle
        keep it; history is history.

        And the project's 芝士 is seated only where the shared seat was this
        room's LAST agent. 总览 already seats it (`b4d1a70c9e52`), so doing it
        here is a no-op there; in a room that has since chosen another agent it
        would be a second 芝士 nobody asked for. The question 「这个房间还有别的
        agent 吗」 is the right one for every room, not just 总览, and it costs
        one roster read.
        """
        legacy = await self._repo.get(topic_id=topic_id, member_handle=CHEESE_HANDLE)
        if legacy is None:
            return
        await self._repo.delete(legacy)
        if await self.agent_handles(topic_id):
            return
        own = await self._project_agent_seat(topic_id)
        if own is not None:
            await self.ensure_agent_seat(topic_id, own)

    async def resolve_agent_handle(
        self, topic_id: uuid.UUID, *, room_id: uuid.UUID | None = None
    ) -> str:
        """The handle 芝士 acts under in this place — for authoring blocks and
        keying its memory. Read-only.

        The roster decides: a room hosting some other agent attributes to that
        one. With no agent seated at all, fall back to the project's own 芝士 —
        a turn still has to answer "who am I", and answering with the shared
        ``cheese`` account would put the collapsed identity back into the audit
        trail.

        Several agents seated: the project's default answers for the room when
        it is one of them — the room-scoped credentials and the room's own pass
        (memory dream, commit identity) all mean the same one — else the first
        on the roster.

        Pass ``room_id`` when ``topic_id`` is a THREAD's: the roster to read is
        the room's (threads do not have one). A thread and its room fall back to
        the same 芝士, because an agent's name comes from the agent and not from
        where it happens to be standing.
        """
        room = room_id or topic_id
        handles = await self.agent_handles(room)
        if len(handles) == 1:
            return handles[0]
        own = await self._project_agent_seat(room)
        if not handles:
            if own is None:
                raise NotFoundError("这个项目还没有芝士，答不出这间房里「我是谁」")
            return own
        return own if own in handles else handles[0]

    async def add(
        self, *, topic_id: uuid.UUID, handle: str, role: TopicRole, actor: str
    ) -> TopicMembership:
        topic = await self._topics.get(topic_id)
        if topic is None:
            raise NotFoundError("Topic not found")
        await self._require_manager(topic_id, actor)
        if handle.startswith(AGENT_HANDLE_PREFIX):
            # A teammate's seat is derived from its instance, and an instance
            # keys a memory pool inside ITS project — so another project's
            # teammate must not be seated here: nothing in this project would
            # address it, and its token would write as this room's default.
            from app.domain.agent_instance.services import AgentInstanceService

            owner = await AgentInstanceService(self._session).project_of_seat(handle)
            if owner is not None and owner != topic.project_id:
                raise NotFoundError("这个项目里没有这个 AI 队友")
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
