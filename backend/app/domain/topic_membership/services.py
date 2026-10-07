"""Who is in a channel.

Everyone in a project can read every public channel; being IN one is what a
person chooses. A member has it in their sidebar, is reached by @所有人, and may
speak in its main line. People join and leave by themselves, and whoever is
assigned a task there is added to it. 综合 is the exception: everyone in the
project is in it, which the project's roster answers, so it seats no person by
name. AI teammates are seated in every channel, 综合 included, and the seat is
their grant to act there.

A channel is managed — renamed, described, archived, its AI teammates and its
members changed — by the person who created it (the ``owner`` row) and by
whoever manages the project.

A private channel (``Topic.members_only``) is seen only by the people in it, so
nobody joins one: anyone in it brings in someone from the project, and whoever
manages the project manages it only from inside. Its managers turn a public
channel private; only someone who manages the project turns one back.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ForbiddenError, NotFoundError, ValidationError
from app.core.sentences import say
from app.domain.conversation.services import room_of
from app.domain.identity.handles import (
    AGENT_HANDLE_PREFIX,
    CHEESE_HANDLE,
    agent_instance_handle,
    looks_like_agent_handle,
)
from app.domain.identity.services import IdentityService
from app.domain.project.repositories import ProjectRepository
from app.domain.thread.models import Thread
from app.domain.topic.models import (
    Topic,
    TopicKind,
    TopicMembership,
    TopicRole,
    TopicStatus,
)
from app.domain.topic.repositories import TopicRepository
from app.domain.topic_membership.repositories import TopicMembershipRepository
from app.domain.topic_membership.schemas import TopicMemberOut

__all__ = ["CHEESE_HANDLE", "TopicMemberService", "addressable_seat"]


def _public(topic: Topic) -> bool:
    """A channel anyone in the project reads and may join, as opposed to a
    private room of two seats."""
    return not topic.is_private


class TopicMemberService:
    def __init__(self, session: AsyncSession):
        self._session = session
        self._repo = TopicMembershipRepository(session)
        self._topics = TopicRepository(session)

    async def _ensure_topic(self, topic_id: uuid.UUID) -> None:
        if await self._topics.get(topic_id) is None:
            raise NotFoundError("Topic not found")

    async def manages(self, topic: Topic, actor: str) -> bool:
        """Does ``actor`` manage this channel: its creator, or someone who
        manages the project? A team always has an owner, so every channel has
        someone who can."""
        from app.domain.membership.services import MemberService

        member = await self._repo.get(topic_id=topic.id, member_handle=actor)
        if topic.members_only and member is None:
            # Nobody manages a private channel they cannot see.
            return False
        if member is not None and member.role == TopicRole.owner:
            return True
        return await MemberService(self._session).manages(topic.project_id, actor)

    async def require_manager(self, topic_id: uuid.UUID, actor: str) -> Topic:
        topic = await self._topics.get(topic_id)
        if topic is None or (
            topic.members_only
            and await self._repo.get(topic_id=topic_id, member_handle=actor) is None
        ):
            # Someone outside a private channel is not told it exists.
            raise NotFoundError("Topic not found")
        if not await self.manages(topic, actor):
            raise ForbiddenError(say("channelManagerOnly"))
        return topic

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

    async def private_seats(self, topic_id: uuid.UUID) -> tuple[str, str] | None:
        """一间私聊的两席：房主那一席，和对面那一席。不是恰好两席时 None。

        私聊是项目内名册两席的房间（结论 19），所以「这间房里的另一位是谁」只有
        名册一个出处：加人、换席位、撤席位改的就是这一份。

        人这一席是 ``owner``：建私聊的人是房主，人对人的私聊里房主是规范化之后
        排在前面的那一位（``TopicService.get_or_create_private``），AI 队友按它
        自己的席位坐在对面。

        不是恰好两席就答 None，席位被撤掉的房间说不出「对面是谁」。调用方走它本
        来就有的那条退路（项目默认的芝士、不读个人记忆池），而不是从别处猜一个。
        """
        members = await self._repo.list_for_topic(topic_id)
        if len(members) != 2:
            return None
        owner = next((m for m in members if m.role == TopicRole.owner), None)
        if owner is None:
            return None
        peer = next(m for m in members if m.id != owner.id)
        return owner.member_handle, peer.member_handle

    @staticmethod
    def _is_agent_handle(handle: str) -> bool:
        """Seeding-time check only: a room whose "owner" is 芝士 has no human
        owner. Both the shared platform handle and a per-topic 分身 handle
        count."""
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

    async def managed_topic_ids(
        self, topics: list[Topic], actor: str
    ) -> set[uuid.UUID]:
        """Which of these channels ``actor`` manages, in two reads whatever the
        batch: whoever manages the project manages all of them."""
        if not topics:
            return set()
        from app.domain.membership.services import MemberService

        if await MemberService(self._session).manages(topics[0].project_id, actor):
            return {t.id for t in topics}
        return await self._repo.topic_ids_for_member(
            [t.id for t in topics], actor, roles=frozenset({TopicRole.owner})
        )

    async def people_in(self, topic: Topic) -> list[str]:
        """The people in this channel, in the order they came in. 综合's are
        everyone in the project; any other channel's are the people seated on
        it."""
        if topic.kind == TopicKind.root:
            return await self.project_people(topic.project_id)
        return await self.people_handles(topic.id)

    async def seats(self, topic: Topic) -> list[TopicMemberOut]:
        """Everyone in this channel, the way its member list shows them: its
        seated rows, and for 综合 everyone in the project besides."""
        seats = [
            TopicMemberOut.model_validate(m)
            for m in await self._repo.list_for_topic(topic.id)
        ]
        if topic.kind == TopicKind.root:
            seated = {m.member_handle for m in seats}
            seats += [
                TopicMemberOut(
                    topic_id=topic.id, member_handle=h, role=TopicRole.member
                )
                for h in await self.people_in(topic)
                if h not in seated
            ]
        return seats

    async def reached(self, topic: Topic, handles: list[str]) -> list[str]:
        """Which of these a notice from this channel may reach: anyone it names,
        except that a private channel's reach only the people and AI teammates
        in it — someone outside is not told its name or what was said."""
        if not topic.members_only:
            return handles
        seated = {m.member_handle for m in await self._repo.list_for_topic(topic.id)}
        return [h for h in handles if h in seated]

    async def people_of(self, topic_id: uuid.UUID) -> list[str]:
        """:meth:`people_in` for a caller holding the channel's id."""
        topic = await self._topics.get(topic_id)
        return await self.people_in(topic) if topic is not None else []

    async def project_people(self, project_id: uuid.UUID) -> list[str]:
        """Everyone in the project who is a person: whom a channel's work may be
        handed to."""
        from app.domain.membership.roster import roster

        return [
            m.handle for m in await roster(self._session, project_id) if not m.agent
        ]

    async def joined_topic_ids(
        self, topics: list[Topic], handle: str
    ) -> set[uuid.UUID]:
        """Which of these channels ``handle`` is in, in one read: 综合 always, as
        long as they are in its project, and every other channel they joined."""
        roots = [t for t in topics if t.kind == TopicKind.root]
        joined = await self._repo.topic_ids_for_member(
            [t.id for t in topics if t.kind != TopicKind.root], handle
        )
        if roots and await self._on_project(roots[0].project_id, handle):
            joined |= {t.id for t in roots}
        return joined

    async def seen(self, topics: list[Topic], handle: str) -> list[Topic]:
        """The ones of these channels ``handle`` sees: the public ones, and the
        private channels they sit in."""
        private = [t for t in topics if t.members_only]
        seated = await self.joined_topic_ids(private, handle) if private else set()
        return [t for t in topics if not t.members_only or t.id in seated]

    async def joined_ids(
        self, topic_ids: list[uuid.UUID], handle: str
    ) -> set[uuid.UUID]:
        """:meth:`joined_topic_ids` for callers holding ids rather than rows."""
        topics = [t for t in [await self._topics.get(i) for i in topic_ids] if t]
        return await self.joined_topic_ids(topics, handle)

    async def on_project(self, project_id: uuid.UUID, handle: str) -> bool:
        """Is ``handle`` a person or AI teammate of this project?"""
        return await self._on_project(project_id, handle)

    async def _channel(self, topic_id: uuid.UUID) -> Topic:
        """A channel people join and leave; a private room is not one."""
        topic = await self._topics.get(topic_id)
        if topic is None or not _public(topic):
            raise NotFoundError("Topic not found")
        return topic

    async def may_speak(self, room: Topic, handle: str) -> bool:
        """May this person speak in the room's main line: a member of a
        channel, or one of the two in a private room (who are seated)."""
        if not _public(room):
            return True
        return room.id in await self.joined_topic_ids([room], handle)

    async def join(self, topic_id: uuid.UUID, actor: str) -> None:
        """``actor`` joins a public channel of a project they are in. 综合 needs
        no joining, and an archived channel takes no one new."""
        topic = await self._channel(topic_id)
        if topic.kind == TopicKind.root:
            return
        if topic.status == TopicStatus.archived:
            raise ValidationError(say("channelArchivedNoJoin"))
        if not await self._on_project(topic.project_id, actor):
            raise ForbiddenError(say("topicAddProjectMembersOnly"))
        await self._ensure_member(topic_id, actor, role=TopicRole.member)

    async def leave(self, topic_id: uuid.UUID, actor: str) -> None:
        """``actor`` leaves a channel. Nobody leaves 综合: it is the project."""
        topic = await self._channel(topic_id)
        if topic.kind == TopicKind.root:
            raise ValidationError(say("channelGeneralNoLeave"))
        member = await self._repo.get(topic_id=topic_id, member_handle=actor)
        if member is not None:
            await self._repo.delete(member)

    async def take_in(self, topic_id: uuid.UUID, handle: str) -> None:
        """Put a person in a channel because work there was handed to them: the
        owner or a contributor of a task in it. 综合 already has them, and a
        private room's seats are its own business."""
        topic = await self._topics.get(topic_id)
        if topic is None or not _public(topic) or topic.kind == TopicKind.root:
            return
        if self._is_agent_handle(handle):
            return
        await self._ensure_member(topic_id, handle, role=TopicRole.member)

    async def owner_of(self, topic_id: uuid.UUID) -> str | None:
        """The human this room belongs to — its ``owner`` member, or None. 综合
        seats nobody by name, so it has none.

        The roster is the ONLY place that answer is reliably recorded.
        ``Topic.created_by`` is not: 芝士 opening a channel creates it under its
        own ``cheese-<hex12>`` handle. Seeding already walked the ladder that
        finds the real human (:meth:`seed` skips 芝士 as owner), so this just
        reads what that ladder wrote.

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

        项目名册问的是同一句，所以两边读的是同一处推导
        (:meth:`IdentityService.agents_among`)：一句话有两份声明，迟早在某一个
        handle 上给出两个答案，而房间说它是 agent、项目名册说它是人，正是这次改动
        要消掉的那种差（I4a）。
        """
        members = await self._repo.list_for_topic(topic_id)
        agents = await IdentityService(self._session).agents_among(
            [m.member_handle for m in members]
        )
        return [m.member_handle for m in members if m.member_handle in agents]

    async def people_handles(self, topic_id: uuid.UUID) -> list[str]:
        """Which of this topic's members are people, in roster order.

        The roster minus its agents, and "is an agent" is the execution
        binding's answer (:meth:`IdentityService.agents_among`), never what a
        handle looks like: a project's members are free to name a teammate
        ``cheese-abcdef123456``.

        名册自己读一遍，不走 :meth:`agent_handles`：那一句会把名册再读一遍，而
        「谁是 agent」和「谁是人」是同一个问题的两半，两边都在这一轮的路上。

        Memory recall asks this: an agent reads one pool per person it is
        sitting with (结论 54), so the list has to be the people, and it has to
        be all of them.
        """
        members = await self._repo.list_for_topic(topic_id)
        robots = await IdentityService(self._session).agents_among(
            [m.member_handle for m in members]
        )
        return [m.member_handle for m in members if m.member_handle not in robots]

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

        The roster is the whole answer, with no handle excepted. There used to be
        one: a project credential authenticated as a name derived from the
        project's root room, which by construction sat on no other room's
        roster, so an off-platform 芝士 (local agent, bot, CI) holding that
        credential had to be waved through everywhere. It authenticates as the
        project's own 芝士 now, and that 芝士 is seated in every room it belongs
        to — so the exception bought nothing and cost the capability this check
        exists to provide: while it stood, revoking that seat in one room left
        the credential writing there anyway.

        Pass the ROOM: threads have no roster of their own.
        """
        return handle in await self.agent_handles(room.id)

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
        it is one of them — the room-scoped credentials and the room's own
        commit identity all mean the same one — else the first on the roster.

        Pass ``room_id`` when ``topic_id`` is a THREAD's: the roster to read is
        the room's (threads do not have one). A thread and its room fall back to
        the same 芝士, because an agent's name comes from the agent and not from
        where it happens to be standing.
        """
        # A task is a conversation of its own with no roster: its room's
        # answers for it, whichever caller forgot to say so.
        room = room_id or await room_of(self._session, topic_id)
        handles = await self.agent_handles(room)
        if len(handles) == 1:
            return handles[0]
        own = await self._project_agent_seat(room)
        if not handles:
            if own is None:
                raise NotFoundError(say("projectHasNoCheese"))
            return own
        return own if own in handles else handles[0]

    async def addressable_agent_handle(
        self, topic_id: uuid.UUID, *, room_id: uuid.UUID | None = None
    ) -> str | None:
        """名册上**真有**的那个 agent 席位，一个都没有就是 None。

        与 :meth:`resolve_agent_handle` 的区别就在这一档，而两者的用途本来就不同：
        那个答的是「这一轮署谁的名」，一个房间无论如何都得答得出来，所以名册空了它
        回落到这个地点的 sandbox token 名下的 ``cheese-<hex12>``。点名答的是「这条
        事件送给谁」，而那个回落出来的 handle 不在名册上 —— 拿它去点名，寻址结果看
        着有一个收件人，落到正文里的 @ 却谁也对不上，于是事件送出去了、却什么也不会
        发生。没人可点就是没人可点，如实答 None。
        """
        if room_id is None:
            # A 支线 seats nobody of its own: its channel's roster answers.
            room_id = await self._session.scalar(
                select(Thread.room_id).where(Thread.id == topic_id)
            )
        if not await self.agent_handles(room_id or topic_id):
            return None
        return await self.resolve_agent_handle(topic_id, room_id=room_id)

    async def add(
        self, *, topic_id: uuid.UUID, handle: str, actor: str
    ) -> TopicMembership:
        topic = await self._brought_in_by(topic_id, actor)
        # Who is an agent is the binding's answer, not the handle's shape (I9):
        # a connector's bound account can be named anything.
        is_agent = handle.startswith(AGENT_HANDLE_PREFIX) or await IdentityService(
            self._session
        ).is_agent(handle)
        if is_agent:
            # A teammate's seat is derived from its instance, and an instance
            # keys a memory pool inside ITS project — so another project's
            # teammate must not be seated here: nothing in this project would
            # address it, and its token would write as this room's default.
            from app.domain.agent_instance.services import AgentInstanceService

            owner = await AgentInstanceService(self._session).project_of_seat(handle)
            if owner is not None and owner != topic.project_id:
                raise NotFoundError(say("projectAiTeammateNotFound"))
        elif not await self._on_project(topic.project_id, handle):
            # A room seat admits on its own, so seating someone the project does
            # not have would let them in without an invitation they accepted.
            # People come into the project first — from its team, or as an
            # external member — and rooms choose among them.
            raise ValidationError(say("topicAddProjectMembersOnly"))
        elif topic.kind == TopicKind.root:
            # Everyone in the project is already in 综合.
            raise ValidationError(say("topicMemberAlready"))
        existing = await self._repo.get(topic_id=topic_id, member_handle=handle)
        if existing is not None:
            raise ValidationError(say("topicMemberAlready"))
        return await self._repo.add(
            topic_id=topic_id, member_handle=handle, role=TopicRole.member
        )

    async def _brought_in_by(self, topic_id: uuid.UUID, actor: str) -> Topic:
        """The channel ``actor`` may add someone to: one they manage, or a
        private one they are in — its people bring the others in. Someone
        outside a private channel is told it does not exist."""
        topic = await self._topics.get(topic_id)
        if topic is None:
            raise NotFoundError("Topic not found")
        if await self._repo.get(topic_id=topic_id, member_handle=actor) is None:
            if topic.members_only:
                raise NotFoundError("Topic not found")
            return await self.require_manager(topic_id, actor)
        if not topic.members_only or await IdentityService(self._session).is_agent(
            actor
        ):
            # An AI teammate's seat lets it act in the channel, not invite.
            return await self.require_manager(topic_id, actor)
        return topic

    async def set_members_only(
        self, topic_id: uuid.UUID, members_only: bool, *, actor: str
    ) -> Topic:
        """Make a channel private or public again.

        Making it private is its managers' call, and whoever made it keeps
        seeing it: they are seated if they were not. Making it public shows its
        whole history to everyone in the project, so it is left to whoever
        manages the project, as Slack leaves it to a workspace's owners and
        admins. 综合 is everyone in the project and is never private.
        """
        from app.domain.membership.services import MemberService

        topic = await self._channel(topic_id)
        if topic.kind == TopicKind.root:
            raise ValidationError(say("channelGeneralNotPrivate"))
        if topic.members_only == members_only:
            return topic
        if members_only:
            await self.require_manager(topic_id, actor)
            await self._ensure_member(topic_id, actor, role=TopicRole.member)
        elif not await self.manages(topic, actor) or not await MemberService(
            self._session
        ).manages(topic.project_id, actor):
            raise ForbiddenError(say("channelPublicProjectManagerOnly"))
        topic.members_only = members_only
        await self._session.flush()
        return topic

    async def _on_project(self, project_id: uuid.UUID, handle: str) -> bool:
        from app.domain.membership.roster import roster

        return any(m.handle == handle for m in await roster(self._session, project_id))

    async def remove(self, *, topic_id: uuid.UUID, handle: str, actor: str) -> None:
        await self.require_manager(topic_id, actor)
        member = await self._repo.get(topic_id=topic_id, member_handle=handle)
        if member is None:
            raise NotFoundError(say("topicMemberNotFound"))
        await self._repo.delete(member)

    async def revoke_project_seats(
        self, *, project_id: uuid.UUID, member_handle: str
    ) -> list[uuid.UUID]:
        """把这个人从这个项目的每一个频道里撤出去 —— 退项目、被移出项目、把自己的
        项目转给别人走这里。返回**真的撤掉席位的频道**（``MemberService.leave`` 靠
        它区分「他在这份名册上只剩这些席位」和「他本来就和这个项目没关系」）。

        没有授权检查：调用方是项目级的退场动作，授权已经在那里做完了。

        只管项目的频道，不管私聊：私聊是两个人之间的一间房，不是项目发的通行证
        （``TopicRepository.list_for_project`` 本来就不含它），人离开项目不该把它
        带走。频道不会因此没人管：管项目的人管它的每一个频道。
        """
        topics = await self._topics.list_for_project(project_id)
        if not topics:
            return []
        return await self._repo.delete_for_member(
            topic_ids=[t.id for t in topics], member_handle=member_handle
        )


