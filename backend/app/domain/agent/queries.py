"""不碰实例状态的问答：只拿一个 session，答案从行里来。

ChatService 上的这一族方法问的都是同一类问题——**这一轮该由谁答、用的模型
过不过闸门、这个项目的 key 该带多少额度、这条记忆改动该说进哪间房**——入参
是 session 和已经读好的对象，出参是答案，既读也不写 `self` 上的任何东西（只
调用同族的纯函数）。散在 ``chat.py`` 里时它们和轮次状态混在一起：一个
`self._x()` 到底动不动这台进程的内存，读的人得翻回定义才知道。

搬出来时按原样搬——入参出参就是它们与调用方之间全部的约定，行为一格没动。唯一
改了形状的是去掉 `self`：它们本来就是模块级函数，`ChatService` 上留一行同名委托，
调用点（含 `api/routes/llm_proxy.py` 那一处）与测试都不用改。

有一半的问题顺手把答案落成房间里一条事件（`_pass_policy_gate` 的提议、
`_bail_notice` 的灰字、`_say_memory_change` 的 diff）：落的都是**调用方那条
session**，提交也归调用方——所以「答一个问题」和「把这句话说出去」在这里是同一
件事的两半，不是两种职责。
"""

import logging
import uuid
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, UnprocessableEntityError, ValidationError
from app.core.sentences import say
from app.domain.agent.announce import announce
from app.domain.agent.model_catalog import catalog
from app.domain.agent.platform_notices import (
    memory_changed_notice,
)
from app.domain.agent.proposals import propose
from app.domain.agent.repositories import AgentTurnRepository
from app.domain.agent_instance.adapters import named_teammate
from app.domain.agent_instance.models import AgentInstance
from app.domain.agent_instance.services import AgentInstanceService, ResolvedAgent
from app.domain.block.models import AGENT_NOTICE_META_KEY, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.block.schemas import BlockOut
from app.domain.event_about import EventAbout, landing
from app.domain.event_author import AuthorType
from app.domain.identity.handles import agent_instance_handle
from app.domain.memory import dream
from app.domain.memory.files import MemoryFileScope
from app.domain.memory.session import MemoryChange, prefix_of_scope
from app.domain.notice_meta import (
    EVENT_MEMORY_CHANGED,
    SEVERITY_INFO,
    WHO_PLATFORM,
    notice,
)
from app.domain.policy import gate
from app.domain.project.models import Project
from app.domain.project.repositories import ProjectRepository
from app.domain.reads import get_by_ids
from app.domain.room_task import binding
from app.domain.room_task.models import Task
from app.domain.room_task.place import Place, PlaceResolver
from app.domain.room_task.services import TaskService
from app.domain.run_record.service import record as keep_record
from app.domain.thread.models import Thread
from app.domain.topic.models import Topic
from app.domain.topic.services import TopicService
from app.domain.topic_membership.services import TopicMemberService
from app.domain.usage.budgets import CreditBudgets
from app.domain.usage.services import UsageService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TaskCreditContext:
    task_id: uuid.UUID
    owner_handle: str
    work_id: uuid.UUID | None


async def task_credit_context(
    session: AsyncSession,
    project_id: uuid.UUID,
    conversation_id: uuid.UUID,
    agent_handles: list[str],
) -> TaskCreditContext | None:
    """The running execution's budget holder, or the next task execution's."""
    place = await PlaceResolver(session).conversation(conversation_id)
    if place is None or place.task is None or place.project_id != project_id:
        return None
    turn = await AgentTurnRepository(session).open_turn_of(
        conversation_id, agent_handles
    )
    owner = await CreditBudgets(session).owner_of(
        turn.id if turn is not None else None, project_id
    )
    if owner is not None:
        return TaskCreditContext(conversation_id, owner, turn.id if turn else None)
    owner = await TaskService(session).credit_owner(place.task)
    return TaskCreditContext(conversation_id, owner, None) if owner else None


