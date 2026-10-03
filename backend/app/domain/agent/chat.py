"""Chat orchestration — ties topic, blocks, memory, and the agent together.

This is the platform "shell" around 芝士: it persists the conversation as
blocks (append-only history, spec H1), injects project memory into the agent's
context (spec §8.4 带记忆回答), retains execution notes in activity blocks,
publishes deliberately sent chat messages, and stores the
resumable session id on the topic.

DB writes happen in short transactions around the (long) streaming call so we
never hold a transaction open across the model round-trip.
"""

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.errors import NotFoundError, ValidationError
from app.core.sentences import exception_text, say
from app.domain.agent import death_evidence, turn_inputs
from app.domain.agent.announce import announce, settle_questions_answered_by
from app.domain.agent.compute import ComputePool, ComputeProvider
from app.domain.agent.device_hub import DeviceCallError, DeviceOffline

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
    _drain_gateway_usage,
    _gateway_project_env,
    _model_kwargs,
    _model_policy_call,
    _schedule_deferred_drain,
    project_gateway_key,
)
from app.domain.agent.harness import (
    Opening,
    SessionRef,
    harness_for,
    runtime_for,
)
from app.domain.agent.harness.prompt import (
    build_system_prompt,
    live_input_lines,
    platform_prompt,
    prompt_line,
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
    _HookWorkState,
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
    cloud_waiting_topics,
    person_mentions,
    project_refs_text,
)
from app.domain.agent.platform_failures import (
    MODEL_LIMIT_REACHED_CODE,
    PROVIDER_OVERLOADED_CODE,
    PROVIDER_UNREACHABLE_CODE,
    RESPONSE_TRUNCATED_CODE,
    TOOL_UNAVAILABLE_CODE,
    classify_cli_notice,
)
from app.domain.agent.platform_notices import (
    EVENT_MCP_NOT_CONNECTED,
    EVENT_PROMPT_REPLAYED,
    EVENT_TURN_FAILED,
    SEVERITY_ERROR,
    SEVERITY_INFO,
    SEVERITY_WARN,
    WHO_HUMAN,
    WHO_PLATFORM,
    delivery_fallback_notice,
    notice,
)
from app.domain.agent.profiles import ProfileRegistry

# 兼容门面：提示词/上下文渲染搬去了 `prompt.py`（那里有直接的单测）。这里重新
# 导出，`app.domain.agent.chat` 仍是既有调用点与测试的导入路径；下面带 noqa 的
# 几个本文件不用，只是给外部（测试）留的导入路径。
from app.domain.agent.prompt import (
    _PROGRESS_MARK,  # noqa: F401 — 搬走的常量，这里仍然导得出来
    _REPLAY_NOTICE_AT,  # noqa: F401
    _REPLAY_NOTICE_EVERY,  # noqa: F401
    PLACEHOLDER_TITLE,  # noqa: F401
    _addressed_to,
    _compaction_notice,  # noqa: F401 — 测试仍从 chat.py 导它
    _is_pending_input,  # noqa: F401
    _pending_input_blocks,
    _pending_platform_notices,
    _platform_preamble,
    _progress_lines,  # noqa: F401
    _prompt_topic_refs,  # noqa: F401
    _replay_notice,
    _resume_notice,
    _sandbox_limits,
    _session_opening_lines,
    _topic_ref_lists,
    offered_attachments,
    project_overview,
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
    require_pinned_seat,
)
from app.domain.agent.recovery import SessionRecovery

# 兼容门面：这一轮往房间里落下的那些行（事件块、步骤的判决、变更汇总）搬去了
# `room_events.py`（那里有它们各自的文档）。这里重新导出，`app.domain.agent.chat`
# 仍是既有调用点与测试的导入路径；`ChatService` 上留一行同名委托，调用点一格没动。
# 带 noqa 的常量本文件不用，只是给外部留的导入路径。
from app.domain.agent.room_events import (
    _CHANGE_COMMIT_WALK,  # noqa: F401 — 搬走的常量，这里仍然导得出来
    _known_commits,
    _mark_step_failed,
    _note_worker,
    _persist_change_summary,
    _persist_room_event,
    _persist_subagent_result,
    _persist_tool_event,
    _persist_worker_event,
    _record_conclusion,
    _record_step_output,
    _turn_changeset,
    post_system_event,
)
from app.domain.agent.service import (
    AgentCompacting,
    AgentEvent,
    AgentResult,
    AgentRetrying,
    AgentSubagentStart,
    AgentSubagentStop,
    AgentToolResult,
    AgentUsage,
)
from app.domain.agent.skills import NATIVE_CHAT_GUIDANCE, load_scenario, load_skills
from app.domain.agent.stages import TopicStage, resolve_stage, stage_scenario
from app.domain.agent.turn_speakers import turn_speakers
from app.domain.agent.work_policy import resolve_compute_id, work_policy
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
from app.domain.delivery.ask_wake import expected_ask_session
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
    held_blocks,
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
from app.domain.membership.roster import roster_rows
from app.domain.memory.files_store import MemoryIndex, memory_index
from app.domain.memory.models import MemoryScope
from app.domain.policy import gate
from app.domain.project import artifacts as project_artifacts
from app.domain.project.models import Project
from app.domain.project.repositories import ProjectRepository
from app.domain.review.models import AcceptStatus
from app.domain.review.repositories import AcceptCardRepository
from app.domain.room_task.place import Place, PlaceResolver
from app.domain.task import teaching as teaching_context
from app.domain.task.teaching import TeachingContext
from app.domain.topic import doc_nudge, naming
from app.domain.topic.models import TitleSource, Topic, TopicStatus
from app.domain.topic.repositories import TopicProgressRepository, TopicRepository
from app.domain.topic_membership.services import TopicMemberService
from app.domain.usage.credits import spend_to_credits
from app.domain.usage.ledger import Ledger, payer_for_project, team_terms

PRIVATE_SKILLS = ["private-chat"]

CHEESE_AUTHOR = "cheese"

#: How many rooms replay their sessions' backlog at once. Each replay writes
#: what it lands to the database, which the turns running meanwhile share.
REPLAYS_AT_ONCE = 4

logger = logging.getLogger(__name__)


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
    acting_agent: str
    agent: ResolvedAgent
    agent_pool: tuple[MemoryScope, str] | None
    role: str | None
    roster: list[dict]
    private_owner: str | None
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
    topic_stage: TopicStage
    topic_refs: list[dict]
    topic_refs_for_prompt: list[dict]
    # 这个项目交出去过的东西 —— 下一次交付要从这几个名字里挑一个。空着是「还没交出
    # 去过东西」，None 是「这间房间不交付」（私聊）。
    artifacts: list[dict] | None

    # Which machine, and whether it reports its own liveness (which decides who
    # owns this turn's clock; see the `turn_ceiling` frame).
    provider: ComputeProvider
    # 这一轮跑在哪个骨架上，解析过一次的那个答案（结论 28）。会话行的键里有它，
    # 所以执行那一半必须读这里，不能自己再解析一次。
    harness: str
    # 这一轮要不要一双手 (结论 19，不变量 I2)。解析的产物，不是房间的属性：同一
    # 条会话可以这一轮只聊天、下一轮动文件，而租手发生在解析之后。
    needs_place: bool


@dataclass(frozen=True, slots=True)
class _TurnBail:
    """The turn ended while it was still being assembled, and these are the
    frames that say so. Not an error: nobody was waiting on an answer, or the
    machine is still being built."""

    frames: list[dict]


#: How many sessions' supply routes to remember. Well past the number of screens
#: one backend drives at once, so in practice nothing is ever evicted; it is a
#: ceiling on a dict nothing else prunes, not a policy.
_SESSION_ROUTES_KEPT = 512


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
    "topics": "actionTopics",
    "notify": "actionNotify",
}


# HTTP statuses worth an automatic re-run: timeouts, throttling, server-side
# blips. Anything else (or a rejected seat rate-limit) surfaces immediately.
_TRANSIENT_HTTP = {408, 429, 500, 502, 503, 504, 529}


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


