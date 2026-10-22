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

# 这个包在 2026-10-16 之前是一个 827 行的单文件 `services.py`。`TopicMemberService`
# 里不说拒绝的那一半（名册怎么读、席位怎么摆）搬进了 `housekeeping.py`，门面留的是
# 闸门和它们问的项目权限——**带 `raise` 的方法一行没搬**。两处错误棘轮按文件记账，
# 这一半留在原处，它们的基线才不用动。
#
# 拆成**包**而不是两个同级模块，是为了不欠新债：配对记在包一级，同级模块
# `housekeeping` 读 `app.domain.topic.models` 在 C3 上会算成一条新配对（基线树里
# 那条的发起方是 `topic_membership.services`），而这个包的 `__init__` 接着当那个
# 发起方——同一笔债换了地方，不是新欠的。形状和 `app/domain/oauth/services/`、
# `app/domain/topic/repositories/` 一样。
#
# **搬走的方法在类上留一行转发**，把 `self` 和自己的参数原样交给 `housekeeping.py`
# 里接收者是 `svc` 的同名函数（跟 `app/domain/oauth/services/` 的 `tokens.py` /
# `connections.py` 一个写法）。于是类上不再有任何判断，一条判据只有一处实现；跨块调用
# 一律走 `svc.<name>(...)`，`patch.object(svc, "…")` 这类打在实例上的补丁仍然拦得住
# 内部调用。
#
# **导入路径和一个名字都没变。** `from app.domain.topic_membership.services import
# TopicMemberService` 取到的还是同一个类，方法还是那些名字和签名；`addressable_seat`
# 还在这儿，它建的就是门面这个类。

import uuid
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.project_roles import may
from app.core.errors import (
    ForbiddenError,
    NotFoundError,
    UnprocessableEntityError,
    ValidationError,
)
from app.core.sentences import say
from app.domain.conversation.services import room_of
from app.domain.identity.handles import (
    AGENT_HANDLE_PREFIX,
    CHEESE_HANDLE,
    agent_instance_handle,
)
from app.domain.identity.services import IdentityService
from app.domain.project_permissions import Permission
from app.domain.topic.models import (
    Topic,
    TopicKind,
    TopicMembership,
    TopicRole,
    TopicStatus,
)
from app.domain.topic.repositories import TopicRepository
from app.domain.topic_membership import project_rows
from app.domain.topic_membership.ports import ProjectTeam, SeatProject
from app.domain.topic_membership.repositories import TopicMembershipRepository
from app.domain.topic_membership.schemas import TopicMemberOut

from . import housekeeping
from .housekeeping import _public

if TYPE_CHECKING:
    # deferred-import: cycle membership.roster -> topic_membership.services
    from app.domain.membership.roster import Member

__all__ = ["CHEESE_HANDLE", "TopicMemberService", "addressable_seat"]


