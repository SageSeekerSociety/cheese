"""Real turn intake: recipient and prompt preparation, without a service host."""

import logging
import time
import uuid
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import BaseError, NotFoundError
from app.core.sentences import say
from app.domain.agent.compute_configs import (
    bind_room_device_choice,
    fix_task_choice,
    machine_policy_call,
    place_choice,
    room_choice,
)
from app.domain.agent.harness import SessionRef
from app.domain.agent.harness.prompt import (
    TASK_MACHINE_NOT_STARTED,
    TASK_MACHINE_STARTED,
    UNTITLED_TASK,
    build_session_opening,
    platform_prompt,
    prompt_line,
    publication_prompt,
)
from app.domain.agent.mcp_notice import unconnected_mcp
from app.domain.agent.prompt import (
    _pending_platform_notices,
    _platform_preamble,
    _replay_notice,
    _resume_notice,
    _sandbox_limits,
    _session_opening_lines,
    _topic_ref_lists,
    offered_attachments,
)
from app.domain.agent.queries import (
    _acting_handle,
    _agent_at,
    _bail_notice,
    _model_policy_call,
    _pass_policy_gate,
    _session_agent,
    require_pinned_seat,
)
from app.domain.agent.realtime.broker import get_broker
from app.domain.agent.room.sessions import RoomSessions
from app.domain.agent.room.system_prompt import session_system_prompt
from app.domain.agent.room.thread_context import thread_context as _thread_context
from app.domain.agent.room.thread_context import thread_tasks as _thread_tasks
from app.domain.agent.session_host.contract import Image
from app.domain.agent.session_host.host import keeps_memory
from app.domain.agent.turn.intake.rooms import _is_dm, room_roster
from app.domain.agent.turn.state.inputs import _addressed_to, _pending_input_blocks
from app.domain.agent.turn.state.live import LiveWork
from app.domain.agent.turn.steps.prepared import PreparedSend
from app.domain.agent.turn.steps.send import SendEffects, send_prepared
from app.domain.agent.turn_speakers import is_routine_run, turn_speakers
from app.domain.agent.work_policy import resolve_compute_id
from app.domain.agent_instance.own import owned_instance
from app.domain.agent_instance.services import (
    AgentInstanceService,
    ResolvedAgent,
    memory_pool,
)
from app.domain.agent_session.services import AgentSessionService
from app.domain.block.output_effects import count_prompt_attempt
from app.domain.block.queries import (
    earlier_message_count,
    message_reply_target,
    turn_history,
)
from app.domain.delivery.receipts import held_blocks
from app.domain.identity.actor import Actor
from app.domain.memory.files_store import MemoryIndex, memory_index
from app.domain.memory.scopes import MemoryScope
from app.domain.policy import gate
from app.domain.project import artifacts as project_artifacts
from app.domain.project.overview import project_brief, render_overview
from app.domain.project.reads import load_project
from app.domain.project.services import ProjectService
from app.domain.room_task import naming
from app.domain.room_task.models import TaskStatus, TaskTitleSource
from app.domain.room_task.place import PlaceResolver, doc_text_of
from app.domain.task import teaching as teaching_context
from app.domain.task.teaching import TeachingContext
from app.domain.thread.services import thread_opening
from app.domain.topic.models import Topic, TopicStatus
from app.domain.topic.reads import conversation_progress, project_topics
from app.domain.topic_membership.services import TopicMemberService
from app.domain.usage.ledger import team_terms

logger = logging.getLogger("app.domain.agent.room.turn")


def _proposal_frames(landed: dict | None) -> list[dict]:
    """撞上档位策略的那一轮怎么收场：房间里刚落下的那条提议，然后 done。

    没有 error 帧——这一轮没有发生，但也没有出错，下一步在提议收件人手上（结论
    40）。`landed` 是 `None` 时这条提议之前就提过了，房间里不再多一句一样的话。
    """
    frames: list[dict] = []
    if landed is not None:
        frames.append({"type": "event_block", "block": landed})
    frames.append({"type": "done"})
    return frames


@dataclass(frozen=True, slots=True)
class Launch:
    """What a seat's session is started with, read without opening a turn
    (``TurnPreparation.launch_inputs``): the same inputs a turn hands its session,
    so a session started from them is the one the next turn would start."""

    session: SessionRef
    runtime: RoomSessions
    system_prompt: str
    resume_token: str | None
    model: str | None
    env: dict[str, str] | None
    needs_place: bool