# CLI 自己印在对话里的那几句英文,换成平台自己的中文提示卡。
#
# 它们过去顶着芝士的名字发出来,读的人看到的是「芝士在说英文报错」,而实际上
# 芝士根本没说话 —— 是它脚下的 CLI 印的。归属错了比语言错了更糟:一个平台故障
# 被读成 AI 的回答,谁也不知道该找谁。
#
# 英文原话一个字都不丢,收进「服务原话」的折叠区 —— 它是唯一的一份。
#
# 每一条是 (那一行的键, severity, who, 说明的键)，句子在 roomNotice 词表。
_CLI_NOTICE_COPY: dict[str, tuple[str, str, str, str]] = {
    PROVIDER_UNREACHABLE_CODE: (
        "cliProviderUnreachable",
        SEVERITY_ERROR,
        WHO_PLATFORM,
        "cliProviderUnreachableHint",
    ),
    PROVIDER_OVERLOADED_CODE: (
        "cliProviderOverloaded",
        SEVERITY_WARN,
        WHO_PLATFORM,
        "cliProviderOverloadedHint",
    ),
    MODEL_LIMIT_REACHED_CODE: (
        "cliModelLimitReached",
        SEVERITY_ERROR,
        WHO_HUMAN,
        "cliModelLimitReachedHint",
    ),
    TOOL_UNAVAILABLE_CODE: (
        "cliToolUnavailable",
        SEVERITY_WARN,
        WHO_PLATFORM,
        "cliToolUnavailableHint",
    ),
    RESPONSE_TRUNCATED_CODE: (
        "cliResponseTruncated",
        SEVERITY_WARN,
        WHO_PLATFORM,
        "cliResponseTruncatedHint",
    ),
}


#: 等一等、再来一次就可能好的那几种。额度用完、工具配置错了，重试不会有变化。
_CLI_RETRYABLE = frozenset(
    {PROVIDER_UNREACHABLE_CODE, PROVIDER_OVERLOADED_CODE, RESPONSE_TRUNCATED_CODE}
)


def _cli_notice(text: str) -> tuple[str, dict] | None:
    """整条消息其实是 CLI 印的一句英文提示时,给出该发的中文提示卡;否则 None。"""
    failure = classify_cli_notice(text)
    if failure is None:
        return None
    line, severity, who, hint = _CLI_NOTICE_COPY[failure]
    return say(line), notice(
        EVENT_TURN_FAILED,
        severity=severity,
        who=who,
        detail=say("hintAndServiceWords", hint=say(hint), said=text.strip()),
        detail_label=say("labelDetails"),
        retryable=failure in _CLI_RETRYABLE,
    )


def _parse_uuid(raw: str | None) -> uuid.UUID | None:
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


# Open (non-final) accept-card statuses, worth telling the agent about at turn
# start — a card in one of these states usually implies "there is follow-up
# work or a wait the agent should know it's in".
_OPEN_CARD_STATUSES = (
    AcceptStatus.pending,
    AcceptStatus.pending_gate,
    AcceptStatus.gate_failed,
    AcceptStatus.gate_blocked,
    AcceptStatus.conflict,
)


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


