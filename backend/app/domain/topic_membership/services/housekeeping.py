"""`TopicMemberService` 里不说拒绝的那一半：名册怎么读、席位怎么摆。

读名册：谁在这间房里、谁收得到这个房间发出去的通知、谁是 agent、这个房间该把事件
送给谁。摆席位：建话题时播种、任务带人进来、旧房间共享的 `cheese` 席位迁给它自己的
分身、退项目时把席位收回。

带 `raise` 的判断一处没搬，全留在门面 `services/__init__.py` —— 那两处错误棘轮按
文件记账，而「谁可以、谁不可以」本来也是另外一半的事：闸门（`require_manager`、
`require_administrator`）和它们问的项目权限都在那边。本模块里连一个 `raise` 都没有。
四个方法体里带延迟 import 的读法（`_on_project`、`project_people`、`seat_counts`、
`managed_topic_ids`）也留在门面，因为本模块要「一个函数体内的 import 都没有」。

**函数体和原来的方法逐字相同，接收者由 `self` 换成 `svc`。** 门面类上的同名方法留
一行转发，把 `self` 和自己的参数原样交过来（跟 `app/domain/oauth/services/` 里
`tokens.py` / `connections.py` 一个写法）。于是类上不再有任何判断，一条判据只有一处
实现；而且跨块调用一律走 `svc.<name>(...)`，`patch.object(svc, "…")` 这类打在实例上
的补丁仍然拦得住内部调用 —— `seed` 与 `migrate_shared_agent_seat` 读门面侧的
`_project_agent_seat`、`addressable_agent_handle` 读 `resolve_agent_handle`、
`joined_topic_ids` 读 `_on_project`，都是这么过去的。

`_public` 也从门面搬了过来：读它的多是本模块的函数，留在原处就得反过来 import
这一半 —— 那正是拆分要避免的循环。门面从本模块取它。

**住在 `services/` 包里**（跟 `app/domain/oauth/services/`、`topic/repositories/`
一个形状）：本模块读同级的 `app.domain.topic.models`，作为**同级**模块加入会成一条
新的 C3 配对，而包里子模块指向父包那条边本来就不算环，基线认的是 `services`。
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from app.core.request_facts import recall, remember
from app.domain.conversation.services import room_of_thread
from app.domain.identity.handles import (
    CHEESE_HANDLE,
    agent_instance_handle,
    looks_like_agent_handle,
)
from app.domain.identity.services import IdentityService
from app.domain.topic.models import Topic, TopicKind, TopicRole, is_private_chat
from app.domain.topic_membership import project_rows
from app.domain.topic_membership.ports import ProjectTeam
from app.domain.topic_membership.schemas import TopicMemberOut

if TYPE_CHECKING:
    from app.domain.topic_membership import services as pkg


def _public(topic: Topic) -> bool:
    """A channel anyone in the project reads and may join, as opposed to a
    private room of two seats."""
    return not is_private_chat(topic)


async def _ensure_member(
    svc: pkg.TopicMemberService, topic_id: uuid.UUID, handle: str, *, role: TopicRole
) -> None:
    existing = await svc._repo.get(topic_id=topic_id, member_handle=handle)
    if existing is None:
        await svc._repo.add(topic_id=topic_id, member_handle=handle, role=role)
    elif role == TopicRole.owner and existing.role != TopicRole.owner:
        existing.role = TopicRole.owner
        await svc._session.flush()


def _is_agent_handle(handle: str) -> bool:
    """Seeding-time check only: a room whose "owner" is 芝士 has no human
    owner. Both the shared platform handle and a per-topic 分身 handle
    count."""
    return looks_like_agent_handle(handle)


async def addressable_agent_handle(
    svc: pkg.TopicMemberService,
    topic_id: uuid.UUID,
    *,
    room_id: uuid.UUID | None = None,
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
        room_id = await room_of_thread(svc._session, topic_id)
    if not await svc.agent_handles(room_id or topic_id):
        return None
    return await svc.resolve_agent_handle(topic_id, room_id=room_id)


async def addressable_agent_handles(
    svc: pkg.TopicMemberService, project_id: uuid.UUID, room_ids: list[uuid.UUID]
) -> dict[uuid.UUID, str | None]:
    """:func:`addressable_agent_handle` for a page of one project's rooms, in a
    fixed number of reads: the rosters at once, who on them is an agent at once,
    and — only when a room seats more than one — the project's default once.

    Same answer per room: None when no agent sits there, the one that does when
    one does, and with several the project's default if it is among them, else
    the first on the roster. Rooms only — a 支线 or a task answers through its
    channel, which the caller already holds.
    """
    if not room_ids:
        return {}
    members = await svc._repo.list_for_topics(room_ids)
    agents = await IdentityService(svc._session).agents_among(
        list({m.member_handle for m in members})
    )
    seated: dict[uuid.UUID, list[str]] = {room: [] for room in room_ids}
    for member in members:
        if member.member_handle in agents:
            seated[member.topic_id].append(member.member_handle)
    own: str | None = None
    if any(len(handles) > 1 for handles in seated.values()):
        default = await project_rows.default_agent_instance_id(svc._session, project_id)
        own = agent_instance_handle(default) if default is not None else None
    return {
        room: (own if own in handles else handles[0]) if handles else None
        for room, handles in seated.items()
    }


_AGENT_SEAT_TABLES = frozenset({"topic_memberships", "user", "agent_bindings"})


async def agent_handles(svc: pkg.TopicMemberService, topic_id: uuid.UUID) -> list[str]:
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
    key = ("agent_handles", topic_id)
    found, cached = recall(svc._session, key)
    if found:
        return cached
    members = await svc._repo.list_for_topic(topic_id)
    agents = await IdentityService(svc._session).agents_among(
        [m.member_handle for m in members]
    )
    handles = [m.member_handle for m in members if m.member_handle in agents]
    remember(svc._session, key, handles, tables=_AGENT_SEAT_TABLES)
    return handles


async def ensure_agent_seat(
    svc: pkg.TopicMemberService, topic_id: uuid.UUID, handle: str
) -> str:
    """Seat THIS agent in this room, and return the handle it acts under.

    The seat is the grant: an action is attributable to the agent that holds
    one, and de-authorizing it is a single row delete rather than waiting out
    a token's TTL. What the seat does not do is tell the room who its agent
    is — a room is a collaboration space and may seat several, so the caller
    names the agent it means.

    Idempotent, and cheap on the fast path (one indexed lookup)."""
    if await svc._repo.get(topic_id=topic_id, member_handle=handle) is None:
        await svc._repo.add(
            topic_id=topic_id, member_handle=handle, role=TopicRole.member
        )
    return handle


async def holds_an_agent_seat(
    svc: pkg.TopicMemberService, room: Topic, handle: str
) -> bool:
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
    return handle in await svc.agent_handles(room.id)


async def joined_ids(
    svc: pkg.TopicMemberService,
    topic_ids: list[uuid.UUID],
    handle: str,
    *,
    team: ProjectTeam,
) -> set[uuid.UUID]:
    """:meth:`joined_topic_ids` for callers holding ids rather than rows."""
    topics = [t for t in [await svc._topics.get(i) for i in topic_ids] if t]
    return await svc.joined_topic_ids(topics, handle, team=team)


async def joined_topic_ids(
    svc: pkg.TopicMemberService, topics: list[Topic], handle: str, *, team: ProjectTeam
) -> set[uuid.UUID]:
    """Which of these channels ``handle`` is in, in one read: 综合 always, as
    long as they are in its project, and every other channel they joined."""
    roots = [t for t in topics if t.kind == TopicKind.root]
    joined = await svc._repo.topic_ids_for_member(
        [t.id for t in topics if t.kind != TopicKind.root], handle
    )
    if roots and await svc._on_project(roots[0].project_id, handle, team=team):
        joined |= {t.id for t in roots}
    return joined


async def may_speak(
    svc: pkg.TopicMemberService, room: Topic, handle: str, *, team: ProjectTeam
) -> bool:
    """May this person speak in the room's main line: a member of a
    channel, or one of the two in a private room (who are seated)."""
    if not _public(room):
        return True
    return room.id in await svc.joined_topic_ids([room], handle, team=team)


async def migrate_shared_agent_seat(
    svc: pkg.TopicMemberService, topic_id: uuid.UUID
) -> None:
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
    legacy = await svc._repo.get(topic_id=topic_id, member_handle=CHEESE_HANDLE)
    if legacy is None:
        return
    await svc._repo.delete(legacy)
    if await svc.agent_handles(topic_id):
        return
    own = await svc._project_agent_seat(topic_id)
    if own is not None:
        await svc.ensure_agent_seat(topic_id, own)


async def on_project(
    svc: pkg.TopicMemberService,
    project_id: uuid.UUID,
    handle: str,
    *,
    team: ProjectTeam,
) -> bool:
    """Is ``handle`` a person or AI teammate of this project?"""
    return await svc._on_project(project_id, handle, team=team)


async def outside(
    svc: pkg.TopicMemberService, room: Topic, handles: list[str], *, team: ProjectTeam
) -> list[str]:
    """The people of the project among ``handles`` who are not in this
    channel, in the order given. 综合 has everyone in the project and a
    private room of two its own two, so neither has anyone outside it."""
    if room.kind == TopicKind.root or not _public(room):
        return []
    people = set(await svc.project_people(room.project_id, team=team))
    seated = {m.member_handle for m in await svc._repo.list_for_topic(room.id)}
    return [h for h in dict.fromkeys(handles) if h in people and h not in seated]


async def owners_of(
    svc: pkg.TopicMemberService, topic_ids: list[uuid.UUID]
) -> dict[uuid.UUID, str | None]:
    """The human each of these rooms belongs to — its ``owner`` member, or
    None. 综合 seats nobody by name, so it has none.

    The roster is checked first so a channel handed over to another manager
    reports that manager. When no ``owner`` row is present (for instance after
    the creator left the channel), a non-root channel created by a human falls
    back to ``Topic.created_by`` unless it is a private channel they are no
    longer in.

    One read of the roster and, only for the channels that fell back, one read
    of their topics: 「全部频道」 asks this of every channel in a project at
    once, and asking per channel copied the same three columns N times.
    :func:`owner_of` is this batch's single-topic call.

    Cheap and unauthorized on purpose: attribution paths (who a PR and its
    commits belong to) call it on every merge, and they are read-only.
    """
    if not topic_ids:
        return {}
    owners: dict[uuid.UUID, str] = {}
    seated: dict[uuid.UUID, set[str]] = {}
    for member in await svc._repo.list_for_topics(topic_ids):
        seated.setdefault(member.topic_id, set()).add(member.member_handle)
        if member.role == TopicRole.owner:
            owners.setdefault(member.topic_id, member.member_handle)
    fallback = {
        topic.id: topic
        for topic in await svc._topics.get_many(set(topic_ids) - set(owners))
    }
    answered: dict[uuid.UUID, str | None] = {}
    for topic_id in topic_ids:
        if topic_id in owners:
            answered[topic_id] = owners[topic_id]
            continue
        topic = fallback.get(topic_id)
        if (
            topic is not None
            and _public(topic)
            and topic.kind != TopicKind.root
            and topic.created_by
            and not svc._is_agent_handle(topic.created_by)
            and (
                not topic.members_only
                or topic.created_by in seated.get(topic_id, set())
            )
        ):
            answered[topic_id] = topic.created_by
        else:
            answered[topic_id] = None
    return answered


async def owner_of(svc: pkg.TopicMemberService, topic_id: uuid.UUID) -> str | None:
    """One room's answer from :meth:`TopicMemberService.owners_of`, which
    states the rule. 走门面而不是模块函数，跨块调用一条路（`patch.object`
    打在门面上的补丁仍拦得住这一次）。"""
    return (await svc.owners_of([topic_id])).get(topic_id)


async def people_handles(svc: pkg.TopicMemberService, topic_id: uuid.UUID) -> list[str]:
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
    members = await svc._repo.list_for_topic(topic_id)
    robots = await IdentityService(svc._session).agents_among(
        [m.member_handle for m in members]
    )
    return [m.member_handle for m in members if m.member_handle not in robots]


async def people_in(
    svc: pkg.TopicMemberService, topic: Topic, *, team: ProjectTeam
) -> list[str]:
    """The people in this channel, in the order they came in. 综合's are
    everyone in the project; any other channel's are the people seated on
    it."""
    if topic.kind == TopicKind.root:
        return await svc.project_people(topic.project_id, team=team)
    return await svc.people_handles(topic.id)


async def people_of(
    svc: pkg.TopicMemberService, topic_id: uuid.UUID, *, team: ProjectTeam
) -> list[str]:
    """:meth:`people_in` for a caller holding the channel's id."""
    topic = await svc._topics.get(topic_id)
    return await svc.people_in(topic, team=team) if topic is not None else []