@dataclass(frozen=True, slots=True)
class _Proposed:
    """闸门把这次调用变成了一条提议：那条提议，和它在房间里刚落下的那条事件。

    `landed` 是 `None` 表示这条提议之前就提过了（`proposals.py` 按身份去重）。两
    个调用点各取一半——轮次要那条事件来收场，平台自己发起的那几轮要那句话来抛。
    """

    proposal: gate.Proposal
    landed: dict | None


def _block_payload(block_out: BlockOut) -> dict:
    return block_out.model_dump(mode="json")


async def _resolved_agent(session: AsyncSession, topic: Topic) -> ResolvedAgent:
    """Which agent is working in *topic* — its own, else the project's.

    Its ``handle`` keys both of the things an agent owns and a room does not:
    the memory pool it writes to, and the conversation it resumes.
    """
    project = await ProjectRepository(session).get(topic.project_id)
    if project is None:
        raise NotFoundError("Project not found")
    return await AgentInstanceService(session).for_topic(topic, project)


async def _session_agent(
    agents: AgentInstanceService,
    topic: Topic,
    project: Project,
    agent_handle: str | None,
) -> ResolvedAgent:
    """The agent a known session belongs to, else the room's answer.

    ``agent_handle`` is the agent's own handle, or the seat it acts under:
    the output a session produces by itself is stamped by its runner with
    the seat, and read as a handle that names no agent it gave the turn to
    the project's default — whose session then received, or was opened for,
    what was said to the one actually working.
    """
    if agent_handle:
        named = await _named_teammate(agents, project, agent_handle)
        if named is not None:
            return named
        logger.warning(
            "session agent %r is not in project %s; using the room's",
            agent_handle,
            project.id,
        )
    return await agents.for_topic(topic, project)


async def _named_teammate(
    agents: AgentInstanceService, project: Project, handle: str | None
) -> ResolvedAgent | None:
    """The saved teammate ``handle`` names, by its own name or by the seat it
    acts under.

    None when it names no teammate of this project: a person, the shared
    ``cheese`` seat, a room-derived seat of a room that no longer says which
    agent sits there, or nobody. ``for_handle`` raises for a name it does not
    know, which is the point — reading an unknown name as the default teammate
    would answer as somebody else."""
    if not handle:
        return None
    try:
        return agents.resolved(await agents.for_handle(project, handle))
    except NotFoundError:
        return await agents.for_seat_handle(project, handle)


async def session_agent_in_room(
    session: AsyncSession, topic_id: uuid.UUID, handle: str | None
) -> ResolvedAgent | None:
    """Resolve admission's agent from room/project policy, returning no ORM rows.

    Internal admission/recovery caller owns seat authorization and transaction.
    Missing rooms return None; existing resolution policy and errors are retained.
    This query never commits or starts a session.
    """
    topic = await TopicService(session).get(topic_id)
    if topic is None:
        return None
    project = await ProjectRepository(session).get(topic.project_id)
    if project is None:
        raise NotFoundError("Project not found")
    return await _session_agent(AgentInstanceService(session), topic, project, handle)


async def conversation_seat(
    session: AsyncSession, conversation_id: uuid.UUID, handle: str | None
) -> tuple[ResolvedAgent, str] | None:
    """Who answers in a conversation, and the seat it authors under.

    In a room it is the agent the message named (or the room's), seated on the
    room's roster. In a task it is the teammate the message named — every
    message in a task is addressed, so the one it @-ed takes the turn there too
    — and otherwise the task's own agent, seated in the room when the task was
    given to it. None for a conversation that no longer exists, and for an
    agent that may not act there (`_seat_of`).

    The single-pair read: it resolves the one place the way it always did, and
    :func:`_seat_in_place` is the judgement :func:`seats_in_conversations`
    shares. Asking the batch for one pair would read all three place tables for
    a room that one ``get`` used to answer."""
    place = await PlaceResolver(session).conversation(conversation_id)
    return await _seat_in_place(session, place, handle)


