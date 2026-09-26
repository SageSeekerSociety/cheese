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
import codecs
import hashlib
import json
import logging
import re
import time
import uuid
from collections import Counter
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.background import hold
from app.core.config import settings
from app.core.errors import GatewayUnavailableError, NotFoundError, ValidationError
from app.core.text import markdown_preview
from app.domain.agent.announce import announce
from app.domain.agent.compute import ComputePool, ComputeProvider
from app.domain.agent.device_hub import DeviceCallError, DeviceOffline
from app.domain.agent.gateway import LlmGateway, drain_new_usage
from app.domain.agent.harness import (
    Opening,
    SessionRef,
    harness_for,
    runtime_for,
)
from app.domain.agent.harness.prompt import (
    OVERVIEW_DOC_CHAR_BUDGET,
    attachment_prompt_line,
    build_system_prompt,
    fit_doc_to_budget,
    platform_prompt,
    prompt_line,
    publication_prompt,
    reply_quote,
    strip_platform_notice,
)
from app.domain.agent.platform_failures import (
    MODEL_LIMIT_REACHED_CODE,
    PROVIDER_OVERLOADED_CODE,
    PROVIDER_UNREACHABLE_CODE,
    RESPONSE_TRUNCATED_CODE,
    TOOL_UNAVAILABLE_CODE,
    TURN_TIMEOUT_MESSAGE,
    classify_cli_notice,
    classify_platform_failure,
)
from app.domain.agent.platform_notices import (
    EVENT_API_RETRY,
    EVENT_DEVICE_WAITING,
    EVENT_PROMPT_REPLAYED,
    EVENT_TURN_FAILED,
    EVENT_TURN_TIMEOUT,
    SEVERITY_ERROR,
    SEVERITY_INFO,
    SEVERITY_WARN,
    WHO_HUMAN,
    WHO_PLATFORM,
    delivery_fallback_notice,
    notice,
)
from app.domain.agent.profiles import ProfileRegistry
from app.domain.agent.service import (
    AgentEvent,
    AgentMessage,
    AgentResult,
    AgentRetrying,
    AgentSessionInfo,
    AgentStepFailed,
    AgentStepOutput,
    AgentSubagentStart,
    AgentSubagentStop,
    AgentToolResult,
    AgentToolUse,
    AgentUsage,
    proves_output,
)
from app.domain.agent.skills import NATIVE_CHAT_GUIDANCE, load_scenario, load_skills
from app.domain.agent.stages import TopicStage, resolve_stage, stage_scenario
from app.domain.agent.step_output import output_tail, without_output
from app.domain.agent.supply import SUBSCRIPTION
from app.domain.agent.tool_preview import (
    SHELL_TOOLS,
    ToolPreview,
    cheese_subcommand,
    tool_detail,
    tool_preview,
    work_subpath,
)
from app.domain.agent_instance.services import (
    AgentInstanceService,
    ResolvedAgent,
    memory_pool,
)
from app.domain.agent_session.services import AgentSessionService
from app.domain.block.about import EventAbout, landing
from app.domain.block.authorship import is_participant
from app.domain.block.models import (
    CONSUMED_TURN_META_KEY,
    AuthorType,
    Block,
    BlockKind,
    agent_notice,
    consumed_turn,
    prompted_turn,
)
from app.domain.block.repositories import BlockRepository
from app.domain.block.schemas import BlockOut
from app.domain.idempotency import store as idem
from app.domain.idempotency.keys import action_key
from app.domain.identity.actor import Actor
from app.domain.identity.handles import (
    agent_instance_handle,
    looks_like_agent_handle,
    names_a_person,
)
from app.domain.membership.roster import roster_rows
from app.domain.memory.models import MemoryScope
from app.domain.memory.pools import pools_for_turn
from app.domain.memory.store import RecallResult, memory_store, recall_pools
from app.domain.mentions import expand_mention_names
from app.domain.milestone.repositories import MilestoneRepository
from app.domain.notification.models import NotificationLevel, NotificationType
from app.domain.notification.services import ProjectNotificationService
from app.domain.policy import gate
from app.domain.policy.proposals import propose
from app.domain.project import artifacts as project_artifacts
from app.domain.project.environment import EnvironmentConfig, pin_environment
from app.domain.project.models import Project
from app.domain.project.repositories import ProjectRepository
from app.domain.review.models import AcceptStatus
from app.domain.review.repositories import AcceptCardRepository
from app.domain.room_task import binding
from app.domain.room_task.place import Place, PlaceResolver
from app.domain.task import teaching as teaching_context
from app.domain.task.teaching import TeachingContext
from app.domain.topic import naming
from app.domain.topic.models import Topic, TopicKind, TopicStatus
from app.domain.topic.repositories import TopicProgressRepository, TopicRepository
from app.domain.topic_membership.services import TopicMemberService
from app.domain.usage.credits import usage_to_credits
from app.domain.usage.repositories import ComputeGrantRepository, UsageRepository

ACTIVITY_SKILLS = ["chat", "chat-detail", "activity-digestion", "doc-form"]
HEARTBEAT_SKILLS = ["heartbeat", "chat", "chat-detail"]
PRIVATE_SKILLS = ["private-chat"]

CHEESE_AUTHOR = "cheese"

logger = logging.getLogger(__name__)


@dataclass
class _HookWorkState:
    """Persistence context for work whose events arrive on a subscription."""

    project_id: uuid.UUID
    topic_id: uuid.UUID
    work_id: uuid.UUID
    pending_ids: set[uuid.UUID]
    reply_to: uuid.UUID | None
    # None where no prompt was assembled to read one — `_persist_assistant_message`
    # then loads it, which is NOT the same as passing []: [] means 私聊 (no member
    # list at all), and conflating the two flags every @ as a non-member.
    roster: list[dict] | None
    topic_refs: list[dict]
    continuation_id: uuid.UUID | None
    route: str
    acting_agent: str
    # Attribution and memory part ways here, deliberately: `acting_agent` is
    # the seat of the agent that ran this turn (who did it), while the pool
    # belongs to that agent across rooms (whose memory it is). Resolved at turn
    # start and carried, because the hook path reaches turn end with no session
    # left open to ask.
    agent_pool: tuple[MemoryScope, str] | None
    user_text: str
    started_at: datetime
    agent_instance_handle: str | None = None
    # The model this turn's session was launched on, for the usage row a turn
    # with no reported usage still writes. "" where this process never
    # assembled a turn for the session (a screen recovered on the way up).
    model: str = ""
    assistant_count: int = 0
    last_chat_at: datetime | None = None
    # When this turn was last told it had gone quiet — NOT whether it has been.
    # A flag meant one reminder per silent stretch, so a turn that worked for
    # three hours without publishing was asked once, at the ten-minute mark, and
    # then left alone for the remaining two hours and fifty minutes. The room
    # showing nothing for that long is the complaint this reminder exists for.
    last_progress_reminder_at: datetime | None = None
    #: 这一轮每次工具调用落在哪个现场块上，按 harness 自己的调用 id。结果回来时要
    #: 写上输出、挂了要标红的就是那一块。只在内存里、只活这一轮：重启丢掉的只是几
    #: 截输出和几个红点，不是记录。
    steps: dict[str, uuid.UUID] = field(default_factory=dict)

    # The topic branch's commits as of turn start — what makes "this turn's
    # changes" answerable at turn end. A task rather than a value, because the
    # read shells out to git and creates the repo on first use; see where it is
    # started. `None` (or a read that failed) means the turn lands NO change
    # summary rather than a wrong one: with no baseline, every commit looks new.
    known_commits: asyncio.Task[set[str] | None] | None = None
    # Did the SESSION open this work rather than the platform? Then its
    # bookkeeping has no coroutine to fall out of, and turn end is the only
    # place the marks it left in the runner can be dropped.
    self_started: bool = False


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
    # 项目总览那一份实况文档：全项目共看的东西住在这里（结论 7）。总览房间自己
    # 那一轮是 None —— 它的 `doc_text` 就是这一份，说两遍只会让模型以为是两份。
    overview_doc_text: str | None
    memories: RecallResult
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


@dataclass(frozen=True, slots=True)
class _Proposed:
    """闸门把这次调用变成了一条提议：那条提议，和它在房间里刚落下的那条事件。

    `landed` 是 `None` 表示这条提议之前就提过了（`policy/proposals.py` 按身份去
    重）。两个调用点各取一半——轮次要那条事件来收场，平台自己发起的那几轮要那句话
    来抛。
    """

    proposal: gate.Proposal
    landed: dict | None


# 施工现场: render each tool call like a Claude Code action line — a Chinese verb
# plus a short preview of its most telling argument. Stored in the event block as
# "verb\npreview" (preview omitted when empty).
_TOOL_VERB = {
    "update_doc": "更新文档",
    "remember": "记入项目记忆",
    "notify": "发送通知",
    "request_accept": "提交审阅",
    "pin_milestone": "添加里程碑",
    "write_file": "写入文件",
    "record_decision": "记录决策",
}


# Native Claude Code tools (sandbox mode) → 现场 labels. Systematic: every tool
# the agent can invoke has a Chinese verb + its most telling argument as the
# preview; an unmapped (future) tool falls back to its raw name, which is the
# signal to extend this table.
_TOOL_VERB.update(
    {
        "Bash": "执行命令",
        "Write": "写入文件",
        "Edit": "修改文件",
        "Read": "读取文件",
        "Glob": "查找文件",
        "Grep": "搜索内容",
        "WebSearch": "搜索网页",
        "WebFetch": "读取网页",
        "Agent": "派出分身",
        "Task": "派出分身",  # older CLI name for Agent
        "NotebookEdit": "修改笔记本",
        "TodoWrite": "更新任务清单",
        "BashOutput": "查看命令输出",
        "KillShell": "终止命令",
        "KillBash": "终止命令",
        "ExitPlanMode": "提交方案待确认",
        "AskUserQuestion": "向用户提问",
        "Skill": "调用技能",
        "ToolSearch": "查找工具",
    }
)

# pi 原生工具。同样的动词，另一套名字 —— 两个 harness 的工具名没有一个重合，所以
# 少了这几行，一个 pi 房间的现场从头到尾只有 bash / read / write 这些英文原名。
_TOOL_VERB.update(
    {
        "bash": "执行命令",
        "read": "读取文件",
        "write": "写入文件",
        "edit": "修改文件",
        "ls": "列出目录",
        "find": "查找文件",
        "grep": "搜索内容",
    }
)


def _format_tool_event(name: str, preview: ToolPreview) -> str:
    """Human-readable FALLBACK text for an event block (old clients / old rows).

    The UI renders from the structured meta (see _tool_event_meta); this baked
    string only shows when meta is absent. Both are built from the SAME
    ToolPreview, so the baked line and the rendered one cannot describe the
    call differently."""
    verb = _TOOL_VERB.get(preview.action or name, name)
    return f"{verb}\n{preview.text}" if preview.text else verb


# 现场圆点分级: a PLATFORM action (amber dot) vs plain work (neutral dot).
# Deterministic by construction — tool-name prefix, or the `cheese` CLI at the
# head of the command segment 现场 displays. NEVER inferred from natural
# language, and never from the word appearing somewhere else in the command:
# the dot and the text on that line have to be about the same thing.
#: 平台动作在各个 harness 里叫什么。同一件事三种拼法，因为把工具交给模型的机制
#: 各不相同：MCP 服务器自己加前缀，pi 那边的目录是从 CLI 的命令树生成的，而
#: `chat_send` 是系统提示每一轮都在点名、于是 extension 额外注册的那个别名。
#: MCP 那个前缀（`mcp__<服务器>__<工具>`）也要认。claude_code 的服务器注册名是
#: `native`（见 `harness/claude_code/remote_execution/client.py` 写 mcp.json 时的
#: `servers = {"native": ...}`），所以真实名字长这样：
#: `mcp__native__cheese_feedback_propose`；
#: `mcp__cheese__` 是这套东西还叫 cheese 时的拼法，仍然认（历史行还躺在库里）。
#: 只认后者会让整件事**静默失效**：前缀认不出来 → 这一格既不算平台动作、中文标签也
#: 查不到，于是时间线上原样渲染 `mcp__native__…` 配一个中性点，而它看着完全正常。
_PLATFORM_PREFIXES = ("mcp__cheese__", "mcp__native__", "cheese_")
#: ……但 `mcp__native__` 底下**不都是平台动作**：`invoke` 是这个 harness 搬运读写与
#: 命令的通道（Read / Edit / Bash 都从它过），把它算成平台动作会在时间线上点一颗琥珀
#: 色的点 —— 而那只是读了一个文件。
_NOT_A_PLATFORM_TOOL = frozenset({"mcp__native__invoke"})
#: 名字里没有 `cheese_` 的那几个平台工具：`chat_send` 和 `todo_write` 是系统提示点名
#: 的两样，名字照模型已经认得的说法起；另外两个是平台自己的 MCP 工具。
_PLATFORM_ALIASES = frozenset(
    {"chat_send", "todo_write", "platform_request", "send_user_file"}
)

#: `mcp__<服务器>__<工具>` 的前缀。**认服务器名，不认某一个写死的**：写死一个的话，
#: 服务器改名那一天这里会静态地失效，而失效的样子和时间线正常的样子一模一样。
_MCP_PREFIX = re.compile(r"^mcp__[a-z0-9_]+__")


def _short_tool_name(raw_name: str) -> str:
    """把 MCP 工具名归一成模型看到的那个（`mcp__native__chat_send` → `chat_send`）。"""
    return _MCP_PREFIX.sub("", raw_name)


def _is_platform_tool(raw_name: str, args: dict) -> bool:
    """True when the tool call is a platform action: a cheese tool under any of
    the names a harness publishes it as, or a shell command that invokes the
    machine's `cheese` CLI."""
    if raw_name in _NOT_A_PLATFORM_TOOL:
        return False
    if raw_name.startswith(_PLATFORM_PREFIXES):
        return True
    if _short_tool_name(raw_name) in _PLATFORM_ALIASES:
        return True
    if raw_name in SHELL_TOOLS and isinstance(args, dict):
        return bool(cheese_subcommand(str(args.get("command", ""))))
    return False


def _tool_event_meta(
    name: str, preview: ToolPreview, *, platform: bool, detail: str = ""
) -> dict:
    """Structured payload persisted on an event block: the UI translates the
    tool name and colors the dot from these fields at DISPLAY time, so a verb
    missing from today's table is never baked in untranslated forever.

    ``as_tool`` rides alongside ``tool`` rather than replacing it: ``tool`` says
    what actually ran, ``as_tool`` says whose label reads better (a Bash
    `cat foo.py` is still a Bash call, but 「读取文件」 is what it did). NOT named
    ``action`` — that key already means "which platform resource this card points
    at" (see the frontend's platformNotice), and one name answering two questions
    is how a card ends up pointing at a resource called "Read".

    ``detail`` is the argument as it was actually written, for the reader who
    opens the line. It is stored NEXT TO ``arg`` rather than replacing it
    because the two want opposite things: ``arg`` is rewritten and cut to stay
    scannable on one line, and what the opener came for is exactly what that
    rewriting removed. Only the preview is ever computed from it, so a line
    with nothing more to say carries no second copy."""
    meta: dict = {"tool": name, "platform": platform}
    if preview.text:
        meta["arg"] = preview.text
    if preview.action:
        meta["as_tool"] = preview.action
    if detail:
        meta["detail"] = detail
    return meta


# 分身回吐 (§9 可见性): a subagent reports to whoever spawned it and nothing else,
# so the room used to see 「派出分身 X」 and never the answer. Its conclusion
# lands as its own 现场 event — CAPPED, because the room is a place people read:
# a subagent can return thousands of words and pasting them here would bury the
# conversation instead of informing it. The full text is in the transcript; what
# the room needs is enough to tell "it answered the question" from "it went off
# the rails", which is the whole point of making it visible.
_SUBAGENT_RESULT_MAX = 500


def _subagent_event_text(description: str, result: str) -> str:
    """现场 line for a returning subagent: "分身查完了：<问题>\\n<结论摘要>".

    NOTE the deliberate absence of ``meta.tool`` on the block this text goes on
    (see _subagent_result_meta): the UI translates meta.tool through its own verb
    table, and a name that table doesn't know renders raw. Leaving it off routes
    this block down the content-text path, which reads correctly with no frontend
    change — while the structured fields ride along for when there is one.
    """
    summary = " ".join(result.split())
    if len(summary) > _SUBAGENT_RESULT_MAX:
        summary = summary[:_SUBAGENT_RESULT_MAX] + "…"
    head = " ".join(description.split())[:80]
    verb = f"分身查完了：{head}" if head else "分身查完了"
    return f"{verb}\n{summary}" if summary else verb


def _subagent_result_meta(name: str, description: str, result: str) -> dict:
    """Structured payload for a subagent conclusion.

    ``truncated`` is what tells a future UI that an 「展开」 affordance has
    something behind it, instead of it having to compare lengths against a
    constant that lives on the other side of the wire.
    """
    summary = " ".join(result.split())
    return {
        "platform": False,  # the agent's own work, not a platform action
        "subagent": {
            "tool": name,
            "description": " ".join(description.split())[:120],
            "summary": summary[:_SUBAGENT_RESULT_MAX],
            "truncated": len(summary) > _SUBAGENT_RESULT_MAX,
        },
    }


# 一轮的改动汇总 (§8.4 commit 可视化): what this turn did to the topic branch, as
# ONE event at turn end. Bounded on purpose — the ask was "改了几个文件、大致
# 增删量，能点开看 diff", NOT the diff body: a 2000-line diff pasted into the
# timeline is the definition of 刷屏, and the diff panel already renders it well.
_CHANGE_FILES_LISTED = 12  # paths named in the event's text and in meta.files
_CHANGE_COMMIT_WALK = 30  # how far back we look for commits new to this turn

# "diff --git a/<old> b/<new>", with git's quoting when a path needs it. The
# b-side is the path AFTER the change, which is where a rename should be filed.
_DIFF_HEADER_RE = re.compile(r'^diff --git "?a/.+?"? "?b/(?P<path>.+?)"?$')


