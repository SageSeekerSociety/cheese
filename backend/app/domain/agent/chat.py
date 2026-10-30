"""Chat orchestration — ties topic, blocks, memory, and the agent together.

This is the platform "shell" around 芝士: it persists the conversation as
blocks (append-only history, spec H1), injects project memory into the agent's
context (spec §8.4 带记忆回答), retains execution notes in activity blocks,
publishes deliberately sent chat messages, and stores the
resumable session id on the topic.

DB writes happen in short transactions around the (long) streaming call so we
never hold a transaction open across the model round-trip.

Turn intake, transport steps, durable writes and live state have separate owners.
"""

import asyncio
import logging
import uuid
from functools import partial

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import BaseError, ValidationError
from app.domain.agent import own_limit, runner_reports, turn_inputs

# 兼容门面：`ChatService` 的四块职责分在同目录的四个 mixin 里，类继承顺序即下面
# 这一列。方法体里的 `self.` 一格没动，所以子类覆盖、类属性读取和测试的
# monkeypatch 打点（`ChatService.has_running_turn`、`chat.post_system_event`……）
# 全部照旧；`chat.py` 只剩组合根与跨块的那几样。
from app.domain.agent.chat_admission import TurnAdmission
from app.domain.agent.chat_lifecycle import TurnLifecycle
from app.domain.agent.chat_live_input import LiveInput
from app.domain.agent.chat_places import ConversationPlaces
from app.domain.agent.compute import ComputePool

# 兼容门面：现场事件行的渲染搬去了 `event_lines.py`（那里有直接的单测）。
# 这里重新导出，`app.domain.agent.chat` 仍是既有调用点与测试的导入路径；下面
# 带 noqa 的几个本文件不用 —— 它们是被搬走的落库那几步的零件，测试仍然从
# chat.py 导它们。
from app.domain.agent.event_lines import (
    _CHANGE_FILES_LISTED,  # noqa: F401 — 搬走的常量，这里仍然导得出来
    _change_summary_meta,  # noqa: F401 — 测试仍从 chat.py 导它
    _Changeset,  # noqa: F401 — 测试仍从 chat.py 导它
    _diff_file_stats,  # noqa: F401 — 测试仍从 chat.py 导它
    _format_change_summary,  # noqa: F401 — 测试仍从 chat.py 导它
)
from app.domain.agent.gateway import LlmGateway

# 兼容门面：这一轮的模型与它的用量账（准入前解析模型/环境/网关 key，轮次结束后把
# 网关的用量落库）搬去了 `gateway_usage.py`（那里有它们各自的文档）。这里重新导出，
# `app.domain.agent.chat` 仍是既有调用点与测试的导入路径；`ChatService` 上留一行
# 同名委托，调用点一格没动。三个设置键的名字（`_GW_KEY` 等）只被这一段读，跟着
# 搬走，不再是 `ChatService` 的属性。
from app.domain.agent.gateway_usage import (
    _gateway_project_env,
    _model_kwargs,
    _schedule_deferred_drain,
    charge_turn_spend,
    project_gateway_key,
)
from app.domain.agent.harness import SessionRef
from app.domain.agent.input_registration import confirm_receipt

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
# 的那几个本文件不用，只是给外部（测试）留的导入路径。
from app.domain.agent.mentions import (
    _MENTION_RE,  # noqa: F401
    _SPECIAL_MENTIONS,  # noqa: F401
    _TOPIC_REF_RE,  # noqa: F401
    MENTION_ALL,  # noqa: F401 — 搬走的常量，这里仍然导得出来
    MENTION_HERE,  # noqa: F401
    PersonMentions,  # noqa: F401
    _expand_mention_names,  # noqa: F401 — 现有测试仍使用此导出
    _resolve_mentions,  # noqa: F401
    _topic_refs,  # noqa: F401
    project_refs_text,  # noqa: F401 — 现有发送路由仍使用此导出
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
    _compaction_notice,  # noqa: F401 — 测试仍从 chat.py 导它
    _progress_lines,  # noqa: F401
    _prompt_topic_refs,  # noqa: F401
)