async def seats_in_conversations(
    session: AsyncSession,
    pairs: Iterable[tuple[uuid.UUID, str | None]],
) -> dict[tuple[uuid.UUID, str | None], tuple[ResolvedAgent, str] | None]:
    """`conversation_seat` for many conversations and handles at once.

    A scan asks the same question of every message it is about to answer, and
    those messages come from a handful of conversations: each conversation's
    place (a room, a task or a 支线) is read once for the whole batch, with one
    ``IN`` query per table instead of one lookup per message. The judgement is
    the single-pair one, pair by pair — the same ``_agent_at`` / ``_seat_of``
    rules — only the place reads are shared.

    Every requested pair is in the answer, including those that resolve to no
    seat. Duplicate pairs are asked once; a missing conversation answers None.
    """
    wanted = list(dict.fromkeys(pairs))
    if not wanted:
        return {}
    places = await _places_in_many(
        session, {conversation_id for conversation_id, _ in wanted}
    )
    return {
        pair: await _seat_in_place(session, places.get(pair[0]), pair[1])
        for pair in wanted
    }


async def _places_in_many(
    session: AsyncSession, conversation_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, Place]:
    """`PlaceResolver.conversation` for many ids, reading each table once.

    Same precedence as the single read: a room's id is a room, a task's is the
    task (a 支线 that became a task shares its id), a 支线's is the thread; each
    inner place carries its room. An id naming no row, or whose room is gone,
    is absent from the answer."""
    ids = list(dict.fromkeys(conversation_ids))
    if not ids:
        return {}
    rooms = await get_by_ids(session, Topic, ids)
    tasks = await get_by_ids(session, Task, ids)
    threads = await get_by_ids(session, Thread, ids)
    inner = {**threads, **tasks}
    rooms.update(
        await get_by_ids(session, Topic, {row.room_id for row in inner.values()})
    )
    places: dict[uuid.UUID, Place] = {}
    for conversation_id in ids:
        room = rooms.get(conversation_id)
        if room is not None:
            places[conversation_id] = Place(room=room)
            continue
        task = tasks.get(conversation_id)
        if task is not None:
            room = rooms.get(task.room_id)
            if room is not None:
                places[conversation_id] = Place(room=room, task=task)
            continue
        thread = threads.get(conversation_id)
        if thread is not None:
            room = rooms.get(thread.room_id)
            if room is not None:
                places[conversation_id] = Place(room=room, thread=thread)
    return places


async def _seat_in_place(
    session: AsyncSession, place: Place | None, handle: str | None
) -> tuple[ResolvedAgent, str] | None:
    """`conversation_seat`'s judgement, with the place already read."""
    if place is None:
        return None
    if place.task is not None:
        agent = await _agent_at(session, place, handle)
        if await _seat_of(session, place.room_id, agent) is None:
            return None
        return agent, agent_instance_handle(agent.instance_id)
    agent = await session_agent_in_room(session, place.room_id, handle)
    if agent is None:
        return None
    seat = await _seat_of(session, place.room_id, agent)
    return None if seat is None else (agent, seat)


async def _agent_at(
    session: AsyncSession, place: Place, handle: str | None = None
) -> ResolvedAgent:
    """Which agent works in *place*: the teammate a task was given, else the
    room's — which is the project's unless the room has its own.

    ``handle`` is the teammate a message named, for the callers that have one:
    the seat a message is delivered to is the one it was addressed to, in a task
    as in a room. Only the wake path passes it — a turn assembles for the task's
    own agent when the message names nobody, and admission seeds its recipient
    from that same answer. Without it a message a task held for the busy seat of
    a teammate the task was not given is refused by the resume scan forever
    (``seat_has_other_agent``), while the live turn, which resolves the named
    instance, serves the same message: the two have to agree."""
    project = await ProjectRepository(session).get(place.project_id)
    if project is None:
        raise NotFoundError("Project not found")
    agents = AgentInstanceService(session)
    if place.task is not None:
        named = await _named_teammate(agents, project, handle)
        if named is not None:
            return named
        return await agents.for_task(project, place.room, place.task.agent_handle)
    return await agents.for_topic(place.room, project)


