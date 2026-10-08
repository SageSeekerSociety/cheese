"""Chat orchestration — ties topic, blocks, memory, and the agent together.

This is the platform "shell" around 芝士: it persists the conversation as
blocks (append-only history, spec H1), injects project memory into the agent's
context (spec §8.4 带记忆回答), retains execution notes in activity blocks,
publishes deliberately sent chat messages, and stores the
resumable session id on the topic.

DB writes happen in short transactions around the (long) streaming call so we
never hold a transaction open across the model round-trip.

One turn, assembled and run, is ``room/turn.py`` (``RoomTurns``).
"""

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.errors import NotFoundError, ValidationError
from app.core.sentences import say
from app.domain.agent import death_evidence, own_calls, own_limit
from app.domain.agent.announce import answer_questions
from app.domain.agent.ask import publish_answered
from app.domain.agent.compute import ComputePool

# 兼容门面：现场事件行的渲染搬去了 `event_lines.py`（那里有直接的单测）。
# 这里重新导出，`app.domain.agent.chat` 仍是既有调用点与测试的导入路径；下面
# 带 noqa 的几个本文件不用 —— 它们是被搬走的落库那几步的零件，测试仍然从
# chat.py 导它们。
from app.domain.agent.event_lines import (
    _CHANGE_FILES_LISTED,  # noqa: F401 — 搬走的常量，这里仍然导得出来
    _change_summary_meta,  # noqa: F401 — 测试仍从 chat.py 导它
    _Changeset,
    _diff_file_stats,  # noqa: F401 — 测试仍从 chat.py 导它
    _format_change_summary,  # noqa: F401 — 测试仍从 chat.py 导它
    _format_tool_event,  # noqa: F401 — 测试仍从 chat.py 导它
    _is_platform_tool,  # noqa: F401 — 测试仍从 chat.py 导它
    _short_tool_name,  # noqa: F401 — 测试仍从 chat.py 导它
    _subagent_event_text,  # noqa: F401 — 测试仍从 chat.py 导它
    _subagent_result_meta,  # noqa: F401 — 测试仍从 chat.py 导它
    _tool_event_meta,  # noqa: F401 — 测试仍从 chat.py 导它
)
from app.domain.agent.gateway import LlmGateway

# 兼容门面：这一轮的模型与它的用量账（准入前解析模型/环境/网关 key，轮次结束后把
# 网关的用量落库）搬去了 `gateway_usage.py`（那里有它们各自的文档）。这里重新导出，
# `app.domain.agent.chat` 仍是既有调用点与测试的导入路径；`ChatService` 上留一行
# 同名委托，调用点一格没动。三个设置键的名字（`_GW_KEY` 等）只被这一段读，跟着
# 搬走，不再是 `ChatService` 的属性。
from app.domain.agent.gateway_usage import (
    OWN_ROUTE,
    _gateway_project_env,
    _model_kwargs,
    _schedule_deferred_drain,
    charge_turn_spend,
    project_gateway_key,
)
from app.domain.agent.harness import harness_for
from app.domain.agent.harness.prompt import (
    live_input_lines,
    platform_prompt,
    publication_prompt,
    strip_platform_notice,
)

# 兼容门面：屏幕订阅送进来的那一条线（读一条事件、落块、重试与整理的提示、关
# 这一轮的书）搬去了 `hook_stream.py`（那里有它们各自的文档）。这里重新导出，
# `app.domain.agent.chat` 仍是既有调用点与测试的导入路径；`ChatService` 上留一行
# 同名委托，调用点一格没动。带 noqa 的名字本文件不用，只是给外部留的导入路径。
from app.domain.agent.hook_stream import (
    _OUT_OF_CREDIT_MARKERS,  # noqa: F401
    _TOOL_ACTION,  # noqa: F401
    _consume_hook_event,
    _is_out_of_credit,  # noqa: F401 — 搬走的判决，测试仍从 chat.py 导它
    _keep_note,  # noqa: F401 — 搬走后本文件不用，只是给外部留的导入路径
    _note_compaction,  # noqa: F401
    _note_reachability,
    _note_retry,  # noqa: F401
    _restate_note,  # noqa: F401
    _turn_failure_notice,  # noqa: F401
    _with_log,  # noqa: F401
)

# 兼容门面：记忆的对账与整理（连同它们按房间记的四份状态）搬去了
# `memory_ledger.py`（那里有整簇的文档）。`ChatService` 上留
# `sweep_memory_dreams` / `run_memory_dream` 两行委托，调用点一格没动；
# `_output_tokens_since` 搬走后本文件不用，只是给外部（测试）留的导入路径。
from app.domain.agent.memory_ledger import (
    MemoryLedger,
    _output_tokens_since,  # noqa: F401 — 测试仍从 chat.py 导它
)

# 兼容门面：点名解析、通知与 refs 搬去了 `mentions.py`（那里有直接的单测）。
# 这里重新导出，`app.domain.agent.chat` 仍是既有调用点与测试的导入路径；下面带
# noqa 的那几个本文件不用，只是给外部（测试）留的导入路径。
from app.domain.agent.mentions import (
    _MENTION_RE,  # noqa: F401
    _SPECIAL_MENTIONS,  # noqa: F401
    _TOPIC_REF_RE,  # noqa: F401
    MENTION_ALL,  # noqa: F401 — 搬走的常量，这里仍然导得出来
    MENTION_HERE,  # noqa: F401
    PersonMentions,  # noqa: F401
    _expand_mention_names,
    _resolve_mentions,  # noqa: F401
    _topic_refs,  # noqa: F401
    announce_mentions,
    person_mentions,
    project_refs_text,
)
from app.domain.agent.platform_notices import (
    EVENT_TURN_FAILED,
    SEVERITY_INFO,
    WHO_HUMAN,
    delivery_checking_notice,
    delivery_fallback_notice,
    notice,
)
from app.domain.agent.prewarm import SeatPrewarm
from app.domain.agent.profiles import ProfileRegistry

# 兼容门面：提示词/上下文渲染搬去了 `prompt.py`（那里有直接的单测）。这里重新
# 导出，`app.domain.agent.chat` 仍是既有调用点与测试的导入路径；下面带 noqa 的
# 几个本文件不用，只是给外部（测试）留的导入路径。
from app.domain.agent.prompt import (
    _PROGRESS_MARK,  # noqa: F401 — 搬走的常量，这里仍然导得出来
    _REPLAY_NOTICE_AT,  # noqa: F401
    _REPLAY_NOTICE_EVERY,  # noqa: F401
    _addressed_to,
    _compaction_notice,  # noqa: F401 — 测试仍从 chat.py 导它
    _is_pending_input,  # noqa: F401
    _pending_input_blocks,
    _progress_lines,  # noqa: F401
    _prompt_topic_refs,  # noqa: F401
    live_inputs,
    read_images,
)

# 兼容门面：不碰实例状态的问答（这一轮谁答、项目 key 带多少额度、这条记忆改动
# 说进哪间房）搬去了 `queries.py`。这里重新导出，`app.domain.agent.chat` 仍是既有
# 调用点与测试的导入路径；`ChatService` 上留一行同名委托，调用点一格没动。
from app.domain.agent.queries import (
    _acting_handle,
    _agent_at,
    _agent_handle,
    _bail_notice,
    _block_payload,
    _gateway_budget_target,
    _pass_policy_gate,
    _Proposed,
    _resolved_agent,
    _session_agent,
)
from app.domain.agent.realtime.broker import get_broker
from app.domain.agent.recovery import SessionRecovery
from app.domain.agent.room import reads as room_reads
from app.domain.agent.room.sessions import RoomSessions
from app.domain.agent.room.turn import RoomTurns, _is_dm, room_roster

# 兼容门面：这一轮往房间里落下的那些行（事件块、步骤的判决、变更汇总）搬去了
# `room_events.py`（那里有它们各自的文档）。这里重新导出，`app.domain.agent.chat`
# 仍是既有调用点与测试的导入路径；`ChatService` 上留一行同名委托，调用点一格没动。
# 带 noqa 的常量本文件不用，只是给外部留的导入路径。
from app.domain.agent.room_events import (
    _known_commits,
    _turn_changeset,
    post_system_event,
)
from app.domain.agent.service import (
    AgentCompacting,
    AgentEvent,
    AgentResult,
    AgentRetrying,
    AgentToolResult,
    AgentUsage,
)
from app.domain.agent.session_turn_events import SessionTurnEvents
from app.domain.agent.skills import NATIVE_CHAT_GUIDANCE
from app.domain.agent.turn.intake.assistant import AssistantMessages
from app.domain.agent.turn.intake.events import (
    _persist_change_summary,
    _persist_room_event,
    _persist_subagent_result,
    _persist_tool_event,
)

# 这一进程正在跑的活（按房间/按轮次的进程内状态，``hook_work`` / 座位锁 /
# 几张 note 表……）收在 `turn/state/live.py` 那片叶子里，`ChatService.live` 是它唯一
# 持有者。状态与处理器之间只有「处理器读状态」一个方向。
from app.domain.agent.turn.state.live import HookWorkState, LiveWork
from app.domain.agent.turn.store.events import _mark_step_failed, _record_step_output
from app.domain.agent.turn_usage import record_turn_usage, reported_usage
from app.domain.agent.work_policy import work_policy
from app.domain.agent_instance.services import (
    AgentInstanceService,
    ResolvedAgent,
    memory_pool,
)
from app.domain.agent_session.services import AgentSessionService
from app.domain.block.about import EventAbout, landing
from app.domain.block.models import (
    AuthorType,
    Block,
    BlockKind,
    prompted_turn,
)
from app.domain.block.repositories import BlockRepository
from app.domain.block.schemas import BlockOut
from app.domain.delivery.input_identity import (
    InputEffects,
    InputOutcomeUnconfirmed,
    InputReceipt,
    InputReconciliationPending,
    InputRegistrar,
    WorkCompletion,
    WorkTermination,
)
from app.domain.delivery.receipts import (
    complete_work_inputs,
    terminate_work_inputs,
)
from app.domain.idempotency import store as idem
from app.domain.idempotency.keys import action_key
from app.domain.identity.actor import Actor
from app.domain.identity.handles import (
    looks_like_agent_handle,
    names_a_person,
    recipient_seat,
)
from app.domain.policy import gate
from app.domain.project.models import Project
from app.domain.project.repositories import ProjectRepository
from app.domain.room_task import naming
from app.domain.room_task.models import TaskStatus
from app.domain.room_task.place import Place, PlaceResolver
from app.domain.thread.services import conversation_inputs
from app.domain.topic.models import Topic, TopicStatus
from app.domain.topic_membership.services import TopicMemberService

CHEESE_AUTHOR = "cheese"

#: How many rooms replay their sessions' backlog at once. Each replay writes
#: what it lands to the database, which the turns running meanwhile share.
REPLAYS_AT_ONCE = 4

logger = logging.getLogger(__name__)


