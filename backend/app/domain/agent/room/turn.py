"""One room turn, from what is in the room to what the agent said: assembling
it (``_assemble_turn``, a dozen reads in one transaction) and running it
(``_converse_impl``, minutes of streaming that hold none).

``ChatService`` (``agent.chat``) is the room's service, and these are two of
its methods, kept here so the turn reads apart from everything else the service
does; ``self`` is that service.
"""

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol

from app.core.errors import NotFoundError, ValidationError
from app.core.sentences import exception_text, say
from app.domain.agent import turn_inputs
from app.domain.agent.harness import SessionRef
from app.domain.agent.harness.prompt import (
    UNTITLED_FIRST,
    build_session_opening,
    build_system_prompt,
    opening_changes,
    platform_prompt,
    prompt_line,
    publication_prompt,
)
from app.domain.agent.hook_stream import _HookWorkState
from app.domain.agent.platform_notices import (
    EVENT_PROMPT_REPLAYED,
    SEVERITY_INFO,
    SEVERITY_WARN,
    WHO_PLATFORM,
    notice,
)
from app.domain.agent.prompt import (
    _addressed_to,
    _pending_input_blocks,
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
    _block_payload,
    _model_policy_call,
    require_pinned_seat,
)
from app.domain.agent.room.sessions import RoomSessions
from app.domain.agent.service import AgentResult
from app.domain.agent.session_host.host import keeps_memory
from app.domain.agent.skills import load_skills
from app.domain.agent.turn_speakers import turn_speakers
from app.domain.agent.work_policy import resolve_compute_id
from app.domain.agent_instance.services import (
    AgentInstanceService,
    ResolvedAgent,
    memory_pool,
)
from app.domain.agent_session.services import AgentSessionService
from app.domain.block.about import EventAbout, landing
from app.domain.block.models import AuthorType, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.block.schemas import BlockOut
from app.domain.delivery.ask_session_wait import waiting_ask_blocks
from app.domain.delivery.ask_wake import expected_ask_session
from app.domain.delivery.input_identity import InputEffects, InputOutcomeUnconfirmed
from app.domain.delivery.receipts import held_blocks
from app.domain.identity.actor import Actor
from app.domain.living_doc.services import Documents
from app.domain.membership.roster import roster_rows
from app.domain.memory.files_store import MemoryIndex, memory_index
from app.domain.memory.models import MemoryScope
from app.domain.policy import gate
from app.domain.project import artifacts as project_artifacts
from app.domain.project.repositories import ProjectRepository
from app.domain.room_task.models import TaskStatus
from app.domain.room_task.place import Place, PlaceResolver
from app.domain.task import teaching as teaching_context
from app.domain.task.teaching import TeachingContext
from app.domain.topic import naming
from app.domain.topic.models import TitleSource, Topic, TopicStatus
from app.domain.topic.repositories import TopicProgressRepository, TopicRepository
from app.domain.usage.ledger import team_terms

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.domain.agent.queries import _Proposed
    from app.domain.agent.service import AgentEvent
    from app.domain.delivery.input_identity import InputRegistrar
    from app.domain.project.models import Project

PRIVATE_SKILLS = ["private-chat"]

logger = logging.getLogger(__name__)

#: How many sessions' supply routes to remember. Well past the number of screens
#: one backend drives at once, so in practice nothing is ever evicted; it is a
#: ceiling on a dict nothing else prunes, not a policy.
_SESSION_ROUTES_KEPT = 512


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


def _is_dm(topic: Topic) -> bool:
    """这间房是不是一间私聊。**这是 `is_private` 在这个文件里唯一的读点。**

    私聊是项目内名册两席的房间（结论 19），这一轮凡是「私聊要不一样」的地方，答
    案都从这里推出来，不再各自问一遍那个布尔：同一件事问 N 遍，N 遍的判据就会各
    自漂移，这次退役的正是漂开了的三十处。推出来的是两件事：

    - **这间房没有名册。**私聊不暴露成员列表，`@` 解析不到项目里的第三个人：解
      析表给 `[]`，`@某某` 原样留在正文里，显示成一条「项目中没有这个成员」
      。这一条管的是正文去了哪里，不只是渲染：名册还要往下走进
      `announce_mentions`，解析到的每个 handle 都会收到一条带正文前 200 字的强提醒。
    - **这一轮不租地点**（`needs_place`，结论 19、不变量 I2）：不碰仓库文件、不
      跑项目命令的一轮不去租手，所以它在所有执行机离线时也答得出来。它桌上只有
      对话、记忆和平台工具，加上会话自己那块 64 MiB 草稿区（不是一个地点，随会
      话生灭）。

    问的是这间房的性质，**不是名册上此刻坐了几个人**。「两席里的人是哪一位」由
    `_private_owner` 答，席位不齐时它答 None，而一间私聊的正文不会因为席位不齐
    就可以广播出去。两个问题分开问，是因为它们答错的后果不同：答不出「对面是
    谁」，退路是项目默认的芝士；答错「这间房有没有名册」，正文就出了房间。
    """
    return topic.is_private