class TopicMemberService:
    def __init__(self, session: AsyncSession):
        self._session = session
        self._repo = TopicMembershipRepository(session)
        self._topics = TopicRepository(session)
        # The project roster `_on_project` last read, kept so a request can hand
        # it to the writer's own session instead of that session reading it again.
        self.roster_read: tuple[Member, ...] | None = None

    async def _ensure_topic(self, topic_id: uuid.UUID) -> None:
        if await self._topics.get(topic_id) is None:
            raise NotFoundError(say("topicNotFound"))

    async def manages(self, topic: Topic, actor: str) -> bool:
        """Does ``actor`` manage this channel: its creator, or someone who
        manages the project? A team always has an owner, so every channel has
        someone who can."""
        member = await self._repo.get(topic_id=topic.id, member_handle=actor)
        if topic.members_only and member is None:
            # Nobody manages a private channel they cannot see.
            return False
        if (
            member is not None and member.role == TopicRole.owner
        ) or await self.owner_of(topic.id) == actor:
            return True
        return await may(
            self._session, topic.project_id, actor, Permission.manage_channels
        )

    async def require_manager(self, topic_id: uuid.UUID, actor: str) -> Topic:
        topic = await self._topics.get(topic_id)
        if topic is None or (
            topic.members_only
            and await self._repo.get(topic_id=topic_id, member_handle=actor) is None
        ):
            # Someone outside a private channel is not told it exists.
            raise NotFoundError(say("topicNotFound"))
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
        return await housekeeping.seed(
            self, topic_id, owner_handle=owner_handle, agent_handle=agent_handle
        )

    async def seed_private(
        self, topic_id: uuid.UUID, *, owner_handle: str, peer_handle: str
    ) -> None:
        return await housekeeping.seed_private(
            self, topic_id, owner_handle=owner_handle, peer_handle=peer_handle
        )

    async def private_seats(self, topic_id: uuid.UUID) -> tuple[str, str] | None:
        return await housekeeping.private_seats(self, topic_id)

    @staticmethod
    def _is_agent_handle(handle: str) -> bool:
        return housekeeping._is_agent_handle(handle)

    async def _ensure_member(
        self, topic_id: uuid.UUID, handle: str, *, role: TopicRole
    ) -> None:
        return await housekeeping._ensure_member(self, topic_id, handle, role=role)

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
        if await may(
            self._session, topics[0].project_id, actor, Permission.manage_channels
        ):
            return {t.id for t in topics}
        owned = await self._repo.topic_ids_for_member(
            [t.id for t in topics], actor, roles=frozenset({TopicRole.owner})
        )
        if not self._is_agent_handle(actor):
            candidates = [
                t
                for t in topics
                if t.id not in owned
                and _public(t)
                and t.kind != TopicKind.root
                and t.created_by == actor
            ]
            if candidates:
                has_owner = await self._repo.topics_with_owner(
                    [t.id for t in candidates]
                )
                private_ids = [
                    t.id for t in candidates if t.id not in has_owner and t.members_only
                ]
                seated_private = (
                    await self._repo.topic_ids_for_member(private_ids, actor)
                    if private_ids
                    else set()
                )
                owned = owned | {
                    t.id
                    for t in candidates
                    if t.id not in has_owner
                    and (not t.members_only or t.id in seated_private)
                }
        return owned

    async def seat_counts(
        self, topics: list[Topic], *, team: ProjectTeam
    ) -> dict[uuid.UUID, int]:
        """How many people and AI teammates are in each of these channels:
        their seats, and for 综合 everyone in the project."""
        counts = await self._repo.counts_for_topics([t.id for t in topics])
        roots = [t for t in topics if t.kind == TopicKind.root]
        if roots:
            # deferred-import: cycle membership.roster -> topic_membership.services
            from app.domain.membership.roster import roster

            everyone = len(await roster(self._session, roots[0].project_id, team=team))
            counts.update({t.id: everyone for t in roots})
        return counts

    async def people_in(self, topic: Topic, *, team: ProjectTeam) -> list[str]:
        return await housekeeping.people_in(self, topic, team=team)

    async def seats(self, topic: Topic, *, team: ProjectTeam) -> list[TopicMemberOut]:
        return await housekeeping.seats(self, topic, team=team)

    async def reached(self, topic: Topic, handles: list[str]) -> list[str]:
        return await housekeeping.reached(self, topic, handles)

    async def people_of(self, topic_id: uuid.UUID, *, team: ProjectTeam) -> list[str]:
        return await housekeeping.people_of(self, topic_id, team=team)

    async def project_people(
        self, project_id: uuid.UUID, *, team: ProjectTeam
    ) -> list[str]:
        """Everyone in the project who is a person: whom a channel's work may be
        handed to."""
        # deferred-import: cycle membership.roster -> topic_membership.services
        from app.domain.membership.roster import roster

        return [
            m.handle
            for m in await roster(self._session, project_id, team=team)
            if not m.agent
        ]

    async def joined_topic_ids(
        self, topics: list[Topic], handle: str, *, team: ProjectTeam
    ) -> set[uuid.UUID]:
        return await housekeeping.joined_topic_ids(self, topics, handle, team=team)

    async def seen(
        self, topics: list[Topic], handle: str, *, team: ProjectTeam
    ) -> list[Topic]:
        return await housekeeping.seen(self, topics, handle, team=team)

    async def joined_ids(
        self, topic_ids: list[uuid.UUID], handle: str, *, team: ProjectTeam
    ) -> set[uuid.UUID]:
        return await housekeeping.joined_ids(self, topic_ids, handle, team=team)

    async def on_project(
        self, project_id: uuid.UUID, handle: str, *, team: ProjectTeam
    ) -> bool:
        return await housekeeping.on_project(self, project_id, handle, team=team)

    async def _channel(self, topic_id: uuid.UUID) -> Topic:
        """A channel people join and leave; a private room is not one."""
        topic = await self._topics.get(topic_id)
        if topic is None or not _public(topic):
            raise NotFoundError(say("topicNotFound"))
        return topic

    async def may_speak(self, room: Topic, handle: str, *, team: ProjectTeam) -> bool:
        return await housekeeping.may_speak(self, room, handle, team=team)

    async def join(self, topic_id: uuid.UUID, actor: str, *, team: ProjectTeam) -> None:
        """``actor`` joins a public channel of a project they are in. 综合 needs
        no joining, and an archived channel takes no one new."""
        topic = await self._channel(topic_id)
        if topic.kind == TopicKind.root:
            return
        if topic.status == TopicStatus.archived:
            raise ValidationError(say("channelArchivedNoJoin"))
        if not await self._on_project(topic.project_id, actor, team=team):
            raise ForbiddenError(say("topicAddProjectMembersOnly"))
        role = (
            TopicRole.owner
            if await self.owner_of(topic_id) == actor
            else TopicRole.member
        )
        await self._ensure_member(topic_id, actor, role=role)

    async def leave(self, topic_id: uuid.UUID, actor: str) -> None:
        """``actor`` leaves a channel. Nobody leaves 综合: it is the project."""
        topic = await self._channel(topic_id)
        if topic.kind == TopicKind.root:
            raise ValidationError(say("channelGeneralNoLeave"))
        member = await self._repo.get(topic_id=topic_id, member_handle=actor)
        if member is not None:
            await self._repo.delete(member)

    async def take_in(self, topic_id: uuid.UUID, handle: str) -> None:
        return await housekeeping.take_in(self, topic_id, handle)

    async def owner_of(self, topic_id: uuid.UUID) -> str | None:
        return await housekeeping.owner_of(self, topic_id)

    async def owners_of(
        self, topic_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, str | None]:
        return await housekeeping.owners_of(self, topic_ids)

    async def topic_ids_for_member(
        self, topic_ids: list[uuid.UUID], member_handle: str
    ) -> set[uuid.UUID]:
        return await housekeeping.topic_ids_for_member(self, topic_ids, member_handle)

    async def agent_handles(self, topic_id: uuid.UUID) -> list[str]:
        return await housekeeping.agent_handles(self, topic_id)

    async def people_handles(self, topic_id: uuid.UUID) -> list[str]:
        return await housekeeping.people_handles(self, topic_id)

    async def holds_an_agent_seat(self, room: Topic, handle: str) -> bool:
        return await housekeeping.holds_an_agent_seat(self, room, handle)

    async def ensure_agent_seat(self, topic_id: uuid.UUID, handle: str) -> str:
        return await housekeeping.ensure_agent_seat(self, topic_id, handle)

    async def _project_agent_seat(self, topic_id: uuid.UUID) -> str | None:
        """这间房所属项目的芝士，在名册上的那一行 handle。

        身份从 agent 自己派生（``agent_instance_handle``），不从房间派生：一间房
        可以坐好几个 agent，从房间派生会给它们同一个名字，也会给同一个 agent 在
        两间房里两个名字。项目还没有芝士时为 None——建项目就播种它，所以这只在
        一个拼出来的、没有芝士的 Project 上成立。"""
        topic = await self._topics.get(topic_id)
        if topic is None:
            return None
        default = await project_rows.default_agent_instance_id(
            self._session, topic.project_id
        )
        if default is None:
            return None
        return agent_instance_handle(default)

    async def migrate_shared_agent_seat(self, topic_id: uuid.UUID) -> None:
        return await housekeeping.migrate_shared_agent_seat(self, topic_id)

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
        return await housekeeping.addressable_agent_handle(
            self, topic_id, room_id=room_id
        )

    async def addressable_agent_handles(
        self, project_id: uuid.UUID, room_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, str | None]:
        return await housekeeping.addressable_agent_handles(self, project_id, room_ids)

    async def add(
        self,
        *,
        topic_id: uuid.UUID,
        handle: str,
        actor: str,
        seat_project: SeatProject,
        team: ProjectTeam,
    ) -> TopicMembership:
        topic = await self._brought_in_by(topic_id, actor)
        if not _public(topic):
            # Whoever joins reads everything said before: a DM keeps its two.
            raise UnprocessableEntityError(say("privateChatTwoPeopleOnly"))
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
            # 哪一席落在哪个项目上，本包在第 2 层读不出来（`SeatProject`）。
            owner = await seat_project(self._session, handle)
            if owner is not None and owner != topic.project_id:
                raise NotFoundError(say("projectAiTeammateNotFound"))
        elif not await self._on_project(topic.project_id, handle, team=team):
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

    async def may_bring_in(self, topic: Topic, actor: str) -> bool:
        """May ``actor`` add someone to this channel: a person in it whose role
        lets them bring people into channels, or someone who manages it. An AI
        teammate's seat lets it act in the channel, not invite. Everyone in the
        project is in 综合 without a seat of their own, and a role that lets
        them bring people in already says they are in the project."""
        if (
            (
                topic.kind == TopicKind.root
                or await self._repo.get(topic_id=topic.id, member_handle=actor)
                is not None
            )
            and not await IdentityService(self._session).is_agent(actor)
            and await self.may_open_channels(topic.project_id, actor)
        ):
            return True
        return await self.manages(topic, actor)

    async def _brought_in_by(self, topic_id: uuid.UUID, actor: str) -> Topic:
        """The channel ``actor`` may add someone to (:meth:`may_bring_in`).
        Someone outside a private channel is told it does not exist."""
        topic = await self._topics.get(topic_id)
        if topic is None:
            raise NotFoundError(say("topicNotFound"))
        if await self.may_bring_in(topic, actor):
            return topic
        if (
            topic.members_only
            and await self._repo.get(topic_id=topic_id, member_handle=actor) is None
        ):
            raise NotFoundError(say("topicNotFound"))
        raise ForbiddenError(say("channelManagerOnly"))

    async def outside(
        self, room: Topic, handles: list[str], *, team: ProjectTeam
    ) -> list[str]:
        return await housekeeping.outside(self, room, handles, team=team)

    async def administers(self, topic: Topic, actor: str) -> bool:
        """May ``actor`` archive this channel or name who manages it without
        being in it: its owner, or whoever manages the project. Seeing what is
        said in a private channel still takes a seat (:meth:`manages`)."""
        member = await self._repo.get(topic_id=topic.id, member_handle=actor)
        if (
            member is not None and member.role == TopicRole.owner
        ) or await self.owner_of(topic.id) == actor:
            return True
        return await may(
            self._session, topic.project_id, actor, Permission.manage_channels
        )

    async def require_administrator(self, topic_id: uuid.UUID, actor: str) -> Topic:
        topic = await self._topics.get(topic_id)
        if topic is None:
            raise NotFoundError(say("topicNotFound"))
        if not _public(topic):
            # A private room of two is its own people's business: someone not
            # in it is not told it exists.
            if await self._repo.get(topic_id=topic_id, member_handle=actor) is None:
                raise NotFoundError(say("topicNotFound"))
            return await self.require_manager(topic_id, actor)
        if await self.administers(topic, actor):
            return topic
        if (
            topic.members_only
            and await self._repo.get(topic_id=topic_id, member_handle=actor) is None
        ):
            # Someone outside a private channel is not told it exists.
            raise NotFoundError(say("topicNotFound"))
        raise ForbiddenError(say("channelManagerOnly"))

    async def hand_over(
        self, topic_id: uuid.UUID, handle: str, *, actor: str, team: ProjectTeam
    ) -> Topic:
        """Name ``handle`` the channel's manager in place of whoever was. They
        are seated if they were not; the one they replace stays a member.
        综合 is managed by whoever manages the project and names nobody."""
        topic = await self.require_administrator(topic_id, actor)
        if topic.kind == TopicKind.root:
            raise ValidationError(say("channelGeneralNoManager"))
        if self._is_agent_handle(handle) or await IdentityService(
            self._session
        ).is_agent(handle):
            raise ValidationError(say("channelManagerPersonOnly"))
        if not await self._on_project(topic.project_id, handle, team=team):
            raise ValidationError(say("topicAddProjectMembersOnly"))
        for member in await self._repo.list_for_topic(topic_id):
            if member.role == TopicRole.owner and member.member_handle != handle:
                member.role = TopicRole.member
        seat = await self._repo.get(topic_id=topic_id, member_handle=handle)
        if seat is None:
            await self._repo.add(
                topic_id=topic_id, member_handle=handle, role=TopicRole.owner
            )
        else:
            seat.role = TopicRole.owner
        await self._session.flush()
        return topic

    async def step_in(self, topic_id: uuid.UUID, actor: str) -> bool:
        """Whoever manages the project joins a private channel they are not in.
        Nobody else joins one: its people bring the others in. Returns whether
        they were seated now, so the caller says so in the channel."""
        topic = await self._channel(topic_id)
        if not topic.members_only:
            raise NotFoundError(say("topicNotFound"))
        if topic.status == TopicStatus.archived:
            raise ValidationError(say("channelArchivedNoJoin"))
        if not await may(
            self._session, topic.project_id, actor, Permission.manage_channels
        ):
            raise NotFoundError(say("topicNotFound"))
        if await self._repo.get(topic_id=topic_id, member_handle=actor) is not None:
            return False
        await self._repo.add(
            topic_id=topic_id, member_handle=actor, role=TopicRole.member
        )
        return True

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
        topic = await self._channel(topic_id)
        if topic.kind == TopicKind.root:
            raise ValidationError(say("channelGeneralNotPrivate"))
        if topic.members_only == members_only:
            return topic
        if members_only:
            await self.require_manager(topic_id, actor)
            await self._ensure_member(topic_id, actor, role=TopicRole.member)
        elif not await self.manages(topic, actor) or not await may(
            self._session, topic.project_id, actor, Permission.manage_channels
        ):
            raise ForbiddenError(say("channelPublicProjectManagerOnly"))
        topic.members_only = members_only
        await self._session.flush()
        return topic

    async def may_open_channels(self, project_id: uuid.UUID, handle: str) -> bool:
        """Whether ``handle`` may open a new channel here (#3830)."""
        return await may(self._session, project_id, handle, Permission.create_channels)

    async def _on_project(
        self, project_id: uuid.UUID, handle: str, *, team: ProjectTeam
    ) -> bool:
        # deferred-import: cycle membership.roster -> topic_membership.services
        from app.domain.membership.roster import roster

        self.roster_read = await roster(self._session, project_id, team=team)
        return any(m.handle == handle for m in self.roster_read)

    async def remove(self, *, topic_id: uuid.UUID, handle: str, actor: str) -> None:
        await self.require_manager(topic_id, actor)
        member = await self._repo.get(topic_id=topic_id, member_handle=handle)
        if member is None:
            raise NotFoundError(say("topicMemberNotFound"))
        await self._repo.delete(member)

    async def revoke_project_seats(
        self, *, project_id: uuid.UUID, member_handle: str
    ) -> list[uuid.UUID]:
        return await housekeeping.revoke_project_seats(
            self, project_id=project_id, member_handle=member_handle
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