# Canonical read/policy implementations; gateway consumers retain their narrow calls.
from app.domain.agent.queries import (
    _agent_handle,
    _gateway_budget_target,
    _pass_policy_gate,
    _Proposed,
)
from app.domain.agent.reads import Report
from app.domain.agent.recovery import SessionRecovery
from app.domain.agent.room import reads as room_reads
from app.domain.agent.room.sessions import RoomSessions

# Compose the actual changeset/event owners with this root's session capability.
from app.domain.agent.room_events import (
    _known_commits,
    _turn_changeset,
)
from app.domain.agent.service import AgentUsage
from app.domain.agent.session_turn_events import SessionTurnEvents
from app.domain.agent.skills import NATIVE_CHAT_GUIDANCE
from app.domain.agent.turn.intake.assistant import AssistantMessages
from app.domain.agent.turn.intake.completion import TurnCompletion
from app.domain.agent.turn.intake.events import _persist_change_summary
from app.domain.agent.turn.intake.hooks import HookEvents
from app.domain.agent.turn.intake.human import HumanMessages
from app.domain.agent.turn.intake.native_turns import NativeTurns
from app.domain.agent.turn.intake.preparation import TurnPreparation
from app.domain.agent.turn.intake.rooms import room_roster

# 这一进程正在跑的活（按房间/按轮次的进程内状态，``hook_work`` / 座位锁 /
# 几张 note 表……）收在 `turn/state/live.py` 那片叶子里，`ChatService.live` 是它唯一
# 持有者。状态与处理器之间只有「处理器读状态」一个方向。
from app.domain.agent.turn.state.live import LiveWork
from app.domain.agent.turn.steps.send import SendEffects
from app.domain.agent.turn.store.completion import CompletionEffects
from app.domain.agent.turn.store.session_inputs import forget_opening
from app.domain.agent.turn_usage import record_turn_usage
from app.domain.agent_instance.services import ResolvedAgent
from app.domain.delivery.input_identity import InputReceipt
from app.domain.policy import gate

#: How many rooms replay their sessions' backlog at once. Each replay writes
#: what it lands to the database, which the turns running meanwhile share.
REPLAYS_AT_ONCE = 4

logger = logging.getLogger(__name__)