@dataclass(frozen=True, slots=True)
class _TurnContext:
    """Everything one turn needs to run, read once before anything runs it.

    A turn is assembled and then executed, and the two halves want opposite
    things from a database session: assembling is a dozen reads that belong in
    one transaction, executing is minutes of streaming that must hold none. This
    is what crosses between them — so a backend that runs a turn some other way
    receives THIS, rather than a session and instructions on what to read.
    """

    # Who is here and what they are working under.
    project_id: uuid.UUID
    # Where: the room, and the task when the conversation is a task's own.
    room_id: uuid.UUID
    task_id: uuid.UUID | None
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
    turn_images: list[dict]
    replay_notice: str | None
    resume_session_id: str | None

    # What it should know: the doc, the memories, the checklist it left behind,
    # the cards waiting on it, and which段 of the flow this topic is in.
    doc_text: str | None
    # 注入用的项目总览：① 从总览文档里取，②③ 从结构化数据现拼（#1889 第 1 条），
    # 不是文档原文。每个房间都有 ①；②③ 只在总览房间拼，别处按需自己查。
    #
    # 总览房间自己那一轮没有 `doc_text` —— 这一份就是它的实况文档，同一份东西说
    # 两遍只会让模型以为是两份。
    overview_doc_text: str | None
    # 这一轮注入的 L1 记忆索引（team 一份 + 本轮发言人各一份）。正文不在里面：
    # 每条记忆的正文在会话目录 `.cheese/memory/` 下，agent 自己去读（见
    # `memory/instructions.py`）。None = 「这一轮没走注入那条路」。
    memory: MemoryIndex | None
    prior_progress: list[dict]
    # Chat messages already in the room, apart from the ones this turn delivers.
    earlier_messages: int
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
    # A task's session only reads until its owner starts it: it discusses and
    # writes the task's document, and changes nothing in the project.
    reads_only: bool


@dataclass(frozen=True, slots=True)
class _TurnBail:
    """The turn ended while it was still being assembled, and these are the
    frames that say so. Not an error: nobody was waiting on an answer, or the
    machine is still being built."""

    frames: list[dict]


class _Machines(Protocol):
    """What a turn asks of the service's compute pool (``agent.compute``)."""

    def choose(
        self, project_settings: Mapping[str, Any] | None, provider_id: str | None
    ) -> tuple[str, RoomSessions | None]: ...

    async def activate(self, session: SessionRef, runtime: RoomSessions) -> None: ...

    async def dismiss(self, topic_id: uuid.UUID, agent_handle: str) -> None: ...


class _MemoryBooks(Protocol):
    """What a turn tells the service's memory ledger (``agent.memory_ledger``)."""

    def remember_turn(
        self, topic_id: uuid.UUID, *, acting: str, speakers: tuple[str, ...]
    ) -> None: ...