async def private_seats(
    svc: pkg.TopicMemberService, topic_id: uuid.UUID
) -> tuple[str, str] | None:
    """一间私聊的两席：房主那一席，和对面那一席。不是恰好两席时 None。

    私聊是项目内名册两席的房间（结论 19），所以「这间房里的另一位是谁」只有
    名册一个出处：加人、换席位、撤席位改的就是这一份。

    人这一席是 ``owner``：建私聊的人是房主，人对人的私聊里房主是规范化之后
    排在前面的那一位（``TopicService.get_or_create_private``），AI 队友按它
    自己的席位坐在对面。

    不是恰好两席就答 None，席位被撤掉的房间说不出「对面是谁」。调用方走它本
    来就有的那条退路（项目默认的芝士、不读个人记忆池），而不是从别处猜一个。
    """
    members = await svc._repo.list_for_topic(topic_id)
    if len(members) != 2:
        return None
    owner = next((m for m in members if m.role == TopicRole.owner), None)
    if owner is None:
        return None
    peer = next(m for m in members if m.id != owner.id)
    return owner.member_handle, peer.member_handle


async def reached(
    svc: pkg.TopicMemberService, topic: Topic, handles: list[str]
) -> list[str]:
    """Which of these a notice from this channel may reach: anyone it names,
    except that a private channel's reach only the people and AI teammates
    in it — someone outside is not told its name or what was said."""
    if not topic.members_only:
        return handles
    seated = {m.member_handle for m in await svc._repo.list_for_topic(topic.id)}
    return [h for h in handles if h in seated]