@dataclass(frozen=True, slots=True)
class TurnContext:
    """Everything one turn needs to run, read once before anything runs it.

    A turn is assembled and then executed, and the two halves want opposite
    things from a database session: assembling is a dozen reads that belong in
    one transaction, executing is minutes of streaming that must hold none. This
    is what crosses between them — so a backend that runs a turn some other way
    receives THIS, rather than a session and instructions on what to read.
    """

    # Who is here and what they are working under.
    project_id: uuid.UUID
    # Where: the room, and the task or 支线 when the conversation is one of
    # theirs.
    room_id: uuid.UUID
    inner_id: uuid.UUID | None
    acting_agent: str
    agent: ResolvedAgent
    agent_pool: tuple[MemoryScope, str] | None
    role: str | None
    roster: list[dict]
    untitled: bool
    # 本周教学范围 (#8d772257). None for every project that is not a course —
    # and None is what keeps the prompt byte-identical to what it was before
    # this key existed, which is the property the non-course tests pin.
    teaching: TeachingContext | None

    # What this turn was given, and what it is being asked about.
    prompt_text: str
    pending_ids: list[uuid.UUID]
    # The platform notices this prompt carries. Stamped consumed alongside the
    # human blocks and by the same turn — a notice this turn actually read must
    # not be read again — but kept separate up to that point, because every
    # other question asked of `pending_ids` is about who spoke.
    notice_ids: list[uuid.UUID]
    turn_images: list[Image]
    replay_notice: str | None
    resume_session_id: str | None

    # What it should know: the doc, the memories, the checklist it left behind,
    # the cards waiting on it, and which段 of the flow this topic is in.
    doc_text: str | None
    # 注入用的项目总览（`project/overview.py`），和芝士读写它时要用的编号。
    overview_doc_text: str | None
    overview_doc_id: uuid.UUID | None
    # 这一轮注入的 L1 记忆索引（team 一份 + 本轮发言人各一份）。正文不在里面：
    # 每条记忆的正文在会话目录 `.cheese/memory/` 下，agent 自己去读（见
    # `memory/instructions.py`）。None = 「这一轮没走注入那条路」。
    memory: MemoryIndex | None
    prior_progress: list[dict]
    # Chat messages already in the room, apart from the ones this turn delivers.
    earlier_messages: int
    # For a 支线: the message it hangs under and what the main line said just
    # before it; and the channel's tasks still open. None elsewhere.
    thread_context: str | None
    thread_tasks: str | None
    topic_refs: list[dict]
    topic_refs_for_prompt: list[dict]
    # 这个项目交出去过的东西 —— 下一次交付要从这几个名字里挑一个。空着是「还没交出
    # 去过东西」，None 是「这间房间不交付」（私聊）。
    artifacts: list[dict] | None

    # Which machine, and whether it reports its own liveness (which decides who
    # owns this turn's clock; see the `turn_ceiling` frame).
    provider: RoomSessions
    # 这一轮跑在哪个骨架上，解析过一次的那个答案（结论 28）。会话行的键里有它，
    # 所以执行那一半必须读这里，不能自己再解析一次。
    harness: str
    # 这一轮要不要一双手 (结论 19，不变量 I2)。解析的产物，不是房间的属性：同一
    # 条会话可以这一轮只聊天、下一轮动文件，而租手发生在解析之后。
    needs_place: bool
    # A 支线's work, and a task's until its owner starts it, is not kept: it
    # may read, run and try things on its own machine, and nothing it does
    # there reaches the project. What changes the project is done in a task
    # its owner started.
    keeps_nothing: bool
    # What a task's session is told it may do on the machine; None outside a
    # task. It changes when the task starts, and is told again then.
    task_machine: str | None


@dataclass(frozen=True, slots=True)
class TurnBail:
    """The turn ended while it was still being assembled, and these are the
    frames that say so. Not an error: nobody was waiting on an answer, or the
    machine is still being built."""

    frames: list[dict]


class _Machines(Protocol):
    """What a turn asks of the service's compute pool (``agent.compute``)."""

    def choose(
        self, project_settings: Mapping[str, Any] | None, provider_id: str | None
    ) -> tuple[str, RoomSessions | None]: ...

    def owned(self, harness: str) -> RoomSessions | None: ...

    async def activate(self, session: SessionRef, runtime: RoomSessions) -> None: ...


class _MemoryBooks(Protocol):
    """What a turn tells the service's memory ledger (``agent.memory_ledger``)."""

    def remember_turn(
        self,
        room_id: uuid.UUID,
        seat: tuple[uuid.UUID, str],
        *,
        acting: str,
        speakers: tuple[str, ...],
    ) -> None: ...


class ModelOptions(Protocol):
    async def __call__(
        self,
        project_id: uuid.UUID,
        provider: RoomSessions | None,
        topic_id: uuid.UUID | None = None,
        *,
        agent: ResolvedAgent | None = None,
        acting_agent: str | None = None,
        platform: bool = False,
    ) -> tuple[dict, str]: ...


async def human_private_owner(session: AsyncSession, topic: Topic) -> str | None:
    if not _is_dm(topic):
        return None
    seats = await TopicMemberService(session).private_seats(topic.id)
    return seats[0] if seats is not None else None