async def _agent_handle(session: AsyncSession, topic_id: uuid.UUID) -> str:
    """The handle 芝士 authors under in this topic.

    Resolved from the roster's execution bindings rather than the fixed
    ``cheese`` string, so a room hosting more than one agent attributes each
    message to the one that wrote it — normally this topic's own 分身.

    A turn is also where a room seeded before 分身独立身份 swaps its shared
    ``cheese`` seat for that 分身: doing it here means every live room migrates
    without a data migration, and one that never runs a turn never needed it.
    """
    members = TopicMemberService(session)
    await members.migrate_shared_agent_seat(topic_id)
    return await members.resolve_agent_handle(topic_id)


async def _seat_of(
    session: AsyncSession, room_id: uuid.UUID, agent: ResolvedAgent
) -> str | None:
    """The seat *agent* authors under in this room, or None when it may not act
    here at all.

    Its own seat when it sits on the room's roster: a room seats any number of
    agents, so the one that answers is the one the message named or the task
    was given, and its blocks carry that one's seat. A teammate a task is given
    is seated in the task's room then (`TopicService.give_task_teammate`).

    Only the room's own agent acts without a seat of its own, under the room's:
    a room seated before agents had seats of their own carries a room-derived
    seat (or the shared ``cheese`` one, which `_agent_handle` swaps), and a
    private 1:1 from then the same, and that seat is the room's agent's. Any
    other agent off the roster gets None, not that seat: lent it, a teammate
    given a task in a room it did not sit in wrote as the room's agent and
    shared that agent's machine and credential (FB-99).
    """
    seat = agent_instance_handle(agent.instance_id)
    if seat in await TopicMemberService(session).agent_handles(room_id):
        return seat
    room = await TopicService(session).get(room_id)
    project = await ProjectRepository(session).get(room.project_id) if room else None
    if room is None or project is None:
        return None
    own = await AgentInstanceService(session).for_topic(room, project)
    if own.instance_id != agent.instance_id:
        return None
    return await _agent_handle(session, room_id)


async def _acting_handle(
    session: AsyncSession, topic_id: uuid.UUID, agent: ResolvedAgent
) -> str:
    """The handle this turn authors under (`_seat_of`); an agent that may not
    act in this room is refused, saying so."""
    seat = await _seat_of(session, topic_id, agent)
    if seat is None:
        raise UnprocessableEntityError(say("teammateNotSeatedHere"))
    return seat


async def require_pinned_seat(
    session: AsyncSession, topic_id: uuid.UUID, instance_id: uuid.UUID
) -> str:
    """An explicit recipient never borrows the room's default seat."""
    seat = agent_instance_handle(instance_id)
    if seat not in await TopicMemberService(session).agent_handles(topic_id):
        raise ValidationError("The addressed agent is no longer seated in this room")
    return seat


async def _gateway_budget_target(
    session: AsyncSession, project_id: uuid.UUID
) -> float | None:
    """The L2 max_budget this project's key should carry, or None when the
    gateway must not be told one at all (no price knob, or the project is
    unmetered). Reading it belongs to the READ path: a caller that finds the
    key's budget already in step has nothing to write and nothing to lock."""

    credits = await UsageService(session).project_credits(project_id)
    return credits["gateway_budget_usd"]


