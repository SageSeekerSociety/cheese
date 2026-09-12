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
import shutil
import time
import uuid
from collections import Counter
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.errors import GatewayUnavailableError, NotFoundError, ValidationError
from app.core.text import markdown_preview
from app.domain.agent.compute import ComputePool, ComputeProvider
from app.domain.agent.gateway import LlmGateway, drain_new_usage
from app.domain.agent.harness import Opening, SessionRef, runtime_for
from app.domain.agent.market import (
    subscription_model_alias,
    subscription_model_listings,
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
    AgentSessionInfo,
    AgentSubagentStart,
    AgentSubagentStop,
    AgentToolResult,
    AgentToolUse,
    AgentUsage,
    proves_output,
)
from app.domain.agent.skills import NATIVE_CHAT_GUIDANCE, load_scenario, load_skills
from app.domain.agent.stages import TopicStage, resolve_stage, stage_scenario
from app.domain.agent.supply import SUBSCRIPTION
from app.domain.agent.tool_preview import ToolPreview, tool_preview, work_subpath
from app.domain.agent_instance.configuration import (
    AgentConfiguration,
    validate_configuration,
)
from app.domain.agent_instance.services import (
    IMPLICIT_DEFAULT,
    AgentInstanceService,
    ResolvedAgent,
    legacy_topic_pool,
    memory_pool,
)
from app.domain.agent_session.services import AgentSessionService
from app.domain.alert.models import AlertKind, AlertLevel
from app.domain.alert.services import AlertService
from app.domain.block.models import (
    CONSUMED_TURN_META_KEY,
    AuthorType,
    Block,
    BlockKind,
    consumed_turn,
)
from app.domain.block.repositories import BlockRepository
from app.domain.block.schemas import BlockOut
from app.domain.idempotency import store as idem
from app.domain.idempotency.keys import action_key
from app.domain.identity.actor import Actor
from app.domain.identity.handles import looks_like_agent_handle
from app.domain.memory.models import MemoryScope
from app.domain.memory.store import RecallResult, memory_store, recall_pools
from app.domain.mentions import expand_mention_names
from app.domain.milestone.repositories import MilestoneRepository
from app.domain.project.environment import EnvironmentConfig, pin_environment
from app.domain.project.repositories import ProjectRepository
from app.domain.review.models import AcceptCard, AcceptStatus
from app.domain.review.repositories import AcceptCardRepository
from app.domain.room_task.place import Place, PlaceResolver
from app.domain.topic.models import Topic, TopicKind, TopicStatus
from app.domain.topic.repositories import TopicProgressRepository, TopicRepository
from app.domain.topic_membership.services import TopicMemberService
from app.domain.usage.credits import usage_to_credits
from app.domain.usage.repositories import ComputeGrantRepository, UsageRepository
from app.domain.workspace import service as ws

ACTIVITY_SKILLS = ["chat", "activity-digestion", "doc-form"]
HEARTBEAT_SKILLS = ["heartbeat", "chat"]
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
    is_private: bool
    private_owner: str | None
    acting_agent: str
    # Attribution and memory part ways here, deliberately: `acting_agent` is
    # this room's 分身 (who did it), while the pool belongs to the agent working
    # the room (whose memory it is). Resolved at turn start and carried, because
    # the hook path reaches turn end with no session left open to ask.
    agent_pool: tuple[MemoryScope, str] | None
    user_text: str
    started_at: datetime
    assistant_count: int = 0
    last_chat_at: datetime | None = None
    progress_reminded: bool = False
    todo: list[dict] = field(default_factory=list)
    #: 每个分身自己那份清单，按它做的那条活分开。Claude Code 的任务编号是**每个
    #: agent 各数各的**，都从 1 开始，所以几份清单混进一个 list 里不只是看着乱：
    #: 分身的 `TaskUpdate("1")` 会去勾掉房间自己的第一条。
    worker_todo: dict[str, list[dict]] = field(default_factory=dict)
    actions: list[str] = field(default_factory=list)

    def todo_of(self, work_id: uuid.UUID | None) -> list[dict]:
        """这条事件该记进谁的清单。None = 房间自己的。"""
        if work_id is None:
            return self.todo
        return self.worker_todo.setdefault(str(work_id), [])

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
    is_private: bool
    private_owner: str | None
    untitled: bool

    # What this turn was given, and what it is being asked about.
    prompt_text: str
    pending_ids: list[uuid.UUID]
    turn_images: list[dict]
    replay_notice: str | None
    resume_session_id: str | None

    # What it should know: the doc, the memories, the checklist it left behind,
    # the cards waiting on it, and which段 of the flow this topic is in.
    doc_text: str | None
    memories: RecallResult
    prior_progress: list[dict]
    open_cards: list[AcceptCard]
    topic_stage: TopicStage | None
    topic_refs: list[dict]
    topic_refs_for_prompt: list[dict]

    # Which machine, and whether it reports its own liveness (which decides who
    # owns this turn's clock; see the `turn_ceiling` frame).
    provider: ComputeProvider


@dataclass(frozen=True, slots=True)
class _TurnBail:
    """The turn ended while it was still being assembled, and these are the
    frames that say so. Not an error: nobody was waiting on an answer, or the
    machine is still being built."""

    frames: list[dict]


# How long a message's spooled flushes may sit incomplete (no final flush, no
# Stop) before the reconcile stops waiting for the missing one and lands what
# arrived. Long enough for the device drainer's retry loop to fill a gap;
# short enough that a mid-message death still surfaces its words.
_SPOOL_PARTIAL_GRACE_S = 120
# How long a read event stays on disk before retention drops it. A spool is not
# only the backfill queue — it is the raw record of what a session emitted, and
# the first thing anyone reaches for when a block looks wrong. A day is long
# enough to answer that and short enough that a busy topic's directory stays
# something a person can list.
_SPOOL_RETENTION_S = 24 * 3600


def _persisted_eids(blocks: list[Block]) -> set[str]:
    """Event ids already materialized in the topic timeline. A coalesced
    message block carries every constituent flush id in ``meta.eids``; each
    one counts, or the flushes after the first would backfill as fragments."""
    out: set[str] = set()
    for block in blocks:
        meta = block.meta if isinstance(block.meta, dict) else None
        if not meta:
            continue
        if isinstance(meta.get("eid"), str):
            out.add(meta["eid"])
        eids = meta.get("eids")
        if isinstance(eids, list):
            out.update(e for e in eids if isinstance(e, str))
    return out


# 施工现场: render each tool call like a Claude Code action line — a Chinese verb
# plus a short preview of its most telling argument. Stored in the event block as
# "verb\npreview" (preview omitted when empty).
_TOOL_VERB = {
    "update_doc": "更新文档",
    "remember": "记入记忆",
    "notify": "发送通知",
    "request_accept": "递出验收卡",
    "pin_milestone": "钉里程碑",
    "write_file": "写文件",
    "record_decision": "记录决策",
}


# Native Claude Code tools (sandbox mode) → 现场 labels. Systematic: every tool
# the agent can invoke has a Chinese verb + its most telling argument as the
# preview; an unmapped (future) tool falls back to its raw name, which is the
# signal to extend this table.
_TOOL_VERB.update(
    {
        "Bash": "执行命令",
        "Write": "写文件",
        "Edit": "改文件",
        "Read": "读文件",
        "Glob": "找文件",
        "Grep": "搜内容",
        "WebSearch": "搜网页",
        "WebFetch": "看网页",
        "Agent": "派分身去查",
        "Task": "派分身去查",  # older CLI name for Agent
        "NotebookEdit": "改笔记本",
        "TodoWrite": "更新任务清单",
        "BashOutput": "看命令输出",
        "KillShell": "停掉命令",
        "KillBash": "停掉命令",
        "ExitPlanMode": "提交方案待确认",
        "AskUserQuestion": "向用户提问",
        "Skill": "调用技能",
        "ToolSearch": "查找工具",
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
# Deterministic by construction — tool-name prefix, or a literal `cheese <sub>`
# word pair inside a Bash command. NEVER inferred from natural language.
_CHEESE_CMD_RE = re.compile(r"\bcheese\s+\w+")


def _is_platform_tool(raw_name: str, args: dict) -> bool:
    """True when the tool call is a platform action: a cheese MCP tool, or a
    Bash command that invokes the in-sandbox `cheese` CLI."""
    if raw_name.startswith("mcp__cheese__"):
        return True
    if raw_name == "Bash" and isinstance(args, dict):
        return _CHEESE_CMD_RE.search(str(args.get("command", ""))) is not None
    return False


def _tool_event_meta(name: str, preview: ToolPreview, *, platform: bool) -> dict:
    """Structured payload persisted on an event block: the UI translates the
    tool name and colors the dot from these fields at DISPLAY time, so a verb
    missing from today's table is never baked in untranslated forever.

    ``as_tool`` rides alongside ``tool`` rather than replacing it: ``tool`` says
    what actually ran, ``as_tool`` says whose label reads better (a Bash
    `cat foo.py` is still a Bash call, but 「读取文件」 is what it did). NOT named
    ``action`` — that key already means "which platform resource this card points
    at" (see the frontend's platformNotice), and one name answering two questions
    is how a card ends up pointing at a resource called "Read"."""
    meta: dict = {"tool": name, "platform": platform}
    if preview.text:
        meta["arg"] = preview.text
    if preview.action:
        meta["as_tool"] = preview.action
    return meta


# 分身回吐 (§9 可见性): a subagent reports to whoever spawned it and nothing else,
# so the room used to see 「派分身去查 X」 and never the answer. Its conclusion
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


# Claude Code's structured Task tools → a live working-log todo (§3.1.1). These
# are the *process* (rendered as a checklist in the in-progress message), so they
# are streamed live but NOT persisted as 现场 events.
_TASK_TOOLS = {"TaskCreate", "TaskUpdate", "TaskList", "TaskGet"}

#: How many sessions' supply routes to remember. Well past the number of screens
#: one backend drives at once, so in practice nothing is ever evicted; it is a
#: ceiling on a dict nothing else prunes, not a policy.
_SESSION_ROUTES_KEPT = 512


def _apply_task_event(todo: list[dict], name: str, args: dict) -> bool:
    """Fold a TaskCreate/TaskUpdate event into the todo list. Returns whether the
    list changed (ids are assigned by creation order, matching the model)."""
    if name == "TaskCreate":
        todo.append(
            {
                "id": str(len(todo) + 1),
                "subject": str(args.get("subject", "")).strip() or "（任务）",
                "status": "pending",
            }
        )
        return True
    if name == "TaskUpdate":
        tid = str(args.get("taskId", ""))
        status = str(args.get("status", "")) or "pending"
        for item in todo:
            if item["id"] == tid:
                item["status"] = status
                return True
    return False


# A platform-mutating `cheese <sub>` command → which UI panel should refresh live
# (the doc/decisions/topics/... — restores mid-turn refresh now cheese runs as Bash).
_CHEESE_RESOURCE = {
    "doc": "doc",
    "decision": "decision",
    "split": "topics",
    "conclude": "topics",
    "milestone": "milestone",
    "accept-request": "accept",
    "describe": "accept",
    "notify": "notify",
}


# Persistent, clickable action cards (§3.1.1 控件): each cheese action 芝士 takes
# is recorded as a system event block (shown in the conversation) tagged
# refs=["action:<resource>"], which the UI renders as a card linking to it.
# NOTE: no "doc" entry — a doc edit already lands the SAME 「编辑了文档」
# event every human edit gets (via the save path). One fact, one line,
# whoever the author is (用户拍板: 芝士不需要专属提示行).
_ACTION_LABEL = {
    "decision": "记录了决策",
    "topics": "更新了这个房间的活",
    "milestone": "添加了里程碑",
    "accept": "提交了验收卡",
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
        line = "芝士这轮没跑完：AI 中继余额用尽，要人充值"
        hint = "这不是等一等就能好的，需要有人充值或把机器切到其他 AI 供给；重试无效。"
    else:
        first = detail.splitlines()[0].strip() if detail else ""
        if len(first) > 160:
            first = first[:160] + "…"
        line = (
            f"芝士这轮没跑完：{first}" if first else "芝士这轮没跑完：AI 服务返回错误"
        )
        hint = "稍后再 @ 它重试。"
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
        "芝士连不上 AI 服务，这一步没做成",
        SEVERITY_ERROR,
        WHO_PLATFORM,
        "这个多半不会自己好:要么是这台机器上的隧道助手掉了,要么是中继在丢连接。"
        "先重新 @ 它一次;还是连不上就该找人看机器,不要反复重试。",
    ),
    PROVIDER_OVERLOADED_CODE: (
        "AI 服务暂时过载，这一步没做成",
        SEVERITY_WARN,
        WHO_PLATFORM,
        "服务端的事,通常一会儿就好。稍后再 @ 它一次。",
    ),
    MODEL_LIMIT_REACHED_CODE: (
        "这个模型的额度用完了",
        SEVERITY_ERROR,
        WHO_HUMAN,
        "这不是等一等就能好的:要换一个模型,或者等额度恢复。重试无效。",
    ),
    TOOL_UNAVAILABLE_CODE: (
        "芝士想用的一个工具没能用上",
        SEVERITY_WARN,
        WHO_PLATFORM,
        "它在等一个没有人能给的授权 —— 那个框画在容器的终端里，房间里够不着。"
        "这说明这台机器上的工具配置不对，要人去看，重试不会有变化。",
    ),
    RESPONSE_TRUNCATED_CODE: (
        "上面那条回复没说完就断了",
        SEVERITY_WARN,
        WHO_PLATFORM,
        "上面那条可能是半截。要它接着说就再 @ 它一次。",
    ),
}


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
    )


