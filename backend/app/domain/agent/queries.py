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
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationError
from app.core.sentences import say
from app.domain.agent.announce import announce
from app.domain.agent.platform_notices import (
    EVENT_MEMORY_CHANGED,
    SEVERITY_INFO,
    WHO_PLATFORM,
    memory_changed_notice,
    notice,
)
from app.domain.agent_instance.services import AgentInstanceService, ResolvedAgent
from app.domain.block.about import EventAbout, landing
from app.domain.block.models import AGENT_NOTICE_META_KEY, AuthorType, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.block.schemas import BlockOut
from app.domain.identity.handles import agent_instance_handle
from app.domain.memory import dream
from app.domain.memory.files import MemoryFileScope
from app.domain.memory.session import MemoryChange, prefix_of_scope
from app.domain.policy import gate
from app.domain.policy.proposals import propose
from app.domain.project.models import Project
from app.domain.project.repositories import ProjectRepository
from app.domain.room_task import binding
from app.domain.room_task.place import Place, PlaceResolver
from app.domain.run_record.service import record as keep_record
from app.domain.topic.models import Topic
from app.domain.topic.services import TopicService
from app.domain.topic_membership.services import TopicMemberService

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _Proposed:
    """闸门把这次调用变成了一条提议：那条提议，和它在房间里刚落下的那条事件。

    `landed` 是 `None` 表示这条提议之前就提过了（`policy/proposals.py` 按身份去
    重）。两个调用点各取一半——轮次要那条事件来收场，平台自己发起的那几轮要那句话
    来抛。
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
        try:
            return agents.resolved(await agents.for_handle(project, agent_handle))
        except NotFoundError:
            seated = await agents.for_seat_handle(project, agent_handle)
            if seated is not None:
                return seated
            logger.warning(
                "session agent %r is not in project %s; using the room's",
                agent_handle,
                project.id,
            )
    return await agents.for_topic(topic, project)


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
    room's roster. In a task it is the task's own agent, which need not sit on
    the room's roster: only the owner talks to it. None for a conversation that
    no longer exists."""
    place = await PlaceResolver(session).conversation(conversation_id)
    if place is None:
        return None
    if place.task is not None:
        agent = await _agent_at(session, place)
        return agent, agent_instance_handle(agent.instance_id)
    agent = await session_agent_in_room(session, place.room_id, handle)
    if agent is None:
        return None
    return agent, await _acting_handle(session, place.room_id, agent)


async def _agent_at(session: AsyncSession, place: Place) -> ResolvedAgent:
    """Which agent works in *place*: the teammate a task was given, else the
    room's — which is the project's unless the room has its own."""
    project = await ProjectRepository(session).get(place.project_id)
    if project is None:
        raise NotFoundError("Project not found")
    agents = AgentInstanceService(session)
    if place.task is not None:
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


async def _acting_handle(
    session: AsyncSession, topic_id: uuid.UUID, agent: ResolvedAgent
) -> str:
    """The handle this turn authors under: the seat of the agent that was
    addressed, when it sits on this room's roster.

    A room seats any number of agents, so the one that answers is the one
    the message named, and its blocks carry that one's seat. A room from
    before agents had seats of their own (only its room-derived seat on the
    roster) and a private 1:1 fall through to the room's seat as before.
    """
    seat = agent_instance_handle(agent.instance_id)
    if seat in await TopicMemberService(session).agent_handles(topic_id):
        return seat
    return await _agent_handle(session, topic_id)


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
    from app.domain.usage.services import UsageService

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
    在 `PUT /topics/{id}/compute-profile` 上是 200 加一个 `proposal` 字段——一
    个判决两种形状，人看到的还是「出错了」。拒绝仍然抛：那一档要的就是一次说
    得出口的拒绝（I27），和「解析不出模型」在调用点是同一种东西。

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
        return f"会话侧拦下 {len(held)} 条删除：" + "、".join(sorted(held)[:10])
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
    from app.domain.topic.services import TopicService

    topic = await TopicService(session).get_or_create_private(
        project_id=project_id, user_handle=owner
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
    if told:
        await announce(
            session,
            place_id=writer_room,
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
    choices = binding.catalog(project.settings)
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
    )


async def instance_of_handle(
    session_factory, place_id: uuid.UUID, agent_handle: str
) -> uuid.UUID | None:
    """The agent instance *agent_handle* names in the project *place_id* is in,
    or None when no instance carries it."""
    from app.domain.agent_instance.models import AgentInstance
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