async def revoke_project_seats(
    svc: pkg.TopicMemberService, *, project_id: uuid.UUID, member_handle: str
) -> list[uuid.UUID]:
    """把这个人从这个项目的每一个频道里撤出去 —— 退项目、被移出项目、把自己的
    项目转给别人走这里。返回**真的撤掉席位的频道**（``MemberService.leave`` 靠
    它区分「他在这份名册上只剩这些席位」和「他本来就和这个项目没关系」）。

    没有授权检查：调用方是项目级的退场动作，授权已经在那里做完了。

    只管项目的频道，不管私聊：私聊是两个人之间的一间房，不是项目发的通行证
    （``TopicRepository.list_for_project`` 本来就不含它），人离开项目不该把它
    带走。频道不会因此没人管：管项目的人管它的每一个频道。
    """
    topic_ids = await svc._topics.ids_for_project(project_id)
    if not topic_ids:
        return []
    return await svc._repo.delete_for_member(
        topic_ids=topic_ids, member_handle=member_handle
    )


async def seats(
    svc: pkg.TopicMemberService, topic: Topic, *, team: ProjectTeam
) -> list[TopicMemberOut]:
    """Everyone in this channel, the way its member list shows them: its
    seated rows, and for 综合 everyone in the project besides."""
    rows = await svc._repo.list_for_topic(topic.id)
    has_owner = any(m.role == TopicRole.owner for m in rows)
    creator = (
        topic.created_by
        if not has_owner
        and _public(topic)
        and topic.kind != TopicKind.root
        and topic.created_by
        and not svc._is_agent_handle(topic.created_by)
        else None
    )
    seats = [
        TopicMemberOut(
            topic_id=m.topic_id,
            member_handle=m.member_handle,
            role=TopicRole.owner if m.member_handle == creator else m.role,
        )
        for m in rows
    ]
    if topic.kind == TopicKind.root:
        seated = {m.member_handle for m in seats}
        seats += [
            TopicMemberOut(topic_id=topic.id, member_handle=h, role=TopicRole.member)
            for h in await svc.people_in(topic, team=team)
            if h not in seated
        ]
    return seats