def _parse_uuid(raw: str | None) -> uuid.UUID | None:
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


def _cheese_resource(command: str) -> str | None:
    """Resource hint for a Bash `cheese <sub>` command, else None."""
    parts = command.split()
    for i, tok in enumerate(parts):
        if tok.endswith("cheese") and i + 1 < len(parts):
            return _CHEESE_RESOURCE.get(parts[i + 1])
    return None


# B2 (引用语法遵循): a bare "backend/app/x.py" inside an injected memory fact is
# a bad few-shot example — the model imitates whatever shape the prompt shows,
# so bare paths in memories beget bare paths in docs/replies. Wrap path-looking
# tokens as <&path> before injection so the prompt itself models the correct
# form. Conservative on purpose: needs ≥1 slash + an extension; a leading "/",
# "://" or "&" (already-wrapped / absolute / URL) disqualifies via lookbehind.
_BARE_PATH_RE = re.compile(
    r"(?<![\w/.&<-])((?:[\w.-]+/)+[\w-]+\.\w{1,8}(?::\d+(?:-\d+)?)?)(?![\w/])"
)


def _chipify_paths(fact: str) -> str:
    return _BARE_PATH_RE.sub(r"<&\1>", fact)


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

_OPEN_CARD_HINTS = {
    AcceptStatus.pending: (
        "等 {reviewer} 采纳——采纳即当场合并；改动提交在本分支上，"
        "要让 PR 立刻看到用 `cheese push-fix`"
    ),
    AcceptStatus.pending_gate: "闸门检查进行中",
    AcceptStatus.gate_failed: (
        "闸门检查未过——用 `cheese status` 看失败输出，修复后重新递卡"
    ),
    AcceptStatus.gate_blocked: (
        "闸门检查没跑成（不是没通过，是没跑起来）——用 `cheese status` 看输出，"
        "把检查环境弄起来再重新递卡"
    ),
    AcceptStatus.conflict: "采纳时发现合并冲突，待处理",
}


def _workspace_disk(root: str) -> tuple[int, int] | None:
    """(free, total) bytes of the workspace filesystem; None when the root
    doesn't exist (fresh deploy, unit tests without a workspace)."""
    try:
        du = shutil.disk_usage(root)
    except OSError:
        return None
    return du.free, du.total


_PROGRESS_MARK = {"completed": "x", "in_progress": "~", "pending": " "}