async def addressable_seat(session_factory, topic_id: uuid.UUID) -> str | None:
    """这个房间的 agent 席位 —— 平台自己那些事件点的就是它的名。

    读名册，不写：`address()` 要的是一个 handle，而「谁是这里的芝士」名册上已经答
    过一次，这里不重答。名册上一个 agent 都没有就返回 None，寻址结果随之为空 ——
    一个没有 agent 席位的房间，平台点不出收件人来，也就什么都不会起。

    读失败**照常抛出去**。这个读只有两种结果：名册上有席位，或者名册是空的。把
    连接池耗尽、数据库抖动这一类失败也答成 None，就等于对调用点说「这个房间谁也
    不在」—— 而问这个问题的几处里有两处是重发（`_recover_silent_turn`、
    `_schedule_resend`），它们存在的全部理由就是「刚才那条消息没送到，原样再送一
    次」，静默地不送等于把那条消息丢了。

    ``session_factory`` 而不是一个 session：问的几处都在自己的事务之外（后台扫、
    连接器挂上来、重发定时器），各自开一个只读的短会话。手上已经有 session 的调用
    点直接用 :meth:`TopicMemberService.addressable_agent_handle`。
    """
    async with session_factory() as session:
        return await TopicMemberService(session).addressable_agent_handle(topic_id)