async def _pass_policy_gate(
    session: AsyncSession,
    topic_id: uuid.UUID | None,
    call: gate.Call,
    policy: gate.Policy,
    *,
    actor: str,
) -> _Proposed | None:
    """闸门放行就返回 `None`；变提议就把提议落进房间，交回它和刚落下的那条事
    件，收场由调用点自己写；拒绝照抛。

    提议**不是报错**（结论 40：产物是一条给人的提议）。所以它不能顺着 `raise`
    走：轮次那条路上抛出去的东西最后是屏幕上一个红色的 error 帧，而同一个判决
    在网关代填那条路（`api/routes/llm_proxy.py`）上是一份 `allow=false` 加
    `reason_kind=binding` 的应答体——一个判决两种形状，人看到的还是「出错了」。
    拒绝仍然抛：那一档要的就是一次说得出口的拒绝（I27），和「解析不出模型」在调
    用点是同一种东西。

    交回来的那条事件可能是 `None`：这条提议已经提过了（`propose` 按身份去重）。
    调用点照样收场，只是房间里不再多一句一样的话。

    提议写在**调用方这条 session** 上，提交也归调用方——`propose` 欠的不变量是
    「落库之后这次调用必须中止」，而收场的那一步本来就在调用点。

    没有房间（项目级的调用）就落不下这条提议：提议是房间里的一条事件。那种情
    形下超档只剩拒绝这一条路，闸门照抛。
    """
    verdict = gate.check(call, policy, actor)
    if isinstance(verdict, gate.Allowed):
        return None
    if topic_id is None:
        raise gate.OverTier(verdict.content)
    block = await propose(session, verdict, place_id=topic_id)
    return _Proposed(
        verdict, _block_payload(BlockOut.model_validate(block)) if block else None
    )


async def _bail_notice(
    *,
    project_id: uuid.UUID,
    topic_id: uuid.UUID,
    turn_id: uuid.UUID,
    session: AsyncSession,
    text: str,
) -> dict:
    """A system event for a turn that ends before it starts, committed with
    the rest of the assembling transaction. A room that shows nothing has no
    way to tell 「没开始」 from 「还在想」."""
    landed = landing(EventAbout.room, project_id=project_id, room_id=topic_id)
    block = await BlockRepository(session).add(
        project_id=landed.project_id,
        conversation_id=landed.conversation_id,
        author="system",
        author_type=AuthorType.platform,
        content=text,
        kind=BlockKind.event,
        turn_id=turn_id,
        meta={"platform": True},
    )
    await session.commit()
    return _block_payload(BlockOut.model_validate(block))


# --- 记忆：这一轮该不该拦、这条改动说进哪间房 ---------------------------


def _dream_refusal_phrase(before: dict[str, str], answer: dict) -> str:
    """整理这一轮该不该拦；该拦就说一句为什么，不该拦就是空串。

    两个判据取一个共同的形状：**会话那一侧挡下的**（`held`，会话自己那条兜底
    已经把它们放回树里了，所以平台这一侧看不见）+ **回来的树里真的少了的**。
    两句话合成一句，读的人要知道是哪个作用域、少了几条。
    """
    held = [str(path) for path in (answer.get("held") or [])]
    if held:
        return f"一次要删的太多，已拦下 {len(held)} 条删除：" + "、".join(
            sorted(held)[:10]
        )
    files = {
        str(path): content
        for path, content in (answer.get("files") or {}).items()
        if isinstance(content, str)
    }
    refused = dream.refused_scopes(before, files)
    if not refused:
        return ""
    parts = [f"{prefix} 要删 {len(paths)} 条" for prefix, paths in refused.items()]
    return "；".join(parts)


async def _private_room(
    session: AsyncSession, project_id: uuid.UUID, owner: str | None
) -> uuid.UUID | None:
    """这个人和芝士的私聊：他 private 那棵树的改动记在这里
    （`get_or_create_private` 先找后建，同一个人打开的是同一间）。"""
    if not owner:
        return None
    # deferred-import: tests replace this name on app.domain.topic.services
    from app.domain.topic.services import TopicService

    topic = await TopicService(session).get_or_create_private(
        project_id=project_id, user_handle=owner, named_teammate=named_teammate
    )
    return topic.id