class ChatService(SessionRecovery):
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker,
        base_system_prompt: str,
        workspace_root: str,
        compute: ComputePool,
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
        # Seats a reachable machine said are gone (recover_sessions),
        # until a session answers on them again (FB-56 legacy③).
        self._dead_sessions: set[tuple] = set()
        self._compute.bind_events(self._consume_hook_event, self._set_hook_activity)
        self._compute.bind_receipts(self.confirm_prompt_receipt)
        self._compute.bind_completions(self.confirm_work_completion)
        self._compute.bind_terminations(self.confirm_work_termination)
        self._compute.bind_unread_probe(self.oldest_unread_at)
        self._compute.bind_reachability(self._note_reachability)
        # Liveness only, keyed by durable input UUID. Settlement never depends
        # on this process cache; cold-start inputs are not mid-turn unread probes.
        self._unread_inputs: dict[uuid.UUID, dict[uuid.UUID, float]] = {}
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
        self._memory = MemoryLedger(
            sessions=session_factory,
            compute=compute,
            gateway=gateway,
            gateway_lock=self._gateway_lock,
            base_prompt=base_system_prompt,
            host=self,
        )
        self._compute.bind_memory(self._memory.sync)
        # Keep the publication contract present before native skills are invoked.
        self._skills = NATIVE_CHAT_GUIDANCE
        # Prompt construction is serialized per (topic, agent) seat: two agents
        # addressed in one room run their turns in parallel, while one agent's
        # turns still queue — a second turn of the SAME seat would assemble
        # while the first is mid-run, claim its pending messages, and starting
        # its session would park the one that is working. The lock is released
        # as soon as an interactive provider injects the prompt; non-interactive
        # providers still hold it while running because they cannot accept a
        # second message into a live screen.
        self._seat_locks: dict[tuple[uuid.UUID, str], asyncio.Lock] = {}
        # Room-level locks for the few operations that are nobody's turn:
        # swapping the room's environment mid-turn and memory consolidation on
        # the root topic.
        self._topic_locks: dict[uuid.UUID, asyncio.Lock] = {}
        # Work currently attributed to each active session. Mid-session delivery
        # captures this id before writing to the lower layer, then stamps the
        # message only after the exact UserPromptSubmit receipt.
        # A SET per topic: several seats can have a live turn in one room, so
        # every reader below answers "is THIS turn among the live ones" rather
        # than "is this THE one".
        self._active_turn_ids: dict[uuid.UUID, set[uuid.UUID]] = {}
        self._hook_work: dict[tuple[uuid.UUID, uuid.UUID], _HookWorkState] = {}
        # 房间里此刻那个会话是哪位队友的（最近一次 AgentSessionInfo 说的）。会话
        # 自己开一轮时没有人告诉我们它是谁的，就按这个认。
        self._room_session_agents: dict[uuid.UUID, str] = {}
        # The notice each open turn is keeping current: a streak of retries, a
        # wait for its machine. One line per streak, restated as it moves on.
        self._retry_notes: dict[uuid.UUID, uuid.UUID] = {}
        self._waiting_notes: dict[uuid.UUID, uuid.UUID] = {}
        self._compact_notes: dict[uuid.UUID, uuid.UUID] = {}
        # Which child agents the running sessions say are still doing something,
        # per room. Only the harness's own lifecycle events can answer this:
        # they fire in the session's process and carry the child's id, while
        # every tool call goes through the MCP transport,
        # whose request has no caller identity on it at all — which is why a
        # worker that only runs tools leaves no trace of its own. The board's
        # "is that worker still alive" reads it (`worker_live`).
        self._live_workers: dict[uuid.UUID, dict[str, bool]] = {}
        # The session each room is currently on. A claim about a child belongs to
        # the session that made it: a new session has never heard of the old
        # one's children, so a claim that outlived its session is void.
        self._room_sessions: dict[uuid.UUID, str] = {}
        # Where each live session's model traffic goes, remembered from the last
        # turn the platform assembled for it. A turn the session starts by itself
        # rides the same screen and therefore the same supply, and has no prompt
        # of its own to resolve one from. Insertion-ordered and trimmed from the
        # front: nothing tells this service a screen is gone, so without a bound
        # this is a dict that only ever grows in a process that runs for weeks.
        # Losing an entry costs the accuracy of one label, never a wrong charge.
        self._session_route: dict[uuid.UUID, str] = {}
        # The model each session was launched on, kept beside its route and for
        # the same reason: a self-started turn has no prompt to resolve it from.
        self._session_model: dict[uuid.UUID, str] = {}
        # Strong refs to in-flight background tasks (asyncio only keeps weak
        # refs; without this a pending commit could be GC'd).
        self._background_tasks: set[asyncio.Task] = set()
        # Each room's replay of what its sessions said while nobody listened
        # (`recover_sessions`), for as long as it runs. A turn in the room waits
        # for it (`replaying`); nothing else does.
        self._replays: dict[uuid.UUID, asyncio.Task] = {}
        self._replay_slots = asyncio.Semaphore(REPLAYS_AT_ONCE)

    @property
    def session_factory(self) -> async_sessionmaker:
        return self._sessions

    def _lock_for(self, topic_id: uuid.UUID) -> asyncio.Lock:
        lock = self._topic_locks.get(topic_id)
        if lock is None:
            lock = asyncio.Lock()
            self._topic_locks[topic_id] = lock
        return lock

    def _seat_lock_for(self, topic_id: uuid.UUID, agent_handle: str) -> asyncio.Lock:
        key = (topic_id, agent_handle)
        lock = self._seat_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._seat_locks[key] = lock
        return lock

    def _mark_turn_active(self, topic_id: uuid.UUID, work_id: uuid.UUID) -> None:
        self._active_turn_ids.setdefault(topic_id, set()).add(work_id)

    def _mark_turn_inactive(self, topic_id: uuid.UUID, work_id: uuid.UUID) -> None:
        active = self._active_turn_ids.get(topic_id)
        if active is None:
            return
        active.discard(work_id)
        if not active:
            self._active_turn_ids.pop(topic_id, None)

    def _consuming_work_id(
        self,
        topic_id: uuid.UUID,
        matches: Callable[[_HookWorkState], bool] | None = None,
        *,
        strict: bool = False,
    ) -> uuid.UUID | None:
        """The live turn an inbound message belongs to, if unambiguous.

        Today turns on a topic serialize, so the active set holds at most one
        id and this is that id (a missing hook state cannot rule it out, which
        preserves the old single-slot tolerance; ``strict`` is for callers
        that must NOT deliver without a state). When several turns are live
        — parallel agents in one room — only an exact ``matches`` hit decides,
        and only when exactly one hits: delivering to a guessed turn is worse
        than holding the message for none.
        """
        active = self._active_turn_ids.get(topic_id)
        if not active:
            return None
        if matches is None:
            return next(iter(active)) if len(active) == 1 else None
        hits = []
        for work_id in active:
            state = self._hook_work.get((topic_id, work_id))
            if state is None:
                if not strict:
                    hits.append(work_id)
            elif matches(state):
                hits.append(work_id)
        return hits[0] if len(hits) == 1 else None

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
                    place = await PlaceResolver(session).resolve(topic_id)
                    if place is not None:
                        pending = _pending_input_blocks(
                            await blocks.turn_history(place.room_id)
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
            topic = await TopicRepository(session).get(topic_id)
            if topic is None:
                raise NotFoundError("Topic not found")
            agent = await self._resolved_agent(session, topic)
            return agent.handle

    @asynccontextmanager
    async def _prompt_lock(
        self,
        topic_id: uuid.UUID,
        work_id: uuid.UUID,
        seat_handle: str,
    ) -> AsyncIterator[None]:
        from app.domain.agent.seat_admission import seat_admission

        async with seat_admission(self._seat_lock_for(topic_id, seat_handle)):
            self._mark_turn_active(topic_id, work_id)
            try:
                yield
            finally:
                if (topic_id, work_id) not in self._hook_work:
                    self._mark_turn_inactive(topic_id, work_id)

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
                payload = await self.post_system_event(
                    topic_id,
                    "输入已登记，发送结果正在核对；不会重复发送",
                    turn_id,
                )
                if payload is not None:
                    yield {"type": "event_block", "block": payload}
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
            fallback = await self.post_system_event(
                topic_id, fallback_text, turn_id, meta=fallback_meta
            )
            if fallback is not None:
                yield {"type": "event_block", "block": fallback}

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

    async def _live_inputs(
        self, block_ids: list[uuid.UUID]
    ) -> tuple[Block | None, Block | None]:
        """Read the persisted authored message, quote and validated reply edge."""
        stored = replied = None
        async with self._sessions() as session:
            blocks = BlockRepository(session)
            for block_id in block_ids:
                block = await blocks.get(block_id)
                if block is not None:
                    if block.kind == BlockKind.message:
                        stored = block
                    if block.reply_to is not None:
                        replied = await blocks.get(block.reply_to)
        return stored, replied

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
        consuming_turn_id = self._consuming_work_id(
            topic_id,
            lambda state: (
                recipient_handle is None
                or state.agent_instance_handle is None
                or state.agent_instance_handle == recipient_handle
            ),
        )
        if consuming_turn_id is None:
            return None
        state = self._hook_work.get((topic_id, consuming_turn_id))
        stored, replied = await self._live_inputs(user_block_ids)
        lines, images = live_input_lines(
            author,
            content,
            attachments,
            stored=stored,
            replied=replied,
            recipient=state.acting_agent if state else None,
        )
        state = self._hook_work.get((topic_id, consuming_turn_id))
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
                room = await TopicRepository(session).get(topic_id)
                archived = room is None or room.status == TopicStatus.archived
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
                delivered = (
                    await self._compute.deliver(
                        topic_id,
                        line,
                        images=images,
                        register_input=registrar,
                        expected_work_id=consuming_turn_id,
                        agent_handle=seat_agent,
                        owes_reply=owes_reply,
                    )
                    if images
                    else await self._compute.deliver(
                        topic_id,
                        line,
                        register_input=registrar,
                        expected_work_id=consuming_turn_id,
                        agent_handle=seat_agent,
                        owes_reply=owes_reply,
                    )
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
            for state in self._hook_work.values()
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
            and state.work_id in self._active_turn_ids.get(state.topic_id, ())
        ]

        async def remind(state: _HookWorkState) -> bool:
            if self._hook_work.get(
                (state.topic_id, state.work_id)
            ) is not state or state.work_id not in self._active_turn_ids.get(
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
        consuming_turn_id = self._consuming_work_id(
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
        state = self._hook_work.get((topic_id, consuming_turn_id))
        seat_agent = (
            (state.agent_instance_handle or state.acting_agent) if state else None
        )
        try:
            return bool(
                await self._compute.deliver(
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
        pending = self._unread_inputs.get(topic_id)
        return min(pending.values()) if pending else None

    def _input_registrar(
        self,
        effects: InputEffects,
        *,
        probe_unread: bool = False,
        fence_delivery: bool = False,
        parent_session_id: str | None = None,
    ) -> InputRegistrar:
        from app.domain.agent.input_registration import input_registrar

        return input_registrar(
            self._sessions,
            effects,
            self._unread_inputs,
            probe_unread=probe_unread,
            fence_delivery=fence_delivery,
            parent_session_id=parent_session_id,
        )

    async def confirm_prompt_receipt(self, receipt: InputReceipt) -> None:
        """Commit identity-bound effects before the journal may acknowledge.

        No in-memory candidate is needed. Commit failure propagates so the same
        journal input is retried, even by a newly reconstructed ChatService.
        """
        from app.domain.agent.input_registration import confirm_receipt

        await confirm_receipt(self, receipt)

    def nudge_ask_receipts(self, identity):
        from app.domain.agent.ask_receipt_wait import nudge_ask_receipts

        nudge_ask_receipts(self, identity)

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
        active = self._active_turn_ids.get(topic_id)
        if not active:
            return False
        if agent_handle is None:
            return True
        for work_id in active:
            state = self._hook_work.get((topic_id, work_id))
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
            history = await BlockRepository(session).list_for_topic(
                topic_id, task_id=None
            )
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
            history = await BlockRepository(session).list_for_topic(
                topic_id, task_id=None
            )
            # 从新到旧：最近一次点名是这批消息现在要交给谁的最新说法。
            for block in reversed(_pending_input_blocks(history)):
                recipient = (block.meta or {}).get("agent_recipient") or {}
                if recipient.get("mentioned"):
                    return recipient_seat(recipient)
            return None

    @asynccontextmanager
    async def edit_environment(self, topic_id: uuid.UUID) -> AsyncIterator[None]:
        """Prevent a new prompt from racing an explicit environment change."""
        lock = self._lock_for(topic_id)
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
        return turn_id in self._active_turn_ids.get(topic_id, ())

    async def _unconnected_mcp(
        self, project_id: uuid.UUID, topic_id: uuid.UUID, agent_handle: str | None
    ) -> tuple[str, ...]:
        """The remote MCP servers this session cannot use yet, its type's too,
        each said once in the room: 「<name> 需要在项目设置里连接」."""

        from app.domain.agent.runtime import get_broker
        from app.domain.remote_mcp import service as remote_mcp

        try:
            async with self._sessions() as session:
                unusable = (
                    await remote_mcp.session_servers(session, project_id, agent_handle)
                ).unusable
                said = set(
                    await session.scalars(
                        select(Block.meta["server"].as_string()).where(
                            Block.topic_id == topic_id,
                            Block.kind == BlockKind.event,
                            Block.meta["event_type"].as_string()
                            == EVENT_MCP_NOT_CONNECTED,
                        )
                    )
                )
                landed = []
                for name in unusable:
                    if name in said:
                        continue
                    block = await announce(
                        session,
                        place_id=topic_id,
                        content=say("mcpNotConnected", server=name),
                        meta={
                            **notice(
                                EVENT_MCP_NOT_CONNECTED,
                                severity=SEVERITY_WARN,
                                who=WHO_HUMAN,
                            ),
                            "server": name,
                        },
                    )
                    if block is not None:
                        landed.append(_block_payload(BlockOut.model_validate(block)))
                await session.commit()
        except Exception:  # noqa: BLE001 — a notice must never fail a turn
            logger.exception("remote MCP check failed for topic %s", topic_id)
            return ()
        for payload in landed:
            await get_broker().publish(
                str(topic_id), {"type": "event_block", "block": payload}
            )
        return unusable

    async def post_system_event(
        self,
        topic_id: uuid.UUID,
        content: str,
        turn_id: uuid.UUID | None = None,
        *,
        meta: dict | None = None,
    ) -> dict | None:
        """Persist a system event into the room (room_events.py)."""
        return await post_system_event(
            self._sessions,
            topic_id,
            content,
            turn_id,
            meta=meta,
        )

    async def cloud_waiting_topics(self, topic_ids: list[uuid.UUID]) -> list[uuid.UUID]:
        """Topics whose latest durable Cloud lifecycle event is still waiting."""
        async with self._sessions() as session:
            return await cloud_waiting_topics(session, topic_ids)

    async def work_policy(self, topic_id: uuid.UUID) -> dict | None:
        """Admission facts the AgentWorkRunner gates on BEFORE running a turn."""
        return await work_policy(self._sessions, self._compute, topic_id)

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
        return death_evidence.row_is_dead(self, topic_id, agent_handle, session_id)

    def seat_state(self, topic_id: uuid.UUID, agent_handle: str) -> str:
        """One of "live" / "dead" / "unknown" for the seat (FB-56 legacy③)."""
        return death_evidence.seat_state(self, topic_id, agent_handle)

    def has_live_screen(
        self, topic_id: uuid.UUID, agent_handle: str | None = None
    ) -> bool:
        """Is a session for this topic still reachable? The orphan sweep's first
        question, and the one that used to be unanswerable.

        With ``agent_handle`` it is that agent's session: a room seats several
        teammates, and one of them still answering says nothing about another.
        """
        return self._compute.holds(topic_id, agent_handle)

    def worker_live(self, topic_id: uuid.UUID, agent_id: str | None) -> bool | None:
        """Is the worker bound to this task still doing it?

        True when the room's session reported that agent starting and has not
        taken it back, None when no one is claiming anything about it — never
        mentioned, or already handed something back. The board takes this as the
        strongest evidence it can get (`TaskFacts.worker_live`): a worker can go
        quiet for forty minutes without being dead — that is what running a long
        command looks like — and only the thing running it can tell the two
        apart. The timestamps stay for the None case, where nobody has spoken.

        Only the memory half — `agent.liveness` adds the task's own open turn.
        A claim holds only while this process still holds that room's session.
        """
        if not agent_id:
            return None
        workers = self._live_workers.get(topic_id)
        if not workers:
            return None
        if not self._compute.holds(topic_id):
            # Their screen is gone, so they are gone with it — and this is the
            # one place that notices, since nothing tells this service a screen
            # died. Dropping the room's claims here is what keeps a claim from
            # outliving the screen it came from and resurrecting on the next one.
            self._live_workers.pop(topic_id, None)
            return None
        return workers.get(agent_id)

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
        while any(self._unread_inputs.values()) and time.monotonic() < deadline:
            await asyncio.sleep(0.2)
        # A replay still running reads its sessions too; the next process
        # replays them again from where this one landed.
        replays = list(self._replays.values())
        for replay in replays:
            replay.cancel()
        await asyncio.gather(*replays, return_exceptions=True)
        await self._compute.stop_listening()

    async def _replay_room(
        self, seats: list[SessionRef], *, after: asyncio.Task | None
    ) -> None:
        if after is not None:
            await asyncio.gather(after, return_exceptions=True)
        async with self._replay_slots:
            for session in seats:
                try:
                    # What the room already shows, so a message the live path
                    # DID persist before this process died is not landed twice.
                    # The room is ours; which of the harness's own records are
                    # still unlanded is the harness's.
                    await self._compute.replay(
                        session, known_texts=await self._said(session)
                    )
                except DeviceOffline:
                    # The machine holding this session is not there. Nothing to
                    # recover and nothing to fix; its next connection runs this.
                    logger.warning(
                        "session not recovered for topic %s: device offline",
                        session.topic_id,
                    )
                except DeviceCallError as exc:
                    # The machine is there and said no — its runner's socket is
                    # not up yet (a cold one takes about a minute), or the room's
                    # home is gone. Same standing as the machine being away: the
                    # next connection recovers this session, and the machine's
                    # own words are what somebody reading this would act on.
                    logger.warning(
                        "session not recovered for topic %s: %s",
                        session.topic_id,
                        exc,
                    )
                except Exception:  # noqa: BLE001 — one topic cannot block startup
                    logger.exception(
                        "session recovery failed for topic %s", session.topic_id
                    )
                else:
                    from app.domain.agent.pending_messages import nudge_messages

                    nudge_messages(self, session.topic_id)

    async def _said(self, session: SessionRef) -> set[str]:
        """What 芝士 has already said in this topic, as the room stores it."""
        async with self._sessions() as db:
            blocks = await BlockRepository(db).list_for_topic(session.topic_id)
        return {
            (block.content or "").strip()
            for block in blocks
            if looks_like_agent_handle(block.author)
            and (block.kind == BlockKind.message or (block.meta or {}).get("progress"))
        }

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
                place = await PlaceResolver(session).resolve(topic_id)
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
                            _AS.topic_id == place.room_id,
                            _AS.agent_handle == agent.handle,
                            _AS.harness == harness,
                        )
                        .with_for_update()
                    )
                    self._dead_sessions.discard(
                        (place.room_id, agent.handle, session_id)
                    )
                    await AgentSessionService(session).remember(
                        topic_id=place.room_id,
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
        from app.domain.agent.runtime import get_broker

        # 「谁在干活」要和块署名答同一个名字：块落在 acting seat 上，所以
        # 帧也带它。轮次开账前（自起的轮次，账还没开）状态不在，退回运行时
        # 给的会话座位。
        state = self._hook_work.get((topic_id, work_id))
        agent = (state.acting_agent if state is not None else None) or agent_handle
        if active:
            self._mark_turn_active(topic_id, work_id)
            frame = {"type": "turn_started", "turn_id": str(work_id), "agent": agent}
        else:
            self._mark_turn_inactive(topic_id, work_id)
            frame = {"type": "turn_finished", "turn_id": str(work_id), "agent": agent}
        await get_broker().publish(str(topic_id), frame)
        if not active:
            # The agent has said what it understood: the moment to check the
            # name the room got from its opening line (topic/naming.py).
            naming.nudge(topic_id, "turn")
            # 同一个时刻也看一眼文档：干过活的房间文档还空着，就请这个队友补上
            # （topic/doc_nudge.py）。
            doc_nudge.nudge(topic_id, self)
            from app.domain.agent.pending_messages import nudge_messages

            nudge_messages(self, topic_id)

    @staticmethod
    def room_is_a_work_room(topic: Topic) -> bool:
        """这间房按不按房间的规矩来 —— ``_is_dm`` 的否定，``is_private`` 在这个
        文件里唯一的那个读点推出来的两个答案之一（名册两席 / 这一轮不租地点）。

        提示词给不给「本话题还没有实况文档」那一段，问的就是这个：`_assemble_turn`
        的 `needs_place` 说的是同一句。`topic/doc_nudge.py` 按同一个答案决定要不要
        提醒，所以那边不提 ``is_private``，问的是这里——两处必须是同一份声明，否则
        一个模型会被提示词要求建文档、却收不到平台的提醒，或者反过来。

        `doc_nudge` 经它手上的 ``chat_service`` 取这个方法（`runtime.py` 那一处收尾
        不 import 本模块，手里只有同一个对象）。**取不到就什么都不做**：轮末那两行
        之间没有 try，多抛一句出去，这一轮就永远是「在跑」（`_live` 摘不掉）。
        """
        return not _is_dm(topic)

    def _note_room_session(self, topic_id: uuid.UUID, session_id: str) -> None:
        """The room is on a (possibly) different session now.

        Whatever the previous session said about its children died with it: the
        children of the old session are not running in the new one, and the new
        one will tell us about its own. Without this, a claim from a screen that
        has since been replaced would outlive it and say "still running" about a
        worker nobody is running.
        """
        previous = self._room_sessions.get(topic_id)
        if previous is not None and previous != session_id:
            self._live_workers.pop(topic_id, None)
        self._room_sessions[topic_id] = session_id

    def _note_worker_agent(self, topic_id: uuid.UUID, event: object) -> None:
        """Record what this session just said about one child agent.

        `SubagentStart`/`SubagentStop` are the only events that both name the
        child they are about and arrive straight from the session's process, so
        they are the only first-hand answer to "is that worker still doing it".
        Nothing is inferred from silence: started means running.

        A stop only takes the claim away, it does not declare the worker dead —
        a subagent that hands something back and stands by, or that is resumed
        later, produces a stop and then more work (see `AgentSubagentStop`). The
        board then falls back to the timestamps, which is what it did before this
        existed, rather than calling a worker dead that is about to speak again.
        """
        agent_id = getattr(event, "agent_id", None)
        if not agent_id:
            return
        workers = self._live_workers.setdefault(topic_id, {})
        if isinstance(event, AgentSubagentStart):
            workers[agent_id] = True
        else:
            workers.pop(agent_id, None)

    async def _work_of_worker(
        self, topic_id: uuid.UUID, thread_label: str | None
    ) -> uuid.UUID | None:
        """Which piece of work this event belongs to, read off the event itself.

        Several sub-threads run inside one session and everything they do
        arrives on the same pipe as the session's own, told apart by the label
        the agent gave each one when it spawned it (结论 43). The platform minted
        that label when it opened the card, so this is a lookup and not a guess
        — nobody has to have reported anything for a sub-thread's very first
        event to reach its card.

        None for three situations that want the same handling: the room itself
        did this, a sub-thread the harness started for its own purposes did, or
        the label names work that is not open in this room. All land on the
        room's own line, where they landed before any of this existed —
        swallowing them instead would make a whole run invisible, which is worse
        than the attribution being coarse.
        """
        from app.domain.room_task.services import TaskService

        if not thread_label:
            return None
        async with self._sessions() as session:
            task = await TaskService(session).open_by_thread_label(
                room_id=topic_id, thread_label=thread_label
            )
        return task.id if task is not None else None

    async def _begin_self_started_turn(
        self,
        project_id: uuid.UUID,
        topic_id: uuid.UUID,
        turn_id: uuid.UUID,
        *,
        opened: bool = False,
        agent_handle: str | None = None,
        session_id: str | None = None,
    ) -> "_HookWorkState | None":
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
        from app.api.deps import get_work_runner
        from app.domain.agent.repositories import AgentTurnRepository

        try:
            async with self._sessions() as session:
                place = await PlaceResolver(session).resolve(topic_id)
                if place is None:
                    return None
                topic = place.room
                project = await ProjectRepository(session).get(project_id)
                if project is None:
                    return None
                agents = AgentInstanceService(session)
                agent = await self._session_agent(agents, topic, project, agent_handle)
                agent_pool = memory_pool(topic.project_id, agent)
                # 署这个会话所属队友的名，不是房间的默认队友：一间坐着几位队友
                # 的房间里，别人的会话自己开的一轮署成默认那位，现场和「正在处
                # 理」就会把干活的人认错。
                acting_agent = await self._acting_handle(session, topic_id, agent)
                # Read whether or not this process opened it: a session keeps
                # working across a backend restart, and the process that fed or
                # first saw the turn wrote what it knew on the row.
                row = await AgentTurnRepository(session).get(turn_id)
            if not opened:
                await get_work_runner().open_turn_the_session_started(
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
        state = _HookWorkState(
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
            or self._session_route.get(topic_id, "native"),
            model=self._session_model.get(topic_id, ""),
            acting_agent=acting_agent,
            agent_pool=agent_pool,
            user_text="",
            started_at=row.started_at if row is not None else datetime.now(UTC),
            agent_instance_handle=agent.handle,
            known_commits=asyncio.ensure_future(
                self._known_commits(project_id, topic_id)
            ),
            self_started=not opened,
        )
        self._hook_work[(topic_id, turn_id)] = state
        if opened:
            # The session announced this turn's start to the process that fed
            # it, so this one never heard it: without this a message sent now
            # would start a turn beside it instead of joining it.
            # The single-slot world OVERWROTE the slot here, silently dropping
            # a stale leftover; keep that hygiene by pruning ids whose hook
            # state is gone — a genuinely live turn always has its state and
            # survives, which is what several agents in one room will need.
            self._active_turn_ids[topic_id] = {
                work_id
                for work_id in self._active_turn_ids.get(topic_id, ())
                if (topic_id, work_id) in self._hook_work
            } | {turn_id}
        return state

    async def _announce_action(self, state: _HookWorkState, resource: str) -> None:
        """Say in the room what 芝士 just did, the moment it did it — once per
        kind of action per turn, however many times the turn does it.

        Asked of the room rather than remembered, so a turn another backend
        picks up halfway does not announce twice, or forget what came before.
        """
        from app.domain.agent.runtime import get_broker

        landed = landing(
            EventAbout.room, project_id=state.project_id, room_id=state.topic_id
        )
        async with self._sessions() as session:
            blocks = BlockRepository(session)
            if await blocks.has_action(state.topic_id, state.work_id, resource):
                return
            block = await blocks.add(
                project_id=landed.project_id,
                topic_id=landed.topic_id,
                task_id=landed.task_id,
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
        from app.api.deps import get_work_runner

        return await _consume_hook_event(
            self,
            self._sessions,
            self._hook_work,
            self._retry_notes,
            self._waiting_notes,
            self._compact_notes,
            self._room_session_agents,
            self._active_turn_ids,
            get_work_runner(),
            project_id,
            topic_id,
            turn_id,
            event,
            eid,
            result_text_seen,
            platform_unsolicited,
        )

    async def _delivered_unread(
        self, session: AsyncSession, state: _HookWorkState
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
        self, state: _HookWorkState, result: AgentResult
    ) -> list[dict]:
        """Commit accounting and prompt consumption after the session stops."""
        usage = result.usage
        if usage is not None and not (
            usage.input_tokens or usage.output_tokens or usage.cost_usd
        ):
            usage = None
        # One row PER MODEL, never one lump: a gateway-routed turn's spend can
        # cover several models in one drain, and collapsing them under
        # `settings.agent_model` is exactly how mimo disappeared from `by_model`.
        usages: list[AgentUsage] = []
        if self._gateway is not None and state.route == "gateway":
            drained = await self._drain_gateway_usage(
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
                    topic_id=state.topic_id,
                    recipient_handle=result.agent_handle,
                    harness=result.harness,
                    native_session_id=result.session_id,
                    work_id=state.work_id,
                )
            if not usages:
                await Ledger(session).record(
                    await payer_for_project(session, state.project_id),
                    credits=0.0,
                    topic_id=state.topic_id,
                    model=state.model or settings.agent_model,
                    input_tokens=0,
                    output_tokens=0,
                    cost_usd=0.0,
                    metered=False,
                    route=state.route,
                    turn_id=state.work_id,
                )
            elif state.route != "gateway" or self._gateway is None:
                payer = await payer_for_project(session, state.project_id)
                for u in usages:
                    await Ledger(session).record(
                        payer,
                        credits=spend_to_credits(u.cost_usd),
                        topic_id=state.topic_id,
                        model=u.model or state.model or settings.agent_model,
                        input_tokens=u.input_tokens,
                        output_tokens=u.output_tokens,
                        cost_usd=u.cost_usd,
                        route=state.route,
                        turn_id=state.work_id,
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
            await session.commit()

        changeset = await self._turn_changeset(
            state.project_id,
            state.topic_id,
            None if state.known_commits is None else await state.known_commits,
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
            place = await PlaceResolver(session).resolve(topic_id)
            if place is None:
                raise NotFoundError("Topic not found")
            topic = place.room
            delivery_key = (
                action_key(place.room_id, "chat_message", author, client_id)
                if client_id
                else None
            )
            if delivery_key and not await idem.claim(
                session,
                delivery_key,
                action="chat_message",
                scope_id=str(place.room_id),
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
            agent = await AgentInstanceService(session).for_topic(topic, project)
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
                "mentioned": seats is not None and looks_like_agent_handle(seats[1]),
            }
            anchor_id: uuid.UUID | None = None
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
                if parent is None or parent.topic_id != topic.id:
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
                user_block = await blocks.add(
                    project_id=topic.project_id,
                    topic_id=place.room_id,
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
                await announce_mentions(session, topic, user_block, author, roster)
                await settle_questions_answered_by(session, user_block)
                if len(agent_handles) > 1:
                    # `agent_recipient` 是单数：它起的是第一位点到的那一轮。同一条
                    # 消息点到的其余几位各记一条投递，和 agent 点名走同一本账。
                    from app.domain.delivery.mention import record_mentions

                    await record_mentions(
                        session,
                        project_id=topic.project_id,
                        room_id=place.room_id,
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
                    topic_id=place.room_id,
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
        # A person's words are what a room gets named by (topic/naming.py).
        if names_a_person(author):
            naming.nudge(place.room_id, "message")
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

    async def _persist_assistant_message(
        self,
        *,
        project_id: uuid.UUID,
        topic_id: uuid.UUID,
        text: str,
        turn_id: uuid.UUID | None,
        reply_to: uuid.UUID | None,
        roster: list[dict] | None,
        topic_refs: list[dict],
        eid: str | None = None,
        eids: tuple[str, ...] = (),
        platform_unsolicited: bool = False,
        continuation_id: uuid.UUID | None = None,
        at: datetime | None = None,
        task_id: uuid.UUID | None = None,
        publish: bool = False,
        author: str | None = None,
        publication_id: str | None = None,
        own_output: bool = False,
        extra_meta: dict | None = None,
        closing: bool = False,
    ) -> dict | None:
        """Persist output immediately; only explicit publications enter chat.

        Terminal output remains in activity without notifying mentioned members.
        ``eid`` (the harness's own id for the event) is stamped into meta so a
        record read twice lands once; ``eids`` carries every id a message was
        delivered under, and any one of them matching an existing block means
        this message already landed.

        Returns None when ``continuation_id`` says this exact message already
        landed in an earlier attempt at the same work (④ 重发): the re-sent
        turn re-narrating "我先看一下 X" must not post a second copy of it. The
        caller treats None as "nothing to broadcast".

        ``own_output`` 告诉轮次输入账目：这一条是作者自己跑出来的产出，不是谁对
        房间说的一句待读的话。默认不必填 —— 「署名是 agent 且落在某一轮里」已经
        答得出这件事。填它的是那种平台填不出轮次号的写入端（远程控制里芝士问出
        口的那句话）。

        ``closing`` 说这一条是轮次收尾的结果文本。它照例就是这一轮最后说过的那
        段话，已经作为一条消息落过了；和那段一字不差时不再落第二遍。比对的是库
        里这一轮最后一段，不是内存账目——发版交接后接手的后端没有账目。"""
        # 有些「助手消息」根本不是芝士说的 —— 是它脚下的 CLI 把自己的英文提示
        # 当成助手输出印了出来。拦在这里而不是调用方:每一条写入路都经过这个方法,
        # 拦在门口才不会有一条漏网。
        as_progress = not publish
        as_notice = None if publish else _cli_notice(text)
        if as_notice is not None:
            line, notice_meta = as_notice
            return await self._persist_room_event(
                project_id=project_id,
                topic_id=topic_id,
                content=line,
                meta=notice_meta,
                turn_id=turn_id,
                eid=eid,
                platform_unsolicited=platform_unsolicited,
                in_room=True,
                author_type=AuthorType.platform,
                task_id=task_id,
            )
        meta: dict | None = (
            {"in_room": False, "progress": True} if as_progress else None
        )
        if eid:
            meta = {**(meta or {}), "eid": eid}
        if len(eids) > 1:
            meta = {**(meta or {}), "eids": list(eids)}
        if platform_unsolicited:
            meta = {**(meta or {}), "platform_unsolicited": True}
        if extra_meta:
            meta = {**(meta or {}), **extra_meta}
        known_ids = [e for e in dict.fromkeys((eid, *eids)) if e]
        async with self._sessions() as session:
            blocks = BlockRepository(session)
            publication_key = None
            publication_input = {
                "text": text,
                "reply_to": str(reply_to) if reply_to else None,
            }
            if publication_id is not None:
                publication_key = action_key(
                    topic_id, "chat-publish", author or "", publication_id
                )
                if not await idem.claim(
                    session,
                    publication_key,
                    action="chat-publish",
                    scope_id=str(topic_id),
                ):
                    previous = await idem.stored_result(session, publication_key)
                    if previous is None or previous["input"] != publication_input:
                        from app.core.errors import ConflictError

                        raise ConflictError("request_id was used for another message")
                    return previous["block"]
            if known_ids and await blocks.has_any_eid(topic_id, known_ids):
                return None
            # `has_any_eid` alone is a SELECT followed by an INSERT, and the same
            # hook event reaches this method from two places at once — the turn's
            # own attribution and the platform-unsolicited path. Both read "not
            # there", both write, and the room gets the message twice ~15ms
            # apart, the two rows carrying the SAME eid (measured across the
            # dev database: every duplicated 芝士 message has this shape).
            # ON CONFLICT DO NOTHING is what actually decides; the read above
            # stays because it also catches a copy landed by an earlier turn,
            # which no claim of ours would.
            if known_ids and not await idem.claim(
                session,
                action_key(topic_id, "block-eid", *sorted(known_ids)),
                action="message",
                scope_id=str(topic_id),
            ):
                return None
            # Claim BEFORE writing, in the SAME session: the key and the block
            # commit together, so "key present" and "message posted" cannot
            # disagree no matter where the process dies.
            if continuation_id is not None and not await idem.claim(
                session,
                action_key(
                    continuation_id, "progress" if as_progress else "message", text
                ),
                action="message",
                scope_id=str(topic_id),
            ):
                return None
            topic = await TopicRepository(session).get(topic_id)
            if roster is None:
                # The reconcile/backfill caller holds no roster. Load it here
                # instead of passing []: [] means 私聊 (no member list at all),
                # and conflating the two flagged every @ in a recovered message
                # as a non-member while silently dropping its notification.
                roster = await _room_roster(session, project_id, topic)
            text = _expand_mention_names(text, roster, topic_refs)
            if (
                closing
                and turn_id is not None
                and await blocks.last_said_in_turn(topic_id, turn_id) == text
            ):
                return None
            author = author or await self._agent_handle(session, topic_id)
            # 「关于什么」由 `task_id` 推出，调用方不另声明：调用方说出这条事件
            # 关于什么的方式**就是**递不递一张卡下来（变更提醒从不递）。再收一个
            # about 形参，是同一个事实在一处声明两遍——不加 `about_kind` 列的同一条理由。
            landed = landing(
                EventAbout.task if task_id is not None else EventAbout.room,
                project_id=project_id,
                room_id=topic_id,
                task_id=task_id,
            )
            block = await blocks.add(
                project_id=landed.project_id,
                topic_id=landed.topic_id,
                task_id=landed.task_id,
                author=author,
                author_type=AuthorType.participant,
                content=text,
                kind=BlockKind.event if as_progress else BlockKind.message,
                reply_to=reply_to,
                turn_id=turn_id,
                meta=meta,
                created_at=at,
                own_output=own_output,
            )
            # <@handle> mentions in 芝士's message → strong notify (the token is
            # the single source of truth: what's shown = who's notified).
            # Hallucinated handles get flagged in 现场, never silently no-op.
            if topic is not None and not as_progress:
                await announce_mentions(
                    session, topic, block, author, roster, flag_unresolved=True
                )
            payload = _block_payload(BlockOut.model_validate(block))
            if publication_key is not None:
                await idem.record_result(
                    session,
                    publication_key,
                    {"input": publication_input, "block": payload},
                )
            await session.commit()
        if publish and task_id is None:
            # The caller attributes the publication to a turn when it can; an
            # agent running off this process (a remote executor) publishes over
            # HTTP, where the runner knows no live work and hands in None. Its
            # turn still exists here, so fall back — but carefully, because a
            # room seats several agents: crediting agent A's publication to
            # agent B's turn would silence B's reminder while A's room stays
            # dark.
            # So: the publisher's own live turn first; an unambiguous single
            # live turn next (covers tokens that don't name an agent seat);
            # nothing when two agents' turns are live and neither is the
            # publisher's. Without a fallback at all, every remote publication
            # missed `last_chat_at` and the sweep kept "reminding" a turn that
            # had just spoken, counting the silence from turn start.
            work_id = turn_id or self._attributed_work_id(topic_id, author)
            state = (
                self._hook_work.get((topic_id, work_id))
                if work_id is not None
                else None
            )
            if state is not None:
                state.last_chat_at = datetime.now(UTC)
                state.last_progress_reminder_at = None
        return payload

    def _attributed_work_id(self, topic_id: uuid.UUID, author: str) -> uuid.UUID | None:
        """Which live hook work a room publication with no turn id belongs to."""
        live = [s for (t, _), s in self._hook_work.items() if t == topic_id]
        own = [s for s in live if s.acting_agent == author]
        if len(own) == 1:
            return own[0].work_id
        if len(own) > 1:
            active = self._active_turn_ids.get(topic_id, ())
            hits = [s.work_id for s in own if s.work_id in active]
            return hits[0] if len(hits) == 1 else None
        if len(live) == 1:
            return live[0].work_id
        return None

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
        task_id: uuid.UUID | None = None,
        author: str | None = None,
        at: datetime | None = None,
    ) -> dict | None:
        return await _persist_tool_event(
            self._sessions,
            self._hook_work,
            project_id=project_id,
            topic_id=topic_id,
            name=name,
            tool_input=tool_input,
            platform=platform,
            turn_id=turn_id,
            eid=eid,
            platform_unsolicited=platform_unsolicited,
            task_id=task_id,
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
        task_id: uuid.UUID | None,
        channel: str,
    ) -> None:
        """Say the turn is retrying a failed request (hook_stream.py)."""
        return await _note_retry(
            self._sessions,
            self._retry_notes,
            topic_id,
            turn_id,
            event,
            author=author,
            task_id=task_id,
            channel=channel,
        )

    async def _note_compaction(
        self, turn_id: uuid.UUID, event: AgentCompacting, *, channel: str
    ) -> None:
        """Restate the turn's compaction line as over (hook_stream.py)."""
        return await _note_compaction(
            self._sessions, self._compact_notes, turn_id, event, channel=channel
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
            self._hook_work,
            self._waiting_notes,
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
        task_id: uuid.UUID | None,
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
            task_id=task_id,
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
        task_id: uuid.UUID | None = None,
        author: str | None = None,
        at: datetime | None = None,
    ) -> dict | None:
        return await _persist_room_event(
            self._sessions,
            self._hook_work,
            project_id=project_id,
            topic_id=topic_id,
            content=content,
            meta=meta,
            turn_id=turn_id,
            eid=eid,
            platform_unsolicited=platform_unsolicited,
            in_room=in_room,
            author_type=author_type,
            task_id=task_id,
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
        task_id: uuid.UUID | None = None,
    ) -> dict | None:
        return await _persist_subagent_result(
            self._sessions,
            self._hook_work,
            project_id=project_id,
            topic_id=topic_id,
            event=event,
            turn_id=turn_id,
            eid=eid,
            platform_unsolicited=platform_unsolicited,
            task_id=task_id,
        )

    async def _persist_worker_event(
        self,
        *,
        project_id: uuid.UUID,
        topic_id: uuid.UUID,
        event: AgentSubagentStart | AgentSubagentStop,
        task_id: uuid.UUID | None,
        turn_id: uuid.UUID | None,
        eid: str | None = None,
        platform_unsolicited: bool = False,
    ) -> dict | None:
        return await _persist_worker_event(
            self._sessions,
            self._hook_work,
            self._active_turn_ids,
            project_id=project_id,
            topic_id=topic_id,
            event=event,
            task_id=task_id,
            turn_id=turn_id,
            eid=eid,
            platform_unsolicited=platform_unsolicited,
        )

    async def _note_worker(
        self,
        task_id: uuid.UUID,
        subagent_id: str,
        *,
        topic_id: uuid.UUID,
        turn_id: uuid.UUID | None,
        parent_session_id: str | None,
    ) -> None:
        return await _note_worker(
            self._sessions,
            self._hook_work,
            self._active_turn_ids,
            task_id,
            subagent_id,
            topic_id=topic_id,
            turn_id=turn_id,
            parent_session_id=parent_session_id,
        )

    async def _record_conclusion(self, task_id: uuid.UUID, text: str) -> None:
        return await _record_conclusion(
            self._sessions,
            task_id,
            text,
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
            self._hook_work,
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
        provider: ComputeProvider | None,
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

    async def _drain_gateway_usage(
        self, project_id: uuid.UUID, topic_id: uuid.UUID, turn_id: uuid.UUID
    ) -> list[AgentUsage] | None:
        """L1: real usage for gateway-routed turns, one entry per model
        (gateway_usage.py)."""
        return await _drain_gateway_usage(
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
    ) -> str:
        """一行委托：拼总览的那段是纯的，住在 `agent/prompt.py` 的 project_overview。

        留这个方法当接缝：它唯一的调用点（`_assemble_turn`）和驱动这个服务的测试
        都照原来的样子读，搬动只换了实现住在哪个文件。
        """
        return await project_overview(
            session,
            project=project,
            room_id=room_id,
            room_doc=room_doc,
            overview_doc=overview_doc,
            all_topics=all_topics,
            roster=roster,
        )

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

            # WHERE this turn runs. A room — the only thing a turn runs in.
            place = await PlaceResolver(session).resolve(topic_id)
            if place is None:
                raise NotFoundError("Topic not found")
            topic = place.room
            if topic.status == TopicStatus.archived:
                raise ValidationError(say("roomArchivedUnarchiveFirst"))

            # Speaker-labelled prompt covering every human message 芝士 hasn't
            # been handed yet — so messages posted without @芝士 are still seen on
            # the next summon (spec §7.1 所有消息 AI 都会收到), each tagged with
            # who said it so 芝士 can tell people apart in a group topic (§8.4).
            #
            # The room's OWN line: its 分身 talk on their cards, and handing
            # the room every card's chatter as its backlog would drown the
            # messages actually addressed to it.
            history = await blocks.turn_history(place.room_id)
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
                pinned_seat = await require_pinned_seat(
                    session, place.room_id, recipient_instance_id
                )
            # 收件人是消息落库时记下来的。记的时候还没有实例行的那些旧消息，
            # 「收件人是项目的芝士」和今天的解析是同一个答案。
            if recipient is None or recipient.get("instance_id") is None:
                agent = await self._resolved_agent(session, topic)
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
                or agent.handle == (await self._resolved_agent(session, topic)).handle
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
                offered_attachments, pending, topic.project_id, topic_id
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
            doc_root = await blocks.doc_root(place.room_id)
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
                await blocks.doc_root(project.root_topic_id)
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
                project.settings if project else None, topic
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
                place.room_id,
                session_agent.handle,
                harness=wanted_harness,
            )
            # The platform names rooms itself (topic/naming.py). Only where it
            # cannot — no gateway to call — is the agent still asked to, and
            # never in a project that chose to name its rooms by hand.
            untitled = (
                topic.title_source == TitleSource.placeholder
                and not naming.available()
                and naming.naming_mode(project.settings if project else None) == "auto"
            )
            # 进度层 (#187): the checklist the last turn left behind. Read inside
            # tx1 with everything else the prompt is built from, so no extra
            # round trip; empty list when this topic has never had one.
            progress_row = await TopicProgressRepository(session).get(place.room_id)
            prior_progress = [
                dict(item) for item in (progress_row.items if progress_row else [])
            ]
            earlier_messages = await blocks.count_messages(
                place.room_id, excluding=prompt_pending_ids
            )
            # Read for the stage derivation below, and for nothing else: what
            # the cards SAY is `cheese_status`'s answer, and restating it in a
            # prompt only froze one turn's copy of it into the whole session.
            open_cards = [
                c
                for c in await AcceptCardRepository(session).list_for_topic(topic_id)
                if c.status in _OPEN_CARD_STATUSES
            ]
            # 按阶段渐进式披露: which段 of the flow this topic is in. Derived
            # entirely from facts already in hand (kind/status + the open cards
            # just queried above for 盲飞防护) — no extra query.
            topic_stage = resolve_stage(
                finished=topic.status == TopicStatus.archived,
                card_statuses=[c.status for c in open_cards],
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
                                topic_id=topic_id,
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
                from app.domain.agent.compute_configs import room_choice

                conversation = await AgentSessionService(session).ensure(
                    topic_id, agent.handle, harness=wanted_harness
                )
                if conversation.execution_request is None:
                    conversation.execution_request = {
                        "generation": str(uuid.uuid4()),
                        "choice": room_choice(topic, project.settings).model_dump(),
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
                        room_choice,
                    )

                    proposed = await self._pass_policy_gate(
                        session,
                        topic_id,
                        await machine_policy_call(
                            session,
                            project=project,
                            topic=topic,
                            choice=room_choice(topic, project.settings),
                        ),
                        policy,
                        actor=actor_handle,
                    )
                    if proposed is not None:
                        await session.commit()
                        return _TurnBail(_proposal_frames(proposed.landed))
                proposed = await self._pass_policy_gate(
                    session,
                    topic_id,
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
                    topic_id=topic_id,
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
                            EventAbout.room,
                            project_id=project_id,
                            room_id=topic_id,
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
            if recipient_instance_id is not None:
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
            if recipient_instance_id is not None:
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
            private_owner=private_owner,
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
            topic_stage=topic_stage,
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
        private_owner = prepared.private_owner
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
        topic_stage = prepared.topic_stage
        turn_images = prepared.turn_images
        untitled = prepared.untitled

        # Tells AgentWorkRunner's outer wall-clock wrap (runtime.py) to reschedule
        # to this backend's real ceiling instead of the generic
        # `agent_turn_timeout_s` (turn 活跃度检测). It is the HARNESS's number:
        # how long a silence may last before it means something is wrong depends
        # on what is producing the output, not on the machine underneath it.
        runtime = runtime_for(provider)
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
        system_prompt = build_system_prompt(
            self._base_prompt,
            skills,
            doc_text,
            memory,
            role,
            # 已停用的队友不进这份名单：这一段教的是「要让某人去做事，在他名字前
            # 加 @」，而一个停用了的实例没有人在驱动它——@ 它等于把活扔进一个没人
            # 接的地方。@ 解析和通知那几路照旧走全量的 `roster`：老房间里已经在的
            # 它仍要 @ 得到，停用挡的是新的活，不是已经接手的。
            [m for m in roster if m["active"]],
            topic_refs_for_prompt,
            untitled,
            artifacts=artifact_refs,
            overview_doc=overview_doc_text,
            teaching=teaching,
            session_opening=_session_opening_lines(
                unconnected_mcp=(
                    await self._unconnected_mcp(project_id, topic_id, acting_agent)
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
            stage_guide=load_scenario(stage_scenario(topic_stage)),
            # 记忆那一段跟着这一轮跑的骨架走：写下来的文件同步不回平台的骨架，
            # 读到它只会以为自己在写项目记忆（`build_system_prompt` 那段注释）。
            keeps_memory=runtime.keeps_memory,
        )
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
            topic_id,
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
        known_commits = asyncio.ensure_future(self._known_commits(project_id, topic_id))
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
                topic_id,
                prepared.agent.handle,
                harness=prepared.harness,
            )
            await self._compute.activate(session_ref, runtime)
            ready = await runtime.send(
                session_ref,
                prompt_text,
                Opening(
                    system_prompt=system_prompt,
                    resume_token=resume_session_id,
                    expected_native_session=expected_session,
                    memory_scope="personal" if private_owner else None,
                    owner=private_owner,
                    model=model_kwargs.get("model"),
                    env=model_kwargs.get("env"),
                    agent_handle=acting_agent,
                    needs_place=needs_place,
                ),
                work_id=turn_id,
                images=turn_images or None,
                on_mark=_register_work,
                register_input=self._input_registrar(
                    effects,
                    fence_delivery=delivery_id is not None,
                    parent_session_id=resume_session_id,
                ),
                owes_reply=summoned,
            )
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

                await report_failure(self, project_id, topic_id, status)
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
            await close_recovery(session, topic_id)
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


async def _room_roster(
    session: AsyncSession, project_id: uuid.UUID, topic: Topic | None
) -> list[dict]:
    """The names an agent's message is read against: the project roster, or
    none at all in a private room."""
    return (
        [] if topic is None or _is_dm(topic) else await roster_rows(session, project_id)
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
    topic = await TopicRepository(session).get(block.topic_id)
    if topic is None:
        raise NotFoundError("Topic not found")
    by_agent = await TopicMemberService(session).holds_an_agent_seat(topic, author)
    if block.task_id is not None:
        text = await project_refs_text(session, topic.project_id, topic.id, content)
        return SentText(topic, text, None, by_agent)
    if by_agent:
        text = await project_refs_text(session, topic.project_id, topic.id, content)
        roster = await _room_roster(session, topic.project_id, topic)
        return SentText(topic, _expand_mention_names(text, roster, []), roster, True)
    project = await ProjectRepository(session).get(topic.project_id)
    if project is None:
        raise NotFoundError("Project not found")
    agent = await AgentInstanceService(session).for_topic(topic, project)
    mentions = await person_mentions(session, topic, content, agent, dm=_is_dm(topic))
    return SentText(topic, mentions.content, mentions.roster, False)