class TurnPreparation:
    """Assemble room, recipient, memory and model facts in one transaction."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        compute: _Machines,
        memory: _MemoryBooks,
        base_prompt: str,
        skills: str,
        model_options: ModelOptions,
        refuse: Callable[[str], BaseError],
        live: LiveWork,
        send_effects: SendEffects,
    ) -> None:
        self.sessions = sessions
        self.compute = compute
        self.memory = memory
        self.base_prompt = base_prompt
        self.skills = skills
        self.model_options = model_options
        self.refuse = refuse
        self.live = live
        self.send_effects = send_effects

    async def _backend_for(
        self, session, agent, project, compute_id
    ) -> tuple[str, RoomSessions | None]:
        """The harness this agent's turn runs and the backend it runs on.

        A member's own coding agent runs its own harness on its owner's machine
        (`owner_provider`), whatever the room chose; every other agent runs the
        project's harness on the room's machine."""
        owned = await owned_instance(session, agent.instance_id)
        if owned is not None:
            return owned.harness, self.compute.owned(owned.harness)
        return self.compute.choose(project.settings if project else None, compute_id)

    async def launch_inputs(
        self, topic_id: uuid.UUID, agent_handle: str, *, acting: str
    ) -> Launch | None:
        """What this seat's session would be started with if a turn started it
        now, read without opening one — no backlog claimed, no policy gate
        passed, nothing written. None when the room has nowhere to run.

        Every input is the one `prepare` and `converse` hand
        `RoomSessions.send`, from the same helpers, so a session started from
        these is the one the next turn would compare equal and keep."""
        async with self.sessions() as session:
            place = await PlaceResolver(session).resolve(topic_id)
            if place is None or place.room.status == TopicStatus.archived:
                return None
            topic = place.room
            project = await load_project(session, topic.project_id)
            if project is None:
                return None
            agents = AgentInstanceService(session)
            agent = await _session_agent(agents, topic, project, agent_handle)
            needs_place = not _is_dm(topic)
            doc_text = await doc_text_of(session, place, needs_place=needs_place)
            role = await agents.system_prompt(agent)
            harness, provider = await self._backend_for(
                session, agent, project, resolve_compute_id(project.settings, topic)
            )
            if provider is None:
                return None
            resume_token = await AgentSessionService(session).resume_token(
                place.room_id, agent.handle, harness=harness
            )
        model_kwargs, _route = await self.model_options(
            project.id, provider, topic_id, agent=agent, acting_agent=acting
        )
        return Launch(
            session=SessionRef(project.id, topic_id, agent.handle, harness=harness),
            runtime=provider,
            system_prompt=session_system_prompt(
                self.base_prompt,
                self.skills,
                needs_place=needs_place,
                has_doc=doc_text is not None,
                role=role,
                harness=provider.harness,
                name=agent.display_name,
            ),
            resume_token=resume_token,
            model=model_kwargs.get("model"),
            env=model_kwargs.get("env"),
            needs_place=needs_place,
        )

    async def prepare(
        self,
        *,
        topic_id: uuid.UUID,
        content: str,
        turn_id: uuid.UUID,
        user_block_id: uuid.UUID | None,
        provision_actor: Actor | None,
        platform_turn: bool = False,
        delivery_id: uuid.UUID | None = None,
        recipient_instance_id: uuid.UUID | None = None,
    ) -> "TurnContext | TurnBail":
        """Read the recipient, history, memory, machine and prompt in one transaction.

        Return a ``TurnBail`` with room frames if no answer is pending or the
        machine is still being built; neither outcome starts an executor.
        """
        started = time.monotonic()
        phases_ms: dict[str, float] = {}
        # --- tx1: load topic + history, load memory ---
        async with self.sessions() as session:
            # WHERE this turn runs: a room's own line, or a task's conversation.
            place = await PlaceResolver(session).conversation(topic_id)
            if place is None:
                raise NotFoundError(say("topicNotFound"))
            topic, task = place.room, place.task
            if topic.status == TopicStatus.archived:
                raise self.refuse(say("roomArchivedUnarchiveFirst"))
            if task is not None and task.status == TaskStatus.closed:
                raise self.refuse(say("taskClosedNoTurn"))

            # Speaker-labelled prompt covering every human message 芝士 hasn't
            # been handed yet — so messages posted without @芝士 are still seen on
            # the next summon (spec §7.1 所有消息 AI 都会收到), each tagged with
            # who said it so 芝士 can tell people apart in a group topic (§8.4).
            #
            # This conversation's OWN line: a room's history leaves out its
            # tasks' conversations, and a task's is only its own.
            history = await turn_history(session, place.conversation_id)
            # A 支线's first input is the message it hangs under and its files,
            # which stay in the channel's main line: read them in while no turn
            # has read them.
            opening = await thread_opening(session, place.conversation_id)
            root = opening[0] if opening else None
            history = [*opening, *history]
            phases_ms["history"] = (time.monotonic() - started) * 1000
            pending = _pending_input_blocks(history)
            # Kept apart from `pending` on purpose: that list answers
            # 「谁说话了」 for the recipient routing, the replay counter and
            # the 「没人在等」 bail, and a platform notice is an answer to
            # none of those. It only rides into the prompt and gets stamped.
            notices = _pending_platform_notices(history)
            addressed = next(
                (block for block in history if block.id == user_block_id),
                pending[0] if pending else None,
            )
            recipient = (
                (addressed.meta or {}).get("agent_recipient") if addressed else None
            )
            agents = AgentInstanceService(session)
            pinned_seat = None
            if recipient_instance_id is not None:
                recipient = {"instance_id": str(recipient_instance_id)}
                # A seat on the room's roster: a task's agent works the task and
                # sits on no roster.
                if task is None:
                    pinned_seat = await require_pinned_seat(
                        session, place.room_id, recipient_instance_id
                    )
            # 收件人是消息落库时记下来的。记的时候还没有实例行的那些旧消息，
            # 「收件人是项目的芝士」和今天的解析是同一个答案。
            if recipient is None or recipient.get("instance_id") is None:
                agent = await _agent_at(session, place)
            else:
                agent = agents.resolved(
                    await agents.get_in_project(
                        project_id=topic.project_id,
                        instance_id=uuid.UUID(recipient["instance_id"]),
                    )
                )
            acting_agent = pinned_seat or await _acting_handle(session, topic.id, agent)
            pending = [block for block in pending if _addressed_to(block, agent.handle)]
            # Holds and waiting answers are kept per conversation, as inputs
            # are registered: a task's or a 支线's own, not its room's.
            held = await held_blocks(
                session,
                project_id=topic.project_id,
                topic_id=place.conversation_id,
                recipient_handle=acting_agent,
            )
            pending = [block for block in pending if block.id not in held]
            notices = [block for block in notices if block.id not in held]
            prompt_pending_ids = [b.id for b in pending]
            if not pending and user_block_id is not None:
                # 有人召唤，但他那条消息已经被前一轮读进 prompt 了（两个人几乎同时
                # @，第一轮在锁上把两条合并答掉）。再跑一轮就是白烧一轮算力，还会
                # 走下面的 platform_prompt 兜底、把已经答过的话当成平台指令重投一
                # 遍。这里直接收工 —— 只是不跑这一轮，不碰任何排队/锁的逻辑。
                return TurnBail([{"type": "done"}])
            # 盖章清单比 prompt 清单窄（多 agent 房间，2026-09-28 定）：
            # 没被 @ 的公共消息每个轮次都看得见，但只由「被人召唤起来的轮次」
            # 或「房间默认 agent 的轮次」盖章认领；其余在场轮次（另一个 agent
            # 被 @、平台自检）看过就算。不这么窄，并行的几个轮次会给同一条
            # 消息各盖一个 consumed_turn，而谁都没答它的话却被所有人收走。
            claims_backlog = (
                user_block_id is not None
                or agent.handle == (await _agent_at(session, place)).handle
            )
            pending_ids = [
                b.id
                for b in pending
                if claims_backlog or (b.meta or {}).get("agent_recipient") is not None
            ]
            # 图片输入: every pending image is offered to the provider; whether it
            # reaches the model as a native block depends on `embeds_images`, so
            # the prompt is built below, after the provider is picked. A file
            # that is gone is not offered (`offered_attachments`).
            turn_images, gone_files = await offered_attachments(
                session, pending, topic.project_id, place.room_id
            )

            # 私聊是名册两席的房间（结论 19）。这一轮凡是「私聊要不一样」的地
            # 方，问的都是下面两个答案之一，不再各自问一遍那个布尔。
            #
            # 一、名册上那两席，人是哪一位（席位不齐时 None）。
            private_owner = await human_private_owner(session, topic)
            # 二、这一轮要不要一双手？见 `_is_dm`：不租地点的一轮桌上只有对话、
            # 记忆和平台工具，加上会话自己那块 64 MiB 草稿区。
            needs_place = not _is_dm(topic)
            acting_agent = pinned_seat or await _acting_handle(session, topic.id, agent)
            doc_text = await doc_text_of(session, place, needs_place=needs_place)
            phases_ms["identity"] = (time.monotonic() - started) * 1000
            # 只加载「本轮发言人」的那一份 private 索引（team 那一份每间房都
            # 有）：一个项目里的人可以很多，而注入是每一轮都要付的。
            #
            # 只算**人**（`names_a_person`）：private 是「人 × 项目」的那一份，
            # 队友手里的句柄不是一个作用域。本轮说话的这几位同时也是这一轮对账
            # 要点名的那几个（`MemoryLedger.remember_turn`），周期任务那一轮的主人也
            # 在里面：他没有署名的消息，只能从那一笔投递上认（`turn_speakers`）。
            speakers = await turn_speakers(session, delivery_id, pending, private_owner)
            memory = await memory_index(
                session, topic.project_id, speaker_handles=list(speakers)
            )
            self.memory.remember_turn(
                topic.id,
                (place.conversation_id, agent.handle),
                acting=acting_agent,
                speakers=speakers,
            )
            phases_ms["memory"] = (time.monotonic() - started) * 1000
            project = await load_project(session, topic.project_id)
            # 本周教学范围 (#8d772257), for a project that came from a course's
            # 赛题. Resolved here — in the transaction everything else the prompt
            # is built from is read in, and fresh on every turn — so a 项目集 that
            # moved on to 第 4 周 is what the NEXT session starts with. (What a
            # session already running sees is `harness.prompt`'s 生效语义.)
            #
            # A project that is not a course pays nothing for this line: no
            # 赛题, or no `teaching` on the 项目集, is no query and no prompt
            # text — not a section that renders empty.
            teaching = await teaching_context.for_project(
                session=session, project=project
            )
            # 人和 agent 共同看的那一份（结论 7）：项目总览。它不是记忆，所以不走
            # 召回那条路——写它的人（或 agent）留了痕，每段对话读到的是同一份，而
            # 这正是共享记忆池做不到的两件事。
            overview_doc = (
                await ProjectService(session).overview_document(project)
                if project is not None
                else None
            )
            overview_doc_id = overview_doc.id if overview_doc is not None else None
            overview_doc_text = (
                render_overview(brief=project_brief(overview_doc.content))
                if overview_doc is not None
                else None
            )
            # Read the selected agent once so this turn's role and model agree.
            role = await agents.system_prompt(agent)
            # 骨架是这个项目在这台机器上跑的那一个（结论 28），不是这个参与者的属
            # 性。这一轮只解析这一次，往下每一处都读它：会话行的键里有骨架，两处
            # 各自解析一次就够把一条会话拆成两条。
            compute_id = resolve_compute_id(
                project.settings if project else None, topic, task
            )
            wanted_harness, provider = await self._backend_for(
                session, agent, project, compute_id
            )
            agent_pool = memory_pool(topic.project_id, agent)
            # Roster so 芝士 can @ real teammates (not just name them in prose).
            # 私聊里没有第三个人可点名，名册也就不进提示词——`[]` 和「没有名册这
            # 回事」在下游是两种情况（见 `HookWorkState.roster`）。问的是这间房
            # 是不是私聊，不是它此刻坐了几个人：名册还要往下走进 `announce_mentions`。
            roster = await room_roster(session, topic.project_id, topic)
            # Topic list so 芝士 can cross-reference topics with <#id> tokens.
            # 两份，故意的：`topic_refs` 是 `@标题` 的**解析表**（全量，含已归档
            # ——用户自己打 @某个归档话题也必须还能变成链接）；
            # `topic_refs_for_prompt` 只是**渲染**进 system prompt 的子集。
            # 私密频道不进别的房间的提示词：它的名字只给在里面的人看。
            listed_topics = await project_topics(session, topic.project_id)
            all_topics = [t for t in listed_topics if not t.members_only]
            topic_refs, topic_refs_for_prompt = _topic_ref_lists(
                all_topics, exclude_id=topic.id
            )
            # 产物清单：交付时点名用的那几个名字 (#1085 结论三)。不租地点的一轮里
            # 没有交付，那里连这一段都不该有；空清单和「没有清单这回事」是两种情况，
            # 前者要说话（第一次交付只能新建），后者一个字都不说，所以给的是 None。
            # 别的私密频道里交付的不进这份清单，理由同上面的话题列表
            # （`artifacts.hidden_from_room`）。
            artifact_refs = (
                [
                    {
                        "id": str(a.id),
                        "name": a.name,
                        "version": a.version,
                        "about": a.about,
                    }
                    for a in await project_artifacts.list_for_project(
                        session,
                        topic.project_id,
                        hidden={
                            t.id
                            for t in listed_topics
                            if t.members_only and t.id != topic.id
                        },
                    )
                ]
                if needs_place
                else None
            )
            project_id = topic.project_id
            # This agent's conversation here, not just any: a room may host
            # several agents and each resumes its own (agent_session/models.py).
            # Looked up under the same key the turn that stores it writes under
            # (`_agent_at`) — reading under one key and writing under another
            # does not fail, it hands back None and starts a brand-new
            # conversation, which is the failure this whole path prevents.
            session_agent = agent
            resume_session_id = await AgentSessionService(session).resume_token(
                place.conversation_id,
                session_agent.handle,
                harness=wanted_harness,
            )
            # The platform names tasks itself (room_task/naming.py). Only where
            # it cannot — no gateway to call — is the task's own session asked
            # to, and never in a project that chose to name its tasks by hand.
            untitled = (
                task is not None
                and task.title_source == TaskTitleSource.placeholder
                and not naming.available()
                and naming.naming_mode(project.settings if project else None) == "auto"
            )
            # 进度层 (#187): the checklist the last turn left behind. Read inside
            # tx1 with everything else the prompt is built from, so no extra
            # round trip; empty list when this topic has never had one.
            progress_row = await conversation_progress(session, place.conversation_id)
            prior_progress = [
                dict(item) for item in (progress_row.items if progress_row else [])
            ]
            earlier_messages = await earlier_message_count(
                session, place.conversation_id, excluding=prompt_pending_ids
            )
            routine_run = await is_routine_run(session, delivery_id)
            thread_context = (
                await _thread_context(session, topic, root, routine_run=routine_run)
                if root is not None
                else None
            )
            thread_tasks = (
                await _thread_tasks(session, topic, root) if root is not None else None
            )
            if place.thread is not None:
                # So the main line hears when an AI teammate starts and stops
                # answering in this 支线 (`InProcessBroker.publish`).

                get_broker().activity.note_thread(place.thread.id, place.room_id)
            # Resolve the room choice, then the explicit project default.
            phases_ms["metadata"] = (time.monotonic() - started) * 1000
            # 先问这台机器上有没有可用的骨架，再过档位策略：策略那一步要解析模型，
            # 而一个没挂上的骨架一个模型都指不到，先问它就会以「没有默认模型」收场，
            # 房间读到的不是真正的原因。
            if provider is None:
                # The machine is fine; nothing this deployment lists runs on it.
                return TurnBail(
                    [
                        {
                            "type": "event_block",
                            "block": await _bail_notice(
                                project_id=topic.project_id,
                                topic_id=place.room_id,
                                turn_id=turn_id,
                                session=session,
                                text=say("harnessNotDeployed", harness=wanted_harness),
                            ),
                        },
                        {"type": "done"},
                    ]
                )
            # 这一轮要占的两样东西 —— 哪台机器、哪个模型 —— 在这里一起过项目的档位
            # 策略（结论 3 后半、结论 40 后半）。位置是**解析之后、占用之前**：再
            # 往下就是写绑定、开机器、发请求，撞上策略的调用一旦走到那里，「这一轮
            # 没有发生」就不再是真的 —— 而那正是提议与拒绝共同的前提。
            #
            # 房间从没打开过算力选择器也照样过闸门：决定一个房间占谁的机器的是这
            # 里，不是 `PUT /topics/{id}/compute-profile`。那条路由是人主动去点的
            # 少数情形，它和这里问的是同一个闸门。
            if provider.deferred_work and project is not None and needs_place:
                conversation = await AgentSessionService(session).ensure(
                    place.conversation_id, agent.handle, harness=wanted_harness
                )
                if conversation.execution_request is None:
                    conversation.execution_request = {
                        "generation": str(uuid.uuid4()),
                        "choice": place_choice(
                            topic, task, project.settings
                        ).model_dump(),
                        "authorized_by": None,
                    }
                if provision_actor is not None and provision_actor.via == "token":
                    conversation.execution_request = {
                        **conversation.execution_request,
                        "authorized_by": {
                            "handle": provision_actor.handle,
                            "user_id": provision_actor.user_id,
                            "via": provision_actor.via,
                        },
                    }
            if project is not None:
                actor_handle = acting_agent or agent.handle
                tiers = (await team_terms(session, project.team_id)).model_tiers
                policy = gate.policy_of(project.settings, tiers)
                # 项目不限档时，机器这一侧一步也不多走：把「要哪台机器」写成一次调用
                # 得列项目设备、host health、再取机主，而判决与这些查询无关。方案的
                # 档位只管模型，不进这一侧。
                if (
                    needs_place
                    and not provider.deferred_work
                    and not policy.lets_everything_through
                ):
                    proposed = await _pass_policy_gate(
                        session,
                        place.room_id,
                        await machine_policy_call(
                            session,
                            project=project,
                            topic=topic,
                            choice=place_choice(topic, task, project.settings),
                        ),
                        policy,
                        actor=actor_handle,
                    )
                    if proposed is not None:
                        await session.commit()
                        return TurnBail(_proposal_frames(proposed.landed))
                proposed = await _pass_policy_gate(
                    session,
                    place.room_id,
                    _model_policy_call(project, agent),
                    policy,
                    actor=actor_handle,
                )
                if proposed is not None:
                    await session.commit()
                    return TurnBail(_proposal_frames(proposed.landed))
            if (
                needs_place
                and not provider.deferred_work
                and compute_id == "device"
                and topic.compute_config is None
            ):
                await bind_room_device_choice(
                    session, topic, project.settings if project else None
                )
            phases_ms["provider"] = (time.monotonic() - started) * 1000
            # The prompt is built HERE, not where `pending` was computed: an
            # attachment line has to describe how the image reaches 芝士 on THIS
            # backend, and that is only knowable once the provider is picked.
            # `getattr` default True: a provider from outside this repo that
            # never declared the capability keeps the old wording rather than
            # being told, wrongly, that it drops images.
            #
            # No pending human block ⇒ nobody spoke: this is a resume nudge
            # or a returned conclusion. Say so, rather than handing
            # 芝士 bare text that looks like a person's message.
            embeds_images = getattr(provider, "embeds_images", True)
            # A reply carries the message it answers: that message is seldom in
            # the backlog (an earlier turn read it), and 「改一下这条」 without
            # 「这条」 is a request 芝士 cannot act on. One already in the
            # backlog is quoted there, so it is not quoted twice.
            replied = {
                b.id: parent
                for b in pending
                if b.reply_to is not None
                and b.reply_to not in prompt_pending_ids
                and (parent := await message_reply_target(session, b.reply_to))
                is not None
            }
            backlog = "\n".join(
                prompt_line(
                    b,
                    embeds_images=embeds_images,
                    replied=replied.get(b.id),
                    recipient=agent.handle,
                    gone=b.id in gone_files,
                )
                for b in pending
            )
            prompt_text = backlog or platform_prompt(content)
            # 平台指令不会被待读消息挤掉。A platform turn EXISTS because of its
            # instruction — raise a worker for this thread, relay this returned
            # card — and nobody re-sends it: the backlog is marked consumed by
            # this same turn, so an instruction dropped here is gone for good and
            # the thread never gets a worker. Dropping it was easy to miss
            # because it needs a room where somebody typed without summoning
            # 芝士, which is a room's ordinary state (没 @ 不等于没说) rather than
            # a rare race.
            if platform_turn and backlog:
                prompt_text = f"{backlog}\n\n{platform_prompt(content)}"
            # 先背景，再这一轮要做的事。What moved under the session is the frame
            # the rest of the prompt has to be read in — a request to revise the
            # 验收标准 means something different once you know that section moved
            # ten minutes ago. One marker over all of them: the marker claims
            # institutional authority, and repeating it per line spends that.
            if preamble := _platform_preamble(notices):
                prompt_text = f"{preamble}\n\n{prompt_text}"
            # 重放可见 (#416): count this attempt on the blocks themselves. A
            # turn that dies stamps no `consumed_turn`, so the SAME batch is
            # re-sent next turn, and the next — correct (a dead turn must not
            # eat a message) but silent. From the room, "every reply fails" and
            # "this one batch keeps failing" look identical, and the second one
            # is the diagnosis. Counting at prompt-build time is the only place
            # that sees a failed attempt at all.
            if recipient_instance_id is not None and task is None:
                await require_pinned_seat(session, place.room_id, recipient_instance_id)
            replay_n = await count_prompt_attempt(session, prompt_pending_ids, turn_id)
            # Committed HERE and not left to ride the conditional commit further
            # down: that one only fires on a topic's FIRST turn (compute_config
            # still None), so on every later turn this session closes without a
            # commit and the counter silently rolls back — which is the exact
            # failure mode this counter exists to expose.
            await session.commit()
            replay_notice = _replay_notice(replay_n, pending)
            # turn 活跃度检测: a driven runtime judges liveness itself
            # (docs/agent-liveness.md) and holds its own inner ceiling
            # (which can be hours), so the outer wall-clock wrap (runtime.py) must
            # be told the REAL ceiling via a `turn_ceiling` frame instead of
            # killing the turn at the generic `agent_turn_timeout_s`. Without this
            # the device's own two-layer fix is dead on arrival — the outer guard
            # still kills at 900s.
            # 不租手的一轮身上不钉机器。钉了就是给一段永远不会用到机器的对话记上
            # 一台机器，而这一行本来是给「以后别换机器」用的。
            if needs_place and provider is not None:
                if await fix_task_choice(session, topic, task, project):
                    await session.commit()
            if needs_place and provider is not None and topic.compute_config is None:
                # v4 affinity red line: materialize the effective target BEFORE
                # the first provider call. A later project-default change must
                # never move an existing work tree or resumable Claude session.
                # The WHOLE choice, not its pool: 一个话题一个容器（2026-09-28
                # 决定，推翻结论 60 的后半）——这份选择就是**这一间房**的选择，
                # 房间里每一条会话（现在的和以后进来的）都工作在它算出来的那台机
                # 器上，所以写下池名而不写整份，后面的每一条都会拿到 标准配置 或
                # 「哪台空着」，而不是第一条会话被给到的那一份规格或那台机器。

                topic.compute_config = room_choice(
                    topic, project.settings if project else None
                ).model_dump()
                await session.commit()
            if recipient_instance_id is not None and task is None:
                await require_pinned_seat(session, place.room_id, recipient_instance_id)
            phases_ms["committed"] = (time.monotonic() - started) * 1000
        logger.info(
            "chat_assembly_timing topic=%s turn=%s elapsed_ms=%.3f phases_ms=%s",
            topic_id,
            turn_id,
            (time.monotonic() - started) * 1000,
            phases_ms,
        )
        return TurnContext(
            room_id=place.room_id,
            inner_id=place.inner_id,
            # Except the turn that is a routine's run in a 支线: a rule its
            # owner confirmed, which keeps what it produces.
            keeps_nothing=not place.keeps_work
            and not (place.thread is not None and routine_run),
            task_machine=None
            if task is None or place.thread is not None
            else TASK_MACHINE_NOT_STARTED
            if task.started_at is None
            else TASK_MACHINE_STARTED,
            acting_agent=acting_agent,
            agent=agent,
            agent_pool=agent_pool,
            doc_text=doc_text,
            overview_doc_text=overview_doc_text,
            overview_doc_id=overview_doc_id,
            memory=memory,
            pending_ids=pending_ids,
            notice_ids=[b.id for b in notices],
            prior_progress=prior_progress,
            earlier_messages=earlier_messages,
            thread_context=thread_context,
            thread_tasks=thread_tasks,
            project_id=project_id,
            prompt_text=prompt_text,
            provider=provider,
            harness=wanted_harness,
            needs_place=needs_place,
            replay_notice=replay_notice,
            resume_session_id=resume_session_id,
            role=role,
            roster=roster,
            topic_refs=topic_refs,
            topic_refs_for_prompt=topic_refs_for_prompt,
            artifacts=artifact_refs,
            teaching=teaching,
            turn_images=turn_images,
            untitled=untitled,
        )

    async def converse(
        self,
        *,
        topic_id: uuid.UUID,
        content: str,
        turn_id: uuid.UUID,
        author: str,
        user_block_id: uuid.UUID | None,
        is_resume: bool = False,
        continuation_id: uuid.UUID | None = None,
        provision_actor: Actor | None = None,
        platform_turn: bool = False,
        delivery_id: uuid.UUID | None = None,
        recipient_instance_id: uuid.UUID | None = None,
    ) -> AsyncIterator[dict]:
        preparation_started = time.monotonic()
        prepared = await self.prepare(
            topic_id=topic_id,
            content=content,
            turn_id=turn_id,
            user_block_id=user_block_id,
            provision_actor=provision_actor,
            platform_turn=platform_turn,
            # 周期任务那一轮从这一笔投递上认主人（`turn_speakers`）。
            delivery_id=delivery_id,
            recipient_instance_id=recipient_instance_id,
        )
        logger.info(
            "chat_preparation_timing topic=%s turn=%s phase=assembled "
            "elapsed_ms=%.3f unix_ms=%.3f",
            topic_id,
            turn_id,
            (time.monotonic() - preparation_started) * 1000,
            time.time() * 1000,
        )
        if isinstance(prepared, TurnBail):
            for frame in prepared.frames:
                yield frame
            return
        # Unpacked into the names the rest of this function already used, rather
        # than read through `prepared.` throughout: what follows is unchanged,
        # and a move that also rewrote seven hundred lines of references would
        # not be reviewable as the inert one it is.
        acting_agent = prepared.acting_agent
        doc_text = prepared.doc_text
        overview_doc_text = prepared.overview_doc_text
        memory = prepared.memory
        needs_place = prepared.needs_place
        prior_progress = prepared.prior_progress
        project_id = prepared.project_id
        prompt_text = prepared.prompt_text
        provider = prepared.provider
        resume_session_id = prepared.resume_session_id
        role = prepared.role
        roster = prepared.roster
        topic_refs_for_prompt = prepared.topic_refs_for_prompt
        artifact_refs = prepared.artifacts
        teaching = prepared.teaching
        untitled = prepared.untitled

        # Tells AgentWorkRunner's outer wall-clock wrap (runtime.py) to reschedule
        # to this backend's real ceiling instead of the generic
        # `agent_turn_timeout_s` (turn 活跃度检测). It is the HARNESS's number:
        # how long a silence may last before it means something is wrong depends
        # on what is producing the output, not on the machine underneath it.
        runtime = provider
        yield {"type": "turn_ceiling", "seconds": runtime.hard_ceiling_s}
        system_prompt = session_system_prompt(
            self.base_prompt,
            self.skills,
            needs_place=needs_place,
            has_doc=doc_text is not None,
            role=role,
            harness=runtime.harness,
            name=prepared.agent.display_name,
        )
        opening = build_session_opening(
            thread=prepared.thread_context,
            tasks=prepared.thread_tasks,
            machine=prepared.task_machine,
            doc=doc_text,
            memory=memory,
            # 已停用的队友不进这份名单：这一段教的是「要让某人去做事，在他名字前
            # 加 @」，而一个停用了的实例没有人在驱动它——@ 它等于把活扔进一个没人
            # 接的地方。@ 解析和通知那几路照旧走全量的 `roster`：老房间里已经在的
            # 它仍要 @ 得到，停用挡的是新的活，不是已经接手的。
            roster=[m for m in roster if m["active"]],
            topics=topic_refs_for_prompt,
            artifacts=artifact_refs,
            overview_doc=overview_doc_text,
            overview_doc_id=prepared.overview_doc_id,
            teaching=teaching,
            environment=_session_opening_lines(
                unconnected_mcp=(
                    await unconnected_mcp(
                        self.sessions,
                        project_id,
                        prepared.room_id,
                        acting_agent,
                        inner_id=prepared.inner_id,
                    )
                    if needs_place
                    else ()
                ),
                progress=prior_progress,
                sandbox=_sandbox_limits(provider),
                # A resumed conversation already holds what was said in it.
                earlier_messages=(
                    prepared.earlier_messages if resume_session_id is None else 0
                ),
            ),
            keeps_memory=keeps_memory(runtime.harness),
        )
        async with self.sessions() as session:
            told = await AgentSessionService(session).told(
                topic_id, prepared.agent.handle, harness=prepared.harness
            )
        if untitled:
            # 每一轮都提醒，直到起了名：哪一轮才弄清要做什么，事先不知道。
            prompt_text = f"{platform_prompt(UNTITLED_TASK)}\n\n{prompt_text}"
        if is_resume:
            prompt_text = f"{platform_prompt(_resume_notice())}\n\n{prompt_text}"
        prompt_text = publication_prompt(prompt_text)
        logger.info(
            "chat_preparation_timing topic=%s turn=%s phase=prompt_built "
            "elapsed_ms=%.3f unix_ms=%.3f",
            topic_id,
            turn_id,
            (time.monotonic() - preparation_started) * 1000,
            time.time() * 1000,
        )

        # Compute: a provider owns the per-topic sandbox + execution (spec §9.1).
        # In a private chat the turn's memory is the owner's own, so the sandbox
        # comes up in the personal scope (spec §8.4). The provider runs a plain
        # model turn when no Docker (tests).
        model_kwargs, route = await self.model_options(
            project_id,
            provider,
            prepared.room_id,
            agent=prepared.agent,
            acting_agent=prepared.acting_agent,
        )
        logger.info(
            "chat_preparation_timing topic=%s turn=%s phase=model_ready "
            "elapsed_ms=%.3f unix_ms=%.3f",
            topic_id,
            turn_id,
            (time.monotonic() - preparation_started) * 1000,
            time.time() * 1000,
        )

        async def activate(ref: SessionRef) -> None:
            await self.compute.activate(ref, runtime)

        async for frame in send_prepared(
            self.sessions,
            self.live,
            self.send_effects,
            PreparedSend(
                project_id=prepared.project_id,
                room_id=prepared.room_id,
                inner_id=prepared.inner_id,
                acting_agent=prepared.acting_agent,
                agent_handle=prepared.agent.handle,
                agent_pool=prepared.agent_pool,
                roster=prepared.roster,
                topic_refs=prepared.topic_refs,
                consumed_ids=prepared.pending_ids + prepared.notice_ids,
                turn_images=prepared.turn_images,
                replay_notice=prepared.replay_notice,
                resume_session_id=prepared.resume_session_id,
                harness=prepared.harness,
                needs_place=prepared.needs_place,
                keeps_nothing=prepared.keeps_nothing,
                sender=runtime,
                activate=activate,
            ),
            topic_id=topic_id,
            turn_id=turn_id,
            author=author,
            content=content,
            user_block_id=user_block_id,
            is_resume=is_resume,
            platform_turn=platform_turn,
            continuation_id=continuation_id,
            delivery_id=delivery_id,
            model_kwargs=model_kwargs,
            route=route,
            prompt_text=prompt_text,
            system_prompt=system_prompt,
            opening=opening,
            told=told,
        ):
            yield frame