@dataclass
class _Changeset:
    """What one turn did to the topic branch."""

    commits: list[str]  # newest first; commits[0] is what a UI opens
    files: list[dict]


def _diff_file_stats(diff: str) -> list[dict]:
    """Per-file added/removed line counts parsed out of a unified diff.

    Counted from the diff text rather than asked of git with ``--numstat``
    because the only workspace readers available here return diff bodies; the
    parse is the cheaper half of that trade (the body is already in memory).

    Content lines are counted only INSIDE a hunk. Skipping "+++"/"---" by prefix
    instead would silently drop a deleted line whose own text starts with "--".
    """
    stats: list[dict] = []
    current: dict | None = None
    in_hunk = False
    for line in diff.splitlines():
        header = _DIFF_HEADER_RE.match(line)
        if header is not None:
            path = header.group("path")
            if line.endswith('"'):
                # Git quotes UTF-8 bytes with C-style octal escapes.
                path = codecs.escape_decode(path.encode())[0].decode()
            current = {"path": path, "added": 0, "removed": 0}
            stats.append(current)
            in_hunk = False
            continue
        if current is None:
            continue
        if line.startswith("@@"):
            in_hunk = True
            continue
        if not in_hunk:
            continue  # index / mode / rename / ---+++ headers
        if line.startswith("+"):
            current["added"] += 1
        elif line.startswith("-"):
            current["removed"] += 1
    return stats


def _format_change_summary(files: list[dict]) -> str:
    """现场 line for a turn's changes: a headline plus the paths it touched."""
    added = sum(f["added"] for f in files)
    removed = sum(f["removed"] for f in files)
    head = f"这一轮改了 {len(files)} 个文件（+{added} -{removed}）"
    listed = [f["path"] for f in files[:_CHANGE_FILES_LISTED]]
    rest = len(files) - len(listed)
    if rest > 0:
        listed.append(f"…另 {rest} 个")
    return f"{head}\n{' · '.join(listed)}" if listed else head


def _change_summary_meta(changeset: _Changeset) -> dict:
    """Structured payload for a turn's change summary.

    ``commit`` is the newest of the turn's commits and the ref a UI should open:
    ``GET /api/projects/{project}/git/diff?ref=<commit>`` already serves exactly
    that diff, so 「点开看 diff」 needs no new endpoint.
    """
    files = changeset.files
    return {
        "platform": True,  # the platform's own bookkeeping, not something 芝士 did
        "changeset": {
            "commit": changeset.commits[0] if changeset.commits else None,
            "commits": changeset.commits,
            "files_total": len(files),
            "added": sum(f["added"] for f in files),
            "removed": sum(f["removed"] for f in files),
            "files": files[:_CHANGE_FILES_LISTED],
            "files_omitted": max(0, len(files) - _CHANGE_FILES_LISTED),
        },
    }


#: How many sessions' supply routes to remember. Well past the number of screens
#: one backend drives at once, so in practice nothing is ever evicted; it is a
#: ceiling on a dict nothing else prunes, not a policy.
_SESSION_ROUTES_KEPT = 512


# A platform tool → the action card 芝士 files for it when it calls it
# (`_ACTION_LABEL`, `_announce_action`). Only the card: telling the room a panel
# went stale is the job of the API handler that changed it (`announce_stale`),
# which knows the change happened whoever called it. Keyed by the tool's short name
# (`mcp__native__cheese_decision` → `cheese_decision`).
_TOOL_ACTION = {
    "cheese_decision": "decision",
    "cheese_task": "topics",
    "cheese_milestone": "milestone",
    "cheese_notify": "notify",
}


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
    "decision": "记录了决策",
    "topics": "更新了这个房间的任务",
    "milestone": "添加了里程碑",
    "notify": "发送了通知",
}


# HTTP statuses worth an automatic re-run: timeouts, throttling, server-side
# blips. Anything else (or a rejected seat rate-limit) surfaces immediately.
_TRANSIENT_HTTP = {408, 429, 500, 502, 503, 504, 529}


def _resolve_compute_id(
    project_settings: dict | None,
    topic_compute_profile: str | None = None,
) -> str | None:
    """A room keeps its choice; otherwise use the explicit project default."""
    from app.domain.agent.compute_configs import project_configs

    return topic_compute_profile or project_configs(project_settings).default.profile


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


def _turn_failure_notice(text: str, code: str | None) -> tuple[str, dict]:
    """A failed turn's room line and the structured card behind it.

    Every failure gets one, classified or not. 平台提示统一契约: the room line
    is ONE line and the service's own words go in `meta.detail` — pasting them
    into the line is what made a plain system row run to seven or eight, and
    only a classified failure used to get meta at all, so the three most common
    ones (座位限流 / 余额用尽 / HTTP 错误) carried no structure whatsoever.

    An unclassified failure shows the SERVICE'S OWN first line rather than a
    generic label: 「AI 服务返回错误」 with the reason buried sent a whole room
    hunting a mystery bug twice in one night (2026-08-16, topic ee17b136 — the
    real text was the delivery timeout all along).
    """
    failure = classify_platform_failure(text, code=code)
    if failure is not None:
        return failure.content, failure.meta
    detail = (text or "").strip()
    if _is_out_of_credit(detail):
        # A spent balance is not a wait — no amount of retrying refills it, and
        # telling someone to try again later sends them into a loop that cannot
        # succeed. Say what actually has to happen.
        line = "本轮未完成：AI 中继余额已用完"
        hint = "需要充值，或者把机器切换到其他 AI 服务。重试没有作用。"
        retryable = False
    else:
        first = detail.splitlines()[0].strip() if detail else ""
        if len(first) > 160:
            first = first[:160] + "…"
        line = f"本轮未完成：{first}" if first else "本轮未完成：AI 服务返回错误"
        hint = "稍后可以重试。"
        retryable = True
    return line, notice(
        EVENT_TURN_FAILED,
        severity=SEVERITY_ERROR,
        who=WHO_HUMAN,
        # 原话是唯一的一份——它没有第二个副本可以「去别处看」，所以原样收进
        # detail，不截、不摘要。
        detail="\n\n".join(
            part for part in (hint, f"服务原话：\n{detail}" if detail else "") if part
        )
        or None,
        detail_label="详细说明",
        retryable=retryable,
    )