def _progress_lines(items: list[dict]) -> list[str]:
    """进度层 (#187): the checklist this topic's work left behind, as prompt text.

    This is the one thing a fresh machine cannot reconstruct from the repo. Code
    survives in git, conclusions survive in the doc and the decision log, but
    "which of the five things am I on" only ever lived in the dead turn's stream.
    So it is stated here as a fact about the topic, not as memory — see
    TopicProgress's docstring for why the two must not be merged.

    The instruction to re-list finished items when building a new checklist is
    load-bearing: the stored row is overwritten by the next turn's first
    TaskCreate, so a plan that silently drops what is already done would erase it.
    """
    if not items:
        return []
    lines = ["- 上次的任务清单（跨轮、跨机器保留下来的进度，不是这一轮新建的）："]
    for item in items:
        mark = _PROGRESS_MARK.get(str(item.get("status", "")), " ")
        subject = str(item.get("subject", "")).strip() or "（任务）"
        lines.append(f"    - [{mark}] {subject}")
    lines.append(
        "  已完成的别重做，接着没做完的往下干。**重新建清单时把已完成的也列进去"
        "并标成 completed**——清单会覆盖上面这份，只列剩下的等于把做过的抹掉。"
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


def _turn_meta_lines(
    *,
    is_resume: bool,
    disk: tuple[int, int] | None,
    open_cards: list[AcceptCard] | None,
    progress: list[dict] | None = None,
    sandbox: tuple[int, int] | None = None,
) -> list[str]:
    """盲飞防护: the run facts an agent has no other way to see — whether it's a
    continuation, disk headroom, and where this topic's accept cards stand.
    Plain bullet lines so the prompt stays small.

    There is no countdown line because there is no countdown (turn 活跃度检测):
    a session ends on real idleness (checked, then confirmed dead) or a
    many-hours hard ceiling, never on a fixed minute count. Saying otherwise was
    observed making the agent rush (dev, 2026-08-08: it shortened verification
    to "save time" against a deadline that was only ever a wedged-turn safety
    net)."""
    lines = [
        "- 这轮跑在有活跃度检测的后端上：没有固定时长倒计时，只要还在"
        "产生动静（工具调用、终端输出）就不会被打断，真的卡死了才会兜底"
        "结束。长活照样要边做边落盘/提交，别把成果都压在最后一步。"
    ]
    if is_resume:
        lines.append(
            "- 本轮接着上一轮跑：上一轮中途断了，这是同一件事的继续。"
            "先确认上一轮做到哪了再继续（翻消息记录、git status），别凭印象重做。"
        )
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

    # 进度层: right after the resume line on purpose — that line tells the agent
    # to work out where it got to, and until now the platform gave it nothing to
    # work that out FROM. It is listed for every turn, not just resumes: a topic
    # picked up days later on a different machine has the same problem.
    lines.extend(_progress_lines(progress or []))
    if disk is not None:
        free_b, total_b = disk
        if total_b > 0:
            used_pct = round((total_b - free_b) * 100 / total_b)
            line = f"- 工作区磁盘：可用 {free_b / 2**30:.1f}G（已用 {used_pct}%）。"
            if used_pct >= 90:
                line += "空间紧张——先清理自己产生的临时文件再写大文件。"
            lines.append(line)
    for card in open_cards or []:
        hint = _OPEN_CARD_HINTS.get(card.status)
        if hint:
            lines.append(
                "- 本话题验收卡：" + hint.format(reviewer=f"@{card.reviewer_handle}")
            )
    lines.append("- 要看完整平台状态（验收卡/闸门输出/额度），运行 `cheese status`。")
    return lines


def _build_system_prompt(
    base: str,
    skills: str,
    doc: str | None,
    memories: list[str],
    role: str | None = None,
    roster: list[dict] | None = None,
    topics: list[dict] | None = None,
    untitled: bool = False,
    turn_meta: list[str] | None = None,
    stage_guide: str | None = None,
    memories_omitted: int = 0,
    memories_core: int = 0,
    memories_core_omitted: int = 0,
) -> str:
    parts = [base]
    if untitled:
        # First in the prompt on purpose: naming the topic is the FIRST action
        # of the session — before the opening reply, before any other tool —
        # so the rail never shows a working-but-unnamed 「新话题」.
        parts.append(
            "## 本轮第一件事：先给本话题起名（先于一切）\n"
            "本话题还叫「新话题」（未命名）。**本轮的第一个动作**——在说开场白、"
            "回复任何内容、调用任何其他工具之前——先根据用户的需求执行 "
            '`cheese title "<标题>"` 起个 ≤12 字简短标题，然后再照常回应、干活。'
            "这条优先于「先回应，再干活」：起标题只是一条命令，几乎不花时间。"
            "（只起一次，定了别反复改。）"
        )
    if role:
        parts.append(f"## 你的专家角色\n{role}")
    if skills:
        parts.append(skills)
    if stage_guide:
        # 按阶段渐进式披露: the flow knowledge for THIS point in the topic's
        # lifecycle only. Statically injected (like every other skill) — the
        # model never gets to decide whether to load it, which is the whole
        # reason this isn't a lazily-read Agent Skill (see stages.py).
        parts.append(
            "## 当前阶段的操作说明（平台按本话题所处的流程阶段自动选出，"
            "只给你这一段）\n" + stage_guide
        )
    if topics:
        lines = "\n".join(f"- {t['title']}" for t in topics)
        parts.append(
            "## 项目话题（交叉引用某个话题/它的文档时，在标题前加 @，如 "
            "`@搭建推荐算法原型`——会渲染成可点的「#标题」链接）\n"
            "下面**只列当前活跃的话题**。项目里还有已归档的话题，它们照常存在、"
            "内容也照常可读，只是不在这里列出来；**没列出来 ≠ 不存在**。需要找"
            "它们时自己查（返回全部话题，含 archived 的标题和 id）：\n"
            '`cheese api GET "/topics?project_id=$CHEESE_PROJECT"`\n'
            "拿到 id 后用 `<#id>` 就能精确引用任何一个话题（包括没列在下面的）。\n"
            + lines
        )
    if roster:
        lines = "\n".join(
            f"- {m['name']}（{m['role']}，handle: {m['handle']}）" for m in roster
        )
        parts.append(
            "## 项目成员 & 怎么点名\n"
            "要让某人去做事/通知到他，**在他名字前加 @**（如 `@张衡`，名字用下表"
            "准确值）——平台会把它变成可点的「@张衡」链接并给他**强提醒**。"
            "只写名字而不加 @ 只是普通文字，不会通知。\n" + lines
        )
    if doc:
        parts.append(
            "## 当前话题的实况文档（这是最新状态；用户可能编辑了它，"
            "请按它继续工作，并在状态变化时用 update_doc 工具更新它）\n" + doc
        )
    if memories or memories_omitted:
        core = [f"- {_chipify_paths(m)}" for m in memories[:memories_core]]
        retrieved = [f"- {_chipify_paths(m)}" for m in memories[memories_core:]]
        block = "## 项目记忆（你已知道的事实，回答时可引用）"
        if core:
            block += "\n\n### 核心记忆（每轮都在场，与本轮说什么无关）\n" + "\n".join(
                core
            )
        if retrieved:
            block += (
                "\n\n### 本轮检索到的记忆（按本话题/本轮消息挑出来的，**不是全部**）\n"
                + "\n".join(retrieved)
            )
        if memories_omitted:
            # 没注入必须可见: what did not come in is stated, never dropped in
            # silence. A reader who cannot tell "nothing was stored" from "this
            # turn did not ask for it" stops trusting memory entirely — and
            # stops asking for the part it can still get.
            block += (
                f"\n\n> ⚠️ 记忆池里还有 **{memories_omitted} 条这一轮没注入**"
                "（按与本轮上下文的相关性排的，排在后面的没进来；不是不存在）。"
                "**没列出来 ≠ 不存在**——换个话题、要用到某条旧约定或踩过的坑时，"
                '用 `cheese recall "<关键词>"` 现查；一次没查到也不等于没有，'
                "换个说法、用更短的词再试一次。"
            )
        if memories_core_omitted:
            # Core is the layer that is supposed to be unconditional. If even
            # it had to be cut, saying so is the only way it gets pruned.
            block += (
                f"\n\n> ⚠️ **核心记忆超预算了**：有 {memories_core_omitted} 条核心记忆"
                "没放下。核心记忆本该每轮全在场，出现这种情况说明它被当成普通记忆写"
                "了——挑几条降级成普通记忆（`cheese remember` 不加 `--core`）。"
            )
        parts.append(block)
    if turn_meta:
        parts.append(
            "## 本轮运行环境（平台元信息，非用户输入）\n" + "\n".join(turn_meta)
        )
    return "\n\n".join(parts)


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
# it via `cheese title` (titles are AI-generated, never deterministically derived
# from human input or the agent's output — see CLAUDE.md).
PLACEHOLDER_TITLE = "新话题"


def _topic_ref_lists(
    topics: list[Topic], *, exclude_id: uuid.UUID
) -> tuple[list[dict], list[dict]]:
    """一次推导出两份话题列表：`(全量解析表, 渲染进 prompt 的子集)`。

    故意成对返回：这两份**必须**从同一批话题推导，且**必须**保持不同。全量那份
    喂给 `expand_mention_names`（`@标题` → `<#id>` 的解析表，含已归档话题）；子集
    那份只喂给 `_build_system_prompt`。合成一份就会把"少注入"变成"少了引用能力"
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


# 新话题开工首轮的内部指令 (讨论升级出一个房间时的 auto-kickoff)。Prompt-only: it
# never appears as a message; what the humans see is 芝士's own opening, generated
# from the brief preset as the topic's living doc (语义内容由 AI 生成 — see
# CLAUDE.md). A ROOM is the only thing this still starts: work inside a room is a
# 分身 in that room's own session, and the room is what raises it.
KICKOFF_PROMPT = (
    "这个话题刚从一条消息升级出来，由你负责推进。任务简报在系统提示的"
    "「当前话题的实况文档」里：被升级的那段讨论 + 它原来所在地方的文档快照。"
    "现在开工：\n"
    "1. 先发开场白：一两句复述你理解的任务、说明打算怎么推进（给人纠偏的机会）；"
    "简报信息不足就明确列出缺什么、@ 升级发起人补充。\n"
    "2. 把实况文档改写成你自己的状态摘要（目标/约束/下一步），别留着简报原文不动。\n"
    "3. 能直接开始的活就开始干；需要拍板的用决策请求找对的人。"
)


def thread_relay_prompt(
    *, task_id: uuid.UUID, task_title: str, author: str, message: str
) -> str:
    """The ROOM's wake-up instruction when a person says something on one of its
    threads — a chat message, a comment on its living doc.

    Same reason as 补证据 and 讨论升级: the person is looking at the thread, but
    the worker doing it lives in the room's session, so the room is the only
    thing that can hear them. What was said stays where it was said — this only
    says who has to act on it.
    """
    return (
        f"有人在活「{task_title}」（task id `{task_id}`）上说话了：\n\n"
        f"---\n[{author}] {message}\n---\n\n"
        "**转达给做这条活的分身**：它还在跑就直接给它发消息；已经收工了，你就自己"
        "看着办——能替它答的当场答，要接着干的照原来的简报重起一个分身并 "
        f"`cheese bind {task_id} <新的 agent_id>`。"
        "回话说在这条活上（`cheese tell` 到它），别只在房间里说，"
        "问话的人看的是那边。"
    )


def thread_upgraded_prompt(*, task_id: uuid.UUID, source_message: str) -> str:
    """The ROOM's wake-up instruction when one of its messages became a thread.

    Addressed to the room because a thread is a 分身 inside the room's own
    session and has no session to wake. The platform writes the row, its card
    block and its brief; raising the worker is the room's, and so is naming the
    thread — it is created untitled and nothing else is in a position to name it.
    """
    return (
        f"你把一条消息升级成了这个房间里的一条活（task id `{task_id}`）。"
        "被升级的那段话就是它的简报，平台已经记在卡上了：\n\n"
        f"---\n{source_message}\n---\n\n"
        "接下来是你的事：\n"
        f'1. `cheese title "<≤12 字的标题>" --task {task_id}`——它现在还叫「新话题」，'
        "只有你能给它起名字。\n"
        "2. 用你的 Agent 工具起一个分身，**把上面这段简报原文放进它的 prompt**"
        "（分身不会自己去读文档）。\n"
        f"3. `cheese bind {task_id} <分身的 agent_id>`——不 bind，这条活在界面上"
        "永远是「没人做」，分身干的每件事都记在你头上。"
    )


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


# How a platform-initiated turn announces itself. A resume nudge, a 分身's
# kickoff and a returned conclusion are NOT anyone speaking, and until now they
# reached 芝士 as bare text indistinguishable from a person's message. Claude
# Code frames its own non-user input the same way ("The user sent a new message
# while you were working:" for a human, a peer marker for another session); this
# is the platform's equivalent for the one channel it owns.
PLATFORM_NOTICE = "【平台】以下是平台自动发出的指令，不是任何人手打的话："


def platform_prompt(content: str) -> str:
    return f"{PLATFORM_NOTICE}\n{content}"


def publication_prompt(content: str, *, is_private: bool = False) -> str:
    """Carry the chat contract on new and resumed terminal input alike."""
    if is_private:
        return (
            content
            + "\n\n"
            + platform_prompt(
                "这是私聊，最终答复会自动发布给用户。直接回答，"
                "不要再用 cheese chat send 重复发送同一答复。"
            )
        )
    return (
        content
        + "\n\n"
        + platform_prompt(
            "普通输出和最终答复都不会自动发到聊天。请用 cheese chat send 发送给用户。"
            "收到需要回应的用户消息（包括排队或执行中追加的消息）时，能直接回答就发答案；"
            "需要继续处理就先说明你理解的意思和接下来要做什么，再继续。"
            "重要进展、改方向、阻碍和完成结果也要主动发消息。"
            "巡检按 heartbeat 的通知规则发言；分身向主 agent 回报。"
        )
    )


def _strip_platform_notice(text: str) -> str:
    """Neutralize the platform marker inside HUMAN text, so a person cannot type
    a message that reads as a platform instruction. The marker is the one thing
    in the prompt that claims institutional authority, so it has to be
    unforgeable from the content side."""
    return text.replace(PLATFORM_NOTICE, "【平台·用户原文】")


def _attachment_prompt_line(
    author: str, path: str, *, embeds_images: bool, mime: str = "image/png"
) -> str:
    if not mime.startswith("image/"):
        return f"[{author}] 发来一个文件：{path}。请用适合该格式的工具读取文件内容。"
    if embeds_images:
        return (
            f"[{author}] 发来一张图片（图片内容已附在本条消息里；"
            f"它同时存在你工作目录的 {path}）"
        )
    return (
        f"[{author}] 发来一张图片：**它没有附在本条消息里**，"
        f"文件在你工作目录的 {path}，需要你自己用 Read 打开它。"
        f"（打不开就直说打不开，不要猜图里是什么。）"
    )


def _prompt_line(b, *, embeds_images: bool) -> str:
    """One speaker-labelled prompt line per pending human block.

    An attachment block is a worktree image, and the line has to describe how it
    actually arrives THIS turn — which is not the same on every backend:

    - ``embeds_images``: the provider produces a native image block. SDK/relay
      providers embed base64 directly; interactive Claude Code resolves the
      prompt's ``@path`` through its native attachment path after a remote
      device has acknowledged staging the bytes.
    - a third-party provider that declares ``embeds_images=False`` gets the
      explicit Read fallback and must not claim the image was attached.

    The wording is load-bearing, not cosmetic. Told "图片内容已附在本条消息里"
    and handed nothing, an agent does not raise — it writes a confident answer
    about a picture it never saw, and nothing downstream marks that answer as
    invented. Saying "去打开这个文件" fails safe: worst case it reports it could
    not read the path."""
    if b.kind == BlockKind.attachment:
        return _attachment_prompt_line(
            b.author, b.content, embeds_images=embeds_images, mime=b.mime_type or ""
        )
    return f"[{b.author}]: {_strip_platform_notice(b.content)}"


# How much of a turn is used to retrieve memory against. A turn is not a
# question — it is a title, a few messages and a live doc — and all of it is
# signal, but past a couple of thousand characters the keyword set stops
# discriminating between facts and starts matching everything equally.
_MEMORY_QUERY_CHARS = 2000


def _memory_query(*parts: str | None) -> str:
    """What this turn is about, as one string, to retrieve memory against.

    Pass the parts in descending order of how much they say about *this* turn —
    what was just said, then the topic's title, then its doc. The cap cuts from
    the tail, so a long doc can never crowd out what somebody just asked.
    """
    return "\n".join(p.strip() for p in parts if p and p.strip())[:_MEMORY_QUERY_CHARS]


def _pending_human_blocks(history: list[Block]) -> list[Block]:
    """The human messages/attachments no agent turn has read into a prompt yet.

    轮次边界按**归属**划，不按位置划：一条在轮次运行中到达的人类消息，created_at
    排在那轮 AI 回复之前，所以"最后一条 AI 消息之后"这个窗口会把它切掉 —— 而且
    切掉就再也捡不回来了（那个下标只会往前走）。这里改成挑「没被任何一轮盖过
    consumed 戳」的块，戳由跑完的轮次自己盖上（BlockRepository.mark_consumed）。

    New inputs carry an explicit ``consumed_turn: null`` marker while pending.
    That presence matters: a newer mid-turn input can be receipted before an
    older queued attachment, so no consumed block may act as a positional
    watermark over another tracked input. Legacy blocks have no marker and keep
    the old "after the latest AI message" fallback.

    `history` 已按 created_at 升序。
    """
    legacy_watermark = -1
    for i, b in enumerate(history):
        if b.author_type == AuthorType.ai and b.kind == BlockKind.message:
            legacy_watermark = i
    return [
        b
        for i, b in enumerate(history)
        if _is_human_input(b)
        and consumed_turn(b) is None
        and (CONSUMED_TURN_META_KEY in (b.meta or {}) or i > legacy_watermark)
    ]


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


def _is_human_input(b: Block) -> bool:
    """A block that carries something a person said to 芝士 this turn."""
    return b.author_type == AuthorType.human and b.kind in (
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
        # Where each live session's model traffic goes, remembered from the last
        # turn the platform assembled for it. A turn the session starts by itself
        # rides the same screen and therefore the same supply, and has no prompt
        # of its own to resolve one from. Insertion-ordered and trimmed from the
        # front: nothing tells this service a screen is gone, so without a bound
        # this is a dict that only ever grows in a process that runs for weeks.
        # Losing an entry costs the accuracy of one label, never a wrong charge.
        self._session_route: dict[uuid.UUID, str] = {}
        # Strong refs to in-flight post-turn memory-extraction tasks (asyncio
        # only keeps weak refs; without this a pending commit could be GC'd).
        self._memory_tasks: set[asyncio.Task] = set()
        # Debounce + strong refs for background spool settles (attach 收账,
        # #316): topics with a settle already scheduled, and the tasks running
        # them so they can't be GC'd mid-drain.
        self._settle_pending: set[uuid.UUID] = set()
        self._settle_tasks: set[asyncio.Task] = set()

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
        self, topic_id: uuid.UUID, work_id: uuid.UUID
    ) -> AsyncIterator[None]:
        async with self._lock_for(topic_id):
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
        summon: bool = True,
        turn_id: uuid.UUID | None = None,
        reply_to: str | None = None,
        attachments: list[dict] | None = None,
        is_resume: bool = False,
        resume_reason: str | None = None,
        nudge_event: str | None = None,
        nudge_meta: dict | None = None,
        continuation_id: uuid.UUID | None = None,
        provision_actor: Actor | None = None,
    ) -> AsyncIterator[dict]:
        """Post the human message instantly, then (if summoned) run the agent
        turn serialized per topic (spec §9.1 串行队列). 现场必须实时: the human
        block persists + broadcasts BEFORE the lock, so a post never queues
        behind a running agent turn. turn_id groups this turn's blocks (R4);
        reply_to threads this message under another (B3); attachments are
        uploaded worktree images this message carries (图片输入).

        ``continuation_id`` is the logical unit of work this turn belongs to — a
        turn and every auto-resume of it share one, so a message the interrupted
        attempt already posted is not posted again (④)."""
        # Record the arrival-time state before persistence and acknowledgements.
        # If live work ends during either operation, queueing is still a fallback
        # from the user's attempted live handoff and must be reported.
        live_delivery_expected = (
            summon
            and not is_resume
            and nudge_event is None
            and self.has_running_turn(topic_id)
        )
        if is_resume or nudge_event:
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
            if not nudge_event:
                nudge_event = resume_reason or "平台重发了上一轮的消息"
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
            user_payloads, user_block_id, user_block_ids = await self.post_user_message(
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

            # Slack-style receipt: the PLATFORM (not the model) puts 芝士's ✅
            # on the summoning message the moment its turn is underway — a
            # deterministic ack. Only a real human summon gets it: a resume /
            # nudge / kickoff turn has no user block and skips this branch.
            ack = await self.ack_summon(user_block_id, topic_id)
            if ack is not None:
                yield {"type": "reaction", **ack}

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

        async with self._prompt_lock(topic_id, turn_id):
            async for frame in self._converse_impl(
                topic_id=topic_id,
                content=content,
                turn_id=turn_id,
                user_block_id=user_block_id,
                is_resume=is_resume,
                continuation_id=continuation_id,
                provision_actor=provision_actor,
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
    ) -> AsyncIterator[dict]:
        """Run the AI half of a human message that is already durable.

        ``AgentWorkRunner.submit_message`` owns the receive-before-admission ordering;
        this method starts only after the project gate admits the model work.
        """
        ack = await self.ack_summon(user_block_id, topic_id)
        if ack is not None:
            yield {"type": "reaction", **ack}
        async with self._prompt_lock(topic_id, turn_id):
            async for frame in self._converse_impl(
                topic_id=topic_id,
                content=content,
                turn_id=turn_id,
                user_block_id=user_block_id,
                continuation_id=continuation_id,
                provision_actor=provision_actor,
            ):
                yield frame

    async def merge_into_running_turn(
        self,
        topic_id: uuid.UUID,
        user_block_ids: list[uuid.UUID],
        content: str,
        author: str,
        attachments: list[dict] | None = None,
    ) -> bool | None:
        """Inject a just-posted human message into the turn already running on
        this topic.

        ``True`` means the live session acknowledged the message, ``False``
        means live delivery was attempted but failed, and ``None`` means no live
        work remained by the time this method checked. Callers use that third
        state to distinguish a normal new message from a raced fallback.

        The text is labelled the same way `_prompt_line` labels a pending block,
        so a message that arrives mid-turn reads identically to one that came in
        the prompt — 芝士 must not have to tell the two apart to know who spoke.

        Images use the same @path input path as an initial prompt. Interactive
        providers resolve that path into a native image block before the model
        sees the message; a remote device first stages the exact bytes and acks
        the file write."""
        consuming_turn_id = self._active_turn_ids.get(topic_id)
        if consuming_turn_id is None:
            return None
        lines = []
        if content:
            lines.append(f"[{author}]: {_strip_platform_notice(content)}")
        images = [
            {
                "path": str(attachment.get("path") or ""),
                "media_type": str(attachment.get("mime") or "image/png"),
            }
            for attachment in attachments or []
            if attachment.get("path")
        ]
        lines.extend(
            _attachment_prompt_line(
                author, image["path"], embeds_images=True, mime=image["media_type"]
            )
            for image in images
        )
        state = self._hook_work.get((topic_id, consuming_turn_id))
        line = publication_prompt(
            "\n".join(lines), is_private=bool(state and state.is_private)
        )
        # Register BEFORE the write so a fast receipt cannot race the entry
        # (#539 decision A). The receipt is still the consumed boundary — it
        # just no longer gates the delivery verdict: write-accept is delivery,
        # and the stamp lands whenever the session actually consumes the text
        # (confirm_prompt_receipt). Until then the message stays pending, so a
        # session death replays it — 宁可重复不可丢失.
        pending = self._pending_receipts.setdefault(topic_id, [])
        entry = (
            line,
            list(user_block_ids),
            consuming_turn_id,
            asyncio.get_running_loop().time(),
        )
        pending.append(entry)
        del pending[:-16]  # a dead session must not grow this forever
        try:
            async with self._sessions() as session:
                room = await TopicRepository(session).lock(topic_id)
                if room is None or room.status == TopicStatus.archived:
                    delivered = False
                else:
                    delivered = (
                        await self._compute.deliver(topic_id, line, images=images)
                        if images
                        else await self._compute.deliver(topic_id, line)
                    )
                await session.commit()
        except Exception:  # noqa: BLE001 — caller reports the queued fallback
            logger.exception("merge into running turn failed (topic=%s)", topic_id)
            delivered = False
        if not delivered:
            if entry in pending:
                pending.remove(entry)
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
            and not state.is_private
            and not state.progress_reminded
            and (now - (state.last_chat_at or state.started_at)).total_seconds()
            >= settings.chat_progress_reminder_after_s
            and self._active_turn_ids.get(state.topic_id) == state.work_id
        ]

        async def remind(state: _HookWorkState) -> bool:
            if (
                self._hook_work.get((state.topic_id, state.work_id)) is not state
                or self._active_turn_ids.get(state.topic_id) != state.work_id
            ):
                return False
            try:
                # Once per silent stretch. Only a new publication re-arms this;
                # tool output and duplicate send requests do not.
                state.progress_reminded = True
                # A blocked terminal must not hold up reminders in other rooms.
                async with asyncio.timeout(5):
                    return await self.notify_running_turn(
                        state.topic_id,
                        "If you are still working on a response and have not "
                        "posted an update since this reminder was queued, "
                        "use cheese chat send to tell the user what is known "
                        "and what you are waiting for. If you have finished, "
                        "ignore this reminder.",
                    )
            except Exception:  # noqa: BLE001 — one room must not stop the sweep
                logger.exception(
                    "chat progress reminder failed (topic=%s)", state.topic_id
                )
                return False

        return sum(await asyncio.gather(*(remind(state) for state in due)))

    async def notify_running_turn(self, topic_id: uuid.UUID, notice: str) -> bool:
        """Tell the turn already running on this topic that the world changed
        under it. Returns whether the live session took it.

        The same channel as a person's mid-turn message, carrying the other kind
        of thing a turn needs to hear. A long turn is built on a snapshot taken
        at its first second — the doc, the roster, the cards — and until now the
        only way anything could reach it afterwards was somebody typing. So a
        person editing the living doc mid-turn changed nothing 芝士 could see,
        and it kept working from, and writing back, the version it started with.

        Framed as a platform notice, and the body is neutralized first: the
        marker is the one thing in a prompt that claims institutional authority,
        so a heading someone typed into the doc must not be able to carry it in.

        A notice that does not land is dropped rather than replayed. It says
        what is true right now — the next turn reads the doc fresh anyway — and
        a version claim replayed into a later session is worse than silence.
        """
        if topic_id not in self._active_turn_ids:
            return False
        try:
            line = platform_prompt(_strip_platform_notice(notice))
            return bool(await self._compute.deliver(topic_id, line))
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

    def has_running_turn(self, topic_id: uuid.UUID) -> bool:
        """Whether this process currently owns live work for the topic."""
        return topic_id in self._active_turn_ids

    async def has_unread_human_input(self, topic_id: uuid.UUID) -> bool:
        """Whether anything a person said is still waiting to reach 芝士.

        「忘了 @」的补救按钮问的就是这一句，所以它必须和真正组装 prompt 时问的
        是同一个问题 —— 同一个 `_pending_human_blocks`，不是一份近似的复制品。
        一份复制品会在窗口语义改动时悄悄和它分叉，而分叉的表现是按钮说「它还没
        看到」、点下去却什么也没有可读，白烧一轮。
        """
        async with self._sessions() as session:
            history = await BlockRepository(session).list_for_topic(
                topic_id, task_id=None
            )
            return bool(_pending_human_blocks(history))

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

    async def kickoff(
        self,
        *,
        topic_id: uuid.UUID,
        turn_id: uuid.UUID | None = None,
        prompt: str | None = None,
    ) -> AsyncIterator[dict]:
        """An agent turn triggered by a PLATFORM EVENT, not a posted message:
        分身自动开工 after a split/upgrade (default prompt), or the parent
        digesting a returned conclusion (custom prompt). No fake human block is
        posted — the instruction is prompt-only, so the visible result is only
        what the agent itself says/does (语义内容由 AI 生成 — CLAUDE.md)."""
        turn_id = turn_id or uuid.uuid4()
        async with self._prompt_lock(topic_id, turn_id):
            async for frame in self._converse_impl(
                topic_id=topic_id,
                content=prompt or KICKOFF_PROMPT,
                turn_id=turn_id,
                user_block_id=None,
                # Same default converse() applies: the turn is its own unit of
                # work, so an auto-resume re-saying a message it already posted
                # is recognized (④) — kickoff turns ran unprotected before.
                continuation_id=turn_id,
                platform_turn=True,
            ):
                yield frame

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
        Returns the block payload, or None if the topic died."""
        async with self._sessions() as session:
            blocks = BlockRepository(session)
            place = await PlaceResolver(session).resolve(topic_id)
            if place is None:
                return None
            block = await blocks.add(
                project_id=place.project_id,
                topic_id=place.room_id,
                author="system",
                author_type=AuthorType.system,
                content=content,
                kind=BlockKind.event,
                turn_id=turn_id,
                meta=meta,
            )
            payload = _block_payload(BlockOut.model_validate(block))
            await session.commit()
        return payload

    async def cloud_waiting_topics(self, topic_ids: list[uuid.UUID]) -> list[uuid.UUID]:
        """Topics whose latest durable Cloud lifecycle event is still waiting."""
        waiting: list[uuid.UUID] = []
        async with self._sessions() as session:
            blocks = BlockRepository(session)
            for topic_id in topic_ids:
                history = await blocks.list_for_topic(topic_id)
                cloud_events = [
                    block
                    for block in history
                    if (block.meta or {}).get("event_type") == "cloud_provisioning"
                ]
                if (
                    cloud_events
                    and (cloud_events[-1].meta or {}).get("state") == "waiting"
                ):
                    waiting.append(topic_id)
        return waiting

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

    async def settle_spool(self, topic_id: uuid.UUID) -> int:
        """Drain the topic's hook spool NOW, with no turn required. Returns how
        many blocks were landed (each is also broadcast on the topic channel).

        The reconcile normally runs at the next turn's start — which is exactly
        never for an orphan the sweep attaches to instead of re-prompting
        (#316): the events the surviving claude keeps sending (its Stop
        included) would sit parked until a human happened to speak. This is
        that missing drain: the same `_reconcile_spool`, under the same topic
        lock so it can never race a turn's own reconcile, frames published to
        the broker so open clients see the backfill live."""
        async with self._sessions() as session:
            topic = await TopicRepository(session).get(topic_id)
        if topic is None:
            return 0
        from app.domain.agent.runtime import get_broker

        broker = get_broker()
        channel = str(topic_id)
        landed = 0
        async with self._lock_for(topic_id):
            async for frame in self._reconcile_spool(topic.project_id, topic_id, None):
                await broker.publish(channel, frame)
                landed += 1
        return landed

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
                # What the room already shows, so a message the live path DID
                # persist before this process died is not landed twice. The room
                # is ours; which of the harness's own records are still unlanded
                # is the harness's.
                await self._compute.replay(
                    session, known_texts=await self._said(session)
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
            if block.author_type == AuthorType.ai
            and (block.kind == BlockKind.message or (block.meta or {}).get("progress"))
        }

    def schedule_spool_settle(self, topic_id: uuid.UUID, delay_s: float = 2.0) -> None:
        """Debounced background ``settle_spool``. Called after a live turn ends,
        so idle time can advance its replay cursor; by the hooks
        endpoint when an event arrives with no turn listening — nothing will
        read what it just wrote until something goes looking, so a working
        claude's progress (and its Stop) lands within seconds instead of waiting
        for the next summon — and the orphan sweep when it attaches to an
        interrupted turn, so anything already waiting lands now."""
        if topic_id in self._settle_pending:
            return
        self._settle_pending.add(topic_id)

        async def _later() -> None:
            try:
                await asyncio.sleep(delay_s)
                # Cleared BEFORE the drain: a hook parked mid-settle must be
                # able to schedule the next round rather than being missed.
                self._settle_pending.discard(topic_id)
                landed = await self.settle_spool(topic_id)
                if landed:
                    logger.info(
                        "spool settle landed %d block(s) for topic %s",
                        landed,
                        topic_id,
                    )
            except Exception:  # noqa: BLE001 — a settle must never crash the loop
                self._settle_pending.discard(topic_id)
                logger.exception("spool settle failed for topic %s", topic_id)

        task = asyncio.create_task(_later())
        self._settle_tasks.add(task)
        task.add_done_callback(self._settle_tasks.discard)

    async def _save_session_pointer(self, topic_id: uuid.UUID, session_id: str) -> None:
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
                    # Resolved here rather than threaded in: the hook-consume
                    # path reaches this with no ResolvedAgent in scope, and this
                    # already opens a session to do its own write.
                    agent = await self._agent_at(session, place)
                    await AgentSessionService(session).remember(
                        topic_id=place.room_id,
                        agent_handle=agent.handle,
                        resume_token=session_id,
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

    async def _work_of_worker(
        self, topic_id: uuid.UUID, agent_id: str | None
    ) -> uuid.UUID | None:
        """Which piece of work this event belongs to, when a worker produced it.

        Several workers run inside one session and everything they do arrives on
        the same pipe as the session's own, told apart only by the id riding on
        each payload — the main thread's hooks carry no such key at all, which is
        what makes the id usable as the sole discriminator.

        None for two different situations that want the same handling: the room
        itself did this, or a worker nobody bound did. Both land where they
        landed before this existed, on the room's own line. Swallowing the
        unbound one instead would make an unclaimed worker's whole run invisible,
        which is worse than the attribution being coarse.
        """
        from app.domain.room_task.services import TaskService

        if not agent_id:
            return None
        async with self._sessions() as session:
            task = await TaskService(session).open_by_subagent(
                room_id=topic_id, subagent_id=agent_id
            )
        return task.id if task is not None else None

    async def _begin_self_started_turn(
        self, project_id: uuid.UUID, topic_id: uuid.UUID, turn_id: uuid.UUID
    ) -> "_HookWorkState | None":
        """Give a turn the session started for itself the context to end like
        any other: an interval a sweep can find, and everything its Stop needs.

        Without this, a self-started turn's Stop landed the message and then did
        nothing at all — no usage row, no conclusion cards settled, no change
        summary, and no interval to close, because none was ever opened. The
        room could not even tell you the turn had happened.

        Read rather than assembled: there is no prompt to build here, so this
        takes only what turn END needs, and takes it in one transaction. Two
        fields are deliberately not read — `roster` stays None so the message
        path loads it (passing [] would mean 私聊 and flag every @ as a
        non-member), and `pending_ids` stays empty because nothing was fed to
        this turn. A message that merges into it mid-flight is stamped consumed
        by its own receipt (`confirm_prompt_receipt`), not from here.

        Returns None if the place is gone or the bookkeeping write fails; the
        event that triggered this still lands, exactly as it did before.
        """
        from app.api.deps import get_work_runner

        try:
            async with self._sessions() as session:
                place = await PlaceResolver(session).resolve(topic_id)
                if place is None:
                    return None
                topic = place.room
                project = await ProjectRepository(session).get(project_id)
                agents = AgentInstanceService(session)
                agent = (
                    await agents.for_topic(topic, project)
                    if project is not None
                    else IMPLICIT_DEFAULT
                )
                agent_pool = memory_pool(topic.project_id, agent)
                acting_agent = await self._agent_handle(session, topic_id)
                is_private = topic.is_private
                private_owner = topic.private_owner
            await get_work_runner().open_self_started_turn(self, topic_id, turn_id)
        except Exception:  # noqa: BLE001 — the event matters more than the row
            logger.exception(
                "could not open a self-started turn (topic=%s, work=%s)",
                topic_id,
                turn_id,
            )
            return None
        state = _HookWorkState(
            project_id=project_id,
            topic_id=topic_id,
            work_id=turn_id,
            pending_ids=set(),
            reply_to=None,
            roster=None,
            topic_refs=[],
            continuation_id=turn_id,
            # "native" when this process has never assembled a turn for this
            # session (a screen recovered on the way up, say). It is the answer
            # that cannot invent spend: the gateway's log is drained by whatever
            # turn closes next, which is exactly what happened before any of
            # this existed.
            route=self._session_route.get(topic_id, "native"),
            is_private=is_private,
            private_owner=private_owner,
            acting_agent=acting_agent,
            agent_pool=agent_pool,
            user_text="",
            started_at=datetime.now(UTC),
            known_commits=asyncio.ensure_future(
                self._known_commits(project_id, topic_id)
            ),
            self_started=True,
        )
        self._hook_work[(topic_id, turn_id)] = state
        return state

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
        # A second frame some events carry: "this panel is now out of date".
        # Separate from `frame` because it is not the record of what happened
        # (that is the event block) — it is the instruction to go re-read a
        # panel, and both have to go out.
        refresh_frame: dict | None = None
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
            else await self._work_of_worker(topic_id, getattr(event, "agent_id", None))
        )
        # A thread's own channel is what its view subscribes to, and it is the
        # room's when there is no thread. Attributed frames must not go out on
        # the room's channel: the block lands in the thread, so a live watcher
        # would see an event that a reload then moves somewhere else.
        channel = str(task_id) if task_id is not None else str(topic_id)
        if isinstance(event, AgentSessionInfo):
            await self._save_session_pointer(topic_id, event.session_id)
        elif isinstance(event, AgentSubagentStart | AgentSubagentStop):
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
                task_id=task_id,
            )
            if payload is not None:
                frame = {"type": "event_block", "block": payload}
        elif isinstance(event, AgentToolUse):
            name = event.name.replace("mcp__cheese__", "")
            args = event.input or {}
            if state is not None and name in _TASK_TOOLS:
                # 清单跟着做事的人走。一个分身的清单是它自己的计划，编号也是它
                # 自己从 1 数的 —— 记进房间那份，房间的清单会被别人的进度改写。
                todo = state.todo_of(task_id)
                if _apply_task_event(todo, name, args):
                    await self._persist_progress(
                        topic_id, todo, turn_id, task_id=task_id
                    )
                    frame = {
                        "type": "todo",
                        "items": [dict(item) for item in todo],
                    }
            elif name not in _TASK_TOOLS:
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
                if name == "Bash":
                    resource = _cheese_resource(str(args.get("command", "")))
                    if resource is not None:
                        # Tell the room a panel just went stale, the moment it
                        # did. Without this the verdict of `cheese accept-request`
                        # / `cheese doc set` only reaches the screen when the
                        # reader switches topics or reloads — a card filed while
                        # someone is watching the conversation simply does not
                        # appear. The frontend has handled this frame all along
                        # (ChatPanel `case 'state'` → TopicView.handleStateChanged);
                        # it was the sender that went missing when cheese moved
                        # from a tool call to a Bash command.
                        refresh_frame = {"type": "state", "resource": resource}
                        if (
                            state is not None
                            and resource in _ACTION_LABEL
                            and resource not in state.actions
                        ):
                            state.actions.append(resource)
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
                await self._save_session_pointer(topic_id, event.session_id)
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
                # Private chats publish the direct reply; work topics retain
                # terminal output in activity and publish through Cheese CLI.
                payload = await self._persist_assistant_message(
                    project_id=project_id,
                    topic_id=topic_id,
                    text=event.text,
                    turn_id=turn_id,
                    reply_to=state.reply_to if state is not None else None,
                    roster=state.roster if state is not None else None,
                    topic_refs=state.topic_refs if state is not None else [],
                    eid=eid,
                    platform_unsolicited=platform_unsolicited,
                    continuation_id=(
                        state.continuation_id if state is not None else None
                    ),
                    publish=bool(state and state.is_private),
                )
                if payload is not None:
                    if state is not None:
                        state.assistant_count += 1
                    frame = {
                        "type": "assistant_block"
                        if payload["kind"] == "message"
                        else "event_block",
                        "block": payload,
                    }
        if frame is not None:
            await broker.publish(channel, frame)
            if frame["type"] in ("assistant_block", "event_block", "todo"):
                get_work_runner().note_session_output(
                    turn_id, tool=isinstance(event, AgentToolUse)
                )
        if refresh_frame is not None:
            # The room's, always: a panel going stale is a fact about the place
            # the panel is in, and the worker that made it stale ran there.
            await broker.publish(str(topic_id), refresh_frame)
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
                        get_work_runner().close_self_started_turn(turn_id)
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
            self.schedule_spool_settle(topic_id)

    async def _close_hook_work(
        self, state: _HookWorkState, result: AgentResult
    ) -> list[dict]:
        """Commit accounting and prompt consumption after the session stops."""
        usage = result.usage
        if usage is not None and not (
            usage.input_tokens or usage.output_tokens or usage.cost_usd
        ):
            usage = None
        if self._gateway is not None and state.route == "gateway":
            usage = await self._drain_gateway_usage(state.project_id)
            if usage is None:
                # Both the turn-end drain and its settle retry saw nothing.
                # LiteLLM batch-writes spend logs, so "nothing yet" is not
                # "nothing" — land it in the background rather than hold the
                # turn open or write the spend off. This is the ONLY place the
                # numbers exist: an interactive session reports no usage of its
                # own, which is why `usage` is None here in the first place.
                self._schedule_deferred_drain(
                    state.project_id, state.topic_id, state.work_id
                )

        action_frames: list[dict] = []
        async with self._sessions() as session:
            blocks = BlockRepository(session)
            if usage is None:
                await UsageRepository(session).add(
                    project_id=state.project_id,
                    topic_id=state.topic_id,
                    model=settings.agent_model,
                    input_tokens=0,
                    output_tokens=0,
                    cost_usd=0.0,
                    metered=False,
                    route=state.route,
                    turn_id=state.work_id,
                )
            else:
                await UsageRepository(session).add(
                    project_id=state.project_id,
                    topic_id=state.topic_id,
                    model=usage.model,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    cost_usd=usage.cost_usd,
                    route=state.route,
                    turn_id=state.work_id,
                )
                await ComputeGrantRepository(session).consume(
                    state.project_id,
                    usage_to_credits(usage, spend_priced=state.route == "gateway"),
                )
            for resource in state.actions:
                block = await blocks.add(
                    project_id=state.project_id,
                    topic_id=state.topic_id,
                    author=state.acting_agent,
                    author_type=AuthorType.system,
                    content=f"芝士 {_ACTION_LABEL[resource]}",
                    kind=BlockKind.event,
                    turn_id=state.work_id,
                    meta={"platform": True, "action": resource},
                )
                action_frames.append(
                    {
                        "type": "event_block",
                        "block": _block_payload(BlockOut.model_validate(block)),
                    }
                )
            if not result.is_error:
                await blocks.mark_consumed(list(state.pending_ids), state.work_id)
            published_text = await blocks.published_text_for_turn(state.work_id)
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
        if not result.is_error:
            self._schedule_memory_extraction(
                topic_id=state.topic_id,
                project_id=state.project_id,
                is_private=state.is_private,
                private_owner=state.private_owner,
                agent_pool=state.agent_pool,
                user_text=state.user_text,
                assistant_text=published_text,
            )
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
    ) -> tuple[list[dict], uuid.UUID, list[uuid.UUID]]:
        """Persist the human message (+ its image attachment blocks) and the
        @mention notifications in one short transaction, outside any turn lock.
        Returns (payloads, anchor_block_id, all_block_ids) — the anchor is what
        芝士's reply threads under; all ids are consumed together after a
        mid-session delivery receipt."""
        async with self._sessions() as session:
            topics = TopicRepository(session)
            blocks = BlockRepository(session)
            place = await PlaceResolver(session).resolve(topic_id)
            if place is None:
                raise NotFoundError("Topic not found")
            topic = place.room
            created_blocks: list[Block] = []
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
                # Private topics have no roster to resolve against (and expose
                # no member list), so they store the content verbatim.
                roster = (
                    []
                    if topic.is_private
                    else await ProjectRepository(session).list_members(topic.project_id)
                )
                if roster:
                    topic_refs = [
                        {"id": str(t.id), "title": t.title}
                        for t in await topics.list_for_project(topic.project_id)
                        if t.kind != TopicKind.root and t.id != topic.id
                    ]
                    content = expand_mention_names(content, roster, topic_refs)
                user_block = await blocks.add(
                    project_id=topic.project_id,
                    topic_id=place.room_id,
                    author=author,
                    author_type=AuthorType.human,
                    content=content,
                    kind=BlockKind.message,
                    turn_id=turn_id,
                    reply_to=reply_uuid,  # B3: thread under another
                    # The sender's own id for this send, echoed straight back on
                    # the broadcast. A client that showed the message the instant
                    # it was typed (§14.1 实时) needs to recognise its own copy
                    # coming home; matching on text cannot do that, because this
                    # method rewrites the text on the way in.
                    meta={"client_id": client_id} if client_id else None,
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
            for att in attachments or []:
                att_block = await blocks.add(
                    project_id=topic.project_id,
                    topic_id=place.room_id,
                    author=author,
                    author_type=AuthorType.human,
                    content=str(att.get("path") or ""),
                    kind=BlockKind.attachment,
                    mime_type=str(att.get("mime") or "") or None,
                    turn_id=attribution_id,
                    # An image-only send still honors the reply thread (B3).
                    reply_to=None if content else reply_uuid,
                )
                if attribution_id is None:
                    attribution_id = att_block.id
                    att_block.turn_id = attribution_id
                if anchor_id is None:
                    anchor_id = att_block.id
                created_blocks.append(att_block)
            if anchor_id is None:  # guarded by the route, but never crash a turn
                raise NotFoundError("empty message")
            await session.commit()
            payloads = [
                _block_payload(BlockOut.model_validate(block))
                for block in created_blocks
            ]
        return payloads, anchor_id, [block.id for block in created_blocks]

    async def ack_summon(
        self, user_block_id: uuid.UUID, topic_id: uuid.UUID
    ) -> dict | None:
        """Add 芝士's ✅ receipt to the summoning user message (idempotent) and
        return the WS reaction payload. Best-effort: a failed receipt must
        never kill the turn."""
        try:
            async with self._sessions() as session:
                blocks = BlockRepository(session)
                await blocks.add_reaction_if_absent(
                    user_block_id, "✅", await self._agent_handle(session, topic_id)
                )
                reactions = await blocks.reactions_for_block(user_block_id)
                await session.commit()
            return {"block_id": str(user_block_id), "reactions": reactions}
        except Exception:  # noqa: BLE001 — the turn matters more than the ack
            logger.exception("failed to ✅-ack block %s", user_block_id)
            return None

    async def _resolved_agent(
        self, session: AsyncSession, topic: Topic
    ) -> ResolvedAgent:
        """Which agent is working in *topic* — its own, else the project's.

        Its ``handle`` keys both of the things an agent owns and a room does not:
        the memory pool it writes to, and the conversation it resumes.
        """
        project = await ProjectRepository(session).get(topic.project_id)
        if project is None:
            return IMPLICIT_DEFAULT
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
            return IMPLICIT_DEFAULT
        return await AgentInstanceService(session).for_topic(place.room, project)

    async def _agent_memory_pool(
        self, session: AsyncSession, topic: Topic
    ) -> tuple[MemoryScope, str]:
        """Where the agent working in *topic* writes what it learns.

        The AGENT owns the pool, not the room — a 芝士 that works in five rooms
        of one project has one memory, which is what "the same 芝士" was
        supposed to mean all along.
        """
        agent = await self._resolved_agent(session, topic)
        return memory_pool(topic.project_id, agent)

    async def _recall_agent_memories(
        self, memory, session: AsyncSession, *, topic: Topic, query: str = ""
    ) -> RecallResult:
        """What this 芝士 remembers inside this project, given what this turn is
        about.

        Its own pool first, then two read-only tails: what this ROOM learned
        while memory was keyed by topic, and the shared ``project`` pool from
        before memory was split per agent at all. Writes only ever go to the
        first, so neither tail grows — but dropping them would make the day this
        shipped look, from inside a room, exactly like amnesia.

        ``query`` is the turn's own context: core memory ignores it (it is in
        every turn by definition), everything else is ranked against it. Returns
        what did *not* come in alongside what did — a pool nobody is told is
        bigger than the prompt is how memory quietly stops existing.
        """
        own = await self._agent_memory_pool(session, topic)
        legacy = legacy_topic_pool(topic.project_id, topic.id)
        pools = [own] + ([legacy] if legacy != own else [])
        pools.append((MemoryScope.project, str(topic.project_id)))
        return await recall_pools(memory, pools, query=query)

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
        backfilled: bool = False,
        platform_unsolicited: bool = False,
        continuation_id: uuid.UUID | None = None,
        at: datetime | None = None,
        task_id: uuid.UUID | None = None,
        publish: bool = False,
        author: str | None = None,
        publication_id: str | None = None,
    ) -> dict | None:
        """Persist output immediately; only explicit publications enter chat.

        Terminal output remains in activity without notifying mentioned members.
        ``eid`` (hooks path) is stamped into meta so the spool reconcile can
        dedup a backfilled copy against this live one; ``eids`` carries every
        constituent flush id of a coalesced message, and any one of them
        matching an existing block means this message already landed.

        Returns None when ``continuation_id`` says this exact message already
        landed in an earlier attempt at the same work (④ 重发): the re-sent
        turn re-narrating "我先看一下 X" must not post a second copy of it. The
        caller treats None as "nothing to broadcast"."""
        # 有些「助手消息」根本不是芝士说的 —— 是它脚下的 CLI 把自己的英文提示
        # 当成助手输出印了出来。拦在这里而不是调用方:活路径、补投、spool 回填
        # 三条路都经过这个方法,拦在门口才不会有一条漏网。
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
                backfilled=backfilled,
                platform_unsolicited=platform_unsolicited,
                in_room=True,
                author_type=AuthorType.system,
                task_id=task_id,
            )
        meta: dict | None = (
            {"in_room": False, "progress": True} if as_progress else None
        )
        if eid:
            meta = {**(meta or {}), "eid": eid}
        if len(eids) > 1:
            meta = {**(meta or {}), "eids": list(eids)}
        if backfilled:
            meta = {**(meta or {}), "backfilled": True}
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
                    if topic is None or topic.is_private
                    else await ProjectRepository(session).list_members(project_id)
                )
            text = _expand_mention_names(text, roster, topic_refs)
            author = author or await self._agent_handle(session, topic_id)
            block = await blocks.add(
                project_id=project_id,
                topic_id=topic_id,
                task_id=task_id,
                author=author,
                author_type=AuthorType.ai,
                content=text,
                kind=BlockKind.event if as_progress else BlockKind.message,
                reply_to=reply_to,
                turn_id=turn_id,
                meta=meta,
                created_at=at,
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
                    warn = f"⚠️ @了 <@{bad}>，项目成员里没有这个 handle，没能通知到"
                    await blocks.add(
                        project_id=project_id,
                        topic_id=topic_id,
                        # Beside the message it is about, not in the room the
                        # message did not go to.
                        task_id=task_id,
                        author=author,
                        author_type=AuthorType.ai,
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
                state.progress_reminded = False
        return payload

    async def _persist_progress(
        self,
        topic_id: uuid.UUID,
        items: list[dict],
        turn_id: uuid.UUID | None,
        *,
        task_id: uuid.UUID | None = None,
    ) -> None:
        """Write the topic's checklist through to storage (进度层, #187).

        Best-effort on purpose: progress is a convenience for the NEXT turn, so a
        storage hiccup must never take down the turn that is currently producing
        real work. Same commit-now contract as _persist_tool_event — batching to
        turn end would lose exactly the case this exists for (the turn dies)."""
        try:
            async with self._sessions() as session:
                # The checklist belongs to whoever is working, not to the room
                # it hangs in — two 分身 in one room keep two lists, each
                # numbered from 1, and one shared list would have them ticking
                # each other's items.
                await TopicProgressRepository(session).save(
                    topic_id, items, task_id=task_id, turn_id=turn_id
                )
                await session.commit()
        except Exception:  # noqa: BLE001 — never fail a turn over its checklist
            logger.warning("progress persist failed for topic %s", topic_id)

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
        backfilled: bool = False,
        platform_unsolicited: bool = False,
        task_id: uuid.UUID | None = None,
    ) -> dict | None:
        """Persist ONE 施工现场 event the moment it streams in, not batched to the
        turn-end tx2. Mirrors _persist_assistant_message's commit-now contract so
        a mid-turn restart/crash never loses the 现场 timeline already produced.
        ``eid`` (the hook forwarder's event id) is stamped into meta so the durable
        spool reconcile can dedup a backfilled copy against this live one. Returns
        the persisted block payload so a caller (live path or spool reconcile) can
        broadcast it as a WS frame."""
        preview = tool_preview(
            name, tool_input, work_dir=work_subpath(project_id, topic_id)
        )
        return await self._persist_room_event(
            project_id=project_id,
            topic_id=topic_id,
            content=_format_tool_event(name, preview),
            meta=_tool_event_meta(name, preview, platform=platform),
            turn_id=turn_id,
            eid=eid,
            backfilled=backfilled,
            platform_unsolicited=platform_unsolicited,
            task_id=task_id,
        )

    async def _persist_room_event(
        self,
        *,
        project_id: uuid.UUID,
        topic_id: uuid.UUID,
        content: str,
        meta: dict,
        turn_id: uuid.UUID | None,
        eid: str | None = None,
        backfilled: bool = False,
        platform_unsolicited: bool = False,
        in_room: bool = False,
        author_type: AuthorType = AuthorType.ai,
        task_id: uuid.UUID | None = None,
    ) -> dict | None:
        """One event block, committed NOW and deduped by event-id.

        Shared by everything the room learns mid-turn — a tool call, a subagent's
        conclusion, the turn's change summary — so all three get the same
        durability and idempotency contract instead of three copies of it that
        drift. Returns None when this event-id already landed.

        ``in_room`` decides whether the conversation shows it at all, and it
        travels as ``meta.in_room`` — its own field, because visibility is not
        authorship. It used to ride on ``author_type`` (system = the room, ai =
        现场 only), which meant an event genuinely written by 芝士 could not be
        shown in the room without lying about who wrote it, and anything that
        later wanted to know the author was reading a field answering a
        different question. Absent means shown: every other writer in the
        codebase posts to the room.

        ``author_type`` is then free to answer its own question, and does: 芝士
        wrote the tool calls and the subagent conclusions, the platform wrote
        the change summary."""
        meta = {**meta, "in_room": in_room}
        if eid:
            meta = {**meta, "eid": eid}
        if backfilled:
            meta = {**meta, "backfilled": True}
        if platform_unsolicited:
            meta = {**meta, "platform_unsolicited": True}
        async with self._sessions() as session:
            blocks = BlockRepository(session)
            if eid and await blocks.has_eid(topic_id, eid):
                return None
            block = await blocks.add(
                project_id=project_id,
                topic_id=topic_id,
                task_id=task_id,
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
        backfilled: bool = False,
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
            backfilled=backfilled,
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

        Nothing is written for a worker the platform never bound, and that is
        not tidiness. Measured twice on 2.1.224: after the session's own Stop, a
        SubagentStop arrives with an id matching no worker we saw, an empty
        type, and a fragment of a prompt where the closing message should be —
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
            # The platform's own sentence about a worker, not anybody's words —
            # so `system`, the same as every other line the platform says out
            # loud. Attributing it to 芝士 would make the room's history contain
            # a remark 芝士 never made.
            content, author_type = "分身开工", AuthorType.system
            meta: dict = {"event_type": "subagent_start"}
        else:
            # The closing message in full, and it IS the worker's own words. It
            # reaches the platform exactly once, here — the room's transcript
            # does not contain it and the worker's dies with its container.
            content = event.text.strip() or "分身交回了一次结果（没有留话）"
            author_type = AuthorType.ai
            meta = {"event_type": "subagent_stop"}
            if event.transcript_path:
                meta["transcript_path"] = event.transcript_path
            # 结论落在卡上, overwriting the previous stop's — the newest is what
            # the room reads when it decides whether the work is done. Only for
            # a worker the platform bound (`task_id` is that check, above), so
            # the fragments Claude Code's own internal agents stop with never
            # become anybody's conclusion.
            await self._record_conclusion(task_id, event.text.strip())
        meta["agent_id"] = event.agent_id
        if event.agent_type:
            meta["agent_type"] = event.agent_type
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

        def _collect() -> _Changeset | None:
            fresh = [h for h in commits if h not in known_commits]
            if not fresh:
                return None
            totals: dict[str, dict] = {}
            for sha in fresh:
                for entry in _diff_file_stats(ws.git_diff(project_id, ref=sha)):
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
            return await asyncio.to_thread(_collect)
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
            from app.domain.room_task.services import TaskService

            async with self._sessions() as session:
                tasks = await TaskService(session).list_in_room(topic_id)
                task_ids = []
                for task in tasks:
                    if task.branch_name:
                        TaskService._bind_workspace(task)
                        task_ids.append(task.id)
            return await asyncio.to_thread(
                lambda: {
                    row["hash"]
                    for task_id in task_ids
                    for row in ws.git_log(
                        project_id, limit=_CHANGE_COMMIT_WALK, topic_id=task_id
                    )
                }
            )
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
            author_type=AuthorType.system,  # 平台自己数出来的，不是芝士说的
        )

    async def _reconcile_spool(
        self, project_id: uuid.UUID, topic_id: uuid.UUID, turn_id: uuid.UUID | None
    ) -> AsyncIterator[dict]:
        """Land what a session said while nobody was listening (backend down, or
        no listener during a prior turn) — idempotent by event id, best-effort,
        and never blocking the turn.

        The harness's ``backlog`` says WHAT was said; this decides what to do
        about it. Scope: 现场 tool events, 芝士 chat messages, and the turn-ending
        result — a copy the live path already persisted is skipped by id.
        Backfilled messages skip mention-notify (the moment passed) but DO yield
        a WS frame like the live path: something that missed its turn's listening
        window must still reach the frontend, just without threading or an
        @-notify (bug: it was landing as a silent DB row nobody saw).

        The result is what lets an ORPHANED turn finish (#316): a backend restart
        kills the waiter, not the working agent, and the sweep no longer
        re-prompts one that heard the task — so its ending arrives with no turn
        listening, gets parked, and has to close the books from here: save the
        finished session's pointer, and land its final message unless a message
        (live or in this same pass) already carries that text — the ending
        normally echoes the last one.

        Whether a message is whole is the harness's call, not ours: ``assemble``
        answers with nothing until it is, ``unfinished`` names what is still
        missing a piece — and the cursor must stop before those, so the pass that
        completes them still sees what they are made of. ``give_up`` is for the
        session that died mid-sentence: what arrived lands joined rather than
        being lost."""
        started = time.monotonic()
        phases_ms: dict[str, float] = {}
        event_count = 0
        try:
            session_ref = SessionRef(project_id, topic_id)
            backlog = self._compute.backlog(session_ref)
            spooled = backlog.unread()
            event_count = len(spooled)
            phases_ms["read"] = (time.monotonic() - started) * 1000
            if not spooled:
                return
            # Dedup against everything the live path already persisted (this +
            # prior turns): event-ids stamped on this topic's blocks (any kind).
            async with self._sessions() as session:
                blocks = await BlockRepository(session).list_for_topic(topic_id)
                topic = await TopicRepository(session).get(topic_id)
                # The roster/topic table that STORED content was canonicalized
                # with. Loaded here because the text dedup below has to compare
                # like for like — see `_canon`.
                roster = (
                    []
                    if topic is None or topic.is_private
                    else await ProjectRepository(session).list_members(project_id)
                )
                topic_refs, _ = _topic_ref_lists(
                    await TopicRepository(session).list_for_project(project_id),
                    exclude_id=topic_id,
                )
            phases_ms["history"] = (time.monotonic() - started) * 1000
            seen = _persisted_eids(blocks)

            def _canon(text: str) -> str:
                """Stored form of a raw hook text.

                Every persist path runs `_expand_mention_names` on the way in, so
                a stored block holds `<@handle>` where the hook payload still
                holds `@名字`. Comparing the two forms directly is why a message
                that mentions ANYONE defeated the dedup below and landed twice.
                """
                return _expand_mention_names(text, roster, topic_refs).strip()

            # Text-level dedup for the Stop's final message (it has its OWN eid,
            # so eid dedup can never match it against the MessageDisplay twin).
            known_texts = {
                (b.content or "").strip()
                for b in blocks
                if b.author_type == AuthorType.ai
                and (b.kind == BlockKind.message or (b.meta or {}).get("progress"))
            }
            recovered = 0

            async def _land_message(
                message: AgentMessage, fallback_eid: str
            ) -> dict | None:
                # A chat message whose live delivery was lost — land it as
                # history (no reply threading, no notify: the moment passed)
                # but still broadcast it, exactly like the live path does.
                #
                # Unless the room already has it. A turn whose own
                # MessageDisplay went to the spool instead of arriving live
                # ends with a Stop that echoes the same words, and that echo is
                # posted as an eid-less fallback — so the spooled copy would
                # land beside it as a twin nobody can tell apart. eid dedup
                # cannot see it (the fallback carries the Stop's id, or none at
                # all), which is why this compares the words. Traced from
                # production: 46 messages, exactly one with empty meta, sitting
                # next to a backfilled duplicate of itself.
                if _canon(message.text) in known_texts:
                    seen.add(fallback_eid)
                    seen.update(message.eids)
                    return None
                block_payload = await self._persist_assistant_message(
                    project_id=project_id,
                    topic_id=topic_id,
                    text=message.text,
                    turn_id=turn_id,
                    reply_to=None,
                    roster=roster,
                    topic_refs=topic_refs,
                    eid=message.eid or fallback_eid,
                    eids=message.eids,
                    backfilled=True,
                    at=message.at,
                    task_id=await self._work_of_worker(topic_id, message.agent_id),
                )
                seen.add(fallback_eid)
                seen.update(message.eids)
                known_texts.add(_canon(message.text))
                return block_payload

            for spooled_event in spooled:
                eid = spooled_event.eid
                if eid in seen:
                    continue
                events = backlog.assemble(spooled_event)
                if events and isinstance(events[-1], AgentResult):
                    # A Stop first drains still-buffered flushes. A drained
                    # partial that is a PREFIX of the Stop's text is the same
                    # message minus its lost tail — skip the fragment and let
                    # the Stop's whole copy land, carrying the fragment's
                    # flush ids so a redelivered flush still matches.
                    result = events[-1]
                    stop_text = (result.text or "").strip()
                    carried: list[str] = []
                    for partial in events[:-1]:
                        if not isinstance(partial, AgentMessage):
                            continue
                        if stop_text.startswith(partial.text.strip()):
                            carried.extend(partial.eids)
                            continue
                        block_payload = await _land_message(partial, eid)
                        if block_payload is not None:
                            recovered += 1
                            yield {"type": "event_block", "block": block_payload}
                    if result.session_id:
                        # The finished session is what the next summon must
                        # resume — without this the topic keeps pointing at
                        # whatever SessionStart last managed to save live.
                        await self._save_session_pointer(topic_id, result.session_id)
                    # `stop_text` stays RAW above (the prefix test matches it
                    # against raw flush text); the dedup compares stored forms.
                    if not stop_text or _canon(result.text or "") in known_texts:
                        seen.add(eid)
                        seen.update(carried)
                        continue
                    block_payload = await self._persist_assistant_message(
                        project_id=project_id,
                        topic_id=topic_id,
                        text=result.text,
                        turn_id=turn_id,
                        reply_to=None,
                        roster=roster,
                        topic_refs=topic_refs,
                        eid=eid,
                        eids=tuple(dict.fromkeys((*carried, eid))),
                        backfilled=True,
                    )
                    seen.add(eid)
                    seen.update(carried)
                    known_texts.add(_canon(stop_text))
                    if block_payload is None:
                        continue
                    recovered += 1
                    yield {"type": "event_block", "block": block_payload}
                    continue
                for event in events:
                    if isinstance(event, AgentMessage):
                        block_payload = await _land_message(event, eid)
                        if block_payload is None:
                            continue
                        recovered += 1
                        yield {"type": "event_block", "block": block_payload}
                        continue
                    if isinstance(event, AgentSubagentStart | AgentSubagentStop):
                        # A worker's closing message reaches the platform exactly
                        # once, in the Stop that carries it — its own transcript
                        # dies with the container. So a lost delivery here is the
                        # answer itself going missing, not a redraw.
                        block_payload = await self._persist_worker_event(
                            project_id=project_id,
                            topic_id=topic_id,
                            event=event,
                            task_id=await self._work_of_worker(
                                topic_id, event.agent_id
                            ),
                            turn_id=turn_id,
                            eid=eid,
                        )
                        seen.add(eid)
                        if block_payload is None:
                            continue
                        recovered += 1
                        yield {"type": "event_block", "block": block_payload}
                        continue
                    if isinstance(event, AgentToolResult):
                        # A subagent's conclusion whose live delivery was lost. Worth
                        # backfilling for the same reason it is worth showing at all:
                        # without it the timeline keeps the question and loses the
                        # answer, and 现场 history is what a late reader reads.
                        block_payload = await self._persist_subagent_result(
                            project_id=project_id,
                            topic_id=topic_id,
                            event=event,
                            turn_id=turn_id,
                            eid=eid,
                            backfilled=True,
                            task_id=await self._work_of_worker(
                                topic_id, event.agent_id
                            ),
                        )
                        seen.add(eid)
                        if block_payload is None:
                            continue
                        recovered += 1
                        yield {"type": "event_block", "block": block_payload}
                        continue
                    if not isinstance(event, AgentToolUse):
                        continue  # SessionStart has no historical counterpart
                    name = event.name.replace("mcp__cheese__", "")
                    if name in _TASK_TOOLS:
                        continue  # task todos are process state, not persisted 现场
                    args = event.input or {}
                    block_payload = await self._persist_tool_event(
                        project_id=project_id,
                        topic_id=topic_id,
                        name=name,
                        tool_input=args,
                        platform=_is_platform_tool(event.name, args),
                        turn_id=turn_id,
                        eid=eid,
                        backfilled=True,
                        task_id=await self._work_of_worker(topic_id, event.agent_id),
                    )
                    seen.add(eid)
                    if block_payload is None:
                        continue
                    recovered += 1
                    yield {"type": "event_block", "block": block_payload}
            pending = backlog.unfinished()
            if pending:
                newest = min(
                    (event.age_s for event in spooled if event.eid in pending),
                    default=0.0,
                )
                if newest > _SPOOL_PARTIAL_GRACE_S:
                    # No Stop and nothing new for a while: the message will
                    # never complete (the screen died mid-message). Land what
                    # arrived, joined, rather than lose it.
                    for partial in backlog.give_up():
                        block_payload = await _land_message(partial, partial.eid or "")
                        if block_payload is not None:
                            recovered += 1
                            yield {"type": "event_block", "block": block_payload}
                    pending = set()
            phases_ms["replay"] = (time.monotonic() - started) * 1000
            # Reading is not consuming: the cursor moves over the events that
            # reached the timeline, and retention — not this pass — is what
            # eventually deletes them. It stops at the first still-buffered
            # flush rather than stepping over it, so the pass that completes
            # that message still sees the flushes it is made of.
            for event in spooled:
                if event.eid in pending:
                    break
                backlog.landed(through=event.key)
            phases_ms["cursor"] = (time.monotonic() - started) * 1000
            backlog.forget(older_than_s=_SPOOL_RETENTION_S)
            phases_ms["retention"] = (time.monotonic() - started) * 1000
            if recovered:
                logger.info(
                    "reconciled %d spooled 现场 event(s) for topic %s",
                    recovered,
                    topic_id,
                )
        except Exception:  # noqa: BLE001 — reconcile is best-effort, never fail a turn
            logger.exception("spool reconcile failed for topic %s", topic_id)
        finally:
            logger.info(
                "spool_reconcile_timing topic=%s turn=%s events=%d "
                "elapsed_ms=%.3f phases_ms=%s",
                topic_id,
                turn_id,
                event_count,
                (time.monotonic() - started) * 1000,
                phases_ms,
            )

    async def _model_kwargs(
        self,
        project_id: uuid.UUID,
        provider: ComputeProvider | None,
        topic_id: uuid.UUID | None = None,
        *,
        agent: ResolvedAgent | None = None,
    ) -> tuple[dict, str]:
        """Resolve a turn's explicit agent model, model environment and usage route.

        Machine providers assemble their own scoped credentials. Other providers
        retain their gateway/profile transport, with the agent's saved model.
        The optional agent snapshot keeps model and role consistent within a turn.

        ``provider=None`` means there is no machine in this turn at all (私聊 走
        platform work): the platform is the one about to call the model, so it needs
        the same base_url + key a sandbox would have been handed. That is exactly
        the not-``builds_model_env`` branch, so it falls through to it rather than
        growing a second way to answer the same question.
        """
        acting_agent: str | None = None
        environment = None
        async with self._sessions() as session:
            project = await ProjectRepository(session).get(project_id)
            if project is None:
                raise NotFoundError("Project not found")
            topic = await TopicRepository(session).get(topic_id) if topic_id else None
            if topic is not None:
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
            config = AgentConfiguration.model_validate(agent.configuration)
            validate_configuration(config, project.settings)
            supply = (
                SUBSCRIPTION
                if config.model in {item.id for item in subscription_model_listings()}
                else "gateway"
            )
        model = (
            subscription_model_alias(config.model)
            if supply == SUBSCRIPTION
            else config.model
        )
        config_hash = hashlib.sha256(
            # Author identity, chat skills, and native RC arguments are installed
            # at process birth; refresh them together at the next task boundary.
            (
                json.dumps(
                    {"agent": agent.configuration, "git_author": acting_agent},
                    sort_keys=True,
                )
                + ":explicit-chat-v3-native-skills"
                + (":native-rc-v1" if supply == SUBSCRIPTION else "")
            ).encode()
        ).hexdigest()
        kwargs: dict = {"model": model, "env": {"CHEESE_AGENT_CONFIG": config_hash}}
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

    async def _gateway_project_env(self, project_id: uuid.UUID) -> dict | None:
        """Env override for a gateway-routed turn: mint (once) and return the
        project's virtual key, and keep its L2 max_budget in step with the
        project's grants. Returns None on any gateway/admin failure; the caller
        must refuse the turn rather than expose default pool credentials."""
        try:
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
                    # L2: budget = total granted credits, in USD. Only when the
                    # price knob is set AND the project is metered at all.
                    if settings.llm_gateway_credit_usd:
                        summary = await ComputeGrantRepository(session).summary(
                            project_id
                        )
                        if not summary["unlimited"]:
                            budget = round(
                                summary["credits_total"]
                                * settings.llm_gateway_credit_usd,
                                6,
                            )
                            if s.get(self._GW_BUDGET) != budget and (
                                await self._gateway.set_key_budget(key, budget)
                            ):
                                s[self._GW_BUDGET] = budget
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
        usage row + credit deduction when they show up. Strong-ref'd like the
        memory tasks so the pending commit can't be GC'd."""

        async def _later() -> None:
            await asyncio.sleep(20.0)
            usage = await self._drain_gateway_usage(project_id)
            if usage is None:
                return  # still nothing — the next turn's drain picks it up
            async with self._sessions() as session:
                await UsageRepository(session).add(
                    project_id=project_id,
                    topic_id=topic_id,
                    model=usage.model,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    cost_usd=usage.cost_usd,
                    route="gateway",
                    # Same turn as the row tx2 already wrote — this is the late
                    # half of ONE turn's spend, not a second turn.
                    turn_id=turn_id,
                )
                await ComputeGrantRepository(session).consume(
                    project_id, usage_to_credits(usage, spend_priced=True)
                )
                await session.commit()
            logger.info(
                "deferred usage drain landed for turn %s (%d+%d tokens)",
                turn_id,
                usage.input_tokens,
                usage.output_tokens,
            )

        task = asyncio.create_task(_later())
        self._memory_tasks.add(task)
        task.add_done_callback(self._memory_tasks.discard)

    async def _drain_gateway_usage(self, project_id: uuid.UUID) -> AgentUsage | None:
        """L1: real usage for gateway-routed turns. The hooks backends can't see
        token usage locally (interactive Claude Code reports none → usage=0), so
        read the project's NEW spend from the gateway's log instead — an
        exactly-once daily cumulative delta (see gateway.drain_new_usage), so
        late-logged rows surface in a later drain instead of being lost."""
        if self._gateway is None:
            return None
        try:
            for attempt in range(2):
                if attempt:
                    # Spend rows can arrive late. Wait without holding the lock
                    # needed by new model requests, then read the current checkpoint.
                    await asyncio.sleep(3.0)
                async with self._gateway_lock:
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
                        if not attempt and (
                            drained is None or drained[0] + drained[1] <= 0
                        ):
                            continue
                        if drained is None:
                            return None
                        prompt, completion, usd, next_ckpt = drained
                        s[self._GW_CKPT] = next_ckpt
                        project.settings = s
                        await session.commit()
                if prompt + completion <= 0:
                    return None
                return AgentUsage(
                    model=settings.agent_model,
                    input_tokens=prompt,
                    output_tokens=completion,
                    cost_usd=usd,
                )
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
            notifs = AlertService(session)
            preview = markdown_preview(text, 200)
            who = "芝士" if looks_like_agent_handle(author) else author
            for h in targets:
                await notifs.create(
                    project_id=topic.project_id,
                    level=AlertLevel.strong,
                    kind=AlertKind.mention,
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
        block = await BlockRepository(session).add(
            project_id=project_id,
            topic_id=topic_id,
            author="system",
            author_type=AuthorType.system,
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
            pending = _pending_human_blocks(history)
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

            is_private = topic.is_private
            # 这一轮走不走「不占机器」那条路。私聊默认走，走不通再退回机器。
            private_owner = topic.private_owner
            acting_agent = await self._agent_handle(session, topic.id)
            doc_root = None if is_private else await blocks.doc_root(place.room_id)
            doc_text = doc_root.content if doc_root else None
            phases_ms["identity"] = (time.monotonic() - started) * 1000
            # Memory is retrieved against what this turn is actually about —
            # newest message first, since a turn is usually about the thing
            # somebody just said, and the doc last because it is the slowest-
            # moving of the three. Fetched before the recall below, which is
            # the only reason the doc lookup moved above it.
            turn_query = _memory_query(
                *(
                    b.content
                    for b in reversed(pending)
                    if b.kind == BlockKind.message and b.content
                ),
                topic.title,
                doc_text,
            )
            if is_private and private_owner:
                # Private chat: the owner's cross-project personal memory.
                memories = await recall_pools(
                    memory, [(MemoryScope.user, private_owner)], query=turn_query
                )
            else:
                memories = await self._recall_agent_memories(
                    memory, session, topic=topic, query=turn_query
                )
            phases_ms["memory"] = (time.monotonic() - started) * 1000
            projects_repo = ProjectRepository(session)
            project = await projects_repo.get(topic.project_id)
            # Read the selected agent once so this turn's role and model agree.
            agents = AgentInstanceService(session)
            agent = (
                await agents.for_topic(topic, project)
                if project is not None
                else IMPLICIT_DEFAULT
            )
            role = await agents.system_prompt(agent)
            wanted_harness = await agents.harness(agent)
            agent_pool = memory_pool(topic.project_id, agent)
            # Roster so 芝士 can @ real teammates (not just name them in prose).
            roster = (
                [] if is_private else await projects_repo.list_members(topic.project_id)
            )
            # Topic list so 芝士 can cross-reference topics with <#id> tokens.
            # 两份，故意的：`topic_refs` 是 `@标题` 的**解析表**（全量，含已归档
            # ——用户自己打 @某个归档话题也必须还能变成链接）；
            # `topic_refs_for_prompt` 只是**渲染**进 system prompt 的子集。
            topic_refs = []
            topic_refs_for_prompt = []
            if not is_private:
                topic_refs, topic_refs_for_prompt = _topic_ref_lists(
                    await topics.list_for_project(topic.project_id),
                    exclude_id=topic.id,
                )
            project_id = topic.project_id
            # This agent's conversation here, not just any: a room may host
            # several agents and each resumes its own (agent_session/models.py).
            # Looked up under the same key the turn that stores it writes under
            # (`_agent_at`) — reading under one key and writing under another
            # does not fail, it hands back None and starts a brand-new
            # conversation, which is the failure this whole path prevents.
            session_agent = await self._agent_at(session, place)
            resume_session_id = await AgentSessionService(session).resume_token(
                place.room_id, session_agent.handle
            )
            untitled = not is_private and topic.title == PLACEHOLDER_TITLE
            # 进度层 (#187): the checklist the last turn left behind. Read inside
            # tx1 with everything else the prompt is built from, so no extra
            # round trip; empty list when this topic has never had one.
            progress_row = await TopicProgressRepository(session).get(place.room_id)
            prior_progress = [
                dict(item) for item in (progress_row.items if progress_row else [])
            ]
            # 盲飞防护: this topic's open accept cards, surfaced in the prompt's
            # turn-meta header so the agent knows a gate/adoption is pending
            # without polling.
            open_cards = []
            if not is_private:
                open_cards = [
                    c
                    for c in await AcceptCardRepository(session).list_for_topic(
                        topic_id
                    )
                    if c.status in _OPEN_CARD_STATUSES
                ]
            # 按阶段渐进式披露: which段 of the flow this topic is in. Derived
            # entirely from facts already in hand (kind/status + the open cards
            # just queried above for 盲飞防护) — no extra query.
            topic_stage = (
                None
                if is_private
                else resolve_stage(
                    finished=topic.status == TopicStatus.archived,
                    card_statuses=[c.status for c in open_cards],
                )
            )
            # Resolve the room choice, then the explicit project default.
            phases_ms["metadata"] = (time.monotonic() - started) * 1000
            compute_id = (
                "device"
                if is_private
                else _resolve_compute_id(
                    project.settings if project else None,
                    topic.compute_profile,
                )
            )
            if (
                not is_private
                and compute_id == "device"
                and topic.compute_config is None
            ):
                from app.domain.agent.compute_configs import (
                    bind_room_device_choice,
                )

                await bind_room_device_choice(
                    session, topic, project.settings if project else None
                )
            provider = self._compute.select(
                provider_id=compute_id, harness=wanted_harness
            )
            if provider is None:
                # The machine is fine; what runs on it is not what this agent's
                # type asked for. Running Claude Code anyway would answer as an
                # agent nobody configured — say so instead, and leave the type
                # to be fixed. (One harness ships, so today this needs a row
                # written before the field was validated at all.)
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
                                    f"这个 agent 的类型要求用 {wanted_harness} "
                                    "跑，而本话题选的机器上没有部署它，本轮没有开始。"
                                ),
                            ),
                        },
                        {"type": "done"},
                    ]
                )
            if provider.provisions_machine:
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
                        waiting_block = await blocks.add(
                            project_id=project_id,
                            topic_id=topic_id,
                            author="system",
                            author_type=AuthorType.system,
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
            # No pending human block ⇒ nobody spoke: this is a resume nudge,
            # a kickoff or a returned conclusion. Say so, rather than handing
            # 芝士 bare text that looks like a person's message.
            embeds_images = getattr(provider, "embeds_images", True)
            backlog = "\n".join(
                _prompt_line(b, embeds_images=embeds_images) for b in pending
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
            # 重放可见 (#416): count this attempt on the blocks themselves. A
            # turn that dies stamps no `consumed_turn`, so the SAME batch is
            # re-sent next turn, and the next — correct (a dead turn must not
            # eat a message) but silent. From the room, "every reply fails" and
            # "this one batch keeps failing" look identical, and the second one
            # is the diagnosis. Counting at prompt-build time is the only place
            # that sees a failed attempt at all.
            replay_n = await blocks.bump_prompt_attempts(pending_ids)
            # Committed HERE and not left to ride the conditional commit further
            # down: that one only fires on a topic's FIRST turn (compute_profile
            # still None), so on every later turn this session closes without a
            # commit and the counter silently rolls back — which is the exact
            # failure mode this counter exists to expose.
            await session.commit()
            replay_notice = _replay_notice(replay_n, pending)
            # turn 活跃度检测: the device channel runs hooks_substrate's two-layer
            # idle-suspect + hard-ceiling loop and manages its own inner ceiling
            # (which can be hours), so the outer wall-clock wrap (runtime.py) must
            # be told the REAL ceiling via a `turn_ceiling` frame instead of
            # killing the turn at the generic `agent_turn_timeout_s`. Without this
            # the device's own two-layer fix is dead on arrival — the outer guard
            # still kills at 900s.
            # 私聊没有机器，所以也不该在它身上钉一台。钉了就是给一段永远不会用到
            # 机器的对话记上一台机器，而这一行本来是给「以后别换机器」用的。
            if provider is not None and topic.compute_profile is None:
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
            is_private=is_private,
            memories=memories,
            open_cards=open_cards,
            pending_ids=pending_ids,
            prior_progress=prior_progress,
            private_owner=private_owner,
            project_id=project_id,
            prompt_text=prompt_text,
            provider=provider,
            replay_notice=replay_notice,
            resume_session_id=resume_session_id,
            role=role,
            roster=roster,
            topic_refs=topic_refs,
            topic_refs_for_prompt=topic_refs_for_prompt,
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
        is_private = prepared.is_private
        memories = prepared.memories
        open_cards = prepared.open_cards
        pending_ids = prepared.pending_ids
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
        skills = load_skills(PRIVATE_SKILLS) if is_private else self._skills
        system_prompt = _build_system_prompt(
            self._base_prompt,
            skills,
            doc_text,
            memories.facts,
            role,
            roster,
            topic_refs_for_prompt,
            untitled,
            memories_omitted=memories.omitted,
            memories_core=memories.core_count,
            memories_core_omitted=memories.core_omitted,
            turn_meta=_turn_meta_lines(
                is_resume=is_resume,
                disk=_workspace_disk(self._workspace_root),
                open_cards=open_cards,
                progress=prior_progress,
                sandbox=_sandbox_limits(provider),
            ),
            stage_guide=(
                load_scenario(stage_scenario(topic_stage))
                if topic_stage is not None
                else None
            ),
        )
        prompt_text = publication_prompt(prompt_text, is_private=is_private)
        logger.info(
            "chat_preparation_timing topic=%s turn=%s phase=prompt_built "
            "elapsed_ms=%.3f unix_ms=%.3f",
            topic_id,
            turn_id,
            (time.monotonic() - preparation_started) * 1000,
            time.time() * 1000,
        )

        # Compute: a provider owns the per-topic sandbox + execution (spec §9.1).
        # In a private chat, `cheese remember` targets the owner's personal memory
        # (spec §8.4). The provider runs a plain model turn when no Docker (tests).
        model_kwargs, route = await self._model_kwargs(
            project_id, provider, topic_id, agent=prepared.agent
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

        # Backfill any 现场 events the live hook path missed (backend down / no
        # listener during a prior turn) from the durable spool WAL — idempotent by
        # event-id. No-op for an empty spool.
        async for frame in self._reconcile_spool(project_id, topic_id, turn_id):
            yield frame
        logger.info(
            "chat_preparation_timing topic=%s turn=%s phase=spool_reconciled "
            "elapsed_ms=%.3f unix_ms=%.3f",
            topic_id,
            turn_id,
            (time.monotonic() - preparation_started) * 1000,
            time.time() * 1000,
        )

        # The turn's own checklist starts EMPTY even when prior_progress is not
        # (see `_HookWorkState.todo`): _apply_task_event numbers items by
        # position and the agent's Task numbering restarts from 1 on a fresh
        # session, so seeding it would make the turn's first TaskUpdate("1")
        # land on a leftover item. The old checklist reaches the agent through
        # the prompt instead, and the UI through the restored frame here.
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
                    get_work_runner().close_self_started_turn(prior_key[1])
            state = self._hook_work.get(key)
            if state is None:
                self._hook_work[key] = _HookWorkState(
                    project_id=project_id,
                    topic_id=topic_id,
                    work_id=marked_work_id,
                    pending_ids=set(pending_ids),
                    reply_to=user_block_id,
                    roster=roster,
                    topic_refs=topic_refs,
                    continuation_id=continuation_id,
                    route=route,
                    is_private=is_private,
                    private_owner=private_owner,
                    acting_agent=acting_agent,
                    agent_pool=agent_pool,
                    user_text=prompt_text,
                    started_at=datetime.now(UTC),
                    known_commits=known_commits,
                )
                return
            state.pending_ids.update(pending_ids)
            if state.reply_to is None:
                state.reply_to = user_block_id
            if prompt_text not in state.user_text:
                state.user_text = f"{state.user_text}\n{prompt_text}"

        try:
            ready = await runtime.send(
                SessionRef(project_id, topic_id),
                prompt_text,
                Opening(
                    system_prompt=system_prompt,
                    resume_token=resume_session_id,
                    memory_scope="personal" if is_private else None,
                    owner=private_owner if is_private else None,
                    model=model_kwargs.get("model"),
                    env=model_kwargs.get("env"),
                    agent_handle=acting_agent,
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

    def _schedule_memory_extraction(
        self,
        *,
        topic_id: uuid.UUID,
        project_id: uuid.UUID,
        is_private: bool,
        private_owner: str | None,
        agent_pool: tuple[MemoryScope, str] | None,
        user_text: str,
        assistant_text: str,
    ) -> None:
        """Fire the post-turn OpenViking session commit in the background.

        Only active on the openviking backend — the flat DB backend has no
        extraction pipeline (there, memory grows via explicit `cheese remember`).
        """
        if settings.memory_backend != "openviking":
            return
        if not settings.openviking_auto_extract:
            return
        if not (user_text.strip() or assistant_text.strip()):
            return
        if is_private and private_owner:
            scope, scope_id = MemoryScope.user, private_owner
        elif agent_pool is not None:
            # Keep each teammate's learned project knowledge in its own pool.
            scope, scope_id = agent_pool
        else:
            return

        async def _run() -> None:
            from app.domain.memory.openviking_store import OpenVikingMemoryStore

            await OpenVikingMemoryStore().ingest_turn(
                scope,
                scope_id,
                conversation_key=str(topic_id),
                exchanges=[("user", user_text), ("assistant", assistant_text)],
            )

        task = asyncio.create_task(_run())
        self._memory_tasks.add(task)

        def _log_done(t: asyncio.Task) -> None:
            self._memory_tasks.discard(t)
            if not t.cancelled() and t.exception() is not None:
                logger.warning(
                    "memory extraction commit failed for topic %s: %s",
                    topic_id,
                    t.exception(),
                )

        task.add_done_callback(_log_done)

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
            await blocks.add(
                project_id=project_id,
                topic_id=topic.id,
                author=author,
                author_type=AuthorType.human,
                content=text,
                kind=BlockKind.event,
                # 原始素材，不是房间里的一句话：房间读的是芝士消化出来的结构化文档。
                meta={"in_room": False},
            )
            memories = await self._recall_agent_memories(
                memory,
                session,
                topic=topic,
                # The activity note IS the whole context here — there is no
                # history yet, the topic was created two statements ago.
                query=_memory_query(text),
            )
            topic_id = topic.id
            compute_id = _resolve_compute_id(
                project.settings,
            )
            await session.commit()

        # --- run 芝士 with the activity-digestion skill + tools ---
        system_prompt = _build_system_prompt(
            self._base_prompt,
            load_skills(ACTIVITY_SKILLS),
            None,
            memories.facts,
            memories_omitted=memories.omitted,
            memories_core=memories.core_count,
            memories_core_omitted=memories.core_omitted,
        )
        prompt = (
            "下面是一条线下活动输入，请按『活动消化』技能把它整理成结构化记录："
            "用 cheese 把 做了什么/定了什么/谁负责/下一步 设为本话题实况文档；"
            "如果这是个关键节点就用 cheese 钉成里程碑；"
            "需要分派的待办用 cheese 通知到人。\n\n---\n" + text
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

        system_prompt = _build_system_prompt(
            self._base_prompt, load_skills(HEARTBEAT_SKILLS), None, []
        )
        prompt = (
            "现在做一次定期巡检。下面是项目当前状态。请：先在回复里写下你的巡检"
            "判断和理由（决策日志：看了什么、该催谁/该拆什么/有什么风险），"
            "然后只对真正需要的事用 cheese 发分级通知（level=silent/light/"
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
            await BlockRepository(session).add(
                project_id=project_id,
                topic_id=root_topic_id,
                author=await self._agent_handle(session, root_topic_id),
                author_type=AuthorType.ai,
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
            memory = memory_store(session)

            project = await projects.get(project_id)
            if project is None:
                raise NotFoundError("Project not found")
            all_topics = await topics.list_for_project(project_id)
            upcoming = await milestones.list_calendar(project_id)
            # Deliberately the shared pool, not any one agent's memory: a project
            # summary describes the project, and what a 芝士 learned for itself is
            # not project knowledge.
            memories = await recall_pools(
                memory,
                [(MemoryScope.project, str(project_id))],
                # The one-pager is about the project as a whole, so its name and
                # its topic titles are the context to pull memory against — the
                # nearest thing this call has to "what is being asked".
                query=_memory_query(
                    project.name, *(t.title for t in all_topics if t.title)
                ),
            )
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
        mem_lines = "\n".join(f"- {_chipify_paths(m)}" for m in memories.facts)
        if memories.omitted:
            mem_lines += f"\n- （另有 {memories.omitted} 条相关性较低的记忆未列出）"
        context = (
            f"项目名：{project.name}\n\n## 话题\n{topic_lines or '（暂无）'}\n\n"
            f"## 临近里程碑\n{ms_lines or '（暂无）'}\n\n"
            f"## 关键记忆\n{mem_lines or '（暂无）'}"
        )
        system_prompt = _build_system_prompt(
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