# Persistent, clickable action cards (§3.1.1 控件): each cheese action 芝士 takes
# is recorded as a system event block (shown in the conversation) tagged
# refs=["action:<resource>"], which the UI renders as a card linking to it.
# NOTE: no "doc" entry — a doc edit already lands the SAME 「编辑了文档」
# event every human edit gets (via the save path). One fact, one line,
# whoever the author is (用户拍板: 芝士不需要专属提示行).
#
# No "accept" entry either, for the same reason: filing a card and correcting
# one each announce themselves (EVENT_CARD_FILED / EVENT_CARD_REDESCRIBED), and
# those lines say who is now waiting on what. A generic 「芝士 提交了验收卡」 next
# to them is the same fact told twice, worse.
_ACTION_LABEL = {
    "notify": "actionNotify",
}


# HTTP statuses worth an automatic re-run: timeouts, throttling, server-side
# blips. Anything else (or a rejected seat rate-limit) surfaces immediately.
_TRANSIENT_HTTP = {408, 429, 500, 502, 503, 504, 529}

#: How many conversations' rooms to remember. Well past the number of
#: conversations one backend hears from at once; a ceiling, not a policy.
_CONVERSATION_ROOMS_KEPT = 2048


def _parse_uuid(raw: str | None) -> uuid.UUID | None:
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