# CLI 自己印在对话里的那几句英文,换成平台自己的中文提示卡。
#
# 它们过去顶着芝士的名字发出来,读的人看到的是「芝士在说英文报错」,而实际上
# 芝士根本没说话 —— 是它脚下的 CLI 印的。归属错了比语言错了更糟:一个平台故障
# 被读成 AI 的回答,谁也不知道该找谁。
#
# 英文原话一个字都不丢,收进「服务原话」的折叠区 —— 它是唯一的一份。
_CLI_NOTICE_COPY: dict[str, tuple[str, str, str, str]] = {
    PROVIDER_UNREACHABLE_CODE: (
        "无法连接 AI 服务，这一步未完成",
        SEVERITY_ERROR,
        WHO_PLATFORM,
        "通常不会自行恢复：可能是这台机器上的隧道助手断开了，也可能是中继连接不稳定。"
        "可以先重试一次；仍然连不上就需要有人检查机器，不要反复重试。",
    ),
    PROVIDER_OVERLOADED_CODE: (
        "AI 服务暂时过载，这一步未完成",
        SEVERITY_WARN,
        WHO_PLATFORM,
        "这是服务端的问题，通常很快恢复。稍后可以重试。",
    ),
    MODEL_LIMIT_REACHED_CODE: (
        "这个模型的额度已用完",
        SEVERITY_ERROR,
        WHO_HUMAN,
        "需要换一个模型，或者等额度恢复。重试没有作用。",
    ),
    TOOL_UNAVAILABLE_CODE: (
        "一个工具无法使用",
        SEVERITY_WARN,
        WHO_PLATFORM,
        "工具在等待授权，但授权提示出现在容器的终端里，房间里无法操作。"
        "这说明这台机器上的工具配置有误，需要有人检查，重试不会有变化。",
    ),
    RESPONSE_TRUNCATED_CODE: (
        "上一条回复没有完整发出",
        SEVERITY_WARN,
        WHO_PLATFORM,
        "上一条回复可能不完整，重试会让它接着说。",
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
    return line, notice(
        EVENT_TURN_FAILED,
        severity=severity,
        who=who,
        detail=f"{hint}\n\n服务原话：\n{text.strip()}",
        detail_label="详细说明",
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

_PROGRESS_MARK = {"completed": "x", "in_progress": "~", "pending": " "}


def _progress_lines(items: list[dict]) -> list[str]:
    """进度层 (#187): the checklist this topic's work left behind, as prompt text.

    This is the one thing a fresh machine cannot reconstruct from the repo. Code
    survives in git, conclusions survive in the doc and the decision log, but
    "which of the five things am I on" only ever lived in the dead turn's stream.
    So it is stated here as a fact about the topic, not as memory — see
    TopicProgress's docstring for why the two must not be merged.

    The instruction to re-list finished items when building a new checklist is
    load-bearing: `todo_write` replaces the stored row whole, so a plan that
    silently drops what is already done would erase it.
    """
    if not items:
        return []
    lines = ["- 上次的任务清单（跨轮、跨机器保留下来的进度，不是这一轮新建的）："]
    for item in items:
        mark = _PROGRESS_MARK.get(str(item.get("status", "")), " ")
        subject = str(item.get("subject", "")).strip() or "（任务）"
        lines.append(f"    - [{mark}] {subject}")
    lines.append(
        "  已完成的别重做，接着没做完的往下干。**用 `todo_write` 重新写清单时把已完成"
        "的也列进去并标成 completed**——清单会整份覆盖上面这份，只列剩下的等于把做过"
        "的抹掉。"
    )
    return lines


def _sandbox_limits(provider: object) -> tuple[int, int] | None:
    """(memory_mb, cores) for backends that know their own size, else None.

    Read off the provider rather than looked up in the machine tables on
    purpose: #282 决定 2 keeps the agent layer out of the machine domain, and
    check-repo-rules enforces it. A backend that does not set these attributes
    genuinely does not know — an enrolled machine belongs to someone else and we
    do not set its limits — and None then means the prompt says nothing at all.
    Inventing a number would be worse than silence: the agent would skip work it
    could have done.
    """
    memory_mb = getattr(provider, "sandbox_memory_mb", None)
    cores = getattr(provider, "sandbox_cores", None)
    if isinstance(memory_mb, int) and isinstance(cores, int) and memory_mb > 0:
        return (memory_mb, cores)
    return None


def _session_opening_lines(
    *,
    progress: list[dict] | None = None,
    sandbox: tuple[int, int] | None = None,
    earlier_messages: int = 0,
) -> list[str]:
    """盲飞防护: what a session cannot find out for itself, at the moment it opens.

    Each survives being written once. The machine's size does not change under
    a session; the checklist answers 「我做到哪了」 and the pointer to the chat
    answers 「之前说了什么」 for a session that was not there — once one is
    running, its own history answers both.

    This is what is left of a per-turn header that came from #175, where the
    complaint was 29 turns timing out against a 900s ceiling nobody had been
    told about. Two things happened to it. The ceiling went away (there is no
    countdown; saying there was one made the agent rush — dev, 2026-08-08), and
    `cheese_status` — added in that same commit, for that same complaint — took
    over the rest: cards, gate output and disk are all one call away, and the
    header was restating them every turn, from a snapshot that stopped being
    true after the first one. What is left is the part no call and no turn can
    reconstruct.
    """
    lines: list[str] = []
    # 机器有多大: the agent cannot read its own cgroup limit, and the failure it
    # produces without knowing — a build the kernel OOM-kills — looks like a
    # broken toolchain rather than a small box. Only the FACT goes here; what to
    # do about it (try it once anyway, never retune --max-old-space-size, say
    # plainly that you did not run it) is a principle and lives in the cheese
    # skill. None means this backend does not know its own size, and then we say
    # nothing at all rather than invent a number.
    if sandbox is not None:
        mem_mb, cores = sandbox
        gb = mem_mb / 1024
        shown = f"{gb:.0f}" if gb == int(gb) else f"{gb:.1f}"
        lines.append(
            f"- 这台机器：内存 {shown}GB、{cores} 核。吃内存的命令"
            "（前端 build/typecheck、大型编译）可能被内核 OOM 杀掉——那不是代码"
            "有问题，也不是工具链坏了。"
        )
    lines.extend(_progress_lines(progress or []))
    # A session that opens in a room with history has read none of it, while
    # the people in the room assume it has. The living docs, memory and the
    # checklist reach it as conclusions; what was said is only in the chat.
    if earlier_messages:
        lines.append(
            f"- 这个房间里已经有 {earlier_messages} 条聊天消息，这个会话一条都没读过。"
            "动手之前先用 `cheese chat list` 读最近的记录；"
            "要找某句原话或某个决定，用 `cheese chat search <关键词>`。"
        )
    return lines


def _platform_preamble(notices: list[Block]) -> str:
    """What moved under the session, as the frame the rest of the prompt is read
    in — so it goes first: a request to revise the 验收标准 means something else
    once you know that section moved ten minutes ago.

    One marker over all of them. The marker is the one thing in a prompt that
    claims institutional authority, and repeating it per line spends that.

    Neutralized exactly as the live push neutralizes it: a notice quotes what
    people typed — a document's own headings — so the marker must not be
    forgeable from the content side.
    """
    said = "\n".join(str(agent_notice(b)) for b in notices)
    return platform_prompt(strip_platform_notice(said)) if said else ""


def _resume_notice() -> str:
    """The one thing that is true of a turn rather than of its session."""
    return (
        "本轮接着上一轮跑：上一轮中途断了，这是同一件事的继续。"
        "先确认上一轮做到哪了再继续（翻消息记录、git status），别凭印象重做。"
    )


# Mentions are an ENCODED token, not guessed-from-prose: 芝士 (and the composer)
# emit `<@handle>`, which the platform resolves deterministically and the UI
# renders as a chip showing the member's name. A literal "@name" is just text.
_MENTION_RE = re.compile(r"<@([\w-]+)>")

# 群播 tokens (fusion-design §3): `<@all>` / `<@here>` are FIXED-LITERAL
# structured tokens (not natural-language semantics — rule 4), reserved handles
# the composer emits and the platform expands to the topic's roster. @all = the
# whole room; @here = active members (no presence yet, so = all — see below).
MENTION_ALL = "all"
MENTION_HERE = "here"
_SPECIAL_MENTIONS = frozenset({MENTION_ALL, MENTION_HERE})


_TOPIC_REF_RE = re.compile(r"<#([0-9a-fA-F-]{8,})>")

# A topic created from the rail's + has no human-typed title ("新话题"); 芝士 names
# it via `cheese_title` (titles are AI-generated, never deterministically derived
# from human input or the agent's output — see CLAUDE.md).
PLACEHOLDER_TITLE = "新话题"


def _topic_ref_lists(
    topics: list[Topic], *, exclude_id: uuid.UUID
) -> tuple[list[dict], list[dict]]:
    """一次推导出两份话题列表：`(全量解析表, 渲染进 prompt 的子集)`。

    故意成对返回：这两份**必须**从同一批话题推导，且**必须**保持不同。全量那份
    喂给 `expand_mention_names`（`@标题` → `<#id>` 的解析表，含已归档话题）；子集
    那份只喂给 `build_system_prompt`。合成一份就会把"少注入"变成"少了引用能力"
    ——用户自己打 `@某个已归档话题` 会不再变成链接。
    """
    visible = [t for t in topics if t.id != exclude_id and t.kind != TopicKind.root]
    return [{"id": str(t.id), "title": t.title} for t in visible], _prompt_topic_refs(
        visible
    )


def _prompt_topic_refs(topics: list[Topic]) -> list[dict]:
    """渐进式披露：从全量话题里挑出**值得渲染进 system prompt** 的那一小撮。

    只影响 prompt 里列出来的那一段；`expand_mention_names` 拿到的仍是全量列表，
    所以过滤掉的话题（含已归档的）用 `@标题` 照样解析得出 <#id> 链接——少注入是
    纯赚的，不损失任何引用能力。

    剔除三类：
    - 已归档：本项目实测占注入量的 74%，而引用一个几周前归档的话题几乎没有价值；
      需要时 agent 自己查（prompt 那段里给了查法）。
    - 未命名（标题就是占位符）：按标题根本引用不了。
    - 同名：`expand_mention_names` 对同名标题只解析第一个（mentions.py 的 `seen`
      去重），其余会**静默指向错的那一个**。所以同名的**全部剔除**而不是留一个
      ——留一个等于在 prompt 里推荐一个会指错的引用；全部不列，它们仍可通过查询
      拿到 id 后用 <#id> 精确引用。
    """
    live = [
        t
        for t in topics
        if t.status != TopicStatus.archived and t.title != PLACEHOLDER_TITLE
    ]
    titles = Counter(t.title for t in live)
    return [{"id": str(t.id), "title": t.title} for t in live if titles[t.title] == 1]


def _topic_refs(text: str) -> list[str]:
    """`<#topicId>` reference tokens in a message → topic refs (for linkage)."""
    return [f"topic:{tid}" for tid in dict.fromkeys(_TOPIC_REF_RE.findall(text or ""))]


# Canonicalization of friendly "@名字 / @话题名" now lives in app.domain.mentions
# so non-chat write paths (doc PUT, decision, conclusion) share the exact same
# rewrite. Re-exported under the old private name for existing callers/tests.
_expand_mention_names = expand_mention_names


def _resolve_mentions(text: str, roster: list[dict]) -> tuple[list[str], list[str]]:
    """Resolve <@handle> mention tokens against the roster. Returns
    (resolved_handles, unresolved_handles); unresolved = a token whose handle is
    not a member (a hallucinated handle → the platform flags it).

    An EMPTY roster means "this topic exposes no member list" (私聊, or a project
    carrying no explicit member rows), not "nobody is a member": with no list to
    check against a concrete handle can be neither confirmed nor refuted, so it
    is left out of BOTH lists — no notification, and no false 「项目里没有这个
    成员」 accusation against a real teammate.

    ``@all``/``@here`` are unaffected by any of that: they are expanded from the
    TOPIC's roster by :meth:`_notify_mentions` (a DB read), never from this list,
    so an empty list must not silence a broadcast."""
    resolved: list[str] = []
    unresolved: list[str] = []
    if not text:
        return resolved, unresolved
    handles = {m["handle"] for m in roster}
    for h in dict.fromkeys(_MENTION_RE.findall(text)):
        # @all/@here are reserved broadcast tokens — always "resolved" (expanded
        # to the roster by _notify_mentions), never flagged as a bad handle.
        if h in _SPECIAL_MENTIONS or h in handles:
            resolved.append(h)
        elif roster:
            unresolved.append(h)
    return resolved, unresolved


def _block_payload(block_out: BlockOut) -> dict:
    return block_out.model_dump(mode="json")


def _pending_input_blocks(history: list[Block]) -> list[Block]:
    """The messages/attachments no turn has read into a prompt yet.

    轮次边界按**归属**划，不按位置划：一条在轮次运行中到达的人类消息，created_at
    排在那轮 AI 回复之前，所以"最后一条 AI 消息之后"这个窗口会把它切掉 —— 而且
    切掉就再也捡不回来了（那个下标只会往前走）。这里改成挑「没被任何一轮盖过
    consumed 戳」的块，戳由干净收尾的轮次盖上（BlockRepository.mark_consumed）。

    New inputs carry an explicit ``consumed_turn: null`` marker while pending.
    That presence matters: a newer mid-turn input can be receipted before an
    older queued attachment, so no consumed block may act as a positional
    watermark over another tracked input. Legacy blocks have no marker and keep
    the old "after the latest AI message" fallback.

    `history` 已按 created_at 升序。
    """
    legacy_watermark = -1
    for i, b in enumerate(history):
        if looks_like_agent_handle(b.author) and b.kind == BlockKind.message:
            legacy_watermark = i
    return [
        b
        for i, b in enumerate(history)
        if _is_pending_input(b)
        and consumed_turn(b) is None
        and (CONSUMED_TURN_META_KEY in (b.meta or {}) or i > legacy_watermark)
    ]


def _addressed_to(block: Block, handle: str) -> bool:
    """Is this input for the agent `handle`? One without a recipient is for
    whichever agent the room resolves to, which the caller passes in."""
    return (block.meta or {}).get("agent_recipient", {}).get("handle", handle) == handle


def _pending_platform_notices(history: list[Block]) -> list[Block]:
    """What the platform has to say to 芝士 and has not managed to say yet.

    A turn is built on a snapshot taken when the SESSION started — the document,
    the roster, the cards — and the session outlives many turns. Everything that
    can invalidate that snapshot is something the platform did, so the code that
    did it leaves a sentence on the block it was already writing, and this reads
    whatever nobody has read yet.

    No watermark and no legacy fallback, unlike the human window above: a notice
    is pending exactly while it has something to say and no turn has stamped it,
    and blocks written before this existed say nothing to 芝士 at all.
    """
    return [b for b in history if agent_notice(b) and consumed_turn(b) is None]


# 重放可见 (#416). The first notice fires on the third attempt: one retry is
# ordinary (a transient provider error, an auto-resume), two is bad luck, three
# is a pattern worth a line in the room. After that the state is known, so the
# reminder throttles hard — a topic retrying every 5 minutes for an hour must
# not bury the conversation under its own status.
_REPLAY_NOTICE_AT = 3
_REPLAY_NOTICE_EVERY = 10


def _replay_notice(attempt: int, pending: list[Block]) -> str | None:
    """The 现场 line for a batch of messages that keeps being re-sent.

    Returns None when there is nothing worth saying yet — the common case.

    ONE line, and it stays one line at any batch size. It names the count and
    the OLDEST message — the one stuck longest, and the one that identifies the
    batch. "这个话题重试了 5 次" leaves the reader exactly where they started;
    dumping all N messages back into the room turns a status line into a second
    copy of the conversation. The messages are already in the timeline right
    above; the notice only has to point at them.
    """
    if attempt < _REPLAY_NOTICE_AT:
        return None
    if attempt > _REPLAY_NOTICE_AT and attempt % _REPLAY_NOTICE_EVERY != 0:
        return None
    first = pending[0] if pending else None
    if first is None:
        head = ""
    elif first.kind == BlockKind.attachment:
        head = f"，最早的一条是 [{first.author}] 发的图片"
    else:
        text = " ".join((first.content or "").split())
        clipped = f"{text[:24]}…" if len(text) > 24 else text
        head = f"，最早的一条是 [{first.author}]「{clipped}」"
    return (
        f"这 {len(pending)} 条消息已经是第 {attempt} 次送进轮次，"
        f"前面几次都没跑完{head}。"
    )


def _is_pending_input(b: Block) -> bool:
    """A block carrying something a participant said into the room.

    「参与者」而不是「人」：一个 AI 队友在房间里说的一句话，对坐在同一个房间里
    的另一个参与者同样是这一轮要读的输入（结论 1）。挡住「芝士自己这一轮的产
    出」的不是这里，而是写入端 —— agent 署名**且**落在某一轮里的块根本不盖
    pending 标记（`BlockRepository.add`）。
    """
    return is_participant(b.author_type) and b.kind in (
        BlockKind.message,
        BlockKind.attachment,
    )


# What an exhausted relay balance looks like coming back from newapi. It arrives
# as HTTP 429, the same status as a rate limit, but the two need opposite advice:
# a rate limit clears on its own, a spent balance never does.
_OUT_OF_CREDIT_MARKERS = (
    "余额不足",
    "请充值",
    "insufficient balance",
    "insufficient_quota",
    "quota exceeded",
    "billing",
)


def _is_out_of_credit(detail: str | None) -> bool:
    if not detail:
        return False
    lowered = detail.lower()
    return any(m.lower() in lowered for m in _OUT_OF_CREDIT_MARKERS)


def _is_dm(topic: Topic) -> bool:
    """这间房是不是一间私聊。**这是 `is_private` 在这个文件里唯一的读点。**

    私聊是项目内名册两席的房间（结论 19），这一轮凡是「私聊要不一样」的地方，答
    案都从这里推出来，不再各自问一遍那个布尔：同一件事问 N 遍，N 遍的判据就会各
    自漂移，这次退役的正是漂开了的三十处。推出来的是两件事：

    - **这间房没有名册。**私聊不暴露成员列表，`@` 解析不到项目里的第三个人：解
      析表给 `[]`，`@某某` 原样留在正文里，显示成一条「项目中没有这个成员」
      。这一条管的是正文去了哪里，不只是渲染：名册还要往下走进
      `_notify_mentions`，解析到的每个 handle 都会收到一条带正文前 200 字的强提醒。
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


class ChatService:
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
        self._compute.bind_events(self._consume_hook_event, self._set_hook_activity)
        self._compute.bind_receipts(self.confirm_prompt_receipt)
        self._compute.bind_unread_probe(self.oldest_unread_at)
        self._compute.bind_reachability(self._note_reachability)
        # Mid-turn messages whose write the transport accepted but whose
        # UserPromptSubmit receipt has not arrived yet (#539 decision A):
        # topic → [(injected text, block ids, consuming turn)]. The receipt
        # stamps them consumed; until then they stay pending, so a session
        # death replays them (宁可重复不可丢失).
        # The loop clock reading is the fourth field, and it is what
        # `oldest_unread_at` reports: how long something has been waiting is a
        # different question from whether it was written, and only the first one
        # can tell a session that stopped reading from one that is busy.
        self._pending_receipts: dict[
            uuid.UUID, list[tuple[str, list[uuid.UUID], uuid.UUID, float]]
        ] = {}
        # 写进会话、还没等到 harness 说「收下了」的人类消息：topic → [(写下去的
        # 那段文本, 该打 👀 的 block)]。
        #
        # 和 `_pending_receipts` 分开，因为两者回答的是不同的问题。那一份管重放
        # 和「有人在等、会话却不读了」（`oldest_unread_at`）——把一轮新对话塞进
        # 去，冷启动那一分多钟就会读成「会话不读了」，而那时根本还没有会话。这一
        # 份只管屏幕上那个记号落在哪条消息上，落完就没了。
        self._awaiting_seen: dict[
            uuid.UUID, list[tuple[str, list[uuid.UUID], str | None]]
        ] = {}
        # Per-project ExecutionProfile (model + provider). None → always the
        # agent's built-in default (tests / single-profile deploys).
        self._profiles = profiles
        # LiteLLM gateway ADMIN client (L1/L2 — defined in
        # `app.domain.agent.gateway`). None = off.
        # The lock serializes key-mint and usage-drain read-modify-writes on
        # project.settings (single-process reality, like the topic locks).
        self._gateway = gateway
        self._gateway_lock = asyncio.Lock()
        # Keep the publication contract present before native skills are invoked.
        self._skills = NATIVE_CHAT_GUIDANCE
        # Prompt construction is serialized per topic. The lock is released as
        # soon as an interactive provider injects the prompt; non-interactive
        # providers still hold it while running because they cannot accept a
        # second message into a live screen.
        self._topic_locks: dict[uuid.UUID, asyncio.Lock] = {}
        # Work currently attributed to each active session. Mid-session delivery
        # captures this id before writing to the lower layer, then stamps the
        # message only after the exact UserPromptSubmit receipt.
        self._active_turn_ids: dict[uuid.UUID, uuid.UUID] = {}
        self._hook_work: dict[tuple[uuid.UUID, uuid.UUID], _HookWorkState] = {}
        # The notice each open turn is keeping current: a streak of retries, a
        # wait for its machine. One line per streak, restated as it moves on.
        self._retry_notes: dict[uuid.UUID, uuid.UUID] = {}
        self._waiting_notes: dict[uuid.UUID, uuid.UUID] = {}
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

    @property
    def session_factory(self) -> async_sessionmaker:
        return self._sessions

    def _lock_for(self, topic_id: uuid.UUID) -> asyncio.Lock:
        lock = self._topic_locks.get(topic_id)
        if lock is None:
            lock = asyncio.Lock()
            self._topic_locks[topic_id] = lock
        return lock

    @asynccontextmanager
    async def _prompt_lock(
        self,
        topic_id: uuid.UUID,
        work_id: uuid.UUID,
        recipient_handle: str | None = None,
    ) -> AsyncIterator[None]:
        async with self._lock_for(topic_id):
            if recipient_handle is not None:
                await self.wait_for_recipient(topic_id, recipient_handle)
            self._active_turn_ids[topic_id] = work_id
            try:
                yield
            finally:
                if (
                    self._active_turn_ids.get(topic_id) == work_id
                    and (topic_id, work_id) not in self._hook_work
                ):
                    self._active_turn_ids.pop(topic_id, None)

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
            # System-initiated turn (重发 / 评论叫醒 / 冲突调度…): no human
            # spoke — the opener is a SYSTEM event in the 现场, and the
            # instruction goes straight to the agent as the prompt.
            #
            # 平台提示统一契约: `nudge_event` is the one line the room sees,
            # `nudge_meta` its structured payload — which is where a caller puts
            # the长文 (CI 日志 / 检查输出 / 冲突文件清单) so the room stays
            # glanceable while nothing is lost. `content` is untouched: it is
            # still the whole instruction 芝士 gets as its prompt.
            if is_resume and not nudge_event:
                nudge_event = resume_reason or "平台重发了上一轮的消息"
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

            # 芝士's 👀 is NOT placed here. This point is "the platform took
            # the message" — the delivery below has not been attempted yet, and
            # the branch right after this one handles it FAILING. A mark put
            # here says the AI has the message while the message may still end
            # up back in the queue.
            #
            # It is placed where the harness says the session took the input:
            # `arm_seen_receipt` names the blocks, `confirm_prompt_receipt`
            # places the mark. That receipt is harness-independent — Claude
            # Code's UserPromptSubmit hook, pi's and codex's app-server response
            # — and every one of them means the same thing: it is in front of
            # 芝士 now.

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
        async with self._prompt_lock(topic_id, turn_id, recipient_handle):
            async for frame in self._converse_impl(
                topic_id=topic_id,
                content=content,
                turn_id=turn_id,
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
    ) -> AsyncIterator[dict]:
        """Run the AI half of a human message that is already durable.

        ``InProcessBroker.receive_message`` owns the receive-before-admission ordering;
        this method starts only after the project gate admits the model work.
        """
        async with self._prompt_lock(topic_id, turn_id, recipient_handle):
            async for frame in self._converse_impl(
                topic_id=topic_id,
                content=content,
                turn_id=turn_id,
                user_block_id=user_block_id,
                continuation_id=continuation_id,
                provision_actor=provision_actor,
            ):
                yield frame

    async def _reply_parent(self, block_ids: list[uuid.UUID]) -> Block | None:
        """The message a just-posted send answers, if it is a reply.

        Read back from the stored blocks rather than from the request: the
        write already dropped a reply target outside this room."""
        async with self._sessions() as session:
            blocks = BlockRepository(session)
            for block_id in block_ids:
                block = await blocks.get(block_id)
                if block is not None and block.reply_to is not None:
                    return await blocks.get(block.reply_to)
        return None

    async def merge_into_running_turn(
        self,
        topic_id: uuid.UUID,
        user_block_ids: list[uuid.UUID],
        content: str,
        author: str,
        attachments: list[dict] | None = None,
        recipient_handle: str | None = None,
    ) -> bool | None:
        """Inject a just-posted human message into the turn already running on
        this topic.

        ``True`` means the live session acknowledged the message, ``False``
        means live delivery was attempted but failed, and ``None`` means no live
        work remained by the time this method checked. Callers use that third
        state to distinguish a normal new message from a raced fallback.

        The text is labelled the same way `prompt_line` labels a pending block,
        so a message that arrives mid-turn reads identically to one that came in
        the prompt — 芝士 must not have to tell the two apart to know who spoke.

        Images use the same @path input path as an initial prompt. Interactive
        providers resolve that path into a native image block before the model
        sees the message; a remote device first stages the exact bytes and acks
        the file write."""
        consuming_turn_id = self._active_turn_ids.get(topic_id)
        if consuming_turn_id is None:
            return None
        state = self._hook_work.get((topic_id, consuming_turn_id))
        if (
            recipient_handle is not None
            and state is not None
            and state.agent_instance_handle is not None
            and state.agent_instance_handle != recipient_handle
        ):
            return None
        lines = []
        if content:
            lines.append(f"[{author}]: {strip_platform_notice(content)}")
        images = [
            {
                "path": str(attachment.get("path") or ""),
                "media_type": str(attachment.get("mime") or "image/png"),
            }
            for attachment in attachments or []
            if attachment.get("path")
        ]
        lines.extend(
            attachment_prompt_line(
                author, image["path"], embeds_images=True, mime=image["media_type"]
            )
            for image in images
        )
        if (replied := await self._reply_parent(user_block_ids)) is not None:
            lines.append(
                reply_quote(replied, recipient=state.acting_agent if state else None)
            )
        state = self._hook_work.get((topic_id, consuming_turn_id))
        line = publication_prompt("\n".join(lines))
        # Register BEFORE the write so a fast receipt cannot race the entry
        # (#539 decision A). The receipt is still the consumed boundary — it
        # just no longer gates the delivery verdict: write-accept is delivery,
        # and the stamp lands whenever the session actually consumes the text
        # (confirm_prompt_receipt). Until then the message stays pending, so a
        # session death replays it — 宁可重复不可丢失.
        pending = self._pending_receipts.setdefault(topic_id, [])
        # 同一个回执，两件事：把消息标记成被消费（上面那段说的重放边界），以及在
        # 它身上落下 👀。人说的话才有记号——平台自己塞进去的通知走的是同一条登记，
        # 但它不是谁发的消息，不该被 ack。
        self.arm_seen_receipt(
            topic_id,
            line,
            list(user_block_ids),
            by=state.acting_agent if state else None,
        )
        entry = (
            line,
            list(user_block_ids),
            consuming_turn_id,
            asyncio.get_running_loop().time(),
        )
        pending.append(entry)
        del pending[:-16]  # a dead session must not grow this forever
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
                delivered = (
                    await self._compute.deliver(topic_id, line, images=images)
                    if images
                    else await self._compute.deliver(topic_id, line)
                )
        except Exception:  # noqa: BLE001 — caller reports the queued fallback
            logger.exception("merge into running turn failed (topic=%s)", topic_id)
            delivered = False
        if not delivered:
            if entry in pending:
                pending.remove(entry)
            return False
        return True

    async def wait_for_recipient(
        self, topic_id: uuid.UUID, recipient_handle: str
    ) -> bool:
        from app.domain.agent.runtime import get_broker

        waited = False
        async with get_broker().subscribe(str(topic_id)) as events:
            while True:
                work_id = self._active_turn_ids.get(topic_id)
                if work_id is None:
                    return waited
                state = self._hook_work.get((topic_id, work_id))
                if (
                    state is None
                    or state.agent_instance_handle is None
                    or state.agent_instance_handle == recipient_handle
                ):
                    return waited
                waited = True
                await events.get()

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
            and self._active_turn_ids.get(state.topic_id) == state.work_id
        ]

        async def remind(state: _HookWorkState) -> bool:
            if (
                self._hook_work.get((state.topic_id, state.work_id)) is not state
                or self._active_turn_ids.get(state.topic_id) != state.work_id
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
                return delivered
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
    ) -> bool:
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
        consuming_turn_id = self._active_turn_ids.get(topic_id)
        if consuming_turn_id is None:
            return False
        if recipient_seat is not None:
            state = self._hook_work.get((topic_id, consuming_turn_id))
            if state is None or state.acting_agent != recipient_seat:
                return False
        line = platform_prompt(strip_platform_notice(notice))
        if blocks:
            # Registered BEFORE the write, for the reason the human-message path
            # registers first: a fast receipt must not race its own entry.
            pending = self._pending_receipts.setdefault(topic_id, [])
            pending.append(
                (
                    line,
                    list(blocks),
                    consuming_turn_id,
                    asyncio.get_running_loop().time(),
                )
            )
            del pending[:-16]  # a dead session must not grow this forever
        try:
            return bool(
                await self._compute.deliver(
                    topic_id, line, expected_work_id=consuming_turn_id
                )
            )
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
        pending = self._pending_receipts.get(topic_id)
        if not pending:
            return None
        return min(entry[3] for entry in pending)

    async def confirm_prompt_receipt(self, topic_id: uuid.UUID, prompt: str) -> None:
        """A UserPromptSubmit receipt from the topic's screen: the session
        consumed an input. If it is one we injected mid-turn, stamp its blocks
        consumed now — this is the boundary that keeps the next turn's pending
        window honest. A provider may append native-image mentions to the text
        it types, so the receipt matches on equality or on carrying our text
        as its prefix."""
        # 落记号是尽力而为的，而它下面那段是功能性的（把消息标记成已消费，也就是
        # 下一轮不再重发它的那个边界）。一个纯装饰的东西不许把功能路径带下去：
        # 记号丢了只是少一个 👀，标记丢了会让这条消息在下一轮被重发一遍。
        try:
            await self._place_seen_receipts(topic_id, prompt)
        except Exception:  # noqa: BLE001 — the consumed stamp matters more
            logger.exception("failed to place the seen receipt (topic=%s)", topic_id)
        pending = self._pending_receipts.get(topic_id)
        if not pending:
            return
        for entry in pending:
            text, block_ids, consuming_turn_id, _written_at = entry
            if prompt == text or (text and prompt.startswith(text)):
                pending.remove(entry)
                try:
                    async with self._sessions() as session:
                        await BlockRepository(session).mark_consumed(
                            block_ids, consuming_turn_id
                        )
                        from app.domain.delivery.agent import receive_attempt

                        await receive_attempt(
                            session, consuming_turn_id, datetime.now(UTC)
                        )
                        await session.commit()
                    logger.info(
                        "prompt receipt matched an injected message — %d "
                        "block(s) stamped consumed (topic=%s, turn=%s)",
                        len(block_ids),
                        topic_id,
                        consuming_turn_id,
                    )
                except Exception:  # noqa: BLE001 — a failed stamp just replays
                    logger.exception(
                        "consumed stamp failed on receipt (topic=%s)", topic_id
                    )
                return

    def session_controls(self, topic_id: uuid.UUID):
        """The runtime whose live session in this room takes controls, if any."""
        return self._compute.session_controls(topic_id)

    async def recover_native_tools(
        self, topic_id: uuid.UUID, agent_handle: str | None = None
    ) -> bool:
        """Platform tools back for this room; True when they had been gone."""
        return await self._compute.recover_native_tools(topic_id, agent_handle)

    def has_running_turn(self, topic_id: uuid.UUID) -> bool:
        """Whether this process currently owns live work for the topic."""
        return topic_id in self._active_turn_ids

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

    @asynccontextmanager
    async def edit_environment(self, topic_id: uuid.UUID) -> AsyncIterator[None]:
        """Prevent a new prompt from racing an explicit environment change."""
        lock = self._lock_for(topic_id)
        if lock.locked() or self.has_running_turn(topic_id):
            raise ValidationError("房间正在工作，请结束当前工作后再应用环境配置")
        async with lock:
            if self.has_running_turn(topic_id):
                raise ValidationError("房间正在工作，请稍后重试")
            yield

    def session_took_over(self, topic_id: uuid.UUID, turn_id: uuid.UUID) -> bool:
        """Did the live session take responsibility for THIS turn's indicator?

        The `session_lifecycle` frame says a session will own the ending; it
        does not say which turn's. When one is already running on the topic, the
        session reuses its activity rather than opening a second — so the newer
        turn gets no start of its own and will get no ending either. Asking by
        turn id is the difference between a handover and an assumption.
        """
        return self._active_turn_ids.get(topic_id) == turn_id

    async def post_system_event(
        self,
        topic_id: uuid.UUID,
        content: str,
        turn_id: uuid.UUID | None = None,
        *,
        meta: dict | None = None,
    ) -> dict | None:
        """Persist a system event into the room (e.g. a turn failure): visible in
        the conversation, scrolls with it, and survives a reload — unlike a
        transient banner. It carries no ``meta.in_room``, and absent means shown,
        which is the whole point of this call: the platform says it out loud.
        Returns the block payload, or None if the topic died.

        Room-only: every caller here reports something about the room itself
        (a turn that failed, an environment that was rebuilt), which nobody was
        named for. A notice that knows whom it points at passes `points_at`
        to `announce` directly (`points_at=Event(...)`)."""
        async with self._sessions() as session:
            block = await announce(
                session,
                place_id=topic_id,
                content=content,
                meta=meta,
                turn_id=turn_id,
            )
            if block is None:
                return None
            payload = _block_payload(BlockOut.model_validate(block))
            await session.commit()
        return payload

    async def cloud_waiting_topics(self, topic_ids: list[uuid.UUID]) -> list[uuid.UUID]:
        """Topics whose latest durable Cloud lifecycle event is still waiting."""
        async with self._sessions() as session:
            return await cloud_waiting_topics(session, topic_ids)

    async def work_policy(self, topic_id: uuid.UUID) -> dict | None:
        """Admission facts the AgentWorkRunner gates on BEFORE running a turn
        (spec §9.1 算力额度): the owning project, its concurrency ceiling, and
        whether its compute credits are exhausted. None when the topic doesn't
        exist (the turn itself will surface the 404)."""
        async with self._sessions() as session:
            topic = await TopicRepository(session).get(topic_id)
            if topic is None:
                return None
            project = await ProjectRepository(session).get(topic.project_id)
            balance = await ComputeGrantRepository(session).summary(topic.project_id)
        max_concurrent = settings.max_concurrent_turns
        override = ((project.settings if project else None) or {}).get(
            "max_concurrent_turns"
        )
        if isinstance(override, int) and override > 0:
            max_concurrent = override
        return {
            "project_id": str(topic.project_id),
            "max_concurrent_turns": max_concurrent,
            # A project with no grants is unlimited (spec §4 自治项目不设限).
            "credits_exhausted": (
                not balance["unlimited"] and balance["credits_remaining"] <= 0
            ),
        }

    async def _close_open_turns(self, topic_id: uuid.UUID) -> None:
        """End every open interval on this topic. Never raises — a Stop that
        cannot update the bookkeeping must still land the message it carries."""
        from datetime import UTC, datetime

        from app.domain.agent.repositories import AgentTurnRepository

        try:
            async with self._sessions() as session:
                closed = await AgentTurnRepository(session).close_for_topic(
                    topic_id, datetime.now(UTC)
                )
                if closed:
                    await session.commit()
        except Exception:  # noqa: BLE001 — the Stop matters more than the row
            logger.exception("could not close open turns for topic %s", topic_id)

    async def note_credits_refusal(self, topic_id: uuid.UUID) -> None:
        """Admission just refused a `/v1/messages` call at this place because
        the project's compute credits are spent (#715). If a turn is running
        here, stamp it once and say so in the room right away — the same
        `CREDITS_EXHAUSTED_EVENT` a turn-start refusal already posts — instead
        of waiting for Claude Code's ten retries to end in `StopFailure` with a
        reading of the 429 that says "Invalid API key".

        Nothing to stamp (no turn in flight at this place) is not an error:
        the turn-start refusal path already covers a turn that has not begun.
        Never raises — the proxy fails OPEN on an admission error, so a
        bookkeeping bug here must not become a reason to let a refused turn
        through.
        """
        from datetime import UTC, datetime

        from app.domain.agent.repositories import AgentTurnRepository
        from app.domain.usage.credits import (
            CREDITS_EXHAUSTED_EVENT,
            CREDITS_EXHAUSTED_META,
        )

        turn_id: uuid.UUID | None = None
        try:
            async with self._sessions() as session:
                repo = AgentTurnRepository(session)
                turn_id = await repo.open_turn_id_for_topic(topic_id)
                if turn_id is None:
                    return
                flipped = await repo.mark_credits_refused(turn_id, datetime.now(UTC))
                if flipped:
                    await session.commit()
        except Exception:  # noqa: BLE001 — see docstring
            logger.exception("could not stamp credits-refused for topic %s", topic_id)
            return
        if not flipped or turn_id is None:
            return
        try:
            payload = await self.post_system_event(
                topic_id,
                CREDITS_EXHAUSTED_EVENT,
                turn_id,
                meta=CREDITS_EXHAUSTED_META,
            )
            if payload is not None:
                from app.domain.agent.runtime import get_broker

                await get_broker().publish(
                    str(topic_id),
                    {
                        "type": "error",
                        "message": CREDITS_EXHAUSTED_EVENT,
                        "persisted": True,
                    },
                )
        except Exception:  # noqa: BLE001 — see docstring
            logger.exception(
                "could not post credits-refused notice for topic %s", topic_id
            )

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

    def has_live_screen(self, topic_id: uuid.UUID) -> bool:
        """Is a session for this topic still reachable? The orphan sweep's first
        question, and the one that used to be unanswerable."""
        return self._compute.holds(topic_id)

    def worker_live(self, topic_id: uuid.UUID, agent_id: str | None) -> bool | None:
        """Is the worker bound to this task still doing it?

        True when the room's session reported that agent starting and has not
        taken it back, None when no one is claiming anything about it — never
        mentioned, or already handed something back. The board takes this as the
        strongest evidence it can get (`TaskFacts.worker_live`): a worker can go
        quiet for forty minutes without being dead — that is what running a long
        command looks like — and only the thing running it can tell the two
        apart. The timestamps stay for the None case, where nobody has spoken.

        The session that made a claim must still be the room's session for the
        claim to hold: this process outlives screens.
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
        while any(self._pending_receipts.values()) and time.monotonic() < deadline:
            await asyncio.sleep(0.2)
        await self._compute.stop_listening()

    async def recover_sessions(self, device_id: str | None = None) -> int:
        """Listen again to sessions that outlived this process, and land what
        they said while nobody was.

        Two calls to the runtime, and the split is deliberate: ``recover``
        establishes that we are listening, ``replay`` hands over the tail. What
        the room already shows is ours to supply; which of the harness's own
        records are still unlanded is its.
        """
        sessions = await self._compute.recover_sessions(device_id)
        unique = {session.topic_id: session for session in sessions}
        for session in unique.values():
            try:
                # A turn still running there was fed by a process that is gone,
                # and its result lands here. Without its bookkeeping that result
                # closes nothing: the batch it answered is never stamped
                # consumed, and the next turn sends it again.
                work = self._compute.work_in_flight(session.topic_id)
                if work is not None and (session.topic_id, work) not in self._hook_work:
                    await self._begin_self_started_turn(
                        session.project_id,
                        session.topic_id,
                        work,
                        opened=True,
                        agent_handle=session.agent_handle or None,
                    )
                # What the room already shows, so a message the live path DID
                # persist before this process died is not landed twice. The room
                # is ours; which of the harness's own records are still unlanded
                # is the harness's.
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
                # The machine is there and said no — its runner's socket is not
                # up yet (a cold one takes about a minute), or the room's home
                # is gone. Same standing as the machine being away: the next
                # connection recovers this session, and the machine's own words
                # are what somebody reading this would act on.
                logger.warning(
                    "session not recovered for topic %s: %s", session.topic_id, exc
                )
            except Exception:  # noqa: BLE001 — one topic cannot block startup
                logger.exception(
                    "session recovery failed for topic %s", session.topic_id
                )
        return len(unique)

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
    ) -> None:
        """Project subscription activity onto the existing realtime protocol."""
        del project_id
        from app.domain.agent.runtime import get_broker

        if active:
            self._active_turn_ids[topic_id] = work_id
            frame = {"type": "turn_started", "turn_id": str(work_id)}
        else:
            if self._active_turn_ids.get(topic_id) == work_id:
                self._active_turn_ids.pop(topic_id, None)
            frame = {"type": "turn_finished", "turn_id": str(work_id)}
        await get_broker().publish(str(topic_id), frame)
        if not active:
            # The agent has said what it understood: the moment to check the
            # name the room got from its opening line (topic/naming.py).
            naming.nudge(topic_id, "turn")

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
        recorded under the default holds every message addressed to that
        teammate in ``wait_for_recipient`` until the turn ends by itself.

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
                acting_agent = await self._agent_handle(session, topic_id)
                row = (
                    await AgentTurnRepository(session).get(turn_id) if opened else None
                )
            if not opened:
                await get_work_runner().open_turn_the_session_started(
                    self, topic_id, turn_id, author=acting_agent
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
            self._active_turn_ids[topic_id] = turn_id
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
                content=f"<@{state.acting_agent}> {_ACTION_LABEL[resource]}",
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
        self, turn_id: uuid.UUID, *, route: str, reply_to: uuid.UUID | None
    ) -> None:
        """Best-effort, like the delivery stamp: losing it costs a turn picked
        up by another backend its reply link and the accuracy of one route
        label, never the turn."""
        from app.domain.agent.repositories import AgentTurnRepository

        try:
            async with self._sessions() as session:
                await AgentTurnRepository(session).note_context(
                    turn_id, route=route, reply_to=reply_to
                )
                await session.commit()
        except Exception:  # noqa: BLE001 — bookkeeping must not stop a turn
            logger.exception("could not record the context of turn %s", turn_id)

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
        """Persist and broadcast one event from a live screen subscription."""
        from app.api.deps import get_work_runner
        from app.domain.agent.runtime import get_broker

        broker = get_broker()
        frame: dict | None = None
        state = self._hook_work.get((topic_id, turn_id))
        if state is None and platform_unsolicited and proves_output([event]):
            # Nobody fed this session anything and it is producing output anyway
            # — one of its workers finished and the completion notice woke it.
            # That is a whole turn, and it gets a turn's bookkeeping from here:
            # an interval a sweep can find, and the context its Stop needs to
            # close the books. Opened on OUTPUT rather than on the first hook of
            # any kind, because only output guarantees the Stop that closes it.
            state = await self._begin_self_started_turn(project_id, topic_id, turn_id)
        # Whose work this is. Deliberately NOT asked of AgentResult: that event
        # is the turn ending, which is the session's business no matter what id
        # rode in on it — re-addressing it would close a turn somewhere else.
        task_id = (
            None
            if isinstance(event, AgentResult)
            else await self._work_of_worker(
                topic_id, getattr(event, "thread_label", None)
            )
        )
        # A thread's own channel is what its view subscribes to, and it is the
        # room's when there is no thread. Attributed frames must not go out on
        # the room's channel: the block lands in the thread, so a live watcher
        # would see an event that a reload then moves somewhere else.
        channel = str(task_id) if task_id is not None else str(topic_id)
        if not isinstance(event, AgentRetrying):
            # Anything else the turn does ends a streak of retries: the request
            # went through. The next retry is news of its own.
            self._retry_notes.pop(turn_id, None)
        if isinstance(event, AgentResult):
            self._waiting_notes.pop(turn_id, None)
        if isinstance(event, AgentSessionInfo):
            self._note_room_session(topic_id, event.session_id)
            await self._save_session_pointer(
                topic_id,
                event.session_id,
                agent_handle=event.agent_handle,
                harness=event.harness,
            )
        elif isinstance(event, AgentSubagentStart | AgentSubagentStop):
            self._note_worker_agent(topic_id, event)
            payload = await self._persist_worker_event(
                project_id=project_id,
                topic_id=topic_id,
                event=event,
                task_id=task_id,
                turn_id=turn_id,
                eid=eid,
                platform_unsolicited=platform_unsolicited,
            )
            if payload is not None:
                frame = {"type": "event_block", "block": payload}
        elif isinstance(event, AgentMessage):
            payload = await self._persist_assistant_message(
                project_id=project_id,
                topic_id=topic_id,
                text=event.text,
                turn_id=turn_id,
                reply_to=(
                    state.reply_to
                    if state is not None and state.assistant_count == 0
                    else None
                ),
                roster=state.roster if state is not None else None,
                topic_refs=state.topic_refs if state is not None else [],
                eid=eid or event.eid,
                eids=event.eids,
                platform_unsolicited=platform_unsolicited,
                continuation_id=(state.continuation_id if state is not None else None),
                at=event.at,
                author=event.agent_handle
                or (state.acting_agent if state is not None else None),
                task_id=task_id,
            )
            if payload is not None:
                frame = {"type": "event_block", "block": payload}
        elif isinstance(event, AgentToolUse):
            name = _short_tool_name(event.name)
            args = event.input or {}
            payload = await self._persist_tool_event(
                project_id=project_id,
                topic_id=topic_id,
                name=name,
                tool_input=args,
                platform=_is_platform_tool(event.name, args),
                turn_id=turn_id,
                eid=eid or event.eid,
                platform_unsolicited=platform_unsolicited,
                task_id=task_id,
            )
            if payload is not None:
                frame = {"type": "event_block", "block": payload}
                if state is not None and event.call_id:
                    state.steps[event.call_id] = uuid.UUID(payload["id"])
            resource = _TOOL_ACTION.get(name)
            if state is not None and resource is not None:
                await self._announce_action(state, resource)
        elif isinstance(event, AgentStepFailed):
            # It changes a line that is already on the timeline rather than
            # adding one, so it goes out as that line, restated. A step whose
            # call we never saw (a restart mid-turn) is simply not marked — the
            # timeline is still true, just less helpful.
            block_id = state.steps.get(event.call_id) if state is not None else None
            if block_id is not None:
                payload = await self._mark_step_failed(block_id, event.text)
                if payload is not None:
                    frame = {"type": "block_updated", "block": without_output(payload)}
        elif isinstance(event, AgentStepOutput):
            # Written onto the step, like a failure. The frame says only that
            # the step now has output: the text is read when somebody opens it.
            block_id = state.steps.get(event.call_id) if state is not None else None
            if block_id is not None:
                payload = await self._record_step_output(block_id, event.text)
                if payload is not None:
                    frame = {"type": "block_updated", "block": without_output(payload)}
        elif isinstance(event, AgentRetrying):
            await self._note_retry(
                topic_id,
                turn_id,
                event,
                author=state.acting_agent if state is not None else None,
                task_id=task_id,
                channel=channel,
            )
        elif isinstance(event, AgentToolResult):
            payload = await self._persist_subagent_result(
                project_id=project_id,
                topic_id=topic_id,
                event=event,
                turn_id=turn_id,
                eid=eid,
                platform_unsolicited=platform_unsolicited,
                task_id=task_id,
            )
            if payload is not None:
                frame = {"type": "event_block", "block": payload}
        elif isinstance(event, AgentResult):
            error_line, error_code = "", None
            if event.session_id:
                await self._save_session_pointer(
                    topic_id,
                    event.session_id,
                    agent_handle=event.agent_handle,
                    harness=event.harness,
                )
            if event.is_error:
                if event.text.strip() == TURN_TIMEOUT_MESSAGE:
                    # The watchdog's own verdict, and the only failure whose
                    # session may still be alive — everything downstream of it
                    # is different, so it keeps its own card. Recognised by the
                    # message because that is what the watchdog emits; nothing
                    # overrides it today.
                    line, meta = (
                        event.text,
                        notice(
                            EVENT_TURN_TIMEOUT,
                            severity=SEVERITY_WARN,
                            who=WHO_HUMAN,
                            detail="会话活动已停止；屏幕订阅仍会接收后续输出。",
                            detail_label="详细说明",
                        ),
                    )
                elif await self._turn_credits_refused(turn_id):
                    # Admission already told the room WHY this turn is ending
                    # (#715): it refused every `/v1/messages` call for spent
                    # credits, and Claude Code's own reading of that refusal
                    # — "Invalid API key" — is wrong advice for a spent
                    # balance. Repeat the platform's own line rather than
                    # Claude Code's text, however the hook happened to word it.
                    from app.domain.usage.credits import (
                        CREDITS_EXHAUSTED_EVENT,
                        CREDITS_EXHAUSTED_META,
                    )

                    line, meta = CREDITS_EXHAUSTED_EVENT, CREDITS_EXHAUSTED_META
                else:
                    line, meta = _turn_failure_notice(event.text, event.failure_code)
                error_line, error_code = line, meta.get("code")
                payload = await self.post_system_event(
                    topic_id, line, turn_id, meta=meta
                )
                if payload is not None:
                    frame = {"type": "event_block", "block": payload}
            elif event.text.strip():
                # Terminal output, the final response included, stays in
                # activity: a reply reaches the room only through chat_send,
                # in a private chat exactly as in any other room.
                payload = await self._persist_assistant_message(
                    project_id=project_id,
                    topic_id=topic_id,
                    text=event.text,
                    turn_id=turn_id,
                    reply_to=state.reply_to if state is not None else None,
                    roster=state.roster if state is not None else None,
                    topic_refs=state.topic_refs if state is not None else [],
                    author=event.agent_handle
                    or (state.acting_agent if state is not None else None),
                    eid=eid,
                    platform_unsolicited=platform_unsolicited,
                    continuation_id=(
                        state.continuation_id if state is not None else None
                    ),
                )
                if payload is not None:
                    if state is not None:
                        state.assistant_count += 1
                    frame = {"type": "event_block", "block": payload}
        if frame is not None:
            await broker.publish(channel, frame)
            if frame["type"] in ("assistant_block", "event_block"):
                get_work_runner().note_session_output(
                    turn_id, tool=isinstance(event, AgentToolUse)
                )
        if isinstance(event, AgentResult):
            # 投喂 → Stop is the interval. Closing it HERE, rather than where the
            # turn's own coroutine ends, is what lets a turn survive the backend
            # being replaced under it: the screen kept working, the subscription
            # reattached, and its Stop closes the books exactly as it would have
            # if nothing had happened. A turn whose coroutine is alive closes the
            # same row a moment later and finds it already closed, which is the
            # correct answer either way.
            await self._close_open_turns(topic_id)
            if state is not None:
                try:
                    for close_frame in await self._close_hook_work(state, event):
                        await broker.publish(str(topic_id), close_frame)
                except Exception:  # noqa: BLE001 — Stop must close room state
                    logger.exception(
                        "hook work close failed (topic=%s, work=%s)",
                        topic_id,
                        turn_id,
                    )
                finally:
                    self._hook_work.pop((topic_id, turn_id), None)
                    if state.self_started:
                        # No coroutine owns this one, so there is no `finally`
                        # anywhere else to drop the marks it left in the runner.
                        get_work_runner().close_turn_the_session_started(turn_id)
            elif event.is_error:
                await self._forget_room_claims(topic_id)
            if event.is_error:
                frame_out = {
                    "type": "error",
                    "message": error_line or event.text,
                    "persisted": True,
                }
                if error_code:
                    frame_out["code"] = error_code
                await broker.publish(str(topic_id), frame_out)
            await broker.publish(str(topic_id), {"type": "done"})

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
            if not usages:
                await UsageRepository(session).add(
                    project_id=state.project_id,
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
                for u in usages:
                    await UsageRepository(session).add(
                        project_id=state.project_id,
                        topic_id=state.topic_id,
                        model=u.model or state.model or settings.agent_model,
                        input_tokens=u.input_tokens,
                        output_tokens=u.output_tokens,
                        cost_usd=u.cost_usd,
                        route=state.route,
                        turn_id=state.work_id,
                    )
                    await ComputeGrantRepository(session).consume(
                        state.project_id,
                        usage_to_credits(u, spend_priced=state.route == "gateway"),
                    )
            # What this session was fed, including by a process that is gone.
            # Settled either way: a failed session must also drop the batches
            # an earlier process fed it, or a later clean Stop would read them
            # as heard — and a self-started turn's own set is always empty.
            fed = list(
                state.pending_ids | set(await self._delivered_unread(session, state))
            )
            if not result.is_error:
                await blocks.mark_consumed(fed, state.work_id)
            else:
                await blocks.forget_prompted_turn(fed)
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
    ) -> tuple[list[dict], uuid.UUID, list[uuid.UUID], bool]:
        """Persist the human message (+ its image attachment blocks) and the
        @mention notifications in one short transaction, outside any turn lock.
        Returns (payloads, anchor_block_id, all_block_ids, duplicate) — the
        anchor is what 芝士's reply threads under; all ids are consumed together
        after a mid-session delivery receipt. ``duplicate`` means the browser
        retried a delivery whose durable result is being echoed again.
        """
        async with self._sessions() as session:
            topics = TopicRepository(session)
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
            agent_handles = (
                await TopicMemberService(session).agent_handles(topic.id)
                if "@" in content
                else []
            )
            # Which agent each seat belongs to — 名册上 @ 到的是席位，而这一轮要跑
            # 起来的是它背后那个实例（记忆池的 key、署名用的 handle 都在实例上）。
            # 房间可以坐好几位，所以这张表按席位建，不按房间（#1192）。
            by_seat = (
                {
                    agent_instance_handle(instance.id): instance
                    for instance in await AgentInstanceService(
                        session
                    ).list_for_project(topic.project_id)
                }
                if agent_handles
                else {}
            )
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
                # Same backstop the doc/chat-reply paths already had, but the
                # human chat-send path used to skip it: a friendly "@Alice /
                # @handle / @话题名" is canonicalized into the structured token
                # (<@alice> / <#id>) BEFORE the block is stored, so it renders
                # as a clickable chip instead of leaking raw "@Alice" text.
                # 私聊没有名册可以解析（也不暴露成员列表），原样存下来。
                # 队友已经在这张名册上（``membership/roster.py``），每一位带着自己
                # 的名字，所以名字不再另拼一份——拼出来的那份就是第二份声明。
                roster = (
                    []
                    if "@" not in content or _is_dm(topic)
                    else await roster_rows(session, topic.project_id)
                )
                # 这间房真正坐着的 AI 席位先答这个名字：排到名册最前，名册上没有它
                # 的补一行。一间还挂着共用 `cheese` 席位的老房间（那一步是惰性的，
                # 等这间房的 agent 下次动手才迁，见 `migrate_shared_agent_seat`）在
                # 项目名册上没有对应的行——名册上叫「芝士」的是项目的默认实例，
                # 「@芝士」展开成它就等于 @ 了一个没坐在这间房里的队友：这一轮起不
                # 来，通知还发给了它。反过来也成立：成员表里历史上落过的一行
                # `cheese` 会以人的身份排在名册最前，在**正常**房间里把「@芝士」抢
                # 成 <@cheese>。谁坐在这间房里，谁先答。
                # 这是同一次读的一个渲染顺序，不是第二份名册——和 `roster_rows()`
                # 的定位一致。
                if roster and agent_handles:
                    row_of = {row["handle"]: row for row in roster}
                    seated = set(agent_handles)
                    roster = [
                        row_of[handle]
                        if handle in row_of
                        else {
                            "handle": handle,
                            "name": (
                                by_seat[handle].display_name
                                if handle in by_seat
                                else agent.display_name
                            ),
                        }
                        for handle in agent_handles
                    ] + [row for row in roster if row["handle"] not in seated]
                if roster:
                    topic_refs = [
                        {"id": str(t.id), "title": t.title}
                        for t in await topics.list_for_project(topic.project_id)
                        if t.kind != TopicKind.root and t.id != topic.id
                    ]
                    content = expand_mention_names(content, roster, topic_refs)
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
                    },
                )
                if attribution_id is None:
                    attribution_id = user_block.id
                    user_block.turn_id = attribution_id
                # Resolve <@handle> mentions in the human message → strong notify.
                resolved, _unresolved = await self._notify_mentions(
                    session, topic, author, content, roster
                )
                refs = [f"user:{h}" for h in resolved] + _topic_refs(content)
                if refs:
                    user_block.refs = refs
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

    #: How many un-marked messages one topic keeps waiting for a receipt.
    _SEEN_RECEIPT_BACKLOG = 8

    def arm_seen_receipt(
        self,
        topic_id: uuid.UUID,
        text: str,
        block_ids: list[uuid.UUID],
        by: str | None = None,
    ) -> None:
        """Say which blocks get 芝士's 👀 when the session says it took ``text``,
        and whose 👀 it is — ``by`` is the agent running the turn; None means
        the room's seat.

        Called BEFORE the write, for the same reason the consumed-stamp entry is:
        the receipt can come back before the caller gets its next line in.
        """
        if not text or not block_ids:
            return
        waiting = self._awaiting_seen.setdefault(topic_id, [])
        waiting.append((text, list(block_ids), by))
        # A receipt that never comes (the session died before reading, a harness
        # that does not report one) leaves its entry behind, and this process
        # runs for weeks. The mark is worth nothing once the next messages have
        # gone by, so the oldest simply fall off — dropping one costs one 👀,
        # never a message.
        del waiting[: -self._SEEN_RECEIPT_BACKLOG]

    async def _place_seen_receipts(self, topic_id: uuid.UUID, prompt: str) -> None:
        """The session took ``prompt`` — put 👀 on whatever that text carried.

        Matching is the same as the consumed stamp's: equality, or our text as
        the prefix of what the session reports (a provider may append its own
        native-image mentions to the line it types).
        """
        waiting = self._awaiting_seen.get(topic_id)
        if not waiting:
            return
        for entry in list(waiting):
            text, block_ids, by = entry
            if prompt != text and not (text and prompt.startswith(text)):
                continue
            waiting.remove(entry)
            if not waiting:
                self._awaiting_seen.pop(topic_id, None)
            from app.domain.agent.runtime import get_broker

            for block_id in block_ids:
                ack = await self.ack_summon(block_id, topic_id, by=by)
                if ack is None:
                    continue
                # 这条不是从 converse 的那个生成器里出去的——回执是会话过一阵子
                # 自己说的，那时候请求早就返回了——所以走 broker，房间里开着的
                # 客户端照样收得到。
                await get_broker().publish(str(topic_id), {"type": "reaction", **ack})
            return

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
        """The agent a known session belongs to, else the room's answer."""
        if agent_handle:
            try:
                return agents.resolved(await agents.for_handle(project, agent_handle))
            except NotFoundError:
                logger.warning(
                    "session agent %r is not in project %s; using the room's",
                    agent_handle,
                    project.id,
                )
        return await agents.for_topic(topic, project)

    async def _resolved_agent(
        self, session: AsyncSession, topic: Topic
    ) -> ResolvedAgent:
        """Which agent is working in *topic* — its own, else the project's.

        Its ``handle`` keys both of the things an agent owns and a room does not:
        the memory pool it writes to, and the conversation it resumes.
        """
        project = await ProjectRepository(session).get(topic.project_id)
        if project is None:
            raise NotFoundError("Project not found")
        return await AgentInstanceService(session).for_topic(topic, project)

    async def _agent_at(self, session: AsyncSession, place: Place) -> ResolvedAgent:
        """Which agent works in *place* — the THREAD's own pick when it is one.

        The room's pick, because the room is the only thing that runs a
        session: every 分身 in it is a worker inside that one conversation, so
        there is no second agent to resolve and a per-card pin would name one
        that never speaks.
        """
        project = await ProjectRepository(session).get(place.project_id)
        if project is None:
            raise NotFoundError("Project not found")
        return await AgentInstanceService(session).for_topic(place.room, project)

    async def _recall_agent_memories(
        self,
        memory,
        session: AsyncSession,
        *,
        topic: Topic,
        agent: ResolvedAgent | None = None,
    ) -> RecallResult:
        """What this 芝士 carries into every turn inside this project.

        Its own pool and one pool per person sitting with it (`pools_for_turn`
        picks them; every key starts with this project's id, so nothing another
        project learned about the same person is reachable from here). That is
        the whole list — every memory belongs to one agent instance (结论 8),
        so no pool here is shared with anyone.

        什么都该看见的那些事实不在这里：它们是文档（结论 7），本轮另外读——
        项目总览那一份和本房间那一份，作为文档进提示词，不冒充记忆。

        Only the core layer comes back; everything else is counted, not
        carried, and reached with `recall`. A pool nobody is told is bigger
        than what arrived is how memory quietly stops existing.
        """
        resolved = (
            agent if agent is not None else await self._resolved_agent(session, topic)
        )
        pools = pools_for_turn(
            topic.project_id,
            resolved.handle,
            await TopicMemberService(session).people_handles(topic.id),
        )
        return await recall_pools(memory, pools)

    async def _acting_handle(
        self, session: AsyncSession, topic_id: uuid.UUID, agent: ResolvedAgent
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
        return await self._agent_handle(session, topic_id)

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
        口的那句话）。"""
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
                roster = (
                    []
                    if topic is None or _is_dm(topic)
                    else await roster_rows(session, project_id)
                )
            text = _expand_mention_names(text, roster, topic_refs)
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
                resolved, unresolved = await self._notify_mentions(
                    session, topic, author, text, roster
                )
                refs = [f"user:{h}" for h in resolved] + _topic_refs(text)
                if refs:
                    block.refs = refs
                for bad in unresolved:
                    warn = f"未能通知 <@{bad}>：项目中没有这个成员"
                    # Beside the message it is about, not in the room the
                    # message did not go to — same landing as the message.
                    await blocks.add(
                        project_id=landed.project_id,
                        topic_id=landed.topic_id,
                        task_id=landed.task_id,
                        author=author,
                        author_type=AuthorType.participant,
                        content=warn,
                        kind=BlockKind.event,
                        turn_id=turn_id,
                        meta={"in_room": False},
                    )
            payload = _block_payload(BlockOut.model_validate(block))
            if publication_key is not None:
                await idem.record_result(
                    session,
                    publication_key,
                    {"input": publication_input, "block": payload},
                )
            await session.commit()
        if publish and task_id is None and turn_id is not None:
            state = self._hook_work.get((topic_id, turn_id))
            if state is not None:
                state.last_chat_at = datetime.now(UTC)
                state.last_progress_reminder_at = None
        return payload

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
    ) -> dict | None:
        """Persist ONE 施工现场 event the moment it streams in, not batched to the
        turn-end tx2. Mirrors _persist_assistant_message's commit-now contract so
        a mid-turn restart/crash never loses the 现场 timeline already produced.
        ``eid`` (the harness's own id for the event) is stamped into meta so a
        record read twice lands once. Returns the persisted block payload so the
        caller can broadcast it as a WS frame."""
        preview = tool_preview(
            name, tool_input, work_dir=work_subpath(project_id, topic_id)
        )
        return await self._persist_room_event(
            project_id=project_id,
            topic_id=topic_id,
            content=_format_tool_event(name, preview),
            meta=_tool_event_meta(
                name,
                preview,
                platform=platform,
                detail=tool_detail(name, tool_input, preview),
            ),
            turn_id=turn_id,
            eid=eid,
            platform_unsolicited=platform_unsolicited,
            task_id=task_id,
        )

    async def _mark_step_failed(self, block_id: uuid.UUID, error: str) -> dict | None:
        """Stamp a 现场 step as failed, and hand back the step as it now reads.
        Never fails a turn over a red dot."""
        try:
            async with self._sessions() as session:
                block = await BlockRepository(session).mark_step_failed(block_id, error)
                payload = (
                    _block_payload(BlockOut.model_validate(block))
                    if block is not None
                    else None
                )
                await session.commit()
            return payload
        except Exception:  # noqa: BLE001 — a step's verdict is not worth a turn
            logger.warning("could not mark step %s failed", block_id)
            return None

    async def _record_step_output(self, block_id: uuid.UUID, text: str) -> dict | None:
        """Keep the tail of what a step printed. Never fails a turn over it."""
        output, total = output_tail(text)
        try:
            async with self._sessions() as session:
                block = await BlockRepository(session).record_step_output(
                    block_id, output, total
                )
                payload = (
                    _block_payload(BlockOut.model_validate(block))
                    if block is not None
                    else None
                )
                await session.commit()
            return payload
        except Exception:  # noqa: BLE001 — a step's output is not worth a turn
            logger.warning("could not record the output of step %s", block_id)
            return None

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
        """Say the turn is retrying a failed request, on one line per streak.

        The first retry of a streak lands a notice; every later one restates
        that same line with the new count, so ten retries read as one line that
        says 10 rather than ten lines. Not session output (`note_session_output`
        is not told): nothing the session produced crossed here."""
        count = (
            f"{event.attempt}/{event.max_attempts}"
            if event.attempt and event.max_attempts
            else str(event.attempt or "")
        )
        content = "AI 服务请求失败，正在重试" + (f"（第 {count} 次）" if count else "")
        said = " ".join(
            part
            for part in (
                event.error,
                f"HTTP {event.status}" if event.status is not None else "",
            )
            if part
        )
        meta = {
            **notice(
                EVENT_API_RETRY,
                severity=SEVERITY_WARN,
                who=WHO_PLATFORM,
                detail=said or None,
                detail_label="服务原话" if said else None,
            ),
            "attempt": event.attempt,
            "max_attempts": event.max_attempts,
            "delay_ms": event.delay_ms,
            "at": datetime.now(UTC).isoformat(),
        }
        await self._keep_note(
            self._retry_notes,
            topic_id,
            turn_id,
            content,
            meta,
            author=author,
            task_id=task_id,
            channel=channel,
        )

    async def _note_reachability(
        self,
        project_id: uuid.UUID,
        topic_id: uuid.UUID,
        work_id: uuid.UUID,
        reachable: bool,
        reason: str,
    ) -> None:
        """Say the turn is waiting for its machine, and later that the wait is
        over — the same line both times."""
        del project_id
        if reachable:
            block_id = self._waiting_notes.pop(work_id, None)
            if block_id is None:
                return
            await self._restate_note(
                block_id,
                "机器已恢复连接",
                {"state": "over", "at": datetime.now(UTC).isoformat()},
                str(topic_id),
            )
            return
        state = self._hook_work.get((topic_id, work_id))
        meta = {
            **notice(
                EVENT_DEVICE_WAITING,
                severity=SEVERITY_WARN,
                who=WHO_PLATFORM,
                detail=reason or None,
                detail_label="原因" if reason else None,
            ),
            "state": "waiting",
            "at": datetime.now(UTC).isoformat(),
        }
        await self._keep_note(
            self._waiting_notes,
            topic_id,
            work_id,
            "等待机器连接",
            meta,
            author=state.acting_agent if state is not None else None,
            task_id=None,
            channel=str(topic_id),
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
        """Land the turn's notice of this kind, or restate the one it has."""
        from app.domain.agent.runtime import get_broker

        block_id = notes.get(turn_id)
        if block_id is not None:
            await self._restate_note(block_id, content, meta, channel)
            return
        try:
            async with self._sessions() as session:
                block = await announce(
                    session,
                    place_id=topic_id,
                    content=content,
                    meta=meta,
                    # The agent whose turn this is: the notice is about its
                    # work, and 现场 files it under whoever did the work.
                    author=author or "system",
                    turn_id=turn_id,
                    task_id=task_id,
                )
                if block is None:
                    return
                payload = _block_payload(BlockOut.model_validate(block))
                await session.commit()
        except Exception:  # noqa: BLE001 — a status line is not worth a turn
            logger.exception("could not note %s for turn %s", content, turn_id)
            return
        notes[turn_id] = uuid.UUID(payload["id"])
        await get_broker().publish(channel, {"type": "event_block", "block": payload})

    async def _restate_note(
        self, block_id: uuid.UUID, content: str, meta: dict, channel: str
    ) -> None:
        from app.domain.agent.runtime import get_broker

        try:
            async with self._sessions() as session:
                block = await BlockRepository(session).restate(
                    block_id, content=content, meta=meta
                )
                if block is None:
                    return
                payload = _block_payload(BlockOut.model_validate(block))
                await session.commit()
        except Exception:  # noqa: BLE001 — a status line is not worth a turn
            logger.exception("could not restate notice %s", block_id)
            return
        await get_broker().publish(channel, {"type": "block_updated", "block": payload})

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
    ) -> dict | None:
        """One event block, committed NOW and deduped by event-id.

        Shared by everything the room learns mid-turn — a tool call, a subagent's
        conclusion, the turn's change summary — so all three get the same
        durability and idempotency contract instead of three copies of it that
        drift. Returns None when this event-id already landed.

        ``in_room`` decides whether the conversation shows it at all, and it
        travels as ``meta.in_room`` — its own field, because visibility is not
        authorship. It used to ride on ``author_type``, which meant an event
        genuinely written by 芝士 could not be shown in the room without lying
        about who wrote it, and anything that later wanted to know the author
        was reading a field answering a different question. Absent means shown:
        every other writer in the codebase posts to the room.

        ``author_type`` is then free to answer its own question, and does: 芝士
        is a participant and wrote the tool calls and the subagent conclusions,
        while the change summary is the platform's own line."""
        meta = {**meta, "in_room": in_room}
        if eid:
            meta = {**meta, "eid": eid}
        if platform_unsolicited:
            meta = {**meta, "platform_unsolicited": True}
        async with self._sessions() as session:
            blocks = BlockRepository(session)
            if eid and await blocks.has_eid(topic_id, eid):
                return None
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
                author=await self._agent_handle(session, topic_id),
                author_type=author_type,
                content=content,
                kind=BlockKind.event,
                turn_id=turn_id,
                meta=meta,
            )
            payload = _block_payload(BlockOut.model_validate(block))
            await session.commit()
        return payload

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
        """Land a returning subagent's conclusion in the room timeline."""
        return await self._persist_room_event(
            project_id=project_id,
            topic_id=topic_id,
            content=_subagent_event_text(event.description, event.text),
            meta=_subagent_result_meta(event.name, event.description, event.text),
            turn_id=turn_id,
            eid=eid or event.eid,
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
        """A worker started, or handed something back — on ITS thread's line.

        Nothing is written for a sub-thread whose label names no card here, and
        that is not tidiness. Measured twice on 2.1.224: after the session's own
        Stop, a SubagentStop arrives with an id matching no worker we saw, an
        empty label, and a fragment of a prompt where the closing message is —
        something inside Claude Code, not work anybody dispatched. Writing those
        would put a stranger's half-sentence in a room as if 芝士 had said it.

        A Stop is "handed something back", never "done": the same worker reports
        finished again after it resumes. So this is an event on the timeline and
        nothing more: acceptance closes delivered work, while an explicit close
        abandons a task. This event does neither.
        """
        if task_id is None:
            return None
        if isinstance(event, AgentSubagentStart):
            # 谁在做这张卡，是平台看见它开工的时候记下来的 —— 这条事件是第一个说
            # 出这个分身 id 的东西（id 在容器里才诞生，派活的时候没有任何东西能提
            # 前说出它）。卡上从此有一个分身在做，看板也就能问它还活着没有。
            await self._note_worker(
                task_id,
                event.agent_id,
                topic_id=topic_id,
                turn_id=turn_id,
                parent_session_id=event.session_id,
            )
            # The platform's own sentence about a worker, not anybody's words —
            # so `platform`, the same as every other line the platform says out
            # loud. Attributing it to 芝士 would make the room's history contain
            # a remark 芝士 never made.
            content, author_type = "分身开工", AuthorType.platform
            meta: dict = {"event_type": "subagent_start"}
        else:
            # The closing message in full, and it IS the worker's own words. It
            # reaches the platform exactly once, here — the room's transcript
            # does not contain it and the worker's dies with its container.
            content = event.text.strip() or "分身交回了一次结果（没有留话）"
            author_type = AuthorType.participant
            meta = {"event_type": "subagent_stop"}
            if event.transcript_path:
                meta["transcript_path"] = event.transcript_path
            # 结论落在卡上, overwriting the previous stop's — the newest is what
            # the room reads when it decides whether the work is done. Only for
            # a sub-thread whose label names this card (`task_id` is that check,
            # above), so the fragments Claude Code's own internal agents stop
            # with never become anybody's conclusion.
            await self._record_conclusion(task_id, event.text.strip())
        meta["agent_id"] = event.agent_id
        if event.thread_label:
            meta["thread_label"] = event.thread_label
        return await self._persist_room_event(
            project_id=project_id,
            topic_id=topic_id,
            content=content,
            meta=meta,
            turn_id=turn_id,
            eid=eid,
            platform_unsolicited=platform_unsolicited,
            task_id=task_id,
            author_type=author_type,
            # Shown in the thread rather than kept to 现场: what a worker handed
            # back is the whole reason anybody opens the thread.
            in_room=True,
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
        """把做这条活的分身记在卡上。"""
        from app.domain.delivery.agent import instance_for_seat
        from app.domain.room_task.models import Task
        from app.domain.room_task.services import TaskService

        if turn_id is None or not parent_session_id:
            return
        async with self._sessions() as session:
            tasks = TaskService(session)
            task = await session.get(Task, task_id, with_for_update=True)
            state = self._hook_work.get((topic_id, turn_id))
            if (
                task is None
                or task.room_id != topic_id
                or state is None
                or not parent_session_id
            ):
                return
            instance = await instance_for_seat(
                session, task.project_id, state.acting_agent
            )
            # A delayed start from a replaced parent may remain historical
            # evidence, but cannot acquire control of the task's current worker.
            if self._active_turn_ids.get(topic_id) != turn_id or instance is None:
                return
            task.execution_agent_instance_id = instance.id
            task.execution_parent_session_id = parent_session_id
            task.execution_turn_id = turn_id
            await tasks.note_worker(task, subagent_id)
            await session.commit()

    async def _record_conclusion(self, task_id: uuid.UUID, text: str) -> None:
        from app.domain.room_task.services import TaskService

        if not text:
            return
        async with self._sessions() as session:
            tasks = TaskService(session)
            task = await tasks.get(task_id)
            if task is None:
                return
            await tasks.record_conclusion(task, text)
            await session.commit()

    async def _turn_changeset(
        self,
        project_id: uuid.UUID,
        topic_id: uuid.UUID,
        known_commits: set[str] | None,
    ) -> _Changeset | None:
        """This turn's net effect on the topic branch, or None when there is none.

        The agent commits and pushes its own work, so "the commits that were not
        there at turn start" is exactly what this turn delivered. Best-effort and
        off the event loop: the numbers are a courtesy, and no turn should die
        (or stall) over them.
        """
        if known_commits is None:
            return None

        commits = await self._known_commits(project_id, topic_id)
        if commits is None:
            return None

        async def _collect() -> _Changeset | None:
            from app.domain.repository.forge_files import ProjectFiles

            fresh = [h for h in commits if h not in known_commits]
            if not fresh:
                return None
            totals: dict[str, dict] = {}
            async with self._sessions() as session:
                files = ProjectFiles(session, project_id, None)
                for sha in fresh:
                    for entry in _diff_file_stats(await files.commit_diff(sha)):
                        acc = totals.setdefault(
                            entry["path"],
                            {"path": entry["path"], "added": 0, "removed": 0},
                        )
                        acc["added"] += entry["added"]
                        acc["removed"] += entry["removed"]
            files = sorted(
                totals.values(), key=lambda f: (-(f["added"] + f["removed"]), f["path"])
            )
            if not files:
                return None  # a commit that changed nothing (empty snapshot)
            return _Changeset(commits=fresh, files=files)

        try:
            return await _collect()
        except Exception:  # noqa: BLE001 — never fail a turn over its summary
            logger.warning("change summary failed for topic %s", topic_id)
            return None

    async def _known_commits(
        self, project_id: uuid.UUID, topic_id: uuid.UUID
    ) -> set[str] | None:
        """The topic branch's commits right now — the baseline the turn's change
        summary is measured against. None when it cannot be read (see
        _HookWorkState.known_commits)."""
        try:
            from app.domain.repository.forge_files import ProjectFiles
            from app.domain.room_task.services import TaskService

            async with self._sessions() as session:
                tasks = await TaskService(session).list_in_room(topic_id)
                commits = set()
                for task in tasks:
                    if task.branch_name:
                        history = await ProjectFiles(
                            session, project_id, task.id
                        ).history()
                        commits.update(
                            row["sha"] for row in history[-_CHANGE_COMMIT_WALK:]
                        )
                return commits
        except Exception:  # noqa: BLE001 — no baseline just means no summary
            logger.warning("commit baseline unreadable for topic %s", topic_id)
            return None

    async def _persist_change_summary(
        self,
        *,
        project_id: uuid.UUID,
        topic_id: uuid.UUID,
        turn_id: uuid.UUID | None,
        changeset: _Changeset,
    ) -> dict | None:
        """Land 「这一轮改了 N 个文件」 in the room timeline.

        This one goes in the ROOM, not just 现场 (spec §8.5 变更提醒). What 芝士
        changed is the one thing about a turn that is nowhere else in the
        conversation: the doc panel lights up on its own and the accept card
        speaks for itself, but "this turn touched these files" was only ever a
        grey line in a drawer nobody has open."""
        return await self._persist_room_event(
            project_id=project_id,
            topic_id=topic_id,
            content=_format_change_summary(changeset.files),
            meta=_change_summary_meta(changeset),
            turn_id=turn_id,
            in_room=True,
            author_type=AuthorType.platform,  # 平台自己数出来的，不是芝士说的
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

    async def _model_kwargs(
        self,
        project_id: uuid.UUID,
        provider: ComputeProvider | None,
        topic_id: uuid.UUID | None = None,
        *,
        agent: ResolvedAgent | None = None,
        acting_agent: str | None = None,
    ) -> tuple[dict, str]:
        """Resolve a turn's model, model environment and usage route.

        Which model comes from the binding of the work this turn belongs to —
        and a room's main thread is not a piece of work, so it always gets the
        project default (`room_task/binding.py`).

        Machine providers assemble their own scoped credentials. Other providers
        retain their gateway/profile transport, carrying that resolved model.
        The optional snapshots keep model, role and author consistent within a turn.

        ``provider=None`` means there is no machine in this turn at all (私聊 走
        platform work): the platform is the one about to call the model, so it needs
        the same base_url + key a sandbox would have been handed. That is exactly
        the not-``builds_model_env`` branch, so it falls through to it rather than
        growing a second way to answer the same question.
        """
        environment = None
        async with self._sessions() as session:
            project = await ProjectRepository(session).get(project_id)
            if project is None:
                raise NotFoundError("Project not found")
            topic = await TopicRepository(session).get(topic_id) if topic_id else None
            if topic is not None:
                if acting_agent is None:
                    acting_agent = await self._agent_handle(session, topic.id)
                # Overview remains available to repair failed project setup.
                environment = (
                    EnvironmentConfig().snapshot()
                    if topic.kind == TopicKind.root
                    else await pin_environment(session, project_id, topic.id)
                )
                await session.commit()
            if agent is None:
                agents = AgentInstanceService(session)
                agent = (
                    await agents.for_topic(topic, project)
                    if topic
                    else await agents.for_project(project)
                )
        # A saved teammate may override the project main model.
        bound = binding.resolve(
            None,
            binding.catalog(project.settings),
            agent_model=agent.configuration.get("model"),
            default_model=(project.settings or {}).get("default_model"),
        )
        # 解析出来的那个模型还要过一遍项目的档位策略（结论 3 后半）。闸门不写进
        # `binding.resolve`：那个函数只答「用哪个模型」，「超档怎么办」是另一个问
        # 题，而且它的另一个调用者是要机器的那条路（`domain/policy/gate.py`）。
        #
        # 一条房间主线在组装那一步就过过闸门了（那里是这一轮占用任何东西之前）；
        # 走到这里还没过的，是平台自己发起的那几轮 —— 活动消化、巡检、项目小结，
        # 它们不经过组装。所以这一处仍然是必要的，而且仍然在任何请求发出去之前。
        async with self._sessions() as session:
            proposed = await self._pass_policy_gate(
                session,
                topic_id,
                _model_policy_call(project, agent),
                gate.policy_of(project.settings),
                actor=acting_agent or agent.handle,
            )
            if proposed is not None:
                await session.commit()
                raise gate.OverTier(proposed.proposal.content)
        supply = bound.supply
        model = bound.wire_model
        child_default = (project.settings or {}).get("default_subagent_model") or (
            project.settings or {}
        ).get("default_model")
        child_choices = binding.catalog(project.settings)
        # An unused invalid child default must not block a valid main override.
        # Preserve it for the child request's admission refusal, never replace it.
        child_model = child_default
        if not child_default or child_default in child_choices:
            child_model = binding.resolve(
                None, child_choices, default_model=child_default
            ).wire_model
        config_hash = hashlib.sha256(
            # Author identity, chat skills, and native RC arguments are installed
            # at process birth; refresh them together at the next task boundary.
            #
            # 模型和它的池也在里面：两条路都在启动那一刻把 model 钉进进程 ——
            # 容器那条路是 `session_launch.py` 拼进 argv 的 `claude --model`，
            # device 那条路是下面的 `ANTHROPIC_MODEL`。Claude Code 按它组装整套
            # 系统提示词和自我介绍（Opus 与 Sonnet 用的是两份不同的提示词），
            # 而这个哈希是唯一比较「屏幕是不是还配得上现在的选择」的地方，所以改
            # 项目模型在两条路上都会到下一个 task boundary 收屏重开一次，提示词
            # 随之换成新模型的。device 上每个请求实际跑哪个模型仍然只由准入决定
            # （结论 46），计量代理把答案写进请求体；启动时的这个名字只决定提示
            # 词，在重开之前的那几轮里它可能落后于绑定。容器那条路没有代理改写，
            # 不放进哈希就是屏幕带着 `--model glm-5.2` 继续跑而准入已经解析成订阅
            # 池，此后每一轮都死在「订阅池收到 glm-5.2」上，直到有人手动重启屏幕。
            (
                json.dumps(
                    {
                        "agent": agent.configuration,
                        "git_author": acting_agent,
                        "model": model,
                        "supply": supply,
                        "subagent_model": (project.settings or {}).get(
                            "default_subagent_model"
                        ),
                    },
                    sort_keys=True,
                )
                + ":explicit-chat-v5-launch-model"
                + (":native-rc-v1" if supply == SUBSCRIPTION else "")
            ).encode()
        ).hexdigest()
        kwargs: dict = {
            "model": model,
            "env": {
                "CHEESE_AGENT_CONFIG": config_hash,
                "CLAUDE_CODE_GATEWAY_HINT_HEADERS": "1",
                # The bound model, so Claude Code builds the system prompt and
                # self-description for the model the turn actually runs on.
                "ANTHROPIC_MODEL": model,
                "CLAUDE_CODE_SUBAGENT_MODEL": child_model,
            },
            # Which conversation the turn belongs to, and so which session's
            # machines it runs on. Separate from `agent_handle` below, which is
            # the SEAT the turn authors under — the two are different strings
            # and the place is recorded under this one.
            "session_agent": agent.handle,
        }
        if acting_agent is not None:
            kwargs["agent_handle"] = acting_agent
        if environment is not None:
            kwargs["env"]["CHEESE_ENVIRONMENT"] = json.dumps(environment)
        if provider is not None and provider.builds_model_env:
            return kwargs, supply
        pool_route = True
        if self._profiles is not None:
            profile = self._profiles.resolve(
                project.settings if project else None,
                project.owner_handle if project else None,
            )
            kwargs["env"].update(profile.full_env())
            kwargs["env"]["ANTHROPIC_DEFAULT_SONNET_MODEL"] = model
            kwargs["env"]["ANTHROPIC_DEFAULT_OPUS_MODEL"] = model
            # Only the pool profile routes through the gateway; the testing
            # (native Claude) profiles pin their own base_url + credentials.
            pool_route = profile.base_url == settings.anthropic_base_url
        routed = pool_route and self._gateway is not None
        if routed:
            # L1/L2: the sandbox runs on the project's VIRTUAL gateway key — never
            # the master key (containment), attributable + budget-capped.
            override = await self._gateway_project_env(project_id)
            if not override:
                raise GatewayUnavailableError(
                    "AI gateway could not provision a project-scoped key; "
                    "no model call was made"
                )
            kwargs["env"] = {**kwargs.get("env", {}), **override}
        return kwargs, "gateway" if routed else "native"

    _GW_KEY = "llm_gateway_key"
    _GW_CKPT = "llm_gateway_usage_ckpt"
    _GW_BUDGET = "llm_gateway_budget_usd"

    async def project_gateway_key(self, project_id: uuid.UUID) -> str | None:
        """The project's virtual gateway key, minted on first use — the same one
        a local sandbox turn runs on. Public because the remote-machine LLM proxy
        (routes/llm_proxy.py) has to swap it in per request: a machine off the box
        never receives a provider credential, only its own scoped cheese token."""
        env = await self._gateway_project_env(project_id)
        return (env or {}).get("ANTHROPIC_AUTH_TOKEN")

    async def _gateway_budget_target(
        self, session: AsyncSession, project_id: uuid.UUID
    ) -> float | None:
        """The L2 max_budget this project's key should carry, or None when the
        gateway must not be told one at all (no price knob, or the project is
        unmetered). Reading it belongs to the READ path: a caller that finds the
        key's budget already in step has nothing to write and nothing to lock."""
        if not settings.llm_gateway_credit_usd:
            return None
        summary = await ComputeGrantRepository(session).summary(project_id)
        if summary["unlimited"]:
            return None
        return round(summary["credits_total"] * settings.llm_gateway_credit_usd, 6)

    async def _gateway_project_env(self, project_id: uuid.UUID) -> dict | None:
        """Env override for a gateway-routed turn: mint (once) and return the
        project's virtual key, and keep its L2 max_budget in step with the
        project's grants. Returns None on any gateway/admin failure; the caller
        must refuse the turn rather than expose default pool credentials.

        **Answering a project that needs nothing written takes no lock at all.**
        `_gateway_lock` serialises the settings read-modify-write, but this
        method used to hold it across the whole body — including the two gateway
        HTTP calls — so one project minting a key, or one drain asking the
        gateway for spend, stalled every admission on the box behind it. These
        calls are on the hot path of every model request (routes/llm_proxy.py
        asks once per turn, and the metering proxy asks per request), and the
        queued wait is what the /llm/admission p95 is made of. Measured on dev,
        2026-09-23: 20 concurrent admissions for ONE project — key long since
        minted, no budget drift to apply — still fanned out into a 5.8 s tail.
        Nothing about answering that request is exclusive, so it is answered
        before the lock is reached.
        """
        try:
            # Read path: the key exists and is in step → nothing to write.
            async with self._sessions() as session:
                project = await ProjectRepository(session).get(project_id)
                if project is None or self._gateway is None:
                    return None
                s = dict(project.settings or {})
                key = s.get(self._GW_KEY)
                if isinstance(key, str) and key:
                    target = await self._gateway_budget_target(session, project_id)
                    if target is None or s.get(self._GW_BUDGET) == target:
                        return {"ANTHROPIC_AUTH_TOKEN": key}

            # Write path: something must be minted or re-priced. Serialised, and
            # the settings row is re-read here so a mint that landed while this
            # call waited on the lock is the one that gets used.
            async with self._gateway_lock:
                async with self._sessions() as session:
                    project = await ProjectRepository(session).get(project_id)
                    if project is None or self._gateway is None:
                        return None
                    s = dict(project.settings or {})
                    key = s.get(self._GW_KEY)
                    if not isinstance(key, str) or not key:
                        key = await self._gateway.mint_project_key(project_id)
                        if not key:
                            return None
                        s[self._GW_KEY] = key
                    target = await self._gateway_budget_target(session, project_id)
                    if target is not None and s.get(self._GW_BUDGET) != target:
                        if await self._gateway.set_key_budget(key, target):
                            s[self._GW_BUDGET] = target
                    if s != (project.settings or {}):
                        project.settings = s
                        await session.commit()
            return {"ANTHROPIC_AUTH_TOKEN": key}
        except Exception:  # noqa: BLE001 — never fail a turn on admin plumbing
            logger.exception("gateway project-env failed for %s", project_id)
            return None

    def _schedule_deferred_drain(
        self, project_id: uuid.UUID, topic_id: uuid.UUID, turn_id: uuid.UUID
    ) -> None:
        """Late-landing spend rows: drain again in the background and land the
        usage row + credit deduction when they show up. Strong-ref'd so the
        pending commit can't be GC'd."""

        async def _later() -> None:
            await asyncio.sleep(20.0)
            usages = await self._drain_gateway_usage(project_id, topic_id, turn_id)
            if not usages:
                return  # still nothing — the next turn's drain picks it up
            logger.info(
                "deferred usage drain landed for turn %s (%s)",
                turn_id,
                ", ".join(
                    f"{u.model or '?'}:{u.input_tokens}+{u.output_tokens}"
                    for u in usages
                ),
            )

        hold(
            asyncio.create_task(_later()),
            self._background_tasks,
            name=f"deferred-usage-drain-{turn_id}",
        )

    async def _drain_gateway_usage(
        self, project_id: uuid.UUID, topic_id: uuid.UUID, turn_id: uuid.UUID
    ) -> list[AgentUsage] | None:
        """L1: real usage for gateway-routed turns, **one entry per model**.

        The hooks backends can't see token usage locally (interactive Claude
        Code reports none → usage=0), so read the project's NEW spend from the
        gateway's log instead — an exactly-once daily cumulative delta per
        model (see gateway.drain_new_usage), so late-logged rows surface in a
        later drain instead of being lost. Usage rows, credits and the checkpoint
        commit together. ``None`` covers both "nothing to
        land yet" and "could not ask the gateway" — the callers treat them the
        same (retry now / settle later) and only differ on whether the
        checkpoint was advanced, which this function already did or did not
        do."""
        if self._gateway is None:
            return None
        try:
            for attempt in range(2):
                if attempt:
                    # Spend rows can arrive late. Wait without holding the lock
                    # needed by new model requests, then read the current checkpoint.
                    await asyncio.sleep(3.0)
                # Read the checkpoint and ASK THE GATEWAY outside the lock. The
                # ask is an HTTP round trip to LiteLLM (`/spend/logs`), and
                # `_gateway_lock` is the box-wide lock that `/llm/admission`
                # takes per request — holding it across a slow spend read queued
                # every admission on the platform behind it (measured on dev,
                # 2026-09-23: 25 admissions in one second, 1.1–6.1 s each, with a
                # drain in flight). Only the checkpoint read-modify-write below
                # needs to be exclusive.
                async with self._sessions() as session:
                    project = await ProjectRepository(session).get(project_id)
                    if project is None:
                        return None
                    s = dict(project.settings or {})
                    key = s.get(self._GW_KEY)
                    if not isinstance(key, str) or not key:
                        return None  # nothing ever routed → nothing to meter
                    ckpt = s.get(self._GW_CKPT)
                    ckpt = ckpt if isinstance(ckpt, dict) else None
                drained = await drain_new_usage(self._gateway, key, ckpt)
                if not attempt and (drained is None or not drained[0]):
                    continue
                if drained is None:
                    return None
                rows, next_ckpt = drained
                # Exactly-once, and two drains can now be in flight at once: the
                # checkpoint must still be the one we read before this drain is
                # allowed to bill its delta. A drain that finds it already
                # advanced has had its window taken by the other one and must
                # land nothing — otherwise the same spend rows are billed twice.
                async with self._gateway_lock:
                    async with self._sessions() as session:
                        project = await ProjectRepository(session).get(project_id)
                        if project is None:
                            return None
                        # Another backend process can drain during a rollout.
                        # Lock and refresh the row before comparing checkpoints.
                        await session.refresh(project, with_for_update=True)
                        s = dict(project.settings or {})
                        if s.get(self._GW_CKPT) != ckpt:
                            return None
                        usages = [
                            AgentUsage(
                                model=row.model,
                                input_tokens=row.prompt_tokens,
                                output_tokens=row.completion_tokens,
                                cost_usd=row.spend_usd,
                            )
                            for row in rows
                        ]
                        for usage in usages:
                            await UsageRepository(session).add(
                                project_id=project_id,
                                topic_id=topic_id,
                                model=usage.model or settings.agent_model,
                                input_tokens=usage.input_tokens,
                                output_tokens=usage.output_tokens,
                                cost_usd=usage.cost_usd,
                                route="gateway",
                                turn_id=turn_id,
                            )
                            await ComputeGrantRepository(session).consume(
                                project_id,
                                usage_to_credits(usage, spend_priced=True),
                            )
                        s[self._GW_CKPT] = next_ckpt
                        project.settings = s
                        await session.commit()
                # `model=""` is the one pre-split migration row (see
                # gateway.drain_new_usage): it is real spend we can only state
                # as a total. Stamp the default name so it is still visible,
                # exactly as before the split — do NOT invent a model.
                # None, not []: "no rows" and "gateway unreachable" both mean
                # there is nothing to land this pass (see the docstring).
                return usages or None
        except Exception:  # noqa: BLE001 — metering must never fail a turn
            logger.exception("gateway usage drain failed for %s", project_id)
            return None

    async def _notify_mentions(
        self, session, topic, author: str, text: str, roster: list[dict]
    ) -> tuple[list[str], list[str]]:
        """@<name> in a message → a strong notification to each matched teammate
        (spec §7: @人 = strong). `<@all>`/`<@here>` expand to the topic's roster
        (群播, fusion-design §3). Returns (resolved_handles, unresolved_names) so
        the caller can set refs and flag the wrong ones."""
        resolved, unresolved = _resolve_mentions(text, roster)
        concrete = [h for h in resolved if h not in _SPECIAL_MENTIONS]
        if any(h in _SPECIAL_MENTIONS for h in resolved):
            # Expand @all/@here to the topic's members. @here should be the
            # ACTIVE members, but there's no presence signal yet, so it equals
            # @all for now (TODO: intersect with presence once it lands).
            #
            # A broadcast reaches the room's humans only: every 芝士 in the room
            # already reads the timeline, so notifying them adds nothing. An
            # explicit <@handle> is different and is NOT filtered here — that is
            # how one agent addresses another, which a room hosting several 芝士
            # depends on.
            member_service = TopicMemberService(session)
            members, _ = await member_service.list_for_topic(topic.id)
            agents = set(await member_service.agent_handles(topic.id))
            concrete += [
                m.member_handle for m in members if m.member_handle not in agents
            ]
        # Nobody needs a notification for their own message.
        targets = [h for h in dict.fromkeys(concrete) if h != author]
        if targets:
            notifs = ProjectNotificationService(session)
            preview = markdown_preview(text, 200)
            who = "芝士" if looks_like_agent_handle(author) else author
            for h in targets:
                await notifs.create(
                    project_id=topic.project_id,
                    level=NotificationLevel.strong,
                    kind=NotificationType.MENTION,
                    title=f"{who} 在「{topic.title}」@了你",
                    body=preview,
                    target_handle=h,
                    topic_id=topic.id,
                )
        return resolved, unresolved

    async def _bail_notice(
        self,
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
            topic_id=landed.topic_id,
            task_id=landed.task_id,
            author="system",
            author_type=AuthorType.platform,
            content=text,
            kind=BlockKind.event,
            turn_id=turn_id,
            meta={"platform": True},
        )
        await session.commit()
        return _block_payload(BlockOut.model_validate(block))

    async def _assemble_turn(
        self,
        *,
        topic_id: uuid.UUID,
        content: str,
        turn_id: uuid.UUID,
        user_block_id: uuid.UUID | None,
        provision_actor: Actor | None,
        platform_turn: bool = False,
        recipient_instance_id: uuid.UUID | None = None,
    ) -> "_TurnContext | _TurnBail":
        """Everything a turn needs before anything runs it, read in one
        transaction: who is here, what was said, what is remembered, which
        machine, and the prompt built out of all of it.

        Returns a ``_TurnBail`` when the turn ends here instead of starting —
        nobody is actually waiting on an answer, or the machine is still being
        built. Both are ordinary outcomes, not errors, and both have to reach
        the room as frames, which is why they travel back rather than being
        yielded: assembling is a question with an answer, and a coroutine can
        return one.
        """
        started = time.monotonic()
        phases_ms: dict[str, float] = {}
        # --- tx1: load topic + history, load memory ---
        async with self._sessions() as session:
            topics = TopicRepository(session)
            blocks = BlockRepository(session)
            memory = memory_store(session)

            # WHERE this turn runs. A room — the only thing a turn runs in.
            place = await PlaceResolver(session).resolve(topic_id)
            if place is None:
                raise NotFoundError("Topic not found")
            topic = place.room
            if topic.status == TopicStatus.archived:
                raise ValidationError("房间已归档，请先取消归档再继续工作")

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
            if recipient_instance_id is not None:
                recipient = {"instance_id": str(recipient_instance_id)}
                if agent_instance_handle(
                    recipient_instance_id
                ) not in await TopicMemberService(session).agent_handles(place.room_id):
                    raise ValidationError(
                        "The addressed agent is no longer seated in this room"
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
            pending = [block for block in pending if _addressed_to(block, agent.handle)]
            pending_ids = [b.id for b in pending]
            if not pending and user_block_id is not None:
                # 有人召唤，但他那条消息已经被前一轮读进 prompt 了（两个人几乎同时
                # @，第一轮在锁上把两条合并答掉）。再跑一轮就是白烧一轮算力，还会
                # 走下面的 platform_prompt 兜底、把已经答过的话当成平台指令重投一
                # 遍。这里直接收工 —— 只是不跑这一轮，不碰任何排队/锁的逻辑。
                return _TurnBail([{"type": "done"}])
            # 图片输入: every pending image is offered to the provider as
            # {"path", "media_type"}. Whether it actually reaches the model as a
            # native base64 block depends on the provider (`embeds_images`), and
            # the prompt is built below — AFTER the provider is picked — so its
            # wording can match what this backend really does.
            turn_images = [
                {"path": b.content, "media_type": b.mime_type or "image/png"}
                for b in pending
                if b.kind == BlockKind.attachment and b.content
            ]

            # 私聊是名册两席的房间（结论 19）。这一轮凡是「私聊要不一样」的地
            # 方，问的都是下面两个答案之一，不再各自问一遍那个布尔。
            #
            # 一、名册上那两席，人是哪一位（席位不齐时 None）。
            private_owner = await self._private_owner(session, topic)
            # 二、这一轮要不要一双手？见 `_is_dm`：不租地点的一轮桌上只有对话、
            # 记忆和平台工具，加上会话自己那块 64 MiB 草稿区。
            needs_place = not _is_dm(topic)
            acting_agent = await self._acting_handle(session, topic.id, agent)
            doc_root = await blocks.doc_root(place.room_id)
            doc_text = doc_root.content if doc_root else None
            phases_ms["identity"] = (time.monotonic() - started) * 1000
            memories = await self._recall_agent_memories(
                memory, session, topic=topic, agent=agent
            )
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
            # 总览房间自己那一轮不读第二遍：`doc_text` 已经是它。
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
            # 骨架是这个项目跑的那一个——项目设置盖过部署设置（结论 28），不是
            # 这个参与者的属性。这一轮只解析这一次，往下每一处都读它：会话行的键
            # 里有骨架，两处各自解析一次就够把一条会话拆成两条。
            wanted_harness = harness_for(project.settings if project else None)
            agent_pool = memory_pool(topic.project_id, agent)
            # Roster so 芝士 can @ real teammates (not just name them in prose).
            # 私聊里没有第三个人可点名，名册也就不进提示词——`[]` 和「没有名册这
            # 回事」在下游是两种情况（见 `_HookWorkState.roster`）。问的是这间房
            # 是不是私聊，不是它此刻坐了几个人：名册还要往下走进 `_notify_mentions`。
            roster = (
                [] if _is_dm(topic) else await roster_rows(session, topic.project_id)
            )
            # Topic list so 芝士 can cross-reference topics with <#id> tokens.
            # 两份，故意的：`topic_refs` 是 `@标题` 的**解析表**（全量，含已归档
            # ——用户自己打 @某个归档话题也必须还能变成链接）；
            # `topic_refs_for_prompt` 只是**渲染**进 system prompt 的子集。
            topic_refs, topic_refs_for_prompt = _topic_ref_lists(
                await topics.list_for_project(topic.project_id),
                exclude_id=topic.id,
            )
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
                topic.title == PLACEHOLDER_TITLE
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
                place.room_id, excluding=pending_ids
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
            compute_id = _resolve_compute_id(
                project.settings if project else None,
                topic.compute_profile,
            )
            # 先问这套部署有没有这个骨架，再过档位策略：策略那一步要解析模型，而一个
            # 没注册的骨架一个模型都指不到（结论 43），先问它就会以「没有默认模型」
            # 收场，房间读到的不是真正的原因。
            provider = self._compute.select(
                provider_id=compute_id, harness=wanted_harness
            )
            if provider is None:
                # The machine is fine; what this deployment runs is not
                # deployed on it. Say so rather than starting something else:
                # a turn taken on another harness is a turn nobody asked for.
                return _TurnBail(
                    [
                        {
                            "type": "event_block",
                            "block": await self._bail_notice(
                                project_id=topic.project_id,
                                topic_id=topic_id,
                                turn_id=turn_id,
                                session=session,
                                text=(
                                    f"本话题选的机器上没有部署 {wanted_harness}，"
                                    "本轮没有开始。"
                                ),
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
                policy = gate.policy_of(project.settings)
                # 不限档的项目——今天的每一个——在机器这一侧一步也不多走：把「要哪
                # 台机器」写成一次调用得列一遍项目设备、列一遍 host health、再取一
                # 次机主，而不限档时判决与这几条查询无关。闸门对现有项目透明，代价
                # 上也得透明，这是每一轮都走的路。
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
                if topic.compute_profile is None:
                    topic.compute_profile = provider.name
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
                                "detail": (
                                    "本话题会保留这条消息，机器就绪后自动继续。"
                                ),
                                "detail_label": "接下来会发生什么",
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
                and b.reply_to not in pending_ids
                and (parent := await blocks.get(b.reply_to)) is not None
            }
            backlog = "\n".join(
                prompt_line(
                    b,
                    embeds_images=embeds_images,
                    replied=replied.get(b.id),
                    recipient=agent.handle,
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
            replay_n = await blocks.bump_prompt_attempts(pending_ids, turn_id)
            # Committed HERE and not left to ride the conditional commit further
            # down: that one only fires on a topic's FIRST turn (compute_profile
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
            if needs_place and provider is not None and topic.compute_profile is None:
                # v4 affinity red line: materialize the effective target BEFORE
                # the first provider call. A later project-default change must
                # never move an existing work tree or resumable Claude session.
                topic.compute_profile = provider.name
                await session.commit()
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
            memories=memories,
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
        from app.api.deps import get_work_runner

        preparation_started = time.monotonic()
        prepared = await self._assemble_turn(
            topic_id=topic_id,
            content=content,
            turn_id=turn_id,
            user_block_id=user_block_id,
            provision_actor=provision_actor,
            platform_turn=platform_turn,
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
        memories = prepared.memories
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
            memories.facts,
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
            memories_omitted=memories.omitted,
            memories_core_omitted=memories.core_omitted,
            teaching=teaching,
            session_opening=_session_opening_lines(
                progress=prior_progress,
                sandbox=_sandbox_limits(provider),
                # A resumed conversation already holds what was said in it.
                earlier_messages=(
                    prepared.earlier_messages if resume_session_id is None else 0
                ),
            ),
            stage_guide=load_scenario(stage_scenario(topic_stage)),
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
        # In a private chat, `cheese_remember` targets the owner's personal memory
        # (spec §8.4). The provider runs a plain model turn when no Docker (tests).
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
        # And on the turn itself: the backend that ends this turn may not be
        # this one (`_begin_self_started_turn`), and it remembers neither.
        await self._note_turn_context(turn_id, route=route, reply_to=user_block_id)
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

        # The checklist the last turn left behind: the agent reads it in the
        # prompt, the room gets it here, marked as not this turn's own. This
        # turn's first `todo_write` replaces it.
        if prior_progress:
            yield {"type": "todo", "items": prior_progress, "restored": True}
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
            # This turn takes the session over, so anything it was doing on its
            # own is over: the Stop that ends this turn will be attributed HERE,
            # and the self-started state would sit in these maps forever waiting
            # for a Stop of its own that is never coming. Its durable row closes
            # either way — `_close_open_turns` closes every open interval on the
            # place — so what is dropped here is only the bookkeeping.
            for prior_key, prior in list(self._hook_work.items()):
                if prior_key[0] == topic_id and prior.self_started:
                    self._hook_work.pop(prior_key, None)
                    get_work_runner().close_turn_the_session_started(prior_key[1])
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
        if user_block_id is not None and not is_resume and not platform_turn:
            self.arm_seen_receipt(
                topic_id, prompt_text, [user_block_id], by=acting_agent
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
            if delivery_id is not None:
                from app.domain.delivery.agent import begin_send

                await begin_send(
                    self._sessions,
                    delivery_id,
                    turn_id,
                    parent_session_id=resume_session_id,
                )
                # Staging a prompt on a booting machine is not receiver input.
                # Register before send so a fast native receipt cannot race it.
                self._pending_receipts.setdefault(topic_id, []).append(
                    (prompt_text, [], turn_id, time.monotonic())
                )
            ready = await runtime.send(
                session_ref,
                prompt_text,
                Opening(
                    system_prompt=system_prompt,
                    resume_token=resume_session_id,
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
            )
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
                    text=str(exc) or "本轮没能把消息送进机器上的会话",
                    session_id=resume_session_id,
                    is_error=True,
                    failure_code=failure_code,
                ),
                None,
                False,
                False,
            )
            status = getattr(exc, "environment_status", None)
            if status is not None:
                from app.domain.project.environment_recovery import report_failure

                await report_failure(self, project_id, topic_id, status)
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

        async with self._sessions() as session:
            await close_recovery(session, topic_id)
            await session.commit()
        yield {"type": "prompt_delivered"}
        if ready is False:
            marked_work_id = marked_work_ids[-1] if marked_work_ids else turn_id
            payload = await self.post_system_event(
                topic_id,
                "机器上的会话正在启动，消息已就位，会自动发送",
                marked_work_id,
            )
            if payload is not None:
                yield {"type": "event_block", "block": payload}
        return

    async def ingest_activity(
        self,
        *,
        project_id: uuid.UUID,
        text: str,
        author: str,
        kind_hint: str | None = None,
    ) -> dict:
        """活动接入 (eval E1/E3): turn raw offline input (记一笔 / 导聊天记录 /
        会议纪要) into a structured event topic. 芝士 digests it with the
        activity-digestion skill — writing the structured doc (做了什么/定了什么/
        谁负责/下一步) and pinning a milestone if it's a key moment."""
        # --- tx1: create the event topic under the project root, seed input ---
        async with self._sessions() as session:
            projects = ProjectRepository(session)
            topics = TopicRepository(session)
            blocks = BlockRepository(session)
            memory = memory_store(session)

            project = await projects.get(project_id)
            if project is None:
                raise NotFoundError("Project not found")

            title = " ".join(text.split())[:40] or "活动记录"
            topic = await topics.add(
                project_id=project_id,
                title=f"[活动] {title}",
                parent_id=project.root_topic_id,
                kind=TopicKind.topic,
                created_by=author,
            )
            landed = landing(EventAbout.room, project_id=project_id, room_id=topic.id)
            await blocks.add(
                project_id=landed.project_id,
                topic_id=landed.topic_id,
                task_id=landed.task_id,
                author=author,
                author_type=AuthorType.participant,
                content=text,
                kind=BlockKind.event,
                # 原始素材，不是房间里的一句话：房间读的是芝士消化出来的结构化文档。
                meta={"in_room": False},
            )
            memories = await self._recall_agent_memories(memory, session, topic=topic)
            topic_id = topic.id
            compute_id = _resolve_compute_id(
                project.settings,
            )
            await session.commit()

        # --- run 芝士 with the activity-digestion skill + tools ---
        system_prompt = build_system_prompt(
            self._base_prompt,
            load_skills(ACTIVITY_SKILLS),
            None,
            memories.facts,
            memories_omitted=memories.omitted,
            memories_core_omitted=memories.core_omitted,
        )
        prompt = (
            "下面是一条线下活动输入，请按『活动消化』技能把它整理成结构化记录："
            "用 cheese_doc_set 把 做了什么/定了什么/谁负责/下一步 设为本话题实况文档；"
            "如果这是个关键节点就用 cheese_milestone 钉成里程碑；"
            "需要分派的待办用 cheese_notify 通知到人。\n\n---\n" + text
        )
        provider = self._compute.platform_work(compute_id)
        runtime = runtime_for(provider)
        final_text = ""
        new_session_id = None
        tools_used: list[str] = []
        async for event in runtime.run_turn(
            project_id=project_id,
            topic_id=topic_id,
            prompt=prompt,
            system_prompt=system_prompt,
            resume_session_id=None,
            **(await self._model_kwargs(project_id, provider, topic_id))[0],
        ):
            if isinstance(event, AgentToolUse):
                tools_used.append(event.name)
            elif isinstance(event, AgentResult):
                final_text = event.text
                new_session_id = event.session_id

        # Chat was published explicitly; terminal output belongs to hook activity.
        async with self._sessions() as session:
            topics = TopicRepository(session)
            topic = await topics.get(topic_id)
            if topic is not None and new_session_id:
                agent = await self._resolved_agent(session, topic)
                await AgentSessionService(session).remember(
                    topic_id=topic_id,
                    agent_handle=agent.handle,
                    resume_token=new_session_id,
                    # 这一轮真正跑在哪个骨架上，问跑它的那个适配器——平台自己起
                    # 的活没有 agent 类型站在后面，``platform_work`` 给的是这台
                    # 机器跑的东西（结论 28）。
                    harness=runtime.harness,
                )
            await session.commit()

        return {
            "topic_id": str(topic_id),
            "summary": final_text,
            "tools_used": tools_used,
        }

    async def run_heartbeat(self, *, project_id: uuid.UUID) -> dict:
        """定期巡检 (eval G1): runs the heartbeat under the root topic's serial
        lock, so a 本体 patrol never races a user's turn on the same topic."""
        async with self._sessions() as session:
            project = await ProjectRepository(session).get(project_id)
            if project is None or project.root_topic_id is None:
                raise NotFoundError("Project has no root topic")
            root_topic_id = project.root_topic_id
        async with self._lock_for(root_topic_id):
            return await self._run_heartbeat_locked(project_id=project_id)

    async def _run_heartbeat_locked(self, *, project_id: uuid.UUID) -> dict:
        """芝士 (本体) inspects the project against topic 状态 + 里程碑, then sends
        graded notifications via the notify tool. Its reasoning is logged as a
        block in the root topic (施工现场 "为什么催")."""
        # --- gather context from the project ---
        async with self._sessions() as session:
            projects = ProjectRepository(session)
            topics = TopicRepository(session)
            milestones = MilestoneRepository(session)

            project = await projects.get(project_id)
            if project is None:
                raise NotFoundError("Project not found")
            if project.root_topic_id is None:
                raise NotFoundError("Project has no root topic")

            all_topics = await topics.list_for_project(project_id)
            upcoming = await milestones.list_calendar(project_id)
            root_topic_id = project.root_topic_id
            compute_id = _resolve_compute_id(
                project.settings,
            )

        topic_lines = "\n".join(
            f"- {t.title} [{t.status.value}] ({t.kind.value})"
            for t in all_topics
            if t.kind != TopicKind.root
        )
        today = datetime.now(UTC).date()

        def _days_left(m) -> str:
            if not m.due_date:
                return "未定"
            d = (m.due_date.date() - today).days
            return (
                f"{m.due_date.date().isoformat()}（剩 {d} 天）"
                if d >= 0
                else (f"{m.due_date.date().isoformat()}（已逾期 {-d} 天）")
            )

        milestone_lines = "\n".join(
            f"- {m.title} 截止 {_days_left(m)}" for m in upcoming
        )
        # Anchor the patrol in time so 芝士 can reason about 临近/拖延 (spec §7.2).
        context = (
            f"## 今天\n{today.isoformat()}\n\n"
            f"## 项目话题\n{topic_lines or '（暂无）'}\n\n"
            f"## 临近里程碑\n{milestone_lines or '（暂无）'}"
        )

        system_prompt = build_system_prompt(
            self._base_prompt, load_skills(HEARTBEAT_SKILLS), None, []
        )
        prompt = (
            "现在做一次定期巡检。下面是项目当前状态。请：先在回复里写下你的巡检"
            "判断和理由（决策日志：看了什么、该催谁/该拆什么/有什么风险），"
            "然后只对真正需要的事用 cheese_notify 发分级通知（level=silent/light/"
            "strong，kind=heartbeat），别骚扰。\n\n" + context
        )
        provider = self._compute.platform_work(compute_id)
        runtime = runtime_for(provider)
        final_text = ""
        tools_used: list[str] = []
        async for event in runtime.run_turn(
            project_id=project_id,
            topic_id=root_topic_id,
            prompt=prompt,
            system_prompt=system_prompt,
            resume_session_id=None,
            **(await self._model_kwargs(project_id, provider, root_topic_id))[0],
        ):
            if isinstance(event, AgentToolUse):
                tools_used.append(event.name)
            elif isinstance(event, AgentResult):
                final_text = event.text

        # Decision log → a block in the root topic (审计/施工现场).
        async with self._sessions() as session:
            landed = landing(
                EventAbout.project,
                project_id=project_id,
                room_id=root_topic_id,
            )
            await BlockRepository(session).add(
                project_id=landed.project_id,
                topic_id=landed.topic_id,
                task_id=landed.task_id,
                author=await self._agent_handle(session, root_topic_id),
                author_type=AuthorType.participant,
                content=f"【巡检决策日志】\n{final_text}",
                kind=BlockKind.event,
                meta={"in_room": False},
            )
            await session.commit()

        return {"decision_log": final_text, "tools_used": tools_used}

    async def summarize_project(self, *, project_id: uuid.UUID) -> dict:
        """一页纸总结 (spec §7.3 / eval F2): 芝士 writes a current, plain-language
        one-pager so a teacher reads the team's state in 30s. Stored on the
        project; surfaced on the overview and Space board."""
        async with self._sessions() as session:
            projects = ProjectRepository(session)
            topics = TopicRepository(session)
            milestones = MilestoneRepository(session)

            project = await projects.get(project_id)
            if project is None:
                raise NotFoundError("Project not found")
            all_topics = await topics.list_for_project(project_id)
            upcoming = await milestones.list_calendar(project_id)
            # 项目的状态在总览那份实况文档里，不在任何一个记忆池里（结论 7）：
            # 一页纸总结描述的是这个项目，而记忆是某一个芝士自己的观察——拿它当
            # 项目知识用，等于把一个实例看到的东西当成大家的共识写进总结。
            overview_root = (
                await BlockRepository(session).doc_root(project.root_topic_id)
                if project.root_topic_id is not None
                else None
            )
            overview_doc = overview_root.content if overview_root else ""
            agents = AgentInstanceService(session)
            agent = await agents.for_project(project)
            role = await agents.system_prompt(agent)
            compute_id = _resolve_compute_id(
                project.settings,
            )

        topic_lines = "\n".join(
            f"- {t.title} [{t.status.value}]"
            for t in all_topics
            if t.kind != TopicKind.root
        )
        ms_lines = "\n".join(
            f"- {m.title} 截止 {m.due_date.isoformat() if m.due_date else '未定'}"
            for m in upcoming
        )
        context = (
            f"项目名：{project.name}\n\n## 话题\n{topic_lines or '（暂无）'}\n\n"
            f"## 临近里程碑\n{ms_lines or '（暂无）'}\n\n"
            "## 项目总览的实况文档\n"
            + (
                fit_doc_to_budget(
                    overview_doc.strip(),
                    OVERVIEW_DOC_CHAR_BUDGET,
                    full_read_hint="在项目根话题里调 `cheese_doc_get` 读全文",
                )
                if overview_doc.strip()
                else "（暂无）"
            )
        )
        system_prompt = build_system_prompt(
            self._base_prompt,
            "",  # This call returns a project summary, without chat publication.
            None,
            [],
            role,
        )
        prompt = (
            "请基于下面的项目状态，写一份『一页纸总结』：3-5 句话，让老师 30 秒读懂"
            "这个团队在做什么、到哪了、下一步和风险。说人话、不堆术语、不要列工具调用，"
            "直接给总结正文。\n\n" + context
        )
        # Pure text generation (no platform actions) — still runs through the
        # provider (root-topic sandbox when present) for a single execution path;
        # topic_id None (no root topic) degrades to a plain model turn.
        provider = self._compute.platform_work(compute_id)
        runtime = runtime_for(provider)
        final_text = ""
        async for event in runtime.run_turn(
            project_id=project_id,
            topic_id=project.root_topic_id,
            prompt=prompt,
            system_prompt=system_prompt,
            resume_session_id=None,
            **(
                await self._model_kwargs(
                    project_id, provider, project.root_topic_id, agent=agent
                )
            )[0],
        ):
            if isinstance(event, AgentResult):
                final_text = event.text

        async with self._sessions() as session:
            projects = ProjectRepository(session)
            project = await projects.get(project_id)
            if project is not None:
                await projects.set_summary(project, final_text)
            await session.commit()

        return {"summary": final_text}


async def cloud_waiting_topics(
    session: AsyncSession, topic_ids: list[uuid.UUID]
) -> list[uuid.UUID]:
    waiting: list[uuid.UUID] = []
    blocks = BlockRepository(session)
    for topic_id in topic_ids:
        history = await blocks.list_for_topic(topic_id)
        events = [
            b
            for b in history
            if (b.meta or {}).get("event_type") == "cloud_provisioning"
        ]
        if events and (events[-1].meta or {}).get("state") == "waiting":
            waiting.append(topic_id)
    return waiting