async def seed(
    svc: pkg.TopicMemberService,
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
    if owner_handle and not svc._is_agent_handle(owner_handle):
        await svc._ensure_member(topic_id, owner_handle, role=TopicRole.owner)
    seat = agent_handle or await svc._project_agent_seat(topic_id)
    if seat is not None:
        await svc.ensure_agent_seat(topic_id, seat)


async def seed_private(
    svc: pkg.TopicMemberService,
    topic_id: uuid.UUID,
    *,
    owner_handle: str,
    peer_handle: str,
) -> None:
    """Seed exactly the two seats a private conversation contains: the
    owner, and the peer — a person's handle or a teammate's seat, the same
    row either way. Idempotency also repairs private topics created before
    rosters existed.
    """
    await svc._ensure_member(topic_id, owner_handle, role=TopicRole.owner)
    await svc._ensure_member(topic_id, peer_handle, role=TopicRole.member)


async def seen(
    svc: pkg.TopicMemberService, topics: list[Topic], handle: str, *, team: ProjectTeam
) -> list[Topic]:
    """The ones of these channels ``handle`` sees: the public ones, and the
    private channels they sit in."""
    private = [t for t in topics if t.members_only]
    seated = (
        await svc.joined_topic_ids(private, handle, team=team) if private else set()
    )
    return [t for t in topics if not t.members_only or t.id in seated]


async def take_in(
    svc: pkg.TopicMemberService, topic_id: uuid.UUID, handle: str
) -> None:
    """Put a person in a channel because work there was handed to them: the
    owner or a contributor of a task in it. 综合 already has them, and a
    private room's seats are its own business."""
    topic = await svc._topics.get(topic_id)
    if topic is None or not _public(topic) or topic.kind == TopicKind.root:
        return
    if svc._is_agent_handle(handle):
        return
    role = (
        TopicRole.owner if await svc.owner_of(topic_id) == handle else TopicRole.member
    )
    await svc._ensure_member(topic_id, handle, role=role)


async def topic_ids_for_member(
    svc: pkg.TopicMemberService, topic_ids: list[uuid.UUID], member_handle: str
) -> set[uuid.UUID]:
    """Which of these topics this handle is in the roster of, in ONE query.

    A read, so it carries no roster-management authorization: the caller
    (与我的相关性 on the topic list) is asking about ITSELF, and every
    answer it gets back is about topics it was already allowed to list.
    """
    return await svc._repo.topic_ids_for_member(topic_ids, member_handle)