class ChatService(SessionRecovery, RoomTurns):
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker,
        base_system_prompt: str,
        workspace_root: str,
        compute: ComputePool,
        work_runner: SessionTurnEvents,
        profiles: ProfileRegistry | None = None,
        gateway: LlmGateway | None = None,
    ):
        self._sessions = session_factory
        self._base_prompt = base_system_prompt
        # For the turn-meta disk line only; sandbox mounting still goes through
        # the compute pool below.
        self._workspace_root = workspace_root
        # Compute side of the two-pool model: a provider owns the machine and
        # its workspace (design §3/v3, review R2). Required rather than
        # defaulted: the default used to be a pool this service could build by
        # itself out of an SDK client, and building compute out of nothing is
        # exactly what no longer exists.
        self._compute = compute
        self._work_runner = work_runner
        # 这一进程正在跑的活：按房间/按轮次键住的进程内状态（`hook_work`、
        # `active_turn_ids`、座位锁与房间锁、几张 note 表、`dead_sessions`……）
        # 全在 `turn/state/live.py` 那片叶子里。本对象唯一持有它，处理器按一个方向读
        # 它——见那里的文档；不是服务依赖（`_compute`/`_sessions` 那种），而是
        # 这台进程此刻手上正接着什么。
        self.live = LiveWork()
        # Per-project ExecutionProfile (model + provider). None → always the
        # agent's built-in default (tests / single-profile deploys).
        self._profiles = profiles
        # LiteLLM gateway ADMIN client (L1/L2 — defined in
        # `app.domain.agent.gateway`). None = off.
        # The lock serializes key-mint and usage-drain read-modify-writes on
        # project.settings (single-process reality, like the topic locks).
        self._gateway = gateway
        self._gateway_lock = asyncio.Lock()
        # 记忆的对账（铺下去 / 收回来）走的是会话那条通道，所以回调挂在这里，
        # 由 harness 在两个时刻问它：输入之前、这一轮结束之后。这一簇连同它按
        # 房间记的四份状态都在 `memory_ledger.py` 里；它会用到的四件协作者显式
        # 传进去（`gateway`/`gateway_lock` 要先建好），留在 `ChatService` 上的
        # 只有 `_lock_for` 和 `_model_kwargs` 两件同事还在用的东西。
        self._memory: MemoryLedger = MemoryLedger(
            sessions=session_factory,
            compute=compute,
            gateway=gateway,
            gateway_lock=self._gateway_lock,
            base_prompt=base_system_prompt,
            host=self,
            live=self.live,
        )
        #: Brings a seat that went quiet up to date (`agent.prewarm`).
        self.prewarm = SeatPrewarm(self)
        self._compute.report_to(
            room_reads.reader(self),
            unread=self.oldest_unread_at,
            memory=self._memory.sync,
            quiet=self.prewarm.nudge,
        )
        # Keep the publication contract present before native skills are invoked.
        self._skills = NATIVE_CHAT_GUIDANCE
        # Strong refs to in-flight background tasks (asyncio only keeps weak
        # refs; without this a pending commit could be GC'd).
        self._background_tasks: set[asyncio.Task] = set()
        self._replay_slots = asyncio.Semaphore(REPLAYS_AT_ONCE)

    @property
    def messages(self) -> AssistantMessages:
        # Composition only: this stateless writer uses the service's real state
        # and transactions; it owns no copied live work or compatibility entry.
        return AssistantMessages(self._sessions, self.live, room_roster)

    @property
    def session_factory(self) -> async_sessionmaker:
        return self._sessions

    async def _turn_seat_handle(
        self,
        topic_id: uuid.UUID,
        *,
        user_block_id: uuid.UUID | None = None,
        recipient_instance_id: uuid.UUID | None = None,
        recipient_handle: str | None = None,
    ) -> str:
        """The conversation handle whose seat this turn serializes on.

        Resolved BEFORE the seat lock is taken, from the same facts
        `_assemble_turn` will resolve the running agent from: an explicitly
        addressed instance, the recipient the message was stamped with at
        posting, the oldest pending message's addressee (a platform turn picks
        that conversation up), and finally the room's default agent. The two
        reads moments apart agree unless the roster mutates mid-admission; a
        stale key then costs one extra parallel turn, never a lost one.
        """
        if recipient_handle is not None:
            return recipient_handle
        from app.domain.agent_instance.models import AgentInstance

        async with self._sessions() as session:
            instance_id = recipient_instance_id
            if instance_id is None:
                blocks = BlockRepository(session)
                addressed = None
                if user_block_id is not None:
                    addressed = await blocks.get(user_block_id)
                else:
                    place = await PlaceResolver(session).conversation(topic_id)
                    if place is not None:
                        pending = _pending_input_blocks(
                            await blocks.turn_history(place.conversation_id)
                        )
                        addressed = pending[0] if pending else None
                recipient = (
                    (addressed.meta or {}).get("agent_recipient")
                    if addressed is not None
                    else None
                )
                if recipient is not None and recipient.get("instance_id") is not None:
                    instance_id = uuid.UUID(recipient["instance_id"])
            if instance_id is not None:
                instance = await session.get(AgentInstance, instance_id)
                if instance is not None and instance.is_active:
                    return instance.handle
            place = await PlaceResolver(session).conversation(topic_id)
            if place is None:
                raise NotFoundError("Topic not found")
            agent = await self._agent_at(session, place)
            return agent.handle

    @asynccontextmanager
    async def _prompt_lock(
        self,
        topic_id: uuid.UUID,
        work_id: uuid.UUID,
        seat_handle: str,
    ) -> AsyncIterator[None]:
        from app.domain.agent.seat_admission import seat_admission

        async with seat_admission(self.live.seat_lock_for(topic_id, seat_handle)):
            self.live.mark_turn_active(topic_id, work_id)
            try:
                yield
            finally:
                if (topic_id, work_id) not in self.live.hook_work:
                    self.live.mark_turn_inactive(topic_id, work_id)

    async def converse(
        self,
        *,
        topic_id: uuid.UUID,
        author: str,
        content: str,
        summon: bool,
        turn_id: uuid.UUID | None = None,
        reply_to: str | None = None,
        attachments: list[dict] | None = None,
        is_resume: bool = False,
        resume_reason: str | None = None,
        nudge_event: str | None = None,
        nudge_meta: dict | None = None,
        continuation_id: uuid.UUID | None = None,
        provision_actor: Actor | None = None,
        delivery_id: uuid.UUID | None = None,
        recipient_instance_id: uuid.UUID | None = None,
    ) -> AsyncIterator[dict]:
        """Post the human message instantly, then (if summoned) run the agent
        turn serialized per topic (spec §9.1 串行队列). 现场必须实时: the human
        block persists + broadcasts BEFORE the lock, so a post never queues
        behind a running agent turn. turn_id groups this turn's blocks (R4);
        reply_to threads this message under another (B3); attachments are
        uploaded worktree images this message carries (图片输入).

        ``continuation_id`` is the logical unit of work this turn belongs to — a
        turn and every auto-resume of it share one, so a message the interrupted
        attempt already posted is not posted again (④).

        ``summon`` 没有默认值：这一轮跑不跑是**调用点算出来的一个答案**（由
        `runtime._a_turn_was_addressed` 从寻址结果读出），不是一个可以不写、写不写
        都默认「跑」的开关。给它默认值，就等于平台又有了一条不点名也能起轮次的路。
        """
        # 谁写了这一轮的正文：不是人写的，就是平台写的。判据是**作者**，不是
        # 「带没带 nudge」—— 平台那几条投递（机器接入、环境修好、记忆整理、闸门红
        # 了…）作者一律是 `system`，正文是平台写的一段提示词，把它当人话落进时间
        # 线就是让平台冒充人说话。房间里那一行由谁写是另一件事，见下面。
        platform_wrote_this = (
            is_resume or nudge_event is not None or not names_a_person(author)
        )
        # 平台指令那一档：下面 `_converse_impl` 用它保证这段指令不会被房间里的待读
        # 消息挤掉。重发不算：重发的 `content` 是原话再送一次，待读窗口本来就会把同
        # 一段话重新递上来，两边都拼就是同一句说两遍。
        platform_turn = platform_wrote_this and not is_resume
        # Record the arrival-time state before persistence and acknowledgements.
        # If live work ends during either operation, queueing is still a fallback
        # from the user's attempted live handoff and must be reported.
        live_delivery_expected = (
            summon
            and not is_resume
            and nudge_event is None
            and self.has_running_turn(topic_id)
        )
        if platform_wrote_this:
            turn_id = turn_id or uuid.uuid4()
            continuation_id = continuation_id or turn_id
            # System-initiated turn (重发 / 冲突调度…): no human
            # spoke — the opener is a SYSTEM event in the 现场, and the
            # instruction goes straight to the agent as the prompt.
            #
            # 平台提示统一契约: `nudge_event` is the one line the room sees,
            # `nudge_meta` its structured payload — which is where a caller puts
            # the长文 (CI 日志 / 检查输出 / 冲突文件清单) so the room stays
            # glanceable while nothing is lost. `content` is untouched: it is
            # still the whole instruction 芝士 gets as its prompt.
            if is_resume and not nudge_event:
                nudge_event = resume_reason or say("turnResent")
            # 开场白留空 = 调用点已经自己写好了那一行。Cloud 机器接入就是这一种：
            # 那一行同时是房间的生命周期记录，必须在这一轮排队之前就落库，否则算力
            # 闸一拒就永远不写（见 api/deps.py 的 `deliver_held`）。这一轮照样不说
            # 人话 —— 只是这次没有第二句要说。
            if nudge_event is not None:
                payload = await self.post_system_event(
                    topic_id, nudge_event, turn_id, meta=nudge_meta
                )
                if payload is None:
                    raise NotFoundError("Topic not found")
                yield {"type": "event_block", "block": payload}
            if not summon:
                yield {"type": "done"}
                return
            user_block_id = None
        else:
            (
                user_payloads,
                user_block_id,
                user_block_ids,
                _duplicate,
            ) = await self.post_user_message(
                topic_id,
                author=author,
                content=content,
                turn_id=turn_id,
                reply_to=reply_to,
                attachments=attachments,
            )
            turn_id = turn_id or user_block_id
            continuation_id = continuation_id or turn_id
            for payload in user_payloads:
                yield {"type": "user_block", "block": payload}

            # Default human-to-human: post and stay quiet (spec C3 / §7.1).
            if not summon:
                yield {"type": "done"}
                return

            # The read marker belongs to the structured native echo's database
            # transaction. Neither accepting the human message here nor an RPC
            # acknowledgement proves the native session read the input.

        # A turn is already running on this topic. Don't queue behind it —
        # hand the message to the session that is running RIGHT NOW.
        #
        # The platform used to be stricter than the tool it drives: an
        # interactive Claude Code accepts input while it works and folds it into
        # the run, but we serialized turns on top of that, so one slow command
        # made every later message wait the whole turn out. Injecting instead
        # gets the message in front of 芝士 in seconds.
        #
        # Only the hooks-driven backends can take it (they own a live screen).
        # If the handoff fails, the message remains pending and runs through the
        # normal queue, but that degradation must be visible in the room.
        if user_block_id is not None and (
            live_delivery_expected or self.has_running_turn(topic_id)
        ):
            delivered = await self.merge_into_running_turn(
                topic_id,
                user_block_ids,
                content,
                author,
                attachments,
            )
            if isinstance(delivered, InputReconciliationPending):
                checking, checking_meta = delivery_checking_notice()
                await self.post_system_event(
                    topic_id, checking, turn_id, meta=checking_meta
                )
                return
            if delivered is True:
                # The answer streams out of the turn already in flight, which
                # every client in this topic is subscribed to — this request has
                # nothing left to yield.
                return
            logger.warning(
                "live delivery fell back to the queue (topic=%s, turn=%s, "
                "delivered=%s)",
                topic_id,
                turn_id,
                delivered,
            )
            fallback_text, fallback_meta = delivery_fallback_notice()
            await self.post_system_event(
                topic_id, fallback_text, turn_id, meta=fallback_meta
            )

        recipient_handle = None
        if recipient_instance_id is not None:
            from app.domain.agent_instance.models import AgentInstance

            async with self._sessions() as session:
                instance = await session.get(AgentInstance, recipient_instance_id)
                if instance is None or not instance.is_active:
                    raise ValidationError("The addressed agent is unavailable")
                recipient_handle = instance.handle
        seat_handle = await self._turn_seat_handle(
            topic_id,
            user_block_id=user_block_id,
            recipient_handle=recipient_handle,
        )
        async with self._prompt_lock(topic_id, turn_id, seat_handle):
            async for frame in self._converse_impl(
                topic_id=topic_id,
                content=content,
                turn_id=turn_id,
                author=author,
                user_block_id=user_block_id,
                is_resume=is_resume,
                continuation_id=continuation_id,
                provision_actor=provision_actor,
                platform_turn=platform_turn,
                delivery_id=delivery_id,
                recipient_instance_id=recipient_instance_id,
            ):
                yield frame

    async def converse_prepared(
        self,
        *,
        topic_id: uuid.UUID,
        author: str,
        content: str,
        turn_id: uuid.UUID,
        user_block_id: uuid.UUID,
        continuation_id: uuid.UUID | None = None,
        provision_actor: Actor | None = None,
        recipient_handle: str | None = None,
        recipient_instance_id: uuid.UUID | None = None,
    ) -> AsyncIterator[dict]:
        """Prepare an already durable message after project admission."""
        seat_handle = await self._turn_seat_handle(
            topic_id,
            user_block_id=user_block_id,
            recipient_handle=recipient_handle,
        )
        async with self._prompt_lock(topic_id, turn_id, seat_handle):
            async for frame in self._converse_impl(
                topic_id=topic_id,
                content=content,
                turn_id=turn_id,
                author=author,
                user_block_id=user_block_id,
                continuation_id=continuation_id,
                provision_actor=provision_actor,
                recipient_instance_id=recipient_instance_id,
            ):
                yield frame

    async def merge_into_running_turn(
        self,
        topic_id: uuid.UUID,
        user_block_ids: list[uuid.UUID],
        content: str,
        author: str,
        attachments: list[dict] | None = None,
        recipient_handle: str | None = None,
        *,
        owes_reply: bool = True,
    ) -> bool | InputReconciliationPending | None:
        """Inject a just-posted human message into the turn already running on
        this topic.

        ``owes_reply`` is whether the message was addressed to the agent: then
        the session answers it in the room before it uses any other tool. One
        said to somebody else in the room is only for the agent to know about.

        ``True`` means transport acceptance was recorded. A reconciliation
        result holds the registered identity without authorizing a queued retry.
        ``False`` means a pre-send failure; ``None`` means no live work remained.

        The text is labelled the same way `prompt_line` labels a pending block,
        so a message that arrives mid-turn reads identically to one that came in
        the prompt — 芝士 must not have to tell the two apart to know who spoke.

        Images use the same @path input path as an initial prompt. Interactive
        providers resolve that path into a native image block before the model
        sees the message; a remote device first stages the exact bytes and acks
        the file write."""
        consuming_turn_id = self.live.consuming_work_id(
            topic_id,
            lambda state: (
                recipient_handle is None
                or state.agent_instance_handle is None
                or state.agent_instance_handle == recipient_handle
            ),
        )
        if consuming_turn_id is None:
            return None
        state = self.live.hook_work.get((topic_id, consuming_turn_id))
        async with self._sessions() as session:
            stored, replied = await live_inputs(session, user_block_ids)
        lines, images = live_input_lines(
            author,
            content,
            attachments,
            stored=stored,
            replied=replied,
            recipient=state.acting_agent if state else None,
        )
        state = self.live.hook_work.get((topic_id, consuming_turn_id))
        line = publication_prompt("\n".join(lines))
        registrar = self._input_registrar(
            InputEffects(
                held_block_ids=tuple(user_block_ids),
                block_ids=tuple(user_block_ids),
                seen_block_ids=tuple(user_block_ids),
                seen_by=state.acting_agent if state else None,
            ),
            probe_unread=True,
        )
        try:
            # Read the room's status, then deliver with no transaction open: a
            # row lock held across the device call queues every writer of the
            # room behind it with a pool connection each (dev outage of
            # 2026-09-18), and it never held archival off anyway — the archive
            # path takes the same non-conflicting KEY SHARE lock.
            async with self._sessions() as session:
                place = await PlaceResolver(session).conversation(topic_id)
                archived = (
                    place is None
                    or place.room.status == TopicStatus.archived
                    or (place.task is not None and place.task.status != TaskStatus.open)
                )
                pictures = await read_images(
                    session, place.room if place else None, images
                )
            if archived:
                delivered = False
            else:
                # 话说给在跑这一轮的那个座位：同房间另一个 agent 也有一轮在跑
                # 时，不带座位就是把话猜进别人的会话里。
                seat_agent = (
                    (state.agent_instance_handle or state.acting_agent)
                    if state
                    else None
                )
                delivered = await self._compute.steer(
                    topic_id,
                    line,
                    images=pictures or None,
                    register_input=registrar,
                    expected_work_id=consuming_turn_id,
                    agent_handle=seat_agent,
                    owes_reply=owes_reply,
                )
        except InputOutcomeUnconfirmed as exc:
            # The registered input may already be in the native session. Leave
            # it with that session for reconciliation, never enqueue a new input.
            logger.exception(
                "live input requires reconciliation (topic=%s, input=%s)",
                topic_id,
                exc.identity.input_id,
            )
            return InputReconciliationPending(exc.identity, exc.accepted)
        except Exception:  # noqa: BLE001 — pre-send failure may queue a fallback
            logger.exception("merge into running turn failed (topic=%s)", topic_id)
            delivered = False
        if not delivered:
            return False
        return True

    async def remind_silent_turns(self) -> int:
        """Queue an internal reminder while a room response is still running."""
        now = datetime.now(UTC)
        due = [
            state
            for state in self.live.hook_work.values()
            # Background inspections have their own notification policy. Only
            # work answering a person owes a periodic chat update.
            if state.reply_to is not None
            and (
                now
                - max(
                    state.last_chat_at or state.started_at,
                    state.last_progress_reminder_at
                    or (state.last_chat_at or state.started_at),
                )
            ).total_seconds()
            >= settings.chat_progress_reminder_after_s
            and state.work_id in self.live.active_turn_ids.get(state.topic_id, ())
        ]

        async def remind(state: HookWorkState) -> bool:
            if self.live.hook_work.get(
                (state.topic_id, state.work_id)
            ) is not state or state.work_id not in self.live.active_turn_ids.get(
                state.topic_id, ()
            ):
                return False
            silent_for = now - (state.last_chat_at or state.started_at)
            minutes = int(silent_for.total_seconds() // 60)
            try:
                # Repeats every `chat_progress_reminder_after_s` of continued
                # silence. A publication clears this and `last_chat_at` together,
                # so speaking is what stops the reminders; tool output and
                # duplicate send requests are not speaking.
                state.last_progress_reminder_at = now
                # A blocked terminal must not hold up reminders in other rooms.
                async with asyncio.timeout(5):
                    delivered = await self.notify_running_turn(
                        state.topic_id,
                        f"You have published nothing to this room for {minutes} "
                        "minutes and the person who asked is still waiting. If "
                        "this turn is still running, call chat_send now with "
                        "what you know so far and what you are waiting on — a "
                        "room that shows nothing cannot be told apart from one "
                        "that is stuck. If your plan has changed, also update "
                        "it with todo_write. Ignore this only if the turn is "
                        "already finished.",
                    )
                # THAT a reminder fired was already visible — the periodic
                # loop logs `chat progress reminder: <count>` on any cycle whose
                # result is worth reporting (app/core/background.py). What a
                # count cannot carry is which room, which turn, how long it had
                # been dark, and whether the transport took it, and those are
                # the four things needed to tell 「the agent was reminded and
                # stayed quiet」 from 「the reminder never reached it」.
                logger.info(
                    "chat progress reminder topic=%s turn=%s silent_min=%d "
                    "delivered=%s",
                    state.topic_id,
                    state.work_id,
                    minutes,
                    delivered,
                )
                return delivered is True
            except Exception:  # noqa: BLE001 — one room must not stop the sweep
                logger.exception(
                    "chat progress reminder failed (topic=%s)", state.topic_id
                )
                return False

        return sum(await asyncio.gather(*(remind(state) for state in due)))

    async def notify_running_turn(
        self,
        topic_id: uuid.UUID,
        notice: str,
        *,
        blocks: Sequence[uuid.UUID] = (),
        recipient_seat: str | None = None,
    ) -> bool | InputReconciliationPending:
        """Tell the turn already running on this topic that the world changed
        under it. Returns whether the live session took it.

        The same channel as a person's mid-turn message, carrying the other kind
        of thing a turn needs to hear. A long turn is built on a snapshot taken
        at its first second — the doc, the roster, the cards — and the only way
        anything could reach it afterwards was somebody typing. So a person
        editing the living doc mid-turn changed nothing 芝士 could see, and it
        kept working from, and writing back, the version it started with.

        Framed as a platform notice, and the body is neutralized first: the
        marker is the one thing in a prompt that claims institutional authority,
        so a heading someone typed into the doc must not be able to carry it in.

        ``blocks`` is where this notice is written down. Given them, delivery
        stops being all-or-nothing: the receipt stamps them consumed, and a
        notice that never landed stays pending for the next turn to read — the
        same 宁可重复不可丢失 a person's mid-turn message already gets. Without
        them a notice that misses is gone, which is right only for something
        that is worthless a minute later (the chat-silence reminder), and was
        wrong for everything else: the old reasoning was that the next turn
        reads the doc fresh anyway, and the next turn does not — a reused
        session keeps the system prompt it was started with.
        """
        consuming_turn_id = self.live.consuming_work_id(
            topic_id,
            lambda state: (
                recipient_seat is None or state.acting_agent == recipient_seat
            ),
            strict=recipient_seat is not None,
        )
        if consuming_turn_id is None:
            return False
        line = platform_prompt(strip_platform_notice(notice))
        registrar = self._input_registrar(
            InputEffects(held_block_ids=tuple(blocks), block_ids=tuple(blocks)),
            probe_unread=bool(blocks),
        )
        state = self.live.hook_work.get((topic_id, consuming_turn_id))
        seat_agent = (
            (state.agent_instance_handle or state.acting_agent) if state else None
        )
        try:
            return bool(
                await self._compute.steer(
                    topic_id,
                    line,
                    register_input=registrar,
                    expected_work_id=consuming_turn_id,
                    agent_handle=seat_agent,
                )
            )
        except InputOutcomeUnconfirmed as exc:
            logger.exception(
                "notice input requires reconciliation (topic=%s, input=%s)",
                topic_id,
                exc.identity.input_id,
            )
            return InputReconciliationPending(exc.identity, exc.accepted)
        except Exception:  # noqa: BLE001 — a failed notice must not fail the write
            logger.exception(
                "platform notice into running turn failed (topic=%s)", topic_id
            )
            return False

    def oldest_unread_at(self, topic_id: uuid.UUID) -> float | None:
        """When the longest-waiting unconsumed injection into this topic was
        written, on the loop clock, or None when nothing is waiting.

        The mirror of `confirm_prompt_receipt`: that one clears an entry when
        the session proves it read the text, this one reports what is left. A
        session that has stopped reading keeps every other liveness signal
        looking healthy, because those all watch what it PRODUCES, and it can
        produce output forever with its input queue frozen. What it cannot do is
        answer anybody, so this is the check that has a person behind it.
        """
        pending = self.live.unread_inputs.get(topic_id)
        return min(pending.values()) if pending else None

    def _input_registrar(
        self,
        effects: InputEffects,
        *,
        probe_unread: bool = False,
        fence_delivery: bool = False,
    ) -> InputRegistrar:
        from app.domain.agent.input_registration import input_registrar

        return input_registrar(
            self._sessions,
            effects,
            self.live,
            probe_unread=probe_unread,
            fence_delivery=fence_delivery,
        )

    async def confirm_prompt_receipt(self, receipt: InputReceipt) -> None:
        """Commit identity-bound effects before the journal may acknowledge.

        No in-memory candidate is needed. Commit failure propagates so the same
        journal input is retried, even by a newly reconstructed ChatService.
        """
        from app.domain.agent.input_registration import confirm_receipt

        await confirm_receipt(self._sessions, self.live, receipt)

    async def confirm_work_completion(self, completion: WorkCompletion) -> None:
        """Settle a journaled completion without process-local work context."""
        from app.domain.agent.pending_messages import finish_work

        await finish_work(self, completion, complete_work_inputs)

    async def confirm_work_termination(self, termination: WorkTermination) -> None:
        """Record that a work interval ended without completing.

        Separate from :meth:`confirm_work_completion` on purpose: a terminated
        work must never reach the completion path, which is what stamps
        ``completed_at`` and consumes blocks. This one only frees the seat so a
        new input can be taken.
        """
        from app.domain.agent.pending_messages import finish_work_termination

        await finish_work_termination(self, termination, terminate_work_inputs)

    def session_controls(self, topic_id: uuid.UUID):
        """The runtime whose live session in this room takes controls, if any."""
        return self._compute.session_controls(topic_id)

    async def recover_native_tools(
        self, topic_id: uuid.UUID, agent_handle: str | None = None
    ) -> bool:
        """Platform tools back for this room; True when they had been gone."""
        return await self._compute.recover_native_tools(topic_id, agent_handle)

    def has_running_turn(
        self, topic_id: uuid.UUID, agent_handle: str | None = None
    ) -> bool:
        """Whether this process currently owns live work for the topic.

        With ``agent_handle``, work of that agent's: teammates in one room run
        side by side, so another seat working leaves this one free. A turn
        whose agent is not known yet counts as anybody's.
        """
        active = self.live.active_turn_ids.get(topic_id)
        if not active:
            return False
        if agent_handle is None:
            return True
        for work_id in active:
            state = self.live.hook_work.get((topic_id, work_id))
            if state is None or state.agent_instance_handle in (None, agent_handle):
                return True
        return False

    async def has_unread_input(self, topic_id: uuid.UUID) -> bool:
        """Whether anything said in the room is still waiting to reach 芝士.

        「一个人说的」不再是判据：人和 agent 是同一种参与者（结论 1），一个 AI
        队友说进房间的一句话同样是没人读过的输入。不算数的是芝士**自己跑出来的
        产出** —— 那一条在写入端就不带待读标记（`BlockRepository.add`）。

        「忘了 @」的补救按钮问的就是这一句，所以它必须和真正组装 prompt 时问的
        是同一个问题 —— 同一个 `_pending_input_blocks`，不是一份近似的复制品。
        一份复制品会在窗口语义改动时悄悄和它分叉，而分叉的表现是按钮说「它还没
        看到」、点下去却什么也没有可读，白烧一轮。
        """
        async with self._sessions() as session:
            history = await conversation_inputs(session, topic_id)
            return bool(_pending_input_blocks(history))

    async def pending_seat(self, topic_id: uuid.UUID) -> str | None:
        """The teammate this room's unread inputs were addressed to, if any.

        重试按钮问的就是这一句。那批还没人读的消息**是点名交给谁的**，这一轮就
        该交给谁：一个房间可以坐好几位 AI 队友，而「房间的默认席位」是另一个答
        案 —— 取它的话，另一位队友的轮次失败之后一点重试就换成默认芝士来接，而
        默认芝士那一轮的待读窗口里根本没有点名给那位队友的消息（`_addressed_to`
        按收件人过滤），于是它接了一轮却读不到真正找它的那句话。

        「没人被点名」是常态而不是异常（没 @ 不等于没说）：那种消息本来就是房间
        认的那一位的事，所以返回 None，由调用点回落到默认席位。

        窗口语义与 :meth:`has_unread_input` 共用同一个 `_pending_input_blocks`，
        理由同它：一份近似的复制品会在窗口语义改动时悄悄和它分叉。
        """
        async with self._sessions() as session:
            history = await conversation_inputs(session, topic_id)
            # 从新到旧：最近一次点名是这批消息现在要交给谁的最新说法。
            for block in reversed(_pending_input_blocks(history)):
                recipient = (block.meta or {}).get("agent_recipient") or {}
                if recipient.get("mentioned"):
                    return recipient_seat(recipient)
            return None

    @asynccontextmanager
    async def edit_environment(self, topic_id: uuid.UUID) -> AsyncIterator[None]:
        """Prevent a new prompt from racing an explicit environment change."""
        lock = self.live.lock_for(topic_id)
        if lock.locked() or self.has_running_turn(topic_id):
            raise ValidationError(say("roomBusyFinishBeforeEnvironment"))
        async with lock:
            if self.has_running_turn(topic_id):
                raise ValidationError(say("roomBusyRetryLater"))
            yield

    def session_took_over(self, topic_id: uuid.UUID, turn_id: uuid.UUID) -> bool:
        """Did the live session take responsibility for THIS turn's indicator?

        The `session_lifecycle` frame says a session will own the ending; it
        does not say which turn's. When one is already running on the topic, the
        session reuses its activity rather than opening a second — so the newer
        turn gets no start of its own and will get no ending either. Asking by
        turn id is the difference between a handover and an assumption.
        """
        return turn_id in self.live.active_turn_ids.get(topic_id, ())

    async def post_system_event(
        self,
        topic_id: uuid.UUID,
        content: str,
        turn_id: uuid.UUID | None = None,
        *,
        meta: dict | None = None,
    ) -> dict | None:
        """Persist a system event into the conversation ``topic_id`` names —
        a room's own line, or a task's (room_events.py)."""
        room_id, inner_id = await self._room_of_conversation(topic_id)
        return await post_system_event(
            self._sessions,
            room_id,
            content,
            turn_id,
            meta=meta,
            inner_id=inner_id,
        )

    async def work_policy(
        self, topic_id: uuid.UUID, agent_instance_id: uuid.UUID | None = None
    ) -> dict | None:
        """Admission facts the AgentWorkRunner gates on BEFORE running a turn,
        for the agent it is addressed to when that is known."""
        return await work_policy(
            self._sessions, self._compute, topic_id, agent_instance_id
        )

    async def _close_open_turns(self, topic_id: uuid.UUID, turn_id: uuid.UUID) -> None:
        """End the one open interval the Stop names (FB-56). Never raises — a
        Stop that cannot update the bookkeeping must still land the message it
        carries. An id that names no live row closes nothing."""
        from datetime import UTC, datetime

        from app.domain.agent.repositories import AgentTurnRepository

        try:
            async with self._sessions() as session:
                closed = await AgentTurnRepository(session).close_one(
                    topic_id, turn_id, datetime.now(UTC)
                )
                if closed:
                    await session.commit()
        except Exception:  # noqa: BLE001 — the Stop matters more than the row
            logger.exception("could not close open turns for topic %s", topic_id)

    async def _turn_credits_refused(self, turn_id: uuid.UUID) -> bool:
        """Was `turn_id` ever stamped refused-for-credits by admission? What
        the turn's own end (`StopFailure`) reads to decide whose wording —
        the platform's or Claude Code's — the room gets."""
        from app.domain.agent.repositories import AgentTurnRepository

        try:
            async with self._sessions() as session:
                return await AgentTurnRepository(session).credits_refused(turn_id)
        except Exception:  # noqa: BLE001 — a failed read must not break the notice
            logger.exception("could not read credits-refused stamp for %s", turn_id)
            return False

    def row_is_dead(
        self, topic_id: uuid.UUID, agent_handle: str, session_id: str | None
    ) -> bool:
        """Is THIS row's conversation known dead (FB-56 legacy③)?"""
        return death_evidence.row_is_dead(
            self.live, self._compute, topic_id, agent_handle, session_id
        )

    def seat_state(self, topic_id: uuid.UUID, agent_handle: str) -> str:
        """One of "live" / "dead" / "unknown" for the seat (FB-56 legacy③)."""
        return death_evidence.seat_state(
            self.live, self._compute, topic_id, agent_handle
        )

    def has_live_screen(
        self, topic_id: uuid.UUID, agent_handle: str | None = None
    ) -> bool:
        """Is a session for this topic still reachable? The orphan sweep's first
        question, and the one that used to be unanswerable.

        With ``agent_handle`` it is that agent's session: a room seats several
        teammates, and one of them still answering says nothing about another.
        """
        return self._compute.holds(topic_id, agent_handle)

    async def turns_that_produced_something(
        self, turn_ids: list[uuid.UUID]
    ) -> set[uuid.UUID]:
        """Which of these turns own at least one AI-authored block.

        Second-hand proof that the prompt arrived, and a narrow backstop rather
        than a source: the runtime records delivery first-hand the moment the
        transport accepts the write, so the only thing this covers is a process
        dying between that write and the record of it. Without it, that
        millisecond re-sends a task 芝士 is already working on.
        """
        if not turn_ids:
            return set()
        async with self._sessions() as session:
            return await BlockRepository(session).ai_turn_ids(turn_ids)

    async def stop_listening(self, timeout_s: float) -> None:
        """Hand every session this process listens to over to the next one.

        A message written into a running session is stamped consumed only when
        the session's receipt for it is read back; stop reading before that and
        the next turn sends it again. So the receipts already on their way are
        read first, for up to ``timeout_s``, and only then does this process
        stop reading. A receipt still missing by then is the ordinary case of a
        session that stopped reading, and replays like one.
        """
        deadline = time.monotonic() + timeout_s
        while any(self.live.unread_inputs.values()) and time.monotonic() < deadline:
            await asyncio.sleep(0.2)
        # A replay still running reads its sessions too; the next process
        # replays them again from where this one landed.
        replays = list(self.live.replays.values())
        for replay in replays:
            replay.cancel()
        await asyncio.gather(*replays, return_exceptions=True)
        await self._compute.stop_listening()

    async def _save_session_pointer(
        self,
        topic_id: uuid.UUID,
        session_id: str,
        *,
        agent_handle: str | None = None,
        harness: str | None = None,
    ) -> None:
        """Best-effort: point the PLACE at the (possibly partial) session so the
        next summon resumes it. Never raises — used on failure paths.

        Resolved as a place, not looked up in `topics`: a thread is a `tasks`
        row, so asking that table for one comes back empty and every write below
        is skipped — silently, on the success path as much as the failure one.
        That row is what "this place has run" IS (agent_session/models.py), so a
        thread that skipped it has no 现场 to open after hours of work, and
        nothing for a cold start to resume.
        """
        try:
            async with self._sessions() as session:
                place = await PlaceResolver(session).conversation(topic_id)
                if place is not None:
                    # An event names who ACTED: the seat its session authors
                    # under. The pointer is keyed by the agent that seat
                    # belongs to, the key the next turn reads it back under
                    # (the resume lookup in `_assemble_turn`); written under
                    # the seat, it is never found and every cold start opens
                    # a new conversation. A room-derived seat, the shared one
                    # and an event that names nobody are the room's agent.
                    owner = await ProjectRepository(session).get(place.project_id)
                    agent = (
                        await AgentInstanceService(session).for_seat_handle(
                            owner, agent_handle
                        )
                        if owner is not None
                        else None
                    ) or await self._agent_at(session, place)
                    # 事件没说骨架，就问这个项目跑的是哪个——同一个答法，和开
                    # 这一轮用的那一个（结论 28）。
                    harness = harness or harness_for(owner.settings if owner else None)
                    # Lock the pointer row before moving it: the active-source
                    # guard in `hook_stream._bind_user_entry` holds the same
                    # lock while it validates and mutates, so the two sides of
                    # a session change serialize on the row itself (FB-56 P2-1).
                    from sqlalchemy import select as _select

                    from app.domain.agent_session.models import AgentSession as _AS

                    await session.execute(
                        _select(_AS.id)
                        .where(
                            _AS.conversation_id == place.conversation_id,
                            _AS.agent_handle == agent.handle,
                            _AS.harness == harness,
                        )
                        .with_for_update()
                    )
                    self.live.dead_sessions.discard(
                        (place.conversation_id, agent.handle, session_id)
                    )
                    await AgentSessionService(session).remember(
                        conversation_id=place.conversation_id,
                        agent_handle=agent.handle,
                        resume_token=session_id,
                        harness=harness,
                    )
                    await session.commit()
        except Exception:  # noqa: BLE001 — never mask the original failure
            logger.exception("failed to save session pointer for %s", topic_id)

    async def _set_hook_activity(
        self,
        project_id: uuid.UUID,
        topic_id: uuid.UUID,
        work_id: uuid.UUID,
        active: bool,
        *,
        agent_handle: str | None = None,
    ) -> None:
        """Project subscription activity onto the existing realtime protocol."""
        del project_id

        # 「谁在干活」要和块署名答同一个名字：块落在 acting seat 上，所以
        # 帧也带它。轮次开账前（自起的轮次，账还没开）状态不在，退回运行时
        # 给的会话座位。
        state = self.live.hook_work.get((topic_id, work_id))
        agent = (state.acting_agent if state is not None else None) or agent_handle
        if active:
            self.live.mark_turn_active(topic_id, work_id)
            frame = {"type": "turn_started", "turn_id": str(work_id), "agent": agent}
        else:
            self.live.mark_turn_inactive(topic_id, work_id)
            frame = {"type": "turn_finished", "turn_id": str(work_id), "agent": agent}
        await get_broker().publish(str(topic_id), frame)
        if not active:
            # The agent has said what it understood: the moment to check the
            # name a task got from its opening line (room_task/naming.py).
            naming.nudge(topic_id, "turn")
            from app.domain.agent.pending_messages import nudge_messages

            nudge_messages(self, topic_id)

    async def _room_of_conversation(
        self, conversation_id: uuid.UUID
    ) -> tuple[uuid.UUID, uuid.UUID | None]:
        """``(room, inner)`` for a conversation: a room is its own room with
        nothing inside, a task or a 支线 is the room it is in and itself.

        Everything a session says arrives keyed by its conversation, while the
        roster, the machine and the files are its room's. A task stays in the
        room it hangs in until someone moves it, which drops the remembered
        answer (``forget_conversation``).
        """
        room, inner, _thread = await self._conversation_place(conversation_id)
        return room, inner

    async def _conversation_place(
        self, conversation_id: uuid.UUID
    ) -> tuple[uuid.UUID, uuid.UUID | None, bool]:
        """``_room_of_conversation``, and whether the conversation is a 支线."""
        known = self.live.conversation_rooms.get(conversation_id)
        if known is not None:
            return known
        async with self._sessions() as session:
            place = await PlaceResolver(session).conversation(conversation_id)
        answer = (
            (place.room_id, place.inner_id, place.thread is not None)
            if place is not None
            else (conversation_id, None, False)
        )
        if len(self.live.conversation_rooms) >= _CONVERSATION_ROOMS_KEPT:
            self.live.conversation_rooms.pop(next(iter(self.live.conversation_rooms)))
        self.live.conversation_rooms[conversation_id] = answer
        return answer

    async def thread_replied(self, conversation_id: uuid.UUID) -> None:
        """A message landed in ``conversation_id``: when that is a 支线, its
        channel's main line shows the 支线 grown."""
        from app.domain.agent.staleness import announce_stale

        room, _inner, thread = await self._conversation_place(conversation_id)
        if thread:
            await announce_stale(room, "threads")

    def forget_conversation(self, conversation_id: uuid.UUID) -> None:
        """A task moved to another room: read its room again next time."""
        self.live.conversation_rooms.pop(conversation_id, None)

    async def _begin_self_started_turn(
        self,
        project_id: uuid.UUID,
        topic_id: uuid.UUID,
        turn_id: uuid.UUID,
        *,
        opened: bool = False,
        agent_handle: str | None = None,
        session_id: str | None = None,
    ) -> "HookWorkState | None":
        """Give a turn the session started for itself the context to end like
        any other: an interval a sweep can find, and everything its Stop needs.

        Without this, a self-started turn's Stop landed the message and then did
        nothing at all — no usage row, no conclusion cards settled, no change
        summary, and no interval to close, because none was ever opened. The
        room could not even tell you the turn had happened.

        署名是这个房间的席位 handle。退场的是那个谁也没写过的作者值（字面量就是
        「会话」两个字，结论 13），不是这条记录 —— 一个 worker 做完唤醒主线程，
        那是同一个 handle 两条线程之间的一条便条，发件人就在这儿。

        Read rather than assembled: there is no prompt to build here, so this
        takes only what turn END needs, and takes it in one transaction. Two
        fields are deliberately not read — `roster` stays None so the message
        path loads it (passing [] would mean 私聊 and flag every @ as a
        non-member), and `pending_ids` stays empty because nothing was fed to
        this turn. A message that merges into it mid-flight is stamped consumed
        by its own receipt (`confirm_prompt_receipt`), not from here.

        ``opened`` is a turn whose interval already exists: one a previous
        process of ours fed and delivered, which outlived it and is found again
        by recovery. It gets the same context and no second row, and what the
        process that assembled it wrote on that row: where its model traffic
        went, the message it answers, and when it really started.

        ``agent_handle`` is the agent whose session this is, when the caller
        knows it. Recovery does — it is on the session's own key — and must pass
        it: the room's fallback is the project default, and a teammate's turn
        recorded under the default authors every block of that turn under the
        wrong seat.

        Returns None if the place is gone or the bookkeeping write fails; the
        event that triggered this still lands, exactly as it did before.
        """
        from app.domain.agent.repositories import AgentTurnRepository

        try:
            async with self._sessions() as session:
                place = await PlaceResolver(session).conversation(topic_id)
                if place is None:
                    return None
                topic = place.room
                project = await ProjectRepository(session).get(project_id)
                if project is None:
                    return None
                agents = AgentInstanceService(session)
                agent = (
                    await self._agent_at(session, place)
                    if place.task is not None and agent_handle is None
                    else await self._session_agent(agents, topic, project, agent_handle)
                )
                agent_pool = memory_pool(topic.project_id, agent)
                # 署这个会话所属队友的名，不是房间的默认队友：一间坐着几位队友
                # 的房间里，别人的会话自己开的一轮署成默认那位，现场和「正在处
                # 理」就会把干活的人认错。
                acting_agent = await self._acting_handle(session, place.room_id, agent)
                # Read whether or not this process opened it: a session keeps
                # working across a backend restart, and the process that fed or
                # first saw the turn wrote what it knew on the row.
                row = await AgentTurnRepository(session).get(turn_id)
            if not opened:
                await self._work_runner.open_turn_the_session_started(
                    self,
                    topic_id,
                    turn_id,
                    author=acting_agent,
                    agent_handle=agent.handle,
                    session_id=session_id,
                )
        except Exception:  # noqa: BLE001 — the event matters more than the row
            logger.exception(
                "could not open the turn a session started for itself "
                "(topic=%s, work=%s)",
                topic_id,
                turn_id,
            )
            return None
        state = HookWorkState(
            project_id=project_id,
            topic_id=topic_id,
            work_id=turn_id,
            pending_ids=set(),
            reply_to=row.reply_to if row is not None else None,
            roster=None,
            topic_refs=[],
            continuation_id=turn_id,
            # "native" when this process has never assembled a turn for this
            # session (a screen recovered on the way up, say). It is the answer
            # that cannot invent spend: the gateway's log is drained by whatever
            # turn closes next, which is exactly what happened before any of
            # this existed.
            route=(row.route if row is not None else None)
            or self.live.session_route.get(topic_id, "native"),
            model=self.live.session_model.get(topic_id, ""),
            acting_agent=acting_agent,
            agent_pool=agent_pool,
            user_text="",
            started_at=row.started_at if row is not None else datetime.now(UTC),
            agent_instance_handle=agent.handle,
            known_commits=asyncio.ensure_future(
                self._known_commits(project_id, place.room_id)
            ),
            self_started=not opened,
        )
        self.live.hook_work[(topic_id, turn_id)] = state
        if opened:
            # The session announced this turn's start to the process that fed
            # it, so this one never heard it: without this a message sent now
            # would start a turn beside it instead of joining it.
            # The single-slot world OVERWROTE the slot here, silently dropping
            # a stale leftover; keep that hygiene by pruning ids whose hook
            # state is gone — a genuinely live turn always has its state and
            # survives, which is what several agents in one room will need.
            self.live.active_turn_ids[topic_id] = {
                work_id
                for work_id in self.live.active_turn_ids.get(topic_id, ())
                if (topic_id, work_id) in self.live.hook_work
            } | {turn_id}
        return state

    async def _announce_action(self, state: HookWorkState, resource: str) -> None:
        """Say in the room what 芝士 just did, the moment it did it — once per
        kind of action per turn, however many times the turn does it.

        Asked of the room rather than remembered, so a turn another backend
        picks up halfway does not announce twice, or forget what came before.
        """

        landed = landing(
            EventAbout.room, project_id=state.project_id, room_id=state.topic_id
        )
        async with self._sessions() as session:
            blocks = BlockRepository(session)
            if await blocks.has_action(state.topic_id, state.work_id, resource):
                return
            block = await blocks.add(
                project_id=landed.project_id,
                conversation_id=landed.conversation_id,
                author=state.acting_agent,
                author_type=AuthorType.platform,
                content=say(_ACTION_LABEL[resource], actor=f"<@{state.acting_agent}>"),
                kind=BlockKind.event,
                turn_id=state.work_id,
                meta={"platform": True, "action": resource},
            )
            await session.commit()
            payload = _block_payload(BlockOut.model_validate(block))
        await get_broker().publish(
            str(state.topic_id), {"type": "event_block", "block": payload}
        )

    async def _note_turn_context(
        self,
        turn_id: uuid.UUID,
        *,
        route: str,
        reply_to: uuid.UUID | None,
        agent_handle: str,
    ) -> None:
        """Best-effort, like the delivery stamp: losing it costs a turn picked
        up by another backend its reply link and the accuracy of one route
        label. It lands after the interval exists, so the row carries this
        turn's exact seat and route — what death evidence is matched against,
        never a room-level guess."""
        from app.domain.agent.repositories import AgentTurnRepository

        try:
            async with self._sessions() as session:
                await AgentTurnRepository(session).note_context(
                    turn_id, route=route, reply_to=reply_to, agent_handle=agent_handle
                )
                await session.commit()
        except Exception:  # noqa: BLE001 — bookkeeping must not stop a turn
            logger.exception("could not record the context of turn %s", turn_id)

    async def sweep_memory_dreams(self) -> dict:
        """巡检一圈：哪些项目该整理记忆了，逐个跑（`periodic_jobs` 里的一个）。

        这一簇连同它按房间记的那四份状态搬去了 `memory_ledger.py`——判据、串行
        和「派到哪个会话上」的文档都在那里。这里留一行委托：`core/background.py`
        的周期任务和测试都按这个入口叫它。
        """
        return await self._memory.sweep()

    async def run_memory_dream(self, *, project_id: uuid.UUID) -> dict:
        """跑一次记忆整理（dream）；不该跑就什么都不做（`memory_ledger.py`）。"""
        return await self._memory.run_dream(project_id=project_id)

    async def _consume_hook_event(
        self,
        project_id: uuid.UUID,
        topic_id: uuid.UUID,
        turn_id: uuid.UUID,
        event: AgentEvent,
        eid: str | None,
        result_text_seen: bool,
        platform_unsolicited: bool,
    ) -> None:
        """Persist and broadcast one event (hook_stream.py)."""
        return await _consume_hook_event(
            self,
            self._sessions,
            self.live,
            self._work_runner,
            project_id,
            topic_id,
            turn_id,
            event,
            eid,
            result_text_seen,
            platform_unsolicited,
        )

    async def _delivered_unread(
        self, session: AsyncSession, state: HookWorkState
    ) -> list[uuid.UUID]:
        """This agent's inputs that a delivered prompt carried and no Stop has
        stamped yet.

        A clean Stop means the session got through every prompt written into it,
        so these are read — whichever turn fed them. `state.pending_ids` cannot
        answer that alone: it lives in this process, and a backend replaced
        mid-turn hands the session's Stop to a process that never saw the
        prompt, so the batch went unstamped and every later turn re-sent it.
        Undelivered prompts stay out: the session never heard them.
        """
        from app.domain.agent.repositories import AgentTurnRepository

        handle = state.agent_instance_handle
        if handle is None:
            return []
        history = await BlockRepository(session).turn_history(state.topic_id)
        fed = {
            block.id: turn
            for block in _pending_input_blocks(history)
            if (turn := prompted_turn(block)) is not None
            and _addressed_to(block, handle)
        }
        delivered = await AgentTurnRepository(session).delivered(set(fed.values()))
        return [block_id for block_id, turn in fed.items() if turn in delivered]

    async def _forget_room_claims(self, topic_id: uuid.UUID) -> None:
        """A session failed and this process holds no turn for it — the
        backend was replaced before the session produced anything here. Which
        agent's batch it was is unknown, so every delivered claim in the room
        is withdrawn: the cost is a replay, never a lost message."""
        try:
            async with self._sessions() as session:
                blocks = BlockRepository(session)
                history = await blocks.turn_history(topic_id)
                await blocks.forget_prompted_turn(
                    [
                        block.id
                        for block in _pending_input_blocks(history)
                        if prompted_turn(block) is not None
                    ]
                )
                await session.commit()
        except Exception:  # noqa: BLE001 — the failure notice matters more
            logger.exception("could not withdraw delivered claims (topic=%s)", topic_id)

    async def _close_hook_work(
        self, state: HookWorkState, result: AgentResult
    ) -> list[dict]:
        """Commit accounting and prompt consumption after the session stops."""
        usage = reported_usage(state.route, result)
        # One row PER MODEL, never one lump: a gateway-routed turn's spend can
        # cover several models in one drain, and collapsing them under
        # `settings.agent_model` is exactly how mimo disappeared from `by_model`.
        usages: list[AgentUsage] = []
        if self._gateway is not None and state.route == "gateway":
            drained = await self.charge_turn_spend(
                state.project_id, state.topic_id, state.work_id
            )
            if drained:
                usages = drained
            else:
                # Both the turn-end drain and its settle retry saw nothing.
                # LiteLLM batch-writes spend logs, so "nothing yet" is not
                # "nothing" — land it in the background rather than hold the
                # turn open or write the spend off. This is the ONLY place the
                # numbers exist: an interactive session reports no usage of its
                # own, which is why `usage` is None here in the first place.
                self._schedule_deferred_drain(
                    state.project_id, state.topic_id, state.work_id
                )
        elif usage is not None:
            usages = [usage]

        action_frames: list[dict] = []
        async with self._sessions() as session:
            blocks = BlockRepository(session)
            # Exact native completion locks its input rows before touching blocks.
            # A synthetic error or an identity-less result cannot release a hold.
            if (
                not result.is_error
                and result.input_work_completed
                and result.session_id
                and result.harness
                and result.agent_handle == state.acting_agent
            ):
                await complete_work_inputs(
                    session,
                    project_id=state.project_id,
                    conversation_id=state.topic_id,
                    recipient_handle=result.agent_handle,
                    harness=result.harness,
                    native_session_id=result.session_id,
                    work_id=state.work_id,
                )
            await record_turn_usage(
                session,
                state,
                usages,
                gateway_charged=state.route == "gateway" and self._gateway is not None,
            )
            # What this session was fed, including by a process that is gone.
            # Settled either way: a failed session must also drop the batches
            # an earlier process fed it, or a later clean Stop would read them
            # as heard — and a self-started turn's own set is always empty.
            fed = list(
                state.pending_ids | set(await self._delivered_unread(session, state))
            )
            if result.is_error:
                await blocks.forget_prompted_turn(fed)
            elif result.harness is None:
                # Non-native providers do not register NativeInput batches.
                await blocks.mark_consumed(fed, state.work_id)
            waiting = (
                await own_limit.after_turn(session, state, result)
                if state.route == OWN_ROUTE
                else None
            )
            await session.commit()
        if waiting is not None:
            await self.post_system_event(
                state.topic_id,
                waiting,
                state.work_id,
                meta=notice(EVENT_TURN_FAILED, severity=SEVERITY_INFO, who=WHO_HUMAN),
            )

        # A task's changes are its branch's, shown on the task; the room's
        # change summary reads the room's checkout.
        _room, inner_id = await self._room_of_conversation(state.topic_id)
        changeset = (
            await self._turn_changeset(
                state.project_id,
                state.topic_id,
                None if state.known_commits is None else await state.known_commits,
            )
            if inner_id is None
            else None
        )
        if changeset is not None:
            payload = await self._persist_change_summary(
                project_id=state.project_id,
                topic_id=state.topic_id,
                turn_id=state.work_id,
                changeset=changeset,
            )
            if payload is not None:
                action_frames.append({"type": "event_block", "block": payload})
        return action_frames

    async def post_user_message(
        self,
        topic_id: uuid.UUID,
        *,
        author: str,
        content: str,
        turn_id: uuid.UUID | None,
        reply_to: str | None,
        attachments: list[dict] | None = None,
        client_id: str | None = None,
        quoted_context: dict | None = None,
    ) -> tuple[list[dict], uuid.UUID, list[uuid.UUID], bool]:
        """Persist a person's message (+ attachment blocks), its @mention notices
        and the questions it answers in one short transaction, outside any turn lock.
        Returns (payloads, anchor_block_id, all_block_ids, duplicate) — the
        anchor is what 芝士's reply threads under; all ids are consumed together
        after a mid-session delivery receipt. ``duplicate`` means the browser
        retried a delivery whose durable result is being echoed again.
        """
        async with self._sessions() as session:
            blocks = BlockRepository(session)
            # A room's own line, or a task's conversation in it.
            place = await PlaceResolver(session).conversation(topic_id)
            if place is None:
                raise NotFoundError("Topic not found")
            topic = place.room
            delivery_key = (
                action_key(place.conversation_id, "chat_message", author, client_id)
                if client_id
                else None
            )
            if delivery_key and not await idem.claim(
                session,
                delivery_key,
                action="chat_message",
                scope_id=str(place.conversation_id),
            ):
                stored = await idem.stored_result(session, delivery_key)
                if stored is None:
                    raise RuntimeError("committed chat delivery has no stored result")
                return (
                    list(stored["payloads"]),
                    uuid.UUID(stored["anchor_block_id"]),
                    [uuid.UUID(value) for value in stored["block_ids"]],
                    True,
                )
            if delivery_key:
                assert client_id is not None
                legacy_blocks = await blocks.client_delivery(
                    place.room_id, author=author, client_id=client_id
                )
                if legacy_blocks:
                    anchor = next(
                        block
                        for block in legacy_blocks
                        if (block.meta or {}).get("client_id") == client_id
                    )
                    payloads = [
                        _block_payload(BlockOut.model_validate(block))
                        for block in legacy_blocks
                    ]
                    block_ids = [block.id for block in legacy_blocks]
                    await idem.record_result(
                        session,
                        delivery_key,
                        {
                            "payloads": payloads,
                            "anchor_block_id": str(anchor.id),
                            "block_ids": [str(block_id) for block_id in block_ids],
                        },
                    )
                    await session.commit()
                    return payloads, anchor.id, block_ids, True
            created_blocks: list[Block] = []
            project = await ProjectRepository(session).get(topic.project_id)
            if project is None:
                raise NotFoundError("Project not found")
            await own_calls.seat_if_named(session, project, topic, content, author)
            agent = await self._agent_at(session, place)
            mentions = await person_mentions(
                session, topic, content, agent, dm=_is_dm(topic)
            )
            agent_handles, by_seat = mentions.agent_handles, mentions.by_seat
            # 私聊是两席的房间（结论 19）：说话就是对着对方说的，不需要 @。以前这
            # 一句是浏览器替服务端说的 —— DM 界面把帧上的 `summon` 置真发上来，
            # 于是「这条消息点了谁的名」有两个答案，其中一个在客户端手上。点名归
            # 服务端算（I13），所以这里自己认下私聊这一档。
            #
            # 判据是**对面那一席是不是 agent**，不是「这是不是私聊」：两个人的私聊
            # 也是私聊，而它没有 agent 可点名 —— 认成「点了名」就等于把芝士叫进两
            # 个人的私密对话里说话。对面是谁只有名册一个出处（`private_seats`，
            # 它答的 owner 那一席恒是人，所以只看 peer）；名册不是恰好两席时它答
            # None，这条消息就不点名，和这间房其余各处的退路同向。
            seats = (
                await TopicMemberService(session).private_seats(topic.id)
                if _is_dm(topic)
                else None
            )
            recipient = {
                "instance_id": str(agent.instance_id),
                "handle": agent.handle,
                # In a task the owner talks to its agent and nobody else, so
                # every message is addressed to it, as in a private chat with one.
                "mentioned": place.task is not None
                or (seats is not None and looks_like_agent_handle(seats[1])),
            }
            anchor_id: uuid.UUID | None = None
            answered: list[Block] = []
            attribution_id = turn_id
            # B3: a reply threads under a block IN THIS TOPIC. A client that
            # kept a stale reply target across a topic switch would otherwise
            # write a cross-topic edge into the conversation tree — invisible on
            # screen (the reader's timeline can't resolve the parent, so no
            # reply cue renders) and wrong in the data that 记忆/摘要 rebuild
            # from. Drop the edge, keep the message: losing the thread link is
            # recoverable, refusing the send is not.
            reply_uuid = _parse_uuid(reply_to)
            if reply_uuid is not None:
                parent = await blocks.get(reply_uuid)
                if parent is None or parent.conversation_id != place.conversation_id:
                    logger.warning(
                        "dropped cross-topic reply_to (topic=%s, reply_to=%s)",
                        topic_id,
                        reply_to,
                    )
                    reply_uuid = None
            if content:
                content, roster = mentions.content, mentions.roster
                # WHICH agent was addressed, not merely whether one was. The
                # flag alone left `handle`/`instance_id` naming whoever the room
                # pointed at, so @-ing the second teammate ran the first one's
                # turn. A room holds members; the one addressed answers, exactly
                # as for a person.
                addressed = next(
                    (h for h in agent_handles if f"<@{h}>" in content), None
                )
                if addressed is not None:
                    recipient["mentioned"] = True
                    named = by_seat.get(addressed)
                    if named is not None:
                        recipient["instance_id"] = str(named.id)
                        recipient["handle"] = named.handle
                    # A seat still under the room-derived handle names no
                    # instance, and that seat IS the agent the room points at,
                    # so the recipient resolved above is already the right one.
                refused = await own_calls.refused(session, project, recipient, author)
                user_block = await blocks.add(
                    project_id=topic.project_id,
                    conversation_id=place.conversation_id,
                    author=author,
                    author_type=AuthorType.participant,
                    content=content,
                    kind=BlockKind.message,
                    turn_id=turn_id,
                    reply_to=reply_uuid,  # B3: thread under another
                    # The sender's own id for this send, echoed straight back on
                    # the broadcast. A client that showed the message the instant
                    # it was typed (§14.1 实时) needs to recognise its own copy
                    # coming home; matching on text cannot do that, because this
                    # method rewrites the text on the way in.
                    meta={
                        "agent_recipient": recipient,
                        **({"client_id": client_id} if client_id else {}),
                        **(
                            {"quoted_context": quoted_context}
                            if quoted_context is not None
                            else {}
                        ),
                    },
                )
                if attribution_id is None:
                    attribution_id = user_block.id
                    user_block.turn_id = attribution_id
                await own_calls.say_refused(session, place, user_block, refused)
                await announce_mentions(session, topic, user_block, author, roster)
                # A reply to an agent's question goes to that agent (`recipient`).
                answered = await answer_questions(session, user_block, recipient)
                if len(agent_handles) > 1:
                    # `agent_recipient` 是单数：它起的是第一位点到的那一轮。同一条
                    # 消息点到的其余几位各记一条投递，和 agent 点名走同一本账。
                    from app.domain.delivery.mention import record_mentions
                    from app.domain.thread.services import answered_in

                    await record_mentions(
                        session,
                        project_id=topic.project_id,
                        room_id=place.room_id,
                        conversation_id=await answered_in(session, user_block),
                        block_id=user_block.id,
                        author=author,
                        content=content,
                        quoted_context=quoted_context,
                        by_agent=False,
                        occurred_at=datetime.now(UTC),
                        skip=frozenset({addressed} if addressed else ()),
                    )
                anchor_id = user_block.id
                created_blocks.append(user_block)
            # 图片输入: each image = an attachment block. content = the worktree
            # path (a REAL file, uploaded before this message), mime_type = how
            # to render it — structured fields, never parsed out of prose.
            for index, att in enumerate(attachments or []):
                att_block = await blocks.add(
                    project_id=topic.project_id,
                    conversation_id=place.conversation_id,
                    author=author,
                    author_type=AuthorType.participant,
                    content=str(att.get("path") or ""),
                    kind=BlockKind.attachment,
                    mime_type=str(att.get("mime") or "") or None,
                    turn_id=attribution_id,
                    # An image-only send still honors the reply thread (B3).
                    reply_to=None if content else reply_uuid,
                    meta={
                        "agent_recipient": recipient,
                        **(
                            {"client_id": client_id}
                            if not content and index == 0 and client_id
                            else {}
                        ),
                    },
                )
                if attribution_id is None:
                    attribution_id = att_block.id
                    att_block.turn_id = attribution_id
                if anchor_id is None:
                    anchor_id = att_block.id
                created_blocks.append(att_block)
            if anchor_id is None:  # guarded by the route, but never crash a turn
                raise NotFoundError("empty message")
            payloads = [
                _block_payload(BlockOut.model_validate(block))
                for block in created_blocks
            ]
            block_ids = [block.id for block in created_blocks]
            if delivery_key:
                await idem.record_result(
                    session,
                    delivery_key,
                    {
                        "payloads": payloads,
                        "anchor_block_id": str(anchor_id),
                        "block_ids": [str(block_id) for block_id in block_ids],
                    },
                )
            await session.commit()
        await publish_answered(place.conversation_id, answered)
        # A person's words are what a task gets named by (room_task/naming.py).
        if names_a_person(author) and place.task is not None:
            naming.nudge(place.task.id, "message")
        await self.thread_replied(place.conversation_id)
        return payloads, anchor_id, block_ids, False

    async def ack_summon(
        self, user_block_id: uuid.UUID, topic_id: uuid.UUID, by: str | None = None
    ) -> dict | None:
        """Add the 👀 receipt of the agent running the turn (``by``, else the
        room's seat) to the summoning user message (idempotent) and return the
        WS reaction payload. Best-effort: a failed receipt must never kill the
        turn."""
        try:
            async with self._sessions() as session:
                blocks = BlockRepository(session)
                await blocks.add_reaction_if_absent(
                    user_block_id,
                    "👀",
                    by or await self._agent_handle(session, topic_id),
                )
                reactions = await blocks.reactions_for_block(user_block_id)
                await session.commit()
            return {"block_id": str(user_block_id), "reactions": reactions}
        except Exception:  # noqa: BLE001 — the turn matters more than the ack
            logger.exception("failed to 👀-ack block %s", user_block_id)
            return None

    @staticmethod
    async def _session_agent(
        agents: AgentInstanceService,
        topic: Topic,
        project: Project,
        agent_handle: str | None,
    ) -> ResolvedAgent:
        return await _session_agent(agents, topic, project, agent_handle)

    async def _resolved_agent(
        self, session: AsyncSession, topic: Topic
    ) -> ResolvedAgent:
        return await _resolved_agent(session, topic)

    async def _agent_at(self, session: AsyncSession, place: Place) -> ResolvedAgent:
        return await _agent_at(session, place)

    async def _acting_handle(
        self, session: AsyncSession, topic_id: uuid.UUID, agent: ResolvedAgent
    ) -> str:
        return await _acting_handle(session, topic_id, agent)

    @staticmethod
    async def _private_owner(session: AsyncSession, topic: Topic) -> str | None:
        """私聊里那位人类，名册上 owner 那一席；不是私聊、或名册已经不是两席时 None。

        个人记忆按他记（`MemoryScope.user`），会话开场也按他开。出处只有名册一处：
        一间私聊就是两席的房间（结论 19），谁坐在里面由加席位、撤席位决定。

        答的只是「人是哪一位」。「这间房是不是私聊」是另一个问题，由 `_is_dm` 答
        （这个文件里 `is_private` 唯一的读点）：席位不齐的时候这里答 None，而那间
        房仍然是私聊，名册和地点都不因为席位不齐就变回房间的那一套。
        """
        if not _is_dm(topic):
            return None
        seats = await TopicMemberService(session).private_seats(topic.id)
        return seats[0] if seats is not None else None

    async def _agent_handle(self, session: AsyncSession, topic_id: uuid.UUID) -> str:
        return await _agent_handle(session, topic_id)

    async def _persist_tool_event(
        self,
        *,
        project_id: uuid.UUID,
        topic_id: uuid.UUID,
        name: str,
        tool_input: dict,
        platform: bool,
        turn_id: uuid.UUID | None,
        eid: str | None = None,
        platform_unsolicited: bool = False,
        inner_id: uuid.UUID | None = None,
        author: str | None = None,
        at: datetime | None = None,
    ) -> dict | None:
        return await _persist_tool_event(
            self._sessions,
            self.live,
            project_id=project_id,
            topic_id=topic_id,
            name=name,
            tool_input=tool_input,
            platform=platform,
            turn_id=turn_id,
            eid=eid,
            platform_unsolicited=platform_unsolicited,
            inner_id=inner_id,
            author=author,
            at=at,
        )

    async def _mark_step_failed(self, block_id: uuid.UUID, error: str) -> dict | None:
        return await _mark_step_failed(
            self._sessions,
            block_id,
            error,
        )

    async def _record_step_output(self, block_id: uuid.UUID, text: str) -> dict | None:
        return await _record_step_output(
            self._sessions,
            block_id,
            text,
        )

    async def _note_retry(
        self,
        topic_id: uuid.UUID,
        turn_id: uuid.UUID,
        event: AgentRetrying,
        *,
        author: str | None,
        inner_id: uuid.UUID | None,
        channel: str,
    ) -> None:
        """Say the turn is retrying a failed request (hook_stream.py)."""
        return await _note_retry(
            self._sessions,
            self.live,
            topic_id,
            turn_id,
            event,
            author=author,
            inner_id=inner_id,
            channel=channel,
        )

    async def _note_compaction(
        self, turn_id: uuid.UUID, event: AgentCompacting, *, channel: str
    ) -> None:
        """Restate the turn's compaction line as over (hook_stream.py)."""
        return await _note_compaction(
            self._sessions, self.live, turn_id, event, channel=channel
        )

    async def _note_reachability(
        self,
        project_id: uuid.UUID,
        topic_id: uuid.UUID,
        work_id: uuid.UUID,
        reachable: bool,
        reason: str,
    ) -> None:
        """Say the turn is waiting for its machine (hook_stream.py)."""
        return await _note_reachability(
            self._sessions,
            self.live,
            project_id,
            topic_id,
            work_id,
            reachable,
            reason,
        )

    async def _keep_note(
        self,
        notes: dict[uuid.UUID, uuid.UUID],
        topic_id: uuid.UUID,
        turn_id: uuid.UUID,
        content: str,
        meta: dict,
        *,
        author: str | None,
        inner_id: uuid.UUID | None,
        channel: str,
    ) -> None:
        """Land the turn's notice of this kind, or restate it (hook_stream.py)."""
        return await _keep_note(
            self._sessions,
            notes,
            topic_id,
            turn_id,
            content,
            meta,
            author=author,
            inner_id=inner_id,
            channel=channel,
        )

    async def _restate_note(
        self, block_id: uuid.UUID, content: str, meta: dict, channel: str
    ) -> None:
        """Restate a notice already on the timeline (hook_stream.py)."""
        return await _restate_note(self._sessions, block_id, content, meta, channel)

    async def _persist_room_event(
        self,
        *,
        project_id: uuid.UUID,
        topic_id: uuid.UUID,
        content: str,
        meta: dict,
        turn_id: uuid.UUID | None,
        eid: str | None = None,
        platform_unsolicited: bool = False,
        in_room: bool = False,
        author_type: AuthorType = AuthorType.participant,
        inner_id: uuid.UUID | None = None,
        author: str | None = None,
        at: datetime | None = None,
    ) -> dict | None:
        return await _persist_room_event(
            self._sessions,
            self.live,
            project_id=project_id,
            topic_id=topic_id,
            content=content,
            meta=meta,
            turn_id=turn_id,
            eid=eid,
            platform_unsolicited=platform_unsolicited,
            in_room=in_room,
            author_type=author_type,
            inner_id=inner_id,
            author=author,
            at=at,
        )

    async def _persist_subagent_result(
        self,
        *,
        project_id: uuid.UUID,
        topic_id: uuid.UUID,
        event: AgentToolResult,
        turn_id: uuid.UUID | None,
        eid: str | None = None,
        platform_unsolicited: bool = False,
        inner_id: uuid.UUID | None = None,
    ) -> dict | None:
        return await _persist_subagent_result(
            self._sessions,
            self.live,
            project_id=project_id,
            topic_id=topic_id,
            event=event,
            turn_id=turn_id,
            eid=eid,
            platform_unsolicited=platform_unsolicited,
            inner_id=inner_id,
        )

    async def _turn_changeset(
        self,
        project_id: uuid.UUID,
        topic_id: uuid.UUID,
        known_commits: set[str] | None,
    ) -> _Changeset | None:
        return await _turn_changeset(
            self._sessions,
            project_id,
            topic_id,
            known_commits,
        )

    async def _known_commits(
        self, project_id: uuid.UUID, topic_id: uuid.UUID
    ) -> set[str] | None:
        return await _known_commits(
            self._sessions,
            project_id,
            topic_id,
        )

    async def _persist_change_summary(
        self,
        *,
        project_id: uuid.UUID,
        topic_id: uuid.UUID,
        turn_id: uuid.UUID | None,
        changeset: _Changeset,
    ) -> dict | None:
        return await _persist_change_summary(
            self._sessions,
            self.live,
            project_id=project_id,
            topic_id=topic_id,
            turn_id=turn_id,
            changeset=changeset,
        )

    async def _pass_policy_gate(
        self,
        session: AsyncSession,
        topic_id: uuid.UUID | None,
        call: gate.Call,
        policy: gate.Policy,
        *,
        actor: str,
    ) -> _Proposed | None:
        return await _pass_policy_gate(session, topic_id, call, policy, actor=actor)

    # --- 网关：这一轮的模型、模型环境与项目的网关用量 ----------------------
    # 这一段搬去了 `gateway_usage.py`（那里有它们各自的文档）。这里留一行同名委托，
    # 调用点一格没动；名字仍然从 `app.domain.agent.chat` 导得出来。

    async def _model_kwargs(
        self,
        project_id: uuid.UUID,
        provider: RoomSessions | None,
        topic_id: uuid.UUID | None = None,
        *,
        agent: ResolvedAgent | None = None,
        acting_agent: str | None = None,
        platform: bool = False,
    ) -> tuple[dict, str]:
        """Resolve a turn's model, model environment and usage route
        (gateway_usage.py)."""
        return await _model_kwargs(
            self,
            self._sessions,
            self._gateway,
            self._profiles,
            self._gateway_lock,
            project_id,
            provider,
            topic_id,
            agent=agent,
            acting_agent=acting_agent,
            platform=platform,
        )

    async def project_gateway_key(self, project_id: uuid.UUID) -> str | None:
        """The project's virtual gateway key, minted on first use
        (gateway_usage.py)."""
        return await project_gateway_key(
            self, self._sessions, self._gateway, self._gateway_lock, project_id
        )

    async def _gateway_budget_target(
        self, session: AsyncSession, project_id: uuid.UUID
    ) -> float | None:
        return await _gateway_budget_target(session, project_id)

    async def _gateway_project_env(self, project_id: uuid.UUID) -> dict | None:
        """Env override for a gateway-routed turn (gateway_usage.py)."""
        return await _gateway_project_env(
            self, self._sessions, self._gateway, self._gateway_lock, project_id
        )

    def _schedule_deferred_drain(
        self, project_id: uuid.UUID, topic_id: uuid.UUID, turn_id: uuid.UUID
    ) -> None:
        """Late-landing spend rows: drain again in the background
        (gateway_usage.py)."""
        return _schedule_deferred_drain(
            self._sessions,
            self._gateway,
            self._gateway_lock,
            self._background_tasks,
            project_id,
            topic_id,
            turn_id,
        )

    async def charge_turn_spend(
        self, project_id: uuid.UUID, topic_id: uuid.UUID | None, turn_id: uuid.UUID
    ) -> list[AgentUsage] | None:
        """What this turn spent on the project's key, charged, one entry per
        model (gateway_usage.py)."""
        return await charge_turn_spend(
            self._sessions,
            self._gateway,
            self._gateway_lock,
            project_id,
            topic_id,
            turn_id,
        )

    async def _bail_notice(
        self,
        *,
        project_id: uuid.UUID,
        topic_id: uuid.UUID,
        turn_id: uuid.UUID,
        session: AsyncSession,
        text: str,
    ) -> dict:
        return await _bail_notice(
            project_id=project_id,
            topic_id=topic_id,
            turn_id=turn_id,
            session=session,
            text=text,
        )


@dataclass(frozen=True, slots=True)
class SentText:
    """A message's text as sending it would have stored it, and how it was sent."""

    room: Topic
    text: str
    # The names its mentions were read against; None where that way of sending
    # announces no mentions at all (a card's conversation).
    roster: list[dict] | None
    by_agent: bool


async def text_as_sent(
    session: AsyncSession, block: Block, author: str, content: str
) -> SentText:
    """What sending ``content`` as ``author`` where ``block`` is would have
    stored. An edit stores exactly that, by calling the same code.

    On a card it is `say_on_task`'s rewrite, for anyone. In the room the author
    is an agent when it holds one of the room's agent seats, the question the
    publication routes ask; its text goes through both halves of the
    publication rewrite, with the arguments a publication passes (no roster, no
    topic list). A person's goes through `person_mentions`, as
    `post_user_message` does."""
    place = await PlaceResolver(session).conversation(block.conversation_id)
    if place is None:
        raise NotFoundError("Topic not found")
    topic = place.room
    by_agent = await TopicMemberService(session).holds_an_agent_seat(topic, author)
    if place.task is not None:
        text = await project_refs_text(session, topic.project_id, topic.id, content)
        return SentText(topic, text, None, by_agent)
    if by_agent:
        text = await project_refs_text(session, topic.project_id, topic.id, content)
        roster = await room_roster(session, topic.project_id, topic)
        return SentText(topic, _expand_mention_names(text, roster, []), roster, True)
    project = await ProjectRepository(session).get(topic.project_id)
    if project is None:
        raise NotFoundError("Project not found")
    agent = await AgentInstanceService(session).for_topic(topic, project)
    mentions = await person_mentions(session, topic, content, agent, dm=_is_dm(topic))
    return SentText(topic, mentions.content, mentions.roster, False)