class RoomTurns:
    """The turn half of ``ChatService``: assembling one turn and running it.
    The attributes and the methods below are the service's; declared here so
    the type checker sees what a turn asks of it."""

    if TYPE_CHECKING:
        _sessions: async_sessionmaker
        _base_prompt: str
        _compute: _Machines
        _memory: _MemoryBooks
        _skills: str
        _hook_work: dict[tuple[uuid.UUID, uuid.UUID], _HookWorkState]
        _session_route: dict[uuid.UUID, str]
        _session_model: dict[uuid.UUID, str]

        def _input_registrar(
            self,
            effects: InputEffects,
            *,
            probe_unread: bool = False,
            fence_delivery: bool = False,
        ) -> InputRegistrar: ...

        async def _unconnected_mcp(
            self, project_id: uuid.UUID, topic_id: uuid.UUID, agent_handle: str | None
        ) -> tuple[str, ...]: ...

        async def post_system_event(
            self,
            topic_id: uuid.UUID,
            content: str,
            turn_id: uuid.UUID | None = None,
            *,
            meta: dict | None = None,
        ) -> dict | None: ...

        async def _note_turn_context(
            self,
            turn_id: uuid.UUID,
            *,
            route: str,
            reply_to: uuid.UUID | None,
            agent_handle: str,
        ) -> None: ...

        async def _consume_hook_event(
            self,
            project_id: uuid.UUID,
            topic_id: uuid.UUID,
            turn_id: uuid.UUID,
            event: AgentEvent,
            eid: str | None,
            result_text_seen: bool,
            platform_unsolicited: bool,
        ) -> None: ...

        async def _resolved_agent(
            self, session: AsyncSession, topic: Topic
        ) -> ResolvedAgent: ...

        async def _agent_at(
            self, session: AsyncSession, place: Place
        ) -> ResolvedAgent: ...

        async def _acting_handle(
            self, session: AsyncSession, topic_id: uuid.UUID, agent: ResolvedAgent
        ) -> str: ...

        @staticmethod
        async def _private_owner(session: AsyncSession, topic: Topic) -> str | None: ...

        async def _known_commits(
            self, project_id: uuid.UUID, topic_id: uuid.UUID
        ) -> set[str] | None: ...

        async def _pass_policy_gate(
            self,
            session: AsyncSession,
            topic_id: uuid.UUID | None,
            call: gate.Call,
            policy: gate.Policy,
            *,
            actor: str,
        ) -> _Proposed | None: ...

        async def _model_kwargs(
            self,
            project_id: uuid.UUID,
            provider: RoomSessions | None,
            topic_id: uuid.UUID | None = None,
            *,
            agent: ResolvedAgent | None = None,
            acting_agent: str | None = None,
            platform: bool = False,
        ) -> tuple[dict, str]: ...

        async def _bail_notice(
            self,
            *,
            project_id: uuid.UUID,
            topic_id: uuid.UUID,
            turn_id: uuid.UUID,
            session: AsyncSession,
            text: str,
        ) -> dict: ...

        async def _project_overview(
            self,
            session: AsyncSession,
            *,
            project: Project,
            room_id: uuid.UUID,
            room_doc: str | None,
            overview_doc: str | None,
            all_topics: list[Topic],
            roster: list[dict],
        ) -> str: ...

    async def dismiss(self, topic_id: uuid.UUID, seat: str) -> None:
        """Stop the work the teammate on rosters as ``seat`` still has running
        in this room: one taken off the room, whose every call there is now
        refused. What it wrote so far stays."""
        async with self._sessions() as session:
            topic = await TopicRepository(session).get(topic_id)
            project = topic and await ProjectRepository(session).get(topic.project_id)
            agent = project and await AgentInstanceService(session).for_seat_handle(
                project, seat
            )
        if agent is not None:
            await self._compute.dismiss(topic_id, agent.handle)

    async def _assemble_turn(
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
    ) -> "_TurnContext | _TurnBail":
        """Read the recipient, history, memory, machine and prompt in one transaction.

        Return a ``_TurnBail`` with room frames if no answer is pending or the
        machine is still being built; neither outcome starts an executor.
        """
        started = time.monotonic()
        phases_ms: dict[str, float] = {}
        # --- tx1: load topic + history, load memory ---
        async with self._sessions() as session:
            topics = TopicRepository(session)
            blocks = BlockRepository(session)

            # WHERE this turn runs: a room's own line, or a task's conversation.
            place = await PlaceResolver(session).conversation(topic_id)
            if place is None:
                raise NotFoundError("Topic not found")
            topic, task = place.room, place.task
            if topic.status == TopicStatus.archived:
                raise ValidationError(say("roomArchivedUnarchiveFirst"))
            if task is not None and task.status == TaskStatus.closed:
                raise ValidationError(say("taskClosedNoTurn"))

            # Speaker-labelled prompt covering every human message 芝士 hasn't
            # been handed yet — so messages posted without @芝士 are still seen on
            # the next summon (spec §7.1 所有消息 AI 都会收到), each tagged with
            # who said it so 芝士 can tell people apart in a group topic (§8.4).
            #
            # This conversation's OWN line: a room's history leaves out its
            # tasks' conversations, and a task's is only its own.
            history = await blocks.turn_history(place.room_id, place.task_id)
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
                agent = await self._agent_at(session, place)
            else:
                agent = agents.resolved(
                    await agents.get_in_project(
                        project_id=topic.project_id,
                        instance_id=uuid.UUID(recipient["instance_id"]),
                    )
                )
            acting_agent = pinned_seat or await self._acting_handle(
                session, topic.id, agent
            )
            pending = [block for block in pending if _addressed_to(block, agent.handle)]
            held = await held_blocks(
                session,
                project_id=topic.project_id,
                topic_id=place.room_id,
                recipient_handle=acting_agent,
            )
            # An answer whose Ask conversation is gone belongs to no prompt:
            # carrying it would fail this turn on the fence that refuses it
            # (`ask_session_wait`).
            held |= await waiting_ask_blocks(
                session,
                topic_id=place.room_id,
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
                return _TurnBail([{"type": "done"}])
            # 盖章清单比 prompt 清单窄（多 agent 房间，2026-09-28 定）：
            # 没被 @ 的公共消息每个轮次都看得见，但只由「被人召唤起来的轮次」
            # 或「房间默认 agent 的轮次」盖章认领；其余在场轮次（另一个 agent
            # 被 @、平台自检）看过就算。不这么窄，并行的几个轮次会给同一条
            # 消息各盖一个 consumed_turn，而谁都没答它的话却被所有人收走。
            claims_backlog = (
                user_block_id is not None
                or agent.handle == (await self._agent_at(session, place)).handle
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
            turn_images, gone_files = await asyncio.to_thread(
                offered_attachments, pending, topic.project_id, place.room_id
            )

            # 私聊是名册两席的房间（结论 19）。这一轮凡是「私聊要不一样」的地
            # 方，问的都是下面两个答案之一，不再各自问一遍那个布尔。
            #
            # 一、名册上那两席，人是哪一位（席位不齐时 None）。
            private_owner = await self._private_owner(session, topic)
            # 二、这一轮要不要一双手？见 `_is_dm`：不租地点的一轮桌上只有对话、
            # 记忆和平台工具，加上会话自己那块 64 MiB 草稿区。
            needs_place = not _is_dm(topic)
            acting_agent = pinned_seat or await self._acting_handle(
                session, topic.id, agent
            )
            # A task's own document, or the room's.
            if task is None:
                doc_root = await Documents(session).of_room(place.room_id)
            elif task.document_id is not None:
                doc_root = await Documents(session).get(task.document_id)
            else:
                doc_root = None
            # 工作话题的文档还空着时是 `""`，不是 None：提示词据此告诉坐进来的
            # 队友「建第一版」（`build_system_prompt`）。私聊没有这份文档要维护。
            doc_text = doc_root.content if doc_root else None
            if needs_place and not (doc_text or "").strip():
                doc_text = ""
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
            self._memory.remember_turn(topic.id, acting=acting_agent, speakers=speakers)
            phases_ms["memory"] = (time.monotonic() - started) * 1000
            projects_repo = ProjectRepository(session)
            project = await projects_repo.get(topic.project_id)
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
            # 人和 agent 共同看的那一份（结论 7）：项目总览房间的实况文档。它不是
            # 记忆，所以不走召回那条路——写它的人（或 agent）留了痕，读它的每一间
            # 房间读到的是同一份，而这正是共享记忆池做不到的两件事。
            # 总览房间自己那一轮不读第二遍：`doc_text` 已经是它（下面注入那一步会
            # 把两者合起来，那里才是「注入什么」的决定）。
            overview_root = (
                await Documents(session).of_room(project.root_topic_id)
                if project is not None
                and project.root_topic_id is not None
                and project.root_topic_id != place.room_id
                else None
            )
            overview_doc_text = overview_root.content if overview_root else None
            # Read the selected agent once so this turn's role and model agree.
            role = await agents.system_prompt(agent)
            # 骨架是这个项目在这台机器上跑的那一个（结论 28），不是这个参与者的属
            # 性。这一轮只解析这一次，往下每一处都读它：会话行的键里有骨架，两处
            # 各自解析一次就够把一条会话拆成两条。
            compute_id = resolve_compute_id(
                project.settings if project else None, topic, task
            )
            wanted_harness, provider = self._compute.choose(
                project.settings if project else None, compute_id
            )
            agent_pool = memory_pool(topic.project_id, agent)
            # Roster so 芝士 can @ real teammates (not just name them in prose).
            # 私聊里没有第三个人可点名，名册也就不进提示词——`[]` 和「没有名册这
            # 回事」在下游是两种情况（见 `_HookWorkState.roster`）。问的是这间房
            # 是不是私聊，不是它此刻坐了几个人：名册还要往下走进 `announce_mentions`。
            roster = (
                [] if _is_dm(topic) else await roster_rows(session, topic.project_id)
            )
            # Topic list so 芝士 can cross-reference topics with <#id> tokens.
            # 两份，故意的：`topic_refs` 是 `@标题` 的**解析表**（全量，含已归档
            # ——用户自己打 @某个归档话题也必须还能变成链接）；
            # `topic_refs_for_prompt` 只是**渲染**进 system prompt 的子集。
            all_topics = await topics.list_for_project(topic.project_id)
            topic_refs, topic_refs_for_prompt = _topic_ref_lists(
                all_topics, exclude_id=topic.id
            )
            if project is not None and project.root_topic_id is not None:
                # 项目总览（#1889 第 1 条）：注入的不是文档原文，而是「① 从文档
                # 来 + ②③ 从结构化数据现拼」的那一份。手抄进正文的旧内容因此读
                # 不到——写在那儿的副本没人读，也就没人再写。
                #
                # 在总览房间它同时就是本房间的实况文档：同一份东西说两遍，模型会
                # 以为是两份，所以那里把 `doc_text` 交出去（它只喂提示词）。
                overview_doc_text = await self._project_overview(
                    session,
                    project=project,
                    room_id=place.room_id,
                    room_doc=doc_text,
                    overview_doc=overview_doc_text,
                    all_topics=all_topics,
                    roster=roster,
                )
                if project.root_topic_id == place.room_id:
                    doc_text = None
            # 产物清单：交付时点名用的那几个名字 (#1085 结论三)。不租地点的一轮里
            # 没有交付，那里连这一段都不该有；空清单和「没有清单这回事」是两种情况，
            # 前者要说话（第一次交付只能新建），后者一个字都不说，所以给的是 None。
            artifact_refs = (
                [
                    {
                        "id": str(a.id),
                        "name": a.name,
                        "version": a.version,
                        "about": a.about,
                    }
                    for a in await project_artifacts.list_for_project(
                        session, topic.project_id
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
            # The platform names rooms itself (topic/naming.py). Only where it
            # cannot — no gateway to call — is the agent still asked to, and
            # never in a project that chose to name its rooms by hand.
            untitled = (
                task is None
                and topic.title_source == TitleSource.placeholder
                and not naming.available()
                and naming.naming_mode(project.settings if project else None) == "auto"
            )
            # 进度层 (#187): the checklist the last turn left behind. Read inside
            # tx1 with everything else the prompt is built from, so no extra
            # round trip; empty list when this topic has never had one.
            progress_row = await TopicProgressRepository(session).get(
                place.room_id, task_id=place.task_id
            )
            prior_progress = [
                dict(item) for item in (progress_row.items if progress_row else [])
            ]
            earlier_messages = await blocks.count_messages(
                place.room_id, excluding=prompt_pending_ids, task_id=place.task_id
            )
            # Resolve the room choice, then the explicit project default.
            phases_ms["metadata"] = (time.monotonic() - started) * 1000
            # 先问这台机器上有没有可用的骨架，再过档位策略：策略那一步要解析模型，
            # 而一个没挂上的骨架一个模型都指不到，先问它就会以「没有默认模型」收场，
            # 房间读到的不是真正的原因。
            if provider is None:
                # The machine is fine; nothing this deployment lists runs on it.
                return _TurnBail(
                    [
                        {
                            "type": "event_block",
                            "block": await self._bail_notice(
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
                from app.domain.agent.compute_configs import place_choice

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
                    from app.domain.agent.compute_configs import (
                        machine_policy_call,
                        place_choice,
                    )

                    proposed = await self._pass_policy_gate(
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
                        return _TurnBail(_proposal_frames(proposed.landed))
                proposed = await self._pass_policy_gate(
                    session,
                    place.room_id,
                    _model_policy_call(project, agent),
                    policy,
                    actor=actor_handle,
                )
                if proposed is not None:
                    await session.commit()
                    return _TurnBail(_proposal_frames(proposed.landed))
            if (
                needs_place
                and not provider.deferred_work
                and compute_id == "device"
                and topic.compute_config is None
            ):
                from app.domain.agent.compute_configs import (
                    bind_room_device_choice,
                )

                await bind_room_device_choice(
                    session, topic, project.settings if project else None
                )
            # 开一台机器是租手的一部分，所以不租手的一轮也不等它开完。
            if needs_place and provider.provisions_machine:
                ready, waiting_text = await provider.prepare_topic(
                    project_id=project_id,
                    topic_id=place.room_id,
                    actor=provision_actor,
                )
                if topic.compute_config is None:
                    from app.domain.agent.compute_configs import room_choice

                    topic.compute_config = room_choice(
                        topic, project.settings if project else None
                    ).model_dump()
                if not ready:
                    cloud_events = [
                        block
                        for block in history
                        if (block.meta or {}).get("event_type") == "cloud_provisioning"
                    ]
                    waiting_payload = None
                    if (
                        not cloud_events
                        or (cloud_events[-1].meta or {}).get("state") != "waiting"
                    ):
                        landed = landing(
                            EventAbout.task if task is not None else EventAbout.room,
                            project_id=project_id,
                            room_id=place.room_id,
                            task_id=place.task_id,
                        )
                        waiting_block = await blocks.add(
                            project_id=landed.project_id,
                            topic_id=landed.topic_id,
                            task_id=landed.task_id,
                            author="system",
                            author_type=AuthorType.platform,
                            content=waiting_text,
                            kind=BlockKind.event,
                            turn_id=turn_id,
                            meta={
                                # 这条已有自己的 event_type / state，前端按它渲染；
                                # 补上轻重和「谁在管」，等待就不必再靠一个 ⏳ 说话。
                                "severity": SEVERITY_INFO,
                                "who": WHO_PLATFORM,
                                "detail": say("cloudProvisioningDetail"),
                                "detail_label": say("labelWhatHappensNext"),
                                "event_type": "cloud_provisioning",
                                "state": "waiting",
                            },
                        )
                        waiting_payload = _block_payload(
                            BlockOut.model_validate(waiting_block)
                        )
                    await session.commit()
                    frames: list[dict] = []
                    if waiting_payload is not None:
                        frames.append({"type": "event_block", "block": waiting_payload})
                    frames.append({"type": "waiting", "state": "cloud_provisioning"})
                    frames.append({"type": "done"})
                    return _TurnBail(frames)
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
                and (parent := await blocks.get(b.reply_to)) is not None
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
            replay_n = await blocks.bump_prompt_attempts(prompt_pending_ids, turn_id)
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
            if (
                task is not None
                and needs_place
                and provider is not None
                and task.compute_config is None
            ):
                # The same red line for a task: its choice is fixed on its first
                # turn, so a later change to the room's does not move it.
                from app.domain.agent.compute_configs import place_choice

                task.compute_config = place_choice(
                    topic, task, project.settings if project else None
                ).model_dump()
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
                from app.domain.agent.compute_configs import room_choice

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
        return _TurnContext(
            room_id=place.room_id,
            task_id=place.task_id,
            reads_only=task is not None and task.started_at is None,
            acting_agent=acting_agent,
            agent=agent,
            agent_pool=agent_pool,
            doc_text=doc_text,
            overview_doc_text=overview_doc_text,
            memory=memory,
            pending_ids=pending_ids,
            notice_ids=[b.id for b in notices],
            prior_progress=prior_progress,
            earlier_messages=earlier_messages,
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

    async def _converse_impl(
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
        """Run the AGENT part of a turn (the human block was already posted by
        post_user_message), yielding WS frames as JSON-ready dicts. Runs under
        the per-topic lock; the prompt is built from history at lock time so a
        queued turn picks up every message posted while it waited."""
        expected_session = await expected_ask_session(self._sessions, delivery_id)
        preparation_started = time.monotonic()
        prepared = await self._assemble_turn(
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
        if isinstance(prepared, _TurnBail):
            for frame in prepared.frames:
                yield frame
            return
        # Unpacked into the names the rest of this function already used, rather
        # than read through `prepared.` throughout: what follows is unchanged,
        # and a move that also rewrote seven hundred lines of references would
        # not be reviewable as the inert one it is.
        acting_agent = prepared.acting_agent
        agent_pool = prepared.agent_pool
        doc_text = prepared.doc_text
        overview_doc_text = prepared.overview_doc_text
        memory = prepared.memory
        needs_place = prepared.needs_place
        pending_ids = prepared.pending_ids
        consumed_ids = pending_ids + prepared.notice_ids
        prior_progress = prepared.prior_progress
        project_id = prepared.project_id
        prompt_text = prepared.prompt_text
        provider = prepared.provider
        replay_notice = prepared.replay_notice
        resume_session_id = prepared.resume_session_id
        role = prepared.role
        roster = prepared.roster
        topic_refs = prepared.topic_refs
        topic_refs_for_prompt = prepared.topic_refs_for_prompt
        artifact_refs = prepared.artifacts
        teaching = prepared.teaching
        turn_images = prepared.turn_images
        untitled = prepared.untitled

        # Tells AgentWorkRunner's outer wall-clock wrap (runtime.py) to reschedule
        # to this backend's real ceiling instead of the generic
        # `agent_turn_timeout_s` (turn 活跃度检测). It is the HARNESS's number:
        # how long a silence may last before it means something is wrong depends
        # on what is producing the output, not on the machine underneath it.
        runtime = provider
        yield {"type": "turn_ceiling", "seconds": runtime.hard_ceiling_s}
        # 私聊是名册两席的房间（结论 19），所以它先拿房间那份发布契约，
        # private-chat 只补私聊独有的那几条。替换会让私聊成为全仓唯一一间
        # 系统提示词里没有 chat_send 的房间：终端里答完而没有发布，房间是空的。
        # 补的那几条说的正是「这一轮没有地点，只有会话自己那块草稿区」，所以
        # 它跟着 `needs_place` 走，而不是再问一遍这间房是不是私聊。
        skills = (
            self._skills
            if needs_place
            else "\n\n---\n\n".join([self._skills, load_skills(PRIVATE_SKILLS)])
        )
        # 规矩进系统提示词，现状进开场快照：系统提示词在一个会话里一字不变，前缀
        # 缓存才接得上（`build_system_prompt` 的说明）。
        system_prompt = build_system_prompt(
            self._base_prompt,
            skills,
            has_doc=doc_text is not None,
            role=role,
            # 记忆那一段跟着这一轮跑的骨架走：写下来的文件同步不回平台的骨架，
            # 读到它只会以为自己在写项目记忆（`build_system_prompt` 那段注释）。
            keeps_memory=keeps_memory(runtime.harness),
        )
        opening = build_session_opening(
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
            teaching=teaching,
            environment=_session_opening_lines(
                unconnected_mcp=(
                    await self._unconnected_mcp(
                        project_id, prepared.room_id, acting_agent
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
        async with self._sessions() as session:
            told = await AgentSessionService(session).told(
                topic_id, prepared.agent.handle, harness=prepared.harness
            )
        if untitled:
            # 起名是这一轮的第一件事，所以排在最前；起完名下一轮就不再说。
            prompt_text = f"{platform_prompt(UNTITLED_FIRST)}\n\n{prompt_text}"
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
        model_kwargs, route = await self._model_kwargs(
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
        # Remembered for the turns this session starts by itself. A route is a
        # fact about where a SESSION's traffic goes, not about one prompt, and a
        # self-started turn has no prompt to resolve it from — it rides the same
        # screen as this one, so this is the answer for both.
        self._session_route.pop(topic_id, None)
        self._session_route[topic_id] = route
        while len(self._session_route) > _SESSION_ROUTES_KEPT:
            del self._session_route[next(iter(self._session_route))]
        self._session_model.pop(topic_id, None)
        self._session_model[topic_id] = model_kwargs["model"]
        while len(self._session_model) > _SESSION_ROUTES_KEPT:
            del self._session_model[next(iter(self._session_model))]

        # Internal: the screen subscription, not this request, owns timeout and
        # thinking lifecycle. Runtime consumes this frame and disables its
        # request-scoped lifecycle before provider setup begins.
        yield {"type": "session_lifecycle"}

        # 重放可见 (#416): say out loud that this turn is re-sending a batch that
        # earlier turns already failed on. Posted BEFORE the stream, because the
        # whole point is that this turn may produce nothing either — a notice
        # written afterwards is exactly the one that never gets written.
        if replay_notice is not None:
            payload = await self.post_system_event(
                topic_id,
                replay_notice,
                turn_id,
                # 「又重投了一次」是一条码说了算的事。它以前只有开头那个 🔁 —— 一个
                # 字符同时当类别、当轻重、当给人看的记号，读它的人和读它的代码都得
                # 猜。码在这里，前端照码渲染。
                meta=notice(
                    EVENT_PROMPT_REPLAYED,
                    severity=SEVERITY_WARN,
                    who=WHO_PLATFORM,
                ),
            )
            if payload is not None:
                yield {"type": "event_block", "block": payload}

        # Baseline for 「这一轮改了哪些文件」, started BEFORE 芝士 can write anything
        # but deliberately NOT awaited here: git_log ensures the repo exists, and
        # on a cold project that is a git init plus a base commit. Awaited in
        # front of the provider, that delay is charged to the start of every
        # turn, and a turn cancelled inside the window dies before it can store
        # its session id. What it measures only becomes commits at the
        # checkpoint, so finishing the read any time before turn end is soon
        # enough. Both backends need it, and the hooks backend returns from this
        # function long before its turn ends — so it is started once here and
        # carried on the work state rather than read twice in two places.
        known_commits = asyncio.ensure_future(
            self._known_commits(project_id, prepared.room_id)
        )
        marked_work_ids: list[uuid.UUID] = []

        def _register_work(marked_work_id: uuid.UUID) -> None:
            marked_work_ids.append(marked_work_id)
            key = (topic_id, marked_work_id)
            # A self-started predecessor's state and runner marks end where the
            # takeover is PROVEN — the bound native user entry's transition
            # (hook_stream's AgentUserEntry branch) — not here: registering a
            # send proves nothing about the session, and this loop used to
            # scan the whole topic for it (FB-56).
            state = self._hook_work.get(key)
            if state is None:
                self._hook_work[key] = _HookWorkState(
                    project_id=project_id,
                    topic_id=topic_id,
                    work_id=marked_work_id,
                    pending_ids=set(consumed_ids),
                    reply_to=user_block_id,
                    roster=roster,
                    topic_refs=topic_refs,
                    continuation_id=continuation_id,
                    route=route,
                    model=model_kwargs["model"],
                    acting_agent=acting_agent,
                    agent_pool=agent_pool,
                    user_text=prompt_text,
                    started_at=datetime.now(UTC),
                    agent_instance_handle=prepared.agent.handle,
                    known_commits=known_commits,
                )
                return
            state.pending_ids.update(consumed_ids)
            if state.reply_to is None:
                state.reply_to = user_block_id
            if prompt_text not in state.user_text:
                state.user_text = f"{state.user_text}\n{prompt_text}"

        # 这一轮的提示词写下去之前先登记：会话说「收下了」的时候，记号落在召唤它
        # 的那条人类消息上。登记在 send 之前，因为回执可能比 send 返回还快。
        # 同一个条件也是「这一轮欠人一句回话」：召唤它的是人，会话就得先在房间里
        # 回一句，再做别的（`driven/runner.py`）。
        # FB-56: the interval and ledger open after every fallible preparation
        # (a failure there leaves no interval); the nonce binds the entry back.
        nonce = turn_inputs.new_nonce()
        prompt_text = f"{prompt_text}\n{nonce}"
        await turn_inputs.open_interval_with_input(
            self._sessions,
            topic_id=topic_id,
            turn_id=turn_id,
            author=author,
            content=content,
            is_resume=is_resume,
            continuation_id=continuation_id,
            harness=prepared.harness,
            nonce=nonce,
            at=datetime.now(UTC),
        )
        # And on the turn itself: the backend that ends this turn may not be
        # this one (`_begin_self_started_turn`), and it remembers neither.
        await self._note_turn_context(
            turn_id,
            route=route,
            reply_to=user_block_id,
            agent_handle=prepared.agent.handle,
        )
        summoned = user_block_id is not None and not is_resume and not platform_turn
        effects = InputEffects(
            held_block_ids=tuple(consumed_ids),
            # Initial prompt consumption remains tied to the clean turn ending;
            # native echo settles the delivery and its summoning read marker.
            seen_block_ids=(user_block_id,)
            if user_block_id is not None and summoned
            else (),
            seen_by=acting_agent if summoned else None,
            delivery_id=delivery_id,
            attempt_id=turn_id if delivery_id is not None else None,
        )
        try:
            # The same key `_assemble_turn` read this turn's resume token under
            # — where this conversation runs is recorded under it too, and a ref
            # built from anything else resolves somebody else's machine.
            session_ref = SessionRef(
                project_id,
                prepared.room_id,
                prepared.agent.handle,
                harness=prepared.harness,
                task_id=prepared.task_id,
            )
            await self._compute.activate(session_ref, runtime)
            ready = await runtime.send(
                session_ref,
                prompt_text,
                system_prompt=system_prompt,
                resume_token=resume_session_id,
                session_opening=opening.text,
                opening_changes=opening_changes(opening, told),
                expected_native_session=expected_session,
                model=model_kwargs.get("model"),
                env=model_kwargs.get("env"),
                acting=acting_agent,
                needs_place=needs_place,
                reads_only=prepared.reads_only,
                work_id=turn_id,
                images=turn_images or None,
                on_mark=_register_work,
                register_input=self._input_registrar(
                    effects,
                    fence_delivery=delivery_id is not None,
                ),
                owes_reply=summoned,
            )
            # 这一轮把现状说到了：下一轮只补在这之后变了的。回答一道 Ask 的那一轮
            # 接着原来的对话，runtime 不往里放现状（`RoomSessions.send`），所以不算。
            if expected_session is None:
                async with self._sessions() as session:
                    await AgentSessionService(session).remember_told(
                        conversation_id=topic_id,
                        agent_handle=prepared.agent.handle,
                        harness=prepared.harness,
                        told=opening.digests(),
                    )
                    await session.commit()
        except InputOutcomeUnconfirmed as exc:
            # The session still owns this work. Its structured echo can settle
            # the committed identity even after this ChatService is replaced.
            logger.exception(
                "initial input requires reconciliation (topic=%s, input=%s)",
                topic_id,
                exc.identity.input_id,
            )
            payload = await self.post_system_event(
                topic_id,
                "输入已登记，发送结果正在核对；不会重复发送",
                turn_id,
            )
            if payload is not None:
                yield {"type": "event_block", "block": payload}
            return
        except Exception as exc:  # noqa: BLE001 — a failed write must be SAID
            # Nothing else will close this turn. `session_lifecycle` above told
            # the runner that the session owns the ending, and the session this
            # was going to be never started — so without this the room shows
            # 正在思考 until the orphan sweep hours later, with no error, which
            # is indistinguishable from an agent thinking hard.
            #
            # Reported as the same error result a dead session's watchdog
            # produces, through the same consumer: the turn row closes, the
            # room gets its message, and the pending batch stays unconsumed so
            # the next turn re-sends it.
            logger.exception(
                "session write failed (topic=%s, turn=%s)", topic_id, turn_id
            )
            failure_code = getattr(exc, "failure_code", None)
            await self._consume_hook_event(
                project_id,
                topic_id,
                turn_id,
                AgentResult(
                    text=exception_text(exc) or "本轮没能把消息送进机器上的会话",
                    session_id=resume_session_id,
                    is_error=True,
                    failure_code=failure_code,
                    log=getattr(exc, "log", None),
                ),
                None,
                False,
                False,
            )
            status = getattr(exc, "environment_status", None)
            if status is not None:
                from app.domain.project.environment_recovery import report_failure

                await report_failure(self, project_id, prepared.room_id, status)
            # The write never reached the transport, so the Stop consumer's
            # close_one rightly refuses this interval (undelivered). Its own
            # coroutine retires it HERE, by exact id — the same end the
            # runner's `_execute` gives a runner-driven turn (FB-56).
            await turn_inputs.retire_failed(
                self._sessions,
                topic_id=topic_id,
                turn_id=turn_id,
                at=datetime.now(UTC),
            )
            return
        # Internal frame: `send` returned, so the transport accepted
        # the write — which IS delivery (#563, per #487's contract that a
        # write either reaches the process or errors). The runtime records
        # that as a fact against the durable in-flight registry. Without it
        # the orphan sweep has to infer arrival after a restart, from
        # whether 芝士 happened to produce a block before the process died,
        # and so calls a prompt that landed two seconds earlier undelivered
        # and re-sends it. Nothing but the runtime acts on this, so it never
        # reaches the broker.
        from app.domain.project.environment_recovery import close_recovery

        # Delivery is recorded AT the source (FB-56): the transport accepted
        # the write, so the interval and its input are stamped delivered in
        # the same commit — a converse driven without the work runner leaves
        # the same fact a runner-driven one does. Monotone, so the runner's
        # own stamp on the frame below is a no-op second write.
        async with self._sessions() as session:
            await turn_inputs.stamp_delivered(
                session, turn_id=turn_id, at=datetime.now(UTC)
            )
            await close_recovery(session, prepared.room_id)
            await session.commit()
        yield {"type": "prompt_delivered"}
        if ready is False:
            marked_work_id = marked_work_ids[-1] if marked_work_ids else turn_id
            payload = await self.post_system_event(
                topic_id,
                say("sessionStartingMessageQueued"),
                marked_work_id,
            )
            if payload is not None:
                yield {"type": "event_block", "block": payload}
        return