class ChatService(
    TurnAdmission, LiveInput, ConversationPlaces, TurnLifecycle, SessionRecovery
):
    """会话编排的组合根。

    自己只留初始化与协作者装配（`live`、`_memory`、`prewarm`、几张
    `HookEvents`/`TurnCompletion` 的工厂）以及跨块共用、被别处按名字叫的那几样
    （`_agent_handle`、`_pass_policy_gate`、网关那一段的一行委托）。四块职责在
    `chat_admission` / `chat_live_input` / `chat_places` / `chat_lifecycle` 里，
    它们的方法仍然依赖本对象的 `self`，不是可以单独使用的服务。
    """

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
        self._turn_preparation: TurnPreparation | None = None
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
        # 写穿回调把「在跑」落到权威的那行 `AgentTurn`：leaf 不认识库，这一层认识。
        self.live = LiveWork(
            write_open=self._confirm_turn_row_open,
            confirm_closed=self._confirm_turn_row_closed,
        )
        # Per-project ExecutionProfile (model + provider). None → always the
        # agent's built-in default (tests / single-profile deploys).
        self._profiles = profiles
        # LiteLLM gateway ADMIN client (L1/L2 — defined in
        # `app.domain.agent.gateway`). None = off.
        self._gateway = gateway
        # 记忆的对账（铺下去 / 收回来）走的是会话那条通道，所以回调挂在这里，
        # 由 harness 在两个时刻问它：输入之前、会话停下来之后（runner 报会话空闲）。这一簇连同它按
        # 房间记的四份状态都在 `memory_ledger.py` 里；它会用到的协作者显式
        # 传进去（`gateway` 要先建好），留在 `ChatService` 上的
        # 只有 `_lock_for` 和 `_model_kwargs` 两件同事还在用的东西。
        self._memory: MemoryLedger = MemoryLedger(
            sessions=session_factory,
            compute=compute,
            gateway=gateway,
            host=self,
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
    def hook_events(self) -> HookEvents:
        return HookEvents(
            sessions=self._sessions,
            live=self.live,
            work_runner=self._work_runner,
            messages=self.messages,
            completion=self.turn_completion,
            begin_turn=self.native_turns.begin,
            save_session=self._save_session_pointer,
            resolve_room=self._room_of_conversation,
            close_interval=self._close_turn_interval,
            activity=self._set_hook_activity,
            thread_replied=self.thread_replied,
            policy=self.work_policy,
        )

    @property
    def turn_preparation(self) -> TurnPreparation:
        if self._turn_preparation is None:
            self._turn_preparation = TurnPreparation(
                self._sessions,
                compute=self._compute,
                memory=self._memory,
                base_prompt=self._base_prompt,
                skills=self._skills,
                model_options=self._model_kwargs,
                refuse=self._refuse_turn,
                live=self.live,
                send_effects=SendEffects(
                    post_system_event=self.post_system_event,
                    consume_hook_event=self.hook_events.accept,
                    known_commits=partial(_known_commits, self._sessions),
                    register_input=self._input_registrar,
                    open_interval=turn_inputs.open_interval_with_input,
                    retire_failed=turn_inputs.retire_failed,
                    stamp_delivery=turn_inputs.stamp_delivered,
                ),
            )
        return self._turn_preparation

    @staticmethod
    def _refuse_turn(message: str) -> BaseError:
        return ValidationError(message)

    @property
    def messages(self) -> AssistantMessages:
        # Composition only: this stateless writer uses the service's real state
        # and transactions; it owns no copied live work or compatibility entry.
        return AssistantMessages(
            self._sessions, self.live, room_roster, self.thread_replied
        )

    @property
    def turn_completion(self) -> TurnCompletion:
        # Composition only: actual stop SQL/commit belongs to the store; gateway
        # scheduling and session/compute affinity remain owned by this root.
        return TurnCompletion(
            self._sessions,
            gateway_enabled=self._gateway is not None,
            charge_spend=self.charge_turn_spend,
            defer_drain=self._schedule_deferred_drain,
            effects=CompletionEffects(
                record_usage=record_turn_usage,
                own_waiting=own_limit.after_turn,
            ),
            post_event=self.post_system_event,
            room_of_conversation=self._room_of_conversation,
            turn_changeset=partial(_turn_changeset, self._sessions),
            persist_summary=partial(_persist_change_summary, self._sessions, self.live),
        )

    @property
    def human_messages(self) -> HumanMessages:
        return HumanMessages(self._sessions, self.thread_replied)

    @property
    def session_factory(self) -> async_sessionmaker:
        return self._sessions

    @property
    def work_runner(self) -> SessionTurnEvents:
        """The runner the composition root gave this service.

        For callers that already hold the service: review's polling clocks
        address agents through it, and they used to import ``app.api.deps`` for
        the runner — a domain reaching up into the API layer
        (``app/.importlinter``, C1). Handing the same assembled collaborator
        back keeps one runner per process, whichever way it is reached.
        """
        return self._work_runner

    @property
    def compute(self) -> ComputePool:
        """The sessions this process holds, and which of them are at work
        (`turn.steps.reminders.QuietSessionReminders`)."""
        return self._compute

    async def confirm_prompt_receipt(self, receipt: InputReceipt) -> None:
        """Commit identity-bound effects before the journal may acknowledge.

        No in-memory candidate is needed. Commit failure propagates so the same
        journal input is retried, even by a newly reconstructed ChatService.
        """

        await confirm_receipt(self._sessions, self.live, receipt)

    async def apply_report(self, report: Report) -> runner_reports.Applied | bool:
        """Apply what a seat's runner reported, once (`runner_reports`): False
        when it was applied and followed through before."""
        return await runner_reports.apply(self, report)

    async def inputs_replaced(
        self,
        conversation_id: uuid.UUID,
        agent_handle: str,
        harness: str,
        native: str,
        *,
        read: tuple[tuple[str, tuple[str, ...]], ...] = (),
    ) -> None:
        """The process holding native session ``native`` was replaced
        (`runner_reports.replaced`)."""
        await runner_reports.replaced(
            self,
            conversation_id,
            agent_handle,
            harness,
            native,
            read=read,
        )

    async def context_compacted(self, session: SessionRef) -> None:
        """``session`` compacted its context (`turn.store.forget_opening`)."""
        await forget_opening(
            self._sessions,
            conversation_id=session.conversation_id,
            agent_handle=session.agent_handle,
            harness=session.harness,
        )

    async def result_landed(self, report: Report) -> None:
        """The room landed the result ``report`` carries (`runner_reports.landed`)."""
        await runner_reports.landed(self, report)

    async def report_followed(self, report: Report) -> None:
        """Everything that follows from ``report`` was done
        (`runner_reports.projected`)."""
        await runner_reports.projected(self, report)

    async def confirm_work_abandoned(self, work_id: uuid.UUID) -> None:
        """End the inputs of a work whose session died inside it. See turn_inputs."""
        await turn_inputs.end_inputs_of_abandoned_work(self._sessions, work_id)

    @property
    def native_turns(self) -> NativeTurns:
        return NativeTurns(
            self._sessions,
            self.live,
            self._open_session_turn,
            partial(_known_commits, self._sessions),
        )

    async def _open_session_turn(
        self,
        topic_id: uuid.UUID,
        turn_id: uuid.UUID,
        *,
        author: str,
        agent_handle: str,
        session_id: str | None,
    ) -> None:
        await self._work_runner.open_turn_the_session_started(
            self,
            topic_id,
            turn_id,
            author=author,
            agent_handle=agent_handle,
            session_id=session_id,
        )

    async def sweep_memory_dreams(self) -> dict:
        """巡检一圈：哪些项目该整理记忆了，逐个跑（`periodic_jobs` 里的一个）。

        这一簇连同它按房间记的那四份状态搬去了 `memory_ledger.py`——判据、串行
        和「派到哪个会话上」的文档都在那里。这里留一行委托：`app/domain/agent/periodic.py`
        的周期任务和测试都按这个入口叫它。
        """
        return await self._memory.sweep()

    async def run_memory_dream(self, *, project_id: uuid.UUID) -> dict:
        """跑一次记忆整理（dream）；不该跑就什么都不做（`memory_ledger.py`）。"""
        return await self._memory.run_dream(project_id=project_id)

    async def _agent_handle(self, session: AsyncSession, topic_id: uuid.UUID) -> str:
        return await _agent_handle(session, topic_id)

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
    ) -> tuple[dict, str]:
        """Resolve a turn's model, model environment and usage route
        (gateway_usage.py)."""
        return await _model_kwargs(
            self,
            self._sessions,
            self._gateway,
            self._profiles,
            project_id,
            provider,
            topic_id,
            agent=agent,
            acting_agent=acting_agent,
        )

    async def project_gateway_key(
        self,
        project_id: uuid.UUID,
        *,
        dream: bool = False,
        task_id: uuid.UUID | None = None,
        credit_owner_handle: str | None = None,
        machine_conversation_id: uuid.UUID | None = None,
    ) -> str | None:
        """The project's virtual gateway key, minted on first use; with
        ``dream``, the key its memory consolidation runs on, and with
        ``machine_conversation_id`` the one that conversation's sandboxes
        call on (gateway_usage.py)."""
        return await project_gateway_key(
            self,
            self._sessions,
            self._gateway,
            project_id,
            dream=dream,
            task_id=task_id,
            credit_owner_handle=credit_owner_handle,
            machine_conversation_id=machine_conversation_id,
        )

    async def _gateway_budget_target(
        self, session: AsyncSession, project_id: uuid.UUID
    ) -> float | None:
        return await _gateway_budget_target(session, project_id)

    async def _gateway_project_env(self, project_id: uuid.UUID) -> dict | None:
        """Env override for a gateway-routed turn (gateway_usage.py)."""
        return await _gateway_project_env(
            self, self._sessions, self._gateway, project_id
        )

    def _schedule_deferred_drain(
        self, project_id: uuid.UUID, topic_id: uuid.UUID, turn_id: uuid.UUID
    ) -> None:
        """Late-landing spend rows: drain again in the background
        (gateway_usage.py)."""
        return _schedule_deferred_drain(
            self._sessions,
            self._gateway,
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
            project_id,
            topic_id,
            turn_id,
        )