async def _say_memory_change(
    session: AsyncSession,
    project_id: uuid.UUID,
    change: MemoryChange,
    scopes: list[tuple[MemoryFileScope, str | None]],
    *,
    writer_room: uuid.UUID,
) -> None:
    """改动记成运行记录：team 的记在写它的那段对话（`writer_room`）的现场里，
    private 的记在那个人的私聊里。

    不进对话：一条记忆是 agent 写下的一份观察，没有人在等它，想知道这一轮改了
    哪几条的人去现场看。两棵树分开记，因为读它们的人不是一批：把某个人 private
    的 diff 记进一间多人的房间，等于把一个人的偏好广播给房间里的人。

    被平台盖回去、或超了上限没收的那几条，还要说给**写它的那个 agent**：它在
    `writer_room`（这次对账的那间房）里，而灰字落的是那棵树的房间——team 的是
    总览，多数时候不是它所在的那一间。`agent_notice` 只进本房间下一轮的
    prompt，跟着灰字进了总览，写它的 agent 一个字都读不到，以为写成功了；读到
    的反倒是总览里那位，而它什么都没写过。所以那一句从灰字上拿下来，单独落进
    `writer_room`，不露面（`in_room: False`）：房间里的人已经在那棵树的房间看
    到了这件事。
    """
    #: (灰字那一行, 说给写它的 agent 的那一句)。那一行只是这条块自己的正文：
    #: 它不露面，读到它的只有 prompt 里的 `agent_notice`。
    told: list[tuple[str, str]] = []
    for scope, owner in scopes:
        part = change.scoped(prefix_of_scope(scope, owner))
        if part.is_empty() and not part.refused and not part.rejected:
            continue
        content, meta = memory_changed_notice(
            where=(
                say("memoryScopeTeam")
                if scope is MemoryFileScope.team
                else say("memoryScopePrivate")
            ),
            summary=part.summary(),
            diff=part.diff,
            refused=part.refused,
            rejected=part.rejected,
            unindexed=part.unindexed,
        )
        if for_writer := meta.pop(AGENT_NOTICE_META_KEY, None):
            told.append((content, for_writer))
        room = (
            writer_room
            if scope is MemoryFileScope.team
            else await _private_room(session, project_id, owner)
        )
        if room is not None:
            await keep_record(session, conversation_id=room, content=content, meta=meta)
    # `writer_room` is the writer's conversation: a room's line, or a task or
    # 支线 inside one, which `announce` reaches through its room.
    writer = await PlaceResolver(session).conversation(writer_room)
    if told and writer is not None:
        await announce(
            session,
            place_id=writer.room_id,
            task_id=writer.inner_id,
            content="\n".join(line for line, _ in told),
            meta={
                **notice(
                    EVENT_MEMORY_CHANGED, severity=SEVERITY_INFO, who=WHO_PLATFORM
                ),
                "in_room": False,
                AGENT_NOTICE_META_KEY: "\n\n".join(said for _, said in told),
            },
        )


def _model_policy_call(project, agent=None) -> gate.Call:
    """这一轮要用的模型，写成闸门认得的那一次调用（结论 3 后半）。

    两处问它：轮次组装（在这一轮占用任何东西之前）和 `_model_kwargs`（平台自己发
    起的那几轮不经过组装）。构造写在这里一处，所以两处问的确实是同一次调用。

    模型花的是项目的额度，所以点头的是项目的主人。空 handle（建库早期留下的项目）
    在寻址那一层被丢掉：房间里照样有这条提议，只是没有人被单独通知 —— 好过把它投
    给一个猜出来的人。
    """
    choices = catalog(project.settings)
    bound = binding.resolve(
        None,
        choices,
        agent_model=agent.configuration.get("model") if agent else None,
        default_model=(project.settings or {}).get("default_model"),
    )
    return gate.Call(
        resource=gate.Resource.model,
        subject=bound.model,
        label=choices[bound.model]["label"],
        tier=choices[bound.model]["tier"],
        approver=project.owner_handle or "",
        overseas=choices[bound.model]["overseas"],
    )


async def instance_of_handle(
    session_factory, place_id: uuid.UUID, agent_handle: str
) -> uuid.UUID | None:
    """The agent instance *agent_handle* names in the project *place_id* is in,
    or None when no instance carries it."""
    # deferred-import: PlaceResolver is bound at the top of this module already
    from app.domain.room_task.place import PlaceResolver

    async with session_factory() as session:
        place = await PlaceResolver(session).conversation(place_id)
        if place is None:
            return None
        return await session.scalar(
            select(AgentInstance.id).where(
                AgentInstance.project_id == place.project_id,
                AgentInstance.handle == agent_handle,
            )
        )
