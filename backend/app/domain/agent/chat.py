"""Chat orchestration — ties topic, blocks, memory, and the agent together.

This is the platform "shell" around 芝士: it persists the conversation as
blocks (append-only history, spec H1), injects project memory into the agent's
context (spec §8.4 带记忆回答), lands each completed assistant message as its
own block (Slack-style discrete messages, no token streaming), and stores the
resumable session id on the topic.

DB writes happen in short transactions around the (long) streaming call so we
never hold a transaction open across the model round-trip.
"""

import asyncio
import logging
import re
import shutil
import time
import uuid
from collections import Counter
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.errors import GatewayUnavailableError, NotFoundError
from app.core.text import markdown_preview
from app.domain.agent import event_spool
from app.domain.agent.cloud_provider import CloudProvider
from app.domain.agent.compute import ComputePool, ComputeProvider
from app.domain.agent.gateway import LlmGateway, drain_new_usage
from app.domain.agent.hook_events import MessageAssembler
from app.domain.agent.hooks_substrate import HooksSessionProvider, TopicSubscription
from app.domain.agent.host_swap import NO_SWAP, handle_host_failure
from app.domain.agent.market import subscription_model_alias
from app.domain.agent.platform_failures import classify_platform_failure
from app.domain.agent.platform_notices import (
    EVENT_TURN_FAILED,
    EVENT_TURN_TIMEOUT,
    SEVERITY_ERROR,
    SEVERITY_WARN,
    WHO_HUMAN,
    WHO_PLATFORM,
    delivery_fallback_notice,
    notice,
)
from app.domain.agent.profiles import ProfileRegistry
from app.domain.agent.service import (
    AgentDeliveryFailure,
    AgentEvent,
    AgentMessage,
    AgentResult,
    AgentService,
    AgentSessionInfo,
    AgentToolResult,
    AgentToolUse,
    AgentUsage,
)
from app.domain.agent.skills import DEFAULT_CHAT_SKILLS, load_scenario, load_skills
from app.domain.agent.stages import resolve_stage, stage_scenario
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
from app.domain.project.repositories import ProjectRepository
from app.domain.review.models import AcceptCard, AcceptStatus
from app.domain.review.repositories import AcceptCardRepository
from app.domain.team.repositories import TeamRepository
from app.domain.topic.models import Topic, TopicKind, TopicStatus
from app.domain.topic.repositories import TopicProgressRepository, TopicRepository
from app.domain.topic_membership.services import TopicMemberService
from app.domain.usage.credits import usage_to_credits
from app.domain.usage.repositories import ComputeGrantRepository, UsageRepository
from app.domain.workspace import identity as ws_identity
from app.domain.workspace import service as ws

ACTIVITY_SKILLS = ["conversation-style", "activity-digestion", "doc-form"]
HEARTBEAT_SKILLS = ["heartbeat", "conversation-style"]
PRIVATE_SKILLS = ["private-chat", "conversation-style"]

CHEESE_AUTHOR = "cheese"

logger = logging.getLogger(__name__)


@dataclass
class _HookWorkState:
    """Persistence context for work whose events arrive on a subscription."""

    project_id: uuid.UUID
    topic_id: uuid.UUID
    work_id: uuid.UUID
    provider: ComputeProvider
    pending_ids: set[uuid.UUID]
    reply_to: uuid.UUID | None
    roster: list[dict]
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
    todo: list[dict] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    # The topic branch's commits as of turn start — what makes "this turn's
    # changes" answerable at turn end. A task rather than a value, because the
    # read shells out to git and creates the repo on first use; see where it is
    # started. `None` (or a read that failed) means the turn lands NO change
    # summary rather than a wrong one: with no baseline, every commit looks new.
    known_commits: asyncio.Task[set[str] | None] | None = None


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


def _spool_age_s(path: Path) -> float:
    """How long ago this spool file was written, in seconds.

    Its mtime, not its name: the name is a sequence number now (event_spool's
    docstring says why it stopped being a clock), and the grace below is a real
    wait for a flush that may still be coming — which only a real clock measures.
    A file that vanished between listing and here reads as brand new, so a
    partial waits one more pass instead of being flushed on a stat error.
    """
    try:
        return max(0.0, time.time() - path.stat().st_mtime)
    except OSError:
        return 0.0


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
    "create_subtopic": "拆出子话题",
    "update_doc": "更新文档",
    "remember": "记入记忆",
    "notify": "发送通知",
    "request_accept": "递出验收卡",
    "return_conclusion": "回流结论",
    "pin_milestone": "钉里程碑",
    "write_file": "写文件",
    "record_decision": "记录决策",
}
_TOOL_ARG = {
    "create_subtopic": "title",
    "update_doc": "content",
    "remember": "fact",
    "notify": "title",
    "request_accept": "reviewer_handle",
    "return_conclusion": "conclusion",
    "pin_milestone": "title",
    "write_file": "path",
    "record_decision": "decision",
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
_TOOL_ARG.update(
    {
        "Bash": "command",
        "Write": "file_path",
        "Edit": "file_path",
        "Read": "file_path",
        "Glob": "pattern",
        "Grep": "pattern",
        "WebSearch": "query",
        "WebFetch": "url",
        "Agent": "description",
        "Task": "description",
        "NotebookEdit": "notebook_path",
        "Skill": "skill",
        "ToolSearch": "query",
    }
)


def _tool_arg_preview(name: str, args: dict) -> str:
    """Whitespace-collapsed preview of the tool's most telling argument."""
    key = _TOOL_ARG.get(name)
    if key and isinstance(args, dict) and args.get(key) is not None:
        return " ".join(str(args[key]).split())[:120]
    return ""


def _format_tool_event(name: str, args: dict) -> str:
    """Human-readable FALLBACK text for an event block (old clients / old rows).

    The UI renders from the structured meta (see _tool_event_meta); this baked
    string only shows when meta is absent."""
    verb = _TOOL_VERB.get(name, name)
    preview = _tool_arg_preview(name, args)
    return f"{verb}\n{preview}" if preview else verb


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


def _tool_event_meta(name: str, args: dict, *, platform: bool) -> dict:
    """Structured payload persisted on an event block: the UI translates the
    tool name and colors the dot from these fields at DISPLAY time, so a verb
    missing from today's table is never baked in untranslated forever."""
    meta: dict = {"tool": name, "platform": platform}
    preview = _tool_arg_preview(name, args)
    if preview:
        meta["arg"] = preview
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
            current = {"path": header.group("path"), "added": 0, "removed": 0}
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
    "topics": "更新了子话题",
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
    team_compute_profile: str | None = None,
) -> str | None:
    """The compute pool a turn runs on (execution-architecture v4 会话级选择): the
    topic's own selection wins, else the project's sticky memory, then the team's
    default, else None (the ComputePool default). An id that isn't deployed here
    is ignored by ``ComputePool.select`` and degrades to the default — never breaks
    a turn."""
    if topic_compute_profile:
        return topic_compute_profile
    return (project_settings or {}).get("compute_profile") or team_compute_profile


async def _team_compute_profile(session: AsyncSession, project) -> str | None:
    """Load the owning team's default without making Project own the setting."""
    if project is None or project.team_id is None:
        return None
    team = await TeamRepository(session).get_by_id(project.team_id)
    return team.compute_profile if team is not None else None


def _transient_provider_error(result: AgentResult) -> bool:
    if classify_platform_failure(result.text) is not None:
        # Retrying cannot create disk space and can make pressure worse.
        return False
    rl = result.rate_limit or {}
    if rl.get("status") == "rejected":
        return False  # seat limit — resets hours later, retrying just burns turns
    if result.api_error_status is not None:
        return result.api_error_status in _TRANSIENT_HTTP
    return True  # unclassified error result with zero output — one more try is cheap


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
_BARE_PATH_RE = re.compile(r"(?<![\w/.&<-])((?:[\w.-]+/)+[\w-]+\.\w{1,8})(?![\w/])")


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
    # 两阶段采纳: `pr_open` 是 open 状态里最容易被漏掉的一个 —— 卡被采纳了但
    # 话题没归档、容器没停、活还没干完。不在这里就等于芝士在 PR 迭代期间
    # 完全收不到"你现在有一条通往 GitHub 的通道"这个事实。
    AcceptStatus.pr_open,
)

_OPEN_CARD_HINTS = {
    AcceptStatus.pending: "闸门已过，等 {reviewer} 采纳",
    AcceptStatus.pending_gate: "闸门检查进行中",
    AcceptStatus.gate_failed: (
        "闸门检查未过——用 `cheese status` 看失败输出，修复后重新递卡"
    ),
    AcceptStatus.gate_blocked: (
        "闸门检查没跑成（不是没通过，是没跑起来）——用 `cheese status` 看输出，"
        "把检查环境弄起来再重新递卡"
    ),
    AcceptStatus.conflict: "采纳时发现合并冲突，待处理",
    AcceptStatus.pr_open: (
        "已被 {reviewer} 采纳并开出 PR，正在等 CI——继续在本分支提交即可，"
        "平台会自动重推到 PR"
    ),
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
    budget_s: float,
    activity_aware: bool,
    is_resume: bool,
    disk: tuple[int, int] | None,
    open_cards: list[AcceptCard] | None,
    progress: list[dict] | None = None,
    sandbox: tuple[int, int] | None = None,
) -> list[str]:
    """盲飞防护: the run facts an agent has no other way to see — its own time
    budget, whether it's a continuation, disk headroom, and where this topic's
    accept cards stand. Plain bullet lines so the prompt stays small.

    ``activity_aware`` (the tmux backend, turn 活跃度检测): that backend no
    longer dies on a fixed minute count — only real idleness (checked, then
    confirmed dead) or a many-hours hard ceiling ends it — so a countdown-style
    "到点会被中断" line is both inaccurate and was observed making the agent
    rush (dev, 2026-08-08: shortened verification to "save time" against a
    deadline that was only ever meant as a wedged-turn safety net)."""
    if activity_aware:
        lines = [
            "- 这轮跑在有活跃度检测的后端上：没有固定时长倒计时，只要还在"
            "产生动静（工具调用、终端输出）就不会被打断，真的卡死了才会兜底"
            "结束。长活照样要边做边落盘/提交，别把成果都压在最后一步。"
        ]
    else:
        minutes = max(1, int(budget_s // 60))
        lines = [
            f"- 时间预算：本轮最多约 {minutes} 分钟，到点会被平台中断"
            "（之后自动续跑一次）。长活边做边落盘/提交，别把成果都压在最后一步。"
        ]
    if is_resume:
        lines.append(
            "- 本轮是自动续跑：上一轮被中断后接着跑。"
            "先确认上一轮做到哪了再继续，别重做。"
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


# 分身开工首轮的内部指令 (split auto-kickoff)。Prompt-only: it never appears as a
# message; what the humans see is the 分身's own opening, generated from the task
# brief preset as the topic's living doc (语义内容由 AI 生成 — see CLAUDE.md).
KICKOFF_PROMPT = (
    "这个话题刚从父话题拆分/升级出来，由你（分身）负责推进。任务简报在系统提示的"
    "「当前话题的实况文档」里：拆分意图（或被升级的那段讨论）+ 父话题文档快照。"
    "现在开工：\n"
    "1. 先发开场白：一两句复述你理解的任务、说明打算怎么推进（给人纠偏的机会）；"
    "简报信息不足就明确列出缺什么、@ 拆分发起人补充。\n"
    "2. 把实况文档改写成你自己的状态摘要（目标/约束/下一步），别留着简报原文不动。\n"
    "3. 能直接开始的活就开始干；需要拍板的用决策请求找对的人。"
)


def conclusion_digest_prompt(
    conclusion_message: str,
    *,
    card_id: str | None = None,
    deadline: datetime | None = None,
) -> str:
    """The parent's wake-up instruction when a sub-topic returns its conclusion
    (结论回流唤醒父话题 — the return leg of the subagent loop: in Claude Code
    the parent resumes when the Task tool result arrives). Prompt-only; the
    conclusion text is copied verbatim, nothing is derived from it.

    结论卡·阶段一: when a card was filed, the parent is told how to settle it —
    and, more importantly, that doing NOTHING is 采信. The prompt is only half
    the mechanism; the platform accepts the card when this turn ends whatever
    the model does (see conclusion.services.settle_turn_cards)."""
    card_note = ""
    if card_id is not None:
        by = f"（{deadline:%H:%M} UTC 前）" if deadline is not None else ""
        card_note = (
            "\n\n---\n"
            f"这条结论挂着一张结论卡 `{card_id}`。**默认采信**：你这一轮结束时"
            f"它就自动采信、子话题随之归档{by}，你不需要做任何事。\n"
            "只有两种情况才动它：\n"
            "- 缺一条关键证据、而子话题的上下文还热着 → "
            f'`cheese conclusion need-evidence {card_id} "要补什么"`'
            "（每张卡只能打回一次）；\n"
            "- 这个结论要以某个人的名义做出去 → "
            f'`cheese conclusion escalate {card_id} "要谁拍什么板"`。'
        )
    return (
        "一个子话题刚回流了结论（原文如下，也已织进本话题实况文档末尾）。"
        "请消化它：\n"
        "1. 把实况文档整理成最新状态——结论的要点合并进对应章节，"
        "别让「子话题结论」堆在文档末尾。\n"
        "2. 判断下一步：这个结论解锁了什么？需要继续拆活就拆（split 带 --brief），"
        "需要人拍板/验收就发通知或验收卡，整件事收尾了就说明结论。\n"
        "3. 在对话里用一两句话向大家报信（结论已在文档里，别复述全文）。\n\n"
        f"---\n{conclusion_message}{card_note}"
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


def _strip_platform_notice(text: str) -> str:
    """Neutralize the platform marker inside HUMAN text, so a person cannot type
    a message that reads as a platform instruction. The marker is the one thing
    in the prompt that claims institutional authority, so it has to be
    unforgeable from the content side."""
    return text.replace(PLATFORM_NOTICE, "【平台·用户原文】")


def _attachment_prompt_line(author: str, path: str, *, embeds_images: bool) -> str:
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
        return _attachment_prompt_line(b.author, b.content, embeds_images=embeds_images)
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
        f"🔁 这 {len(pending)} 条消息已经是第 {attempt} 次送进轮次，"
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
        agent: AgentService,
        base_system_prompt: str,
        workspace_root: str,
        sandbox_enabled: bool = False,
        profiles: ProfileRegistry | None = None,
        compute: ComputePool | None = None,
        gateway: LlmGateway | None = None,
        replace_cloud_machine: Callable[..., Awaitable[None]] | None = None,
    ):
        self._sessions = session_factory
        self._base_prompt = base_system_prompt
        # For the turn-meta disk line only; sandbox mounting still goes through
        # the compute pool below.
        self._workspace_root = workspace_root
        # Compute side of the two-pool model: a provider owns sandbox creation +
        # turn execution + workspace checkpointing (design §3/v3, review R2). The
        # turn path talks to the pool, never to a sandbox dict. Defaults to a local
        # Docker pool; deps injects a remote pool when configured.
        self._compute = compute or ComputePool.local(
            agent=agent,
            workspace_root=workspace_root,
            sandbox_enabled=sandbox_enabled,
        )
        self._compute.bind_hook_event_consumer(
            self._consume_hook_event, self._set_hook_activity
        )
        self._compute.bind_prompt_receipt_consumer(self.confirm_prompt_receipt)
        # Mid-turn messages whose write the transport accepted but whose
        # UserPromptSubmit receipt has not arrived yet (#539 decision A):
        # topic → [(injected text, block ids, consuming turn)]. The receipt
        # stamps them consumed; until then they stay pending, so a session
        # death replays them (宁可重复不可丢失).
        self._pending_receipts: dict[
            uuid.UUID, list[tuple[str, list[uuid.UUID], uuid.UUID]]
        ] = {}
        # Per-project ExecutionProfile (model + provider). None → always the
        # agent's built-in default (tests / single-profile deploys).
        self._profiles = profiles
        # LiteLLM gateway ADMIN client (docs/llm-gateway.md L1/L2). None = off.
        # The lock serializes key-mint and usage-drain read-modify-writes on
        # project.settings (single-process reality, like the topic locks).
        self._gateway = gateway
        self._replace_cloud_machine = replace_cloud_machine
        self._gateway_lock = asyncio.Lock()
        # Load the conversation skills once (spec §8.3 product "soul").
        self._skills = load_skills(DEFAULT_CHAT_SKILLS)
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

    def tmux_activity_status(self, topic_id: uuid.UUID) -> dict | None:
        """`cheese status`'s idle-suspect signal (turn 活跃度检测) — see
        `ComputePool.tmux_activity_status`."""
        return self._compute.tmux_activity_status(topic_id)

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
            # System-initiated turn (自动续跑 / 评论叫醒 / 冲突调度…): no human
            # spoke — the opener is a SYSTEM event in the 现场, and the
            # instruction goes straight to the agent as the prompt.
            #
            # 平台提示统一契约: `nudge_event` is the one line the room sees,
            # `nudge_meta` its structured payload — which is where a caller puts
            # the长文 (CI 日志 / 检查输出 / 冲突文件清单) so the room stays
            # glanceable while nothing is lost. `content` is untouched: it is
            # still the whole instruction 芝士 gets as its prompt.
            if not nudge_event:
                why = resume_reason or "从上一轮的断点继续"
                nudge_event = f"⏯️ 自动续跑：{why}"
            payload = await self.post_system_event(
                topic_id, nudge_event, turn_id, meta=nudge_meta
            )
            if payload is None:
                raise NotFoundError("Topic not found")
            yield {"type": "event_block", "block": payload}
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
            _attachment_prompt_line(author, image["path"], embeds_images=True)
            for image in images
        )
        line = "\n".join(lines)
        # Register BEFORE the write so a fast receipt cannot race the entry
        # (#539 decision A). The receipt is still the consumed boundary — it
        # just no longer gates the delivery verdict: write-accept is delivery,
        # and the stamp lands whenever the session actually consumes the text
        # (confirm_prompt_receipt). Until then the message stays pending, so a
        # session death replays it — 宁可重复不可丢失.
        pending = self._pending_receipts.setdefault(topic_id, [])
        entry = (line, list(user_block_ids), consuming_turn_id)
        pending.append(entry)
        del pending[:-16]  # a dead session must not grow this forever
        try:
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
            text, block_ids, consuming_turn_id = entry
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
        """Persist a system event into the 现场 timeline (e.g. a turn failure):
        visible in the flow, scrolls with it, and survives a reload — unlike a
        transient banner. Returns the block payload, or None if the topic died."""
        async with self._sessions() as session:
            topics = TopicRepository(session)
            blocks = BlockRepository(session)
            topic = await topics.get(topic_id)
            if topic is None:
                return None
            block = await blocks.add(
                project_id=topic.project_id,
                topic_id=topic.id,
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

    async def orphan_turn_evidence(
        self, topic_id: uuid.UUID, turn_ids: list[uuid.UUID]
    ) -> dict:
        """Did claude demonstrably RECEIVE each of these turns' prompts?

        The orphan sweep decides attach-vs-re-send on this (#316): re-sending a
        task claude already heard is how one deploy stacked five zombie turns
        on a topic. Two sources, matching the two places a hook can leave a
        durable trace:

        - ``delivered``: turn ids among ``turn_ids`` that own at least one
          AI-authored block — the live hook/stream path persisted it, so claude
          acted on the prompt.
        - ``spool``: the topic's durable spool holds any event beyond
          SessionStart (which fires at launch, BEFORE the prompt is typed).
          Spool entries carry no turn id, so this is topic-level evidence — it
          vetoes every re-send on the topic rather than crediting one turn.
        """
        async with self._sessions() as session:
            topic = await TopicRepository(session).get(topic_id)
            if topic is None:
                return {"delivered": set(), "spool": False}
            blocks = await BlockRepository(session).list_for_topic(topic_id)
        wanted = set(turn_ids)
        delivered = {
            b.turn_id
            for b in blocks
            if b.turn_id in wanted and b.author_type == AuthorType.ai
        }
        spool = False
        spool_dir = ws.spool_dir(topic.project_id, topic_id)
        # Only the UNREAD tail counts. A read event has already become a block,
        # so the `delivered` arm above speaks for it — and events now live out
        # their retention on disk instead of being deleted as they are read, so
        # counting the whole directory would let one ancient hook veto every
        # re-send this topic ever needs.
        for _path, _eid, payload in event_spool.spool_entries(
            spool_dir, after=event_spool.read_cursor(spool_dir)
        ):
            if not isinstance(payload, dict):
                continue
            name = str(
                payload.get("hook_event_name") or payload.get("hookEventName") or ""
            )
            if name != "SessionStart":
                spool = True
                break
        return {"delivered": delivered, "spool": spool}

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

    async def recover_hook_subscriptions(self, device_id: str | None = None) -> int:
        """Reattach surviving hook screens and replay their crash-recovery logs."""
        subscriptions = await self._compute.recover_hook_subscriptions(device_id)
        unique = {subscription.topic_id: subscription for subscription in subscriptions}
        for subscription in unique.values():
            try:
                await self._replay_hook_subscription(subscription)
            except Exception:  # noqa: BLE001 — one topic cannot block startup
                logger.exception(
                    "hook subscription recovery failed for topic %s",
                    subscription.topic_id,
                )
                subscription.ready.set()
        return len(unique)

    async def _replay_hook_subscription(self, subscription: TopicSubscription) -> None:
        """Replay one topic spool's unread tail into the recovered subscription.

        Where the tail starts is the spool's own cursor. It used to be inferred
        — walk the topic's blocks backwards for the newest event id that also
        appears in the spool — which was a guess dressed as a fact: an event the
        live path had persisted WITHOUT an id, or a tail whose every event was
        of a kind that persists nothing, left the search empty and replayed the
        whole spool from the beginning. The cursor is the same claim, written by
        whoever actually persisted the events instead of reconstructed from
        their leftovers.
        """
        spool = ws.spool_dir(subscription.project_id, subscription.topic_id)
        entries = event_spool.spool_entries(spool, after=event_spool.read_cursor(spool))
        if not entries:
            subscription.ready.set()
            return

        async with self._sessions() as session:
            blocks = await BlockRepository(session).list_for_topic(
                subscription.topic_id
            )

        subscription.replay_spool = spool
        replayed: dict[str, dict] = {}
        for path, eid, payload in entries:
            subscription.replay_queue.append((path.name, eid))
            if payload is None:
                # Unparseable, so nothing can ever be made of it — mark it done
                # so the cursor steps over it rather than stopping here forever.
                subscription.replay_done.add(eid)
                continue
            if eid in replayed:
                continue
            replayed[eid] = payload

        if replayed:
            subscription.replay_seen_messages.update(
                (block.content or "").strip()
                for block in blocks
                if block.kind == BlockKind.message
                and block.author_type == AuthorType.ai
            )
        for eid, payload in replayed.items():
            replay = dict(payload)
            replay["_eid"] = eid
            subscription.sink.queue.put_nowait(replay)
        subscription.ready.set()
        await asyncio.wait_for(subscription.sink.queue.join(), timeout=30)

    def schedule_spool_settle(self, topic_id: uuid.UUID, delay_s: float = 2.0) -> None:
        """Debounced background ``settle_spool``. Two callers: the hooks
        endpoint when it parks an event with no turn listening (so a working
        claude's progress — and its Stop — lands within seconds instead of
        waiting for the next summon), and the orphan sweep when it attaches to
        an interrupted turn (so anything already parked lands now)."""
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
        """Best-effort: point the topic at the (possibly partial) session so the
        next summon resumes it. Never raises — used on failure paths."""
        try:
            async with self._sessions() as session:
                topic = await TopicRepository(session).get(topic_id)
                if topic is not None:
                    # Resolved here rather than threaded in: the hook-consume
                    # path reaches this with no ResolvedAgent in scope, and this
                    # already opens a session to do its own write.
                    agent = await self._resolved_agent(session, topic)
                    await AgentSessionService(session).remember(
                        topic_id=topic_id,
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

    async def _consume_hook_event(
        self,
        project_id: uuid.UUID,
        topic_id: uuid.UUID,
        turn_id: uuid.UUID,
        event: AgentEvent | AgentDeliveryFailure,
        eid: str | None,
        result_text_seen: bool,
        platform_unsolicited: bool,
    ) -> None:
        """Persist and broadcast one event from a live screen subscription."""
        from app.domain.agent.runtime import get_broker

        broker = get_broker()
        frame: dict | None = None
        state = self._hook_work.get((topic_id, turn_id))
        if isinstance(event, AgentSessionInfo):
            await self._save_session_pointer(topic_id, event.session_id)
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
            )
            if payload is not None:
                if state is not None:
                    state.assistant_count += 1
                frame = {"type": "assistant_block", "block": payload}
        elif isinstance(event, AgentToolUse):
            name = event.name.replace("mcp__cheese__", "")
            args = event.input or {}
            if state is not None and name in _TASK_TOOLS:
                if _apply_task_event(state.todo, name, args):
                    await self._persist_progress(topic_id, state.todo, turn_id)
                    frame = {
                        "type": "todo",
                        "items": [dict(item) for item in state.todo],
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
                )
                if payload is not None:
                    frame = {"type": "event_block", "block": payload}
                if state is not None and name == "Bash":
                    resource = _cheese_resource(str(args.get("command", "")))
                    if resource in _ACTION_LABEL and resource not in state.actions:
                        state.actions.append(resource)
        elif isinstance(event, AgentToolResult):
            payload = await self._persist_subagent_result(
                project_id=project_id,
                topic_id=topic_id,
                event=event,
                turn_id=turn_id,
                eid=eid,
                platform_unsolicited=platform_unsolicited,
            )
            if payload is not None:
                frame = {"type": "event_block", "block": payload}
        elif isinstance(event, AgentResult):
            if event.session_id:
                await self._save_session_pointer(topic_id, event.session_id)
            if event.is_error:
                payload = await self.post_system_event(
                    topic_id,
                    event.text,
                    turn_id,
                    meta=notice(
                        EVENT_TURN_TIMEOUT,
                        severity=SEVERITY_WARN,
                        who=WHO_HUMAN,
                        detail="会话活动已停止；屏幕订阅仍会接收后续输出。",
                        detail_label="详细说明",
                    ),
                )
                if payload is not None:
                    frame = {"type": "event_block", "block": payload}
            elif event.text.strip() and not result_text_seen:
                payload = await self._persist_assistant_message(
                    project_id=project_id,
                    topic_id=topic_id,
                    text=event.text,
                    turn_id=turn_id,
                    reply_to=None,
                    roster=state.roster if state is not None else None,
                    topic_refs=state.topic_refs if state is not None else [],
                    eid=eid,
                    platform_unsolicited=platform_unsolicited,
                    continuation_id=(
                        state.continuation_id if state is not None else None
                    ),
                )
                if payload is not None:
                    if state is not None:
                        state.assistant_count += 1
                    frame = {"type": "assistant_block", "block": payload}
        if frame is not None:
            await broker.publish(str(topic_id), frame)
        if isinstance(event, AgentResult):
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
            if event.is_error:
                await broker.publish(
                    str(topic_id),
                    {"type": "error", "message": event.text, "persisted": True},
                )
            await broker.publish(str(topic_id), {"type": "done"})

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
            await session.commit()

        state.provider.checkpoint(state.project_id, state.topic_id)
        # AFTER the checkpoint: that is what turns this turn's edits into the
        # commit the summary is about.
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
                assistant_text=result.text,
            )
            try:
                from app.domain.conclusion.services import settle_turn_cards

                await settle_turn_cards(
                    self._sessions,
                    state.topic_id,
                    turn_started_at=state.started_at,
                )
            except Exception:  # noqa: BLE001 — periodic settlement is the backstop
                logger.exception(
                    "conclusion settle failed for topic %s", state.topic_id
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
            topic = await topics.get(topic_id)
            if topic is None:
                raise NotFoundError("Topic not found")
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
                    topic_id=topic.id,
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
                    topic_id=topic.id,
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
    ) -> dict | None:
        """Persist ONE discrete 芝士 message (Slack-style): committed the moment
        the SDK reports the AssistantMessage complete, so a turn lands as
        several complete messages instead of one growing streamed bubble.
        Handles the same mention canonicalization / notify / refs as before.
        ``eid`` (hooks path) is stamped into meta so the spool reconcile can
        dedup a backfilled copy against this live one; ``eids`` carries every
        constituent flush id of a coalesced message, and any one of them
        matching an existing block means this message already landed.

        Returns None when ``continuation_id`` says this exact message already
        landed in an earlier attempt at the same work (④ 自动续跑): the resumed
        turn re-narrating "我先看一下 X" must not post a second copy of it. The
        caller treats None as "nothing to broadcast"."""
        meta: dict | None = None
        if eid:
            meta = {"eid": eid}
        if len(eids) > 1:
            meta = {**(meta or {}), "eids": list(eids)}
        if backfilled:
            meta = {**(meta or {}), "backfilled": True}
        if platform_unsolicited:
            meta = {**(meta or {}), "platform_unsolicited": True}
        known_ids = [e for e in dict.fromkeys((eid, *eids)) if e]
        async with self._sessions() as session:
            blocks = BlockRepository(session)
            if known_ids and await blocks.has_any_eid(topic_id, known_ids):
                return None
            # Claim BEFORE writing, in the SAME session: the key and the block
            # commit together, so "key present" and "message posted" cannot
            # disagree no matter where the process dies.
            if continuation_id is not None and not await idem.claim(
                session,
                action_key(continuation_id, "message", text),
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
            author = await self._agent_handle(session, topic_id)
            block = await blocks.add(
                project_id=project_id,
                topic_id=topic_id,
                author=author,
                author_type=AuthorType.ai,
                content=text,
                kind=BlockKind.message,
                reply_to=reply_to,
                turn_id=turn_id,
                meta=meta,
            )
            # <@handle> mentions in 芝士's message → strong notify (the token is
            # the single source of truth: what's shown = who's notified).
            # Hallucinated handles get flagged in 现场, never silently no-op.
            if topic is not None:
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
                        author=author,
                        author_type=AuthorType.ai,
                        content=warn,
                        kind=BlockKind.event,
                        turn_id=turn_id,
                    )
            payload = _block_payload(BlockOut.model_validate(block))
            await session.commit()
        return payload

    async def _persist_progress(
        self,
        topic_id: uuid.UUID,
        items: list[dict],
        turn_id: uuid.UUID | None,
    ) -> None:
        """Write the topic's checklist through to storage (进度层, #187).

        Best-effort on purpose: progress is a convenience for the NEXT turn, so a
        storage hiccup must never take down the turn that is currently producing
        real work. Same commit-now contract as _persist_tool_event — batching to
        turn end would lose exactly the case this exists for (the turn dies)."""
        try:
            async with self._sessions() as session:
                await TopicProgressRepository(session).save(
                    topic_id, items, turn_id=turn_id
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
    ) -> dict | None:
        """Persist ONE 施工现场 event the moment it streams in, not batched to the
        turn-end tx2. Mirrors _persist_assistant_message's commit-now contract so
        a mid-turn restart/crash never loses the 现场 timeline already produced.
        ``eid`` (the hook forwarder's event id) is stamped into meta so the durable
        spool reconcile can dedup a backfilled copy against this live one. Returns
        the persisted block payload so a caller (live path or spool reconcile) can
        broadcast it as a WS frame."""
        return await self._persist_room_event(
            project_id=project_id,
            topic_id=topic_id,
            content=_format_tool_event(name, tool_input),
            meta=_tool_event_meta(name, tool_input, platform=platform),
            turn_id=turn_id,
            eid=eid,
            backfilled=backfilled,
            platform_unsolicited=platform_unsolicited,
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
    ) -> dict | None:
        """One event block, committed NOW and deduped by event-id.

        Shared by everything the room learns mid-turn — a tool call, a subagent's
        conclusion, the turn's change summary — so all three get the same
        durability and idempotency contract instead of three copies of it that
        drift. Returns None when this event-id already landed.

        ``in_room`` decides whether the conversation shows it at all. The
        frontend reads that off ``author_type`` (system = the room, ai = 现场
        only), which is an implicit switch with no error path: pick wrong and the
        event simply never appears, silently, forever. Naming it here at least
        makes the choice visible at every call site."""
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
                author=await self._agent_handle(session, topic_id),
                author_type=AuthorType.system if in_room else AuthorType.ai,
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
        )

    async def _turn_changeset(
        self,
        project_id: uuid.UUID,
        topic_id: uuid.UUID,
        known_commits: set[str] | None,
    ) -> _Changeset | None:
        """This turn's net effect on the topic branch, or None when there is none.

        Called AFTER the provider's checkpoint, which is what turns the turn's
        native edits into a commit — so "the commits that were not there at turn
        start" is exactly this turn's work. Best-effort and off the event loop:
        the numbers are a courtesy, and no turn should die (or stall) over them.
        """
        if known_commits is None:
            return None

        def _collect() -> _Changeset | None:
            commits = [
                row["hash"]
                for row in ws.git_log(
                    project_id, limit=_CHANGE_COMMIT_WALK, topic_id=topic_id
                )
            ]
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
            return await asyncio.to_thread(
                lambda: {
                    row["hash"]
                    for row in ws.git_log(
                        project_id, limit=_CHANGE_COMMIT_WALK, topic_id=topic_id
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
        )

    async def _reconcile_spool(
        self, project_id: uuid.UUID, topic_id: uuid.UUID, turn_id: uuid.UUID | None
    ) -> AsyncIterator[dict]:
        """Backfill 现场 events the live hook path missed (backend down / no listener
        during a prior turn) from the durable spool WAL — idempotent by event-id.
        No-op for the sdk backend (no spool dir) and an empty spool. Best-effort: a
        reconcile failure never blocks the turn.

        Scope: 现场 tool events, 芝士 chat messages (MessageDisplay), AND the
        turn-ending Stop — all idempotent by event-id, so a copy the live path
        already persisted is skipped. Backfilled messages skip mention-notify
        (the moment passed), but they DO yield a WS frame like the live path — a
        hook that missed its turn's listening window must still reach the
        frontend, just without threading or an @-notify (bug: it was landing as
        a silent DB row nobody saw).

        The Stop is what lets an ORPHANED turn finish (#316): a backend restart
        kills the waiter, not the working claude, and the sweep no longer
        re-prompts a claude that heard the task — so its Stop arrives with no
        turn listening, gets parked, and has to close the books from here: save
        the finished session's pointer, and land its final message unless a
        MessageDisplay (live or in this same batch) already carries that text —
        Stop's last_assistant_message is normally a copy of the last one.

        MessageDisplay entries are per-flush, not per-message, so they fold
        through a MessageAssembler exactly like the live path: one whole block
        per message. Flushes of a message that has not completed yet keep
        their spool files for the pass where the missing flush has arrived —
        unless they are stale (no Stop, nothing new for a while), in which
        case what arrived lands joined rather than being lost."""
        try:
            spool = ws.spool_dir(project_id, topic_id)
            entries = event_spool.spool_entries(
                spool, after=event_spool.read_cursor(spool)
            )
            if not entries:
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
                if b.kind == BlockKind.message and b.author_type == AuthorType.ai
            }
            assembler = MessageAssembler()
            recovered = 0

            async def _land_message(
                message: AgentMessage, fallback_eid: str
            ) -> dict | None:
                # A chat message whose live delivery was lost — land it as
                # history (no reply threading, no notify: the moment passed)
                # but still broadcast it, exactly like the live path does.
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
                )
                seen.add(fallback_eid)
                seen.update(message.eids)
                # Feed the Stop's text dedup even when this copy itself was
                # suppressed — the text exists either way. Stored form, so it
                # is comparable with `known_texts` seeded from the DB.
                known_texts.add(_canon(message.text))
                return block_payload

            for _path, eid, payload in entries:
                if payload is None or eid in seen:
                    continue
                payload["_eid"] = eid
                events = assembler.translate(payload)
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
                            yield {"type": "assistant_block", "block": block_payload}
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
                    known_texts.add(stop_text)
                    if block_payload is None:
                        continue
                    recovered += 1
                    yield {"type": "assistant_block", "block": block_payload}
                    continue
                for event in events:
                    if isinstance(event, AgentMessage):
                        block_payload = await _land_message(event, eid)
                        if block_payload is None:
                            continue
                        recovered += 1
                        yield {"type": "assistant_block", "block": block_payload}
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
                    )
                    seen.add(eid)
                    if block_payload is None:
                        continue
                    recovered += 1
                    yield {"type": "event_block", "block": block_payload}
            pending = assembler.pending_eids()
            if pending:
                newest = min(
                    (
                        _spool_age_s(path)
                        for path, entry_eid, _payload in entries
                        if entry_eid in pending
                    ),
                    default=0.0,
                )
                if newest > _SPOOL_PARTIAL_GRACE_S:
                    # No Stop and nothing new for a while: the message will
                    # never complete (the screen died mid-message). Land what
                    # arrived, joined, rather than lose it.
                    for partial in assembler.drain():
                        block_payload = await _land_message(partial, partial.eid or "")
                        if block_payload is not None:
                            recovered += 1
                            yield {"type": "assistant_block", "block": block_payload}
                    pending = set()
            # Reading is not consuming: the cursor moves over the events that
            # reached the timeline, and retention — not this pass — is what
            # eventually deletes them. It stops at the first still-buffered
            # flush rather than stepping over it, so the pass that completes
            # that message still sees the flushes it is made of.
            for path, entry_eid, _payload in entries:
                if entry_eid in pending:
                    break
                event_spool.write_cursor(spool, path.name)
            event_spool.prune(spool, older_than_s=_SPOOL_RETENTION_S)
            if recovered:
                logger.info(
                    "reconciled %d spooled 现场 event(s) for topic %s",
                    recovered,
                    topic_id,
                )
        except Exception:  # noqa: BLE001 — reconcile is best-effort, never fail a turn
            logger.exception("spool reconcile failed for topic %s", topic_id)

    async def _model_kwargs(
        self,
        project_id: uuid.UUID,
        provider_name: str,
        topic_id: uuid.UUID | None = None,
    ) -> tuple[dict, str]:
        """Per-turn overrides for the agent call, resolved from project.settings:
        the ExecutionProfile → model+env (design §2), and the sandbox image (spec
        §9.1 environment — a project can run on cheesex-dev for dogfooding). model
        is skipped when no registry is configured (the agent uses its default); the
        image is resolved regardless (it's independent of the AI profile).

        Also returns the turn's supply ROUTE — where its model traffic actually
        goes, which names the ONE authoritative meter (issue #218):

          "gateway"      LiteLLM, directly or via /llm from a machine; metered by
                         the gateway spend log, never by provider-reported
                         numbers (double count).
          "subscription" the metering proxy; metered by its usage log.
          "native"       profile-pinned credentials; the SDK's own usage report
                         is all there is.

        The route is a fact about where the PROVIDER actually sends the turn's
        traffic. On a subscription deployment the device provider builds the
        same metering-proxy env the tmux provider does (#325 G2: the device never
        holds a credential), so its route is "subscription"
        and its spend is metered by the proxy's usage log. Only WITHOUT the
        subscription does a device turn ride /llm → gateway. Labeling device
        turns "subscription" while their traffic went through /llm was a real
        bug once — the label must follow the traffic, in both directions.

        The model a turn runs on is the AGENT's before it is the project's: an
        agent whose type names a model runs on that model in every room it
        works in, which is the whole of "the model follows the agent". A type
        that names none declines to choose, and the project's pick still
        applies — so the override is `agent or project`, never a blank winning."""
        agent_model: str | None = None
        async with self._sessions() as session:
            project = await ProjectRepository(session).get(project_id)
            if topic_id is not None and project is not None:
                topic = await TopicRepository(session).get(topic_id)
                if topic is not None:
                    agents = AgentInstanceService(session)
                    agent_model = await agents.model(
                        await agents.for_topic(topic, project)
                    )
        kwargs: dict = {}
        image = (project.settings or {}).get("sandbox_image") if project else None
        if image:
            kwargs["sandbox_image"] = image
        if provider_name == "device":
            # A machine's model env is the device provider's own affair — handing
            # it this box's profile env would put a box-local URL and a raw
            # provider key on hardware the platform does not control. Under the
            # subscription the provider builds the metering-proxy env itself;
            # only the project's model pick travels from here, as the --model
            # alias ("" = the subscription's default, no flag). Without the
            # subscription it gets the backend's /llm route + its scoped token,
            # and the backend swaps in the project's virtual key per request
            # (routes/llm_proxy).
            if settings.subscription_enabled:
                choice = agent_model or (
                    (project.settings or {}).get("subscription_model")
                    if project
                    else None
                )
                kwargs["model"] = subscription_model_alias(choice)
                return kwargs, "subscription"
            return kwargs, "gateway"
        if settings.subscription_enabled and provider_name == "tmux-hooks":
            # The subscription path doesn't route through the gateway or a
            # profile: the tmux provider points Claude Code at the metering proxy
            # and the model is the project's own pick (Sonnet 5 by default, Opus 5
            # opt-in). Pass the --model alias ("" = default, no flag); the sandbox
            # env is set by the provider, not a profile. Only tmux implements
            # that env — the sdk provider under this flag used to fall through
            # with no env at all and run on whatever the backend process itself
            # inherited.
            choice = agent_model or (
                (project.settings or {}).get("subscription_model") if project else None
            )
            kwargs["model"] = subscription_model_alias(choice)
            return kwargs, "subscription"
        pool_route = True
        if self._profiles is not None:
            profile = self._profiles.resolve(
                project.settings if project else None,
                project.owner_handle if project else None,
            )
            kwargs["model"] = profile.model
            kwargs["env"] = profile.full_env()
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
                    if drained is None or drained[0] + drained[1] <= 0:
                        # LiteLLM writes spend logs asynchronously — at turn end
                        # the rows often lag by a few seconds (verified live).
                        # One bounded settle-retry keeps per-turn attribution;
                        # anything still missing lands in the NEXT drain
                        # (cumulative deltas are exactly-once either way).
                        await asyncio.sleep(3.0)
                        drained = await drain_new_usage(self._gateway, key, ckpt)
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

    async def _stream_with_retry(self, provider, **kwargs):
        """Run a streaming turn via the compute provider, retrying transient
        failures with backoff — but ONLY before any output is produced (cold-start
        / SDK exit-1 races). Two failure shapes are retried: exceptions, and a
        run whose RESULT is a transient provider error (structured
        api_error_status 408/429/5xx — e.g. a momentary overload). A rejected
        seat rate-limit is NOT transient (resets hours later) and surfaces
        immediately. If a turn fails mid-stream, re-raise / surface so the
        partial turn shows rather than replaying work."""
        delay = 0.5
        for attempt in range(3):
            produced = False
            retry_result = False
            try:
                async for event in provider.run_turn(**kwargs):
                    if (
                        isinstance(event, AgentResult)
                        and event.is_error
                        and not produced
                        and attempt < 2
                        and _transient_provider_error(event)
                    ):
                        retry_result = True
                        break  # swallow the error result and re-run the turn
                    produced = True
                    yield event
                if not retry_result:
                    return
                # Provider-side hiccup: back off a little longer than the
                # cold-start schedule before asking again.
                await asyncio.sleep(max(delay, 2.0))
            except Exception:  # noqa: BLE001 — transient sandbox/model errors
                if produced or attempt == 2:
                    raise
                await asyncio.sleep(delay)
            delay *= 2

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
    ) -> AsyncIterator[dict]:
        """Run the AGENT part of a turn (the human block was already posted by
        post_user_message), yielding WS frames as JSON-ready dicts. Runs under
        the per-topic lock; the prompt is built from history at lock time so a
        queued turn picks up every message posted while it waited."""
        # --- tx1: load topic + history, load memory ---
        async with self._sessions() as session:
            topics = TopicRepository(session)
            blocks = BlockRepository(session)
            memory = memory_store(session)

            topic = await topics.get(topic_id)
            if topic is None:
                raise NotFoundError("Topic not found")

            # Speaker-labelled prompt covering every human message 芝士 hasn't
            # been handed yet — so messages posted without @芝士 are still seen on
            # the next summon (spec §7.1 所有消息 AI 都会收到), each tagged with
            # who said it so 芝士 can tell people apart in a group topic (§8.4).
            history = await blocks.list_for_topic(topic_id)
            pending = _pending_human_blocks(history)
            pending_ids = [b.id for b in pending]
            if not pending and user_block_id is not None:
                # 有人召唤，但他那条消息已经被前一轮读进 prompt 了（两个人几乎同时
                # @，第一轮在锁上把两条合并答掉）。再跑一轮就是白烧一轮算力，还会
                # 走下面的 platform_prompt 兜底、把已经答过的话当成平台指令重投一
                # 遍。这里直接收工 —— 只是不跑这一轮，不碰任何排队/锁的逻辑。
                yield {"type": "done"}
                return
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
            private_owner = topic.private_owner
            acting_agent = await self._agent_handle(session, topic.id)
            doc_root = None if is_private else await blocks.doc_root(topic.id)
            doc_text = doc_root.content if doc_root else None
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
            projects_repo = ProjectRepository(session)
            project = await projects_repo.get(topic.project_id)
            # The persona comes from the AGENT working here, via its type — the
            # room's own agent if it has one, else the project's default.
            agents = AgentInstanceService(session)
            agent = (
                await agents.for_topic(topic, project)
                if project is not None
                else IMPLICIT_DEFAULT
            )
            role = await agents.system_prompt(agent)
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
            # Who this topic's commits are authored by. Refreshed every turn
            # rather than once at creation: people connect GitHub after their
            # first topic, and topics that predate this have no record at all.
            # Best-effort by construction — see workspace/identity.py.
            if not is_private:
                await ws_identity.sync_for_topic(session, topic)
            # This agent's thread here, not the room's: a room may host several
            # and each resumes its own (agent_session/models.py).
            resume_session_id = await AgentSessionService(session).resume_token(
                topic_id, agent.handle
            )
            untitled = not is_private and topic.title == PLACEHOLDER_TITLE
            # 进度层 (#187): the checklist the last turn left behind. Read inside
            # tx1 with everything else the prompt is built from, so no extra
            # round trip; empty list when this topic has never had one.
            progress_row = await TopicProgressRepository(session).get(topic_id)
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
                    kind=topic.kind,
                    status=topic.status,
                    card_statuses=[c.status for c in open_cards],
                )
            )
            # Which compute this topic runs on (v4): topic → project sticky → team.
            compute_id = _resolve_compute_id(
                project.settings if project else None,
                topic.compute_profile,
                await _team_compute_profile(session, project),
            )
            provider = self._compute.select(provider_id=compute_id)
            if isinstance(provider, CloudProvider):
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
                                "event_type": "cloud_provisioning",
                                "state": "waiting",
                            },
                        )
                        waiting_payload = _block_payload(
                            BlockOut.model_validate(waiting_block)
                        )
                    await session.commit()
                    if waiting_payload is not None:
                        yield {"type": "event_block", "block": waiting_payload}
                    yield {"type": "waiting", "state": "cloud_provisioning"}
                    yield {"type": "done"}
                    return
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
            prompt_text = "\n".join(
                _prompt_line(b, embeds_images=getattr(provider, "embeds_images", True))
                for b in pending
            ) or platform_prompt(content)
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
            # turn 活跃度检测: the hooks-driven backends (LOCAL tmux + remote
            # device) run hooks_substrate's two-layer idle-suspect + hard-ceiling
            # loop and manage their own inner ceiling (which can be hours), so the
            # outer wall-clock wrap (runtime.py) must be told their REAL ceiling via
            # a `turn_ceiling` frame instead of killing them at the generic
            # `agent_turn_timeout_s`. The SDK / remote-cheesed backends have no such
            # signal and keep the generic default. Without this the device's own
            # two-layer fix is dead on arrival — the outer guard still kills at 900s.
            is_activity_aware_backend = isinstance(provider, HooksSessionProvider)
            if topic.compute_profile is None:
                # v4 affinity red line: materialize the effective target BEFORE
                # the first provider call. A later team-default/sticky change must
                # never move an existing work tree or resumable Claude session.
                topic.compute_profile = provider.name
                await session.commit()

        # --- streaming: no DB transaction held open ---
        if is_activity_aware_backend:
            # Tells AgentWorkRunner's outer wall-clock wrap (runtime.py) to reschedule
            # to this backend's real ceiling instead of the generic
            # `agent_turn_timeout_s` — the ONLY frame kind that does so, and only
            # emitted here, so every other backend's outer-wrap behaviour is
            # untouched (turn 活跃度检测).
            yield {"type": "turn_ceiling", "seconds": provider.hard_ceiling_s}
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
                budget_s=settings.agent_turn_timeout_s,
                activity_aware=is_activity_aware_backend,
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
        final_text = ""
        new_session_id = resume_session_id
        result_error = False
        last_assistant_text: str | None = None
        last_assistant_block_id: str | None = None
        api_error_status: int | None = None
        rate_limit: dict | None = None

        # Compute: a provider owns the per-topic sandbox + execution (spec §9.1).
        # In a private chat, `cheese remember` targets the owner's personal memory
        # (spec §8.4). The provider runs a plain model turn when no Docker (tests).
        model_kwargs, route = await self._model_kwargs(
            project_id, provider.name, topic_id
        )
        if isinstance(provider, HooksSessionProvider):
            # Internal: the screen subscription, not this request, owns timeout
            # and thinking lifecycle. Runtime consumes this frame and disables
            # its request-scoped lifecycle before provider setup begins.
            yield {"type": "session_lifecycle"}

        # 重放可见 (#416): say out loud that this turn is re-sending a batch that
        # earlier turns already failed on. Posted BEFORE the stream, because the
        # whole point is that this turn may produce nothing either — a notice
        # written afterwards is exactly the one that never gets written.
        if replay_notice is not None:
            payload = await self.post_system_event(topic_id, replay_notice, turn_id)
            if payload is not None:
                yield {"type": "event_block", "block": payload}

        # Backfill any 现场 events the live hook path missed (backend down / no
        # listener during a prior turn) from the durable spool WAL — idempotent by
        # event-id. No-op for the sdk backend and an empty spool.
        async for frame in self._reconcile_spool(project_id, topic_id, turn_id):
            yield frame

        # Starts EMPTY even when prior_progress is non-empty: _apply_task_event
        # numbers items by position, and the agent's own Task tool numbering
        # restarts from 1 on a fresh session — seeding the list would make the
        # turn's first TaskUpdate("1") land on a leftover item from last time.
        # The old checklist reaches the agent through the prompt instead, and
        # reaches the UI through the restored frame just below.
        todo: list[dict] = []
        if prior_progress:
            yield {"type": "todo", "items": prior_progress, "restored": True}
        # Baseline for 「这一轮改了哪些文件」, started BEFORE 芝士 can write anything
        # but deliberately NOT awaited here: git_log ensures the repo exists, and
        # on a cold project that is a git init plus a jj colocate. Awaited in
        # front of the provider, that delay is charged to the start of every
        # turn, and a turn cancelled inside the window dies before it can store
        # its session id. What it measures only becomes commits at the
        # checkpoint, so finishing the read any time before turn end is soon
        # enough. Both backends need it, and the hooks backend returns from this
        # function long before its turn ends — so it is started once here and
        # carried on the work state rather than read twice in two places.
        known_commits = asyncio.ensure_future(self._known_commits(project_id, topic_id))
        if isinstance(provider, HooksSessionProvider):
            marked_work_ids: list[uuid.UUID] = []

            def _register_work(marked_work_id: uuid.UUID) -> None:
                marked_work_ids.append(marked_work_id)
                key = (topic_id, marked_work_id)
                state = self._hook_work.get(key)
                if state is None:
                    self._hook_work[key] = _HookWorkState(
                        project_id=project_id,
                        topic_id=topic_id,
                        work_id=marked_work_id,
                        provider=provider,
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

            ready = await provider.inject_work(
                project_id=project_id,
                topic_id=topic_id,
                prompt=prompt_text,
                system_prompt=system_prompt,
                resume_session_id=resume_session_id,
                memory_scope="personal" if is_private else None,
                owner=private_owner if is_private else None,
                work_id=turn_id,
                images=turn_images or None,
                on_mark=_register_work,
                **model_kwargs,
            )
            # Internal frame: `inject_work` returned, so the transport accepted
            # the write — which IS delivery (#563, per #487's contract that a
            # write either reaches the process or errors). The runtime records
            # that as a fact against the durable in-flight registry. Without it
            # the orphan sweep has to infer arrival after a restart, from
            # whether 芝士 happened to produce a block before the process died,
            # and so calls a prompt that landed two seconds earlier undelivered
            # and re-sends it. Nothing but the runtime acts on this, so it never
            # reaches the broker.
            yield {"type": "prompt_delivered"}
            if ready is False:
                marked_work_id = marked_work_ids[-1] if marked_work_ids else turn_id
                payload = await self.post_system_event(
                    topic_id,
                    "⏳ 机器上的会话正在启动，提示词已就位，输入框一出现就会自动发送。",
                    marked_work_id,
                )
                if payload is not None:
                    yield {"type": "event_block", "block": payload}
            return
        seen_eids: set[str] = set()  # dedup device-drainer re-deliveries this turn
        actions: list[str] = []  # cheese-action resources this turn (→ persisted cards)
        usage = None
        assistant_count = 0  # discrete 芝士 messages landed this turn
        try:
            async for event in self._stream_with_retry(
                provider,
                project_id=project_id,
                topic_id=topic_id,
                prompt=prompt_text,
                system_prompt=system_prompt,
                resume_session_id=resume_session_id,
                memory_scope="personal" if is_private else None,
                owner=private_owner if is_private else None,
                turn_id=turn_id,
                images=turn_images or None,
                **model_kwargs,
            ):
                if isinstance(event, AgentSessionInfo):
                    new_session_id = event.session_id
                    # Commit the pointer NOW, not at turn end and not from the
                    # failure handlers below (④ 超时重跑, 2026-08-11).
                    #
                    # Those handlers only run if the process lives long enough
                    # to run them. `asyncio.shield` survives cancellation; it
                    # does not survive SIGKILL or the machine losing power. A
                    # turn killed that way left this agent with no session row,
                    # so the auto-resume found nothing to `--resume`, started a FRESH
                    # claude, and 芝士 came back with no memory of what it had
                    # already done — measured on this very topic: a 7-minute,
                    # 132-message turn whose entire transcript was orphaned on
                    # disk while `locked` still read false.
                    #
                    # SessionStart is the first hook of the turn, so writing it
                    # here shrinks the "effect happened, record didn't" window
                    # from a whole turn to the few ms before 芝士 can act at
                    # all. It cannot close the window — that is what the
                    # idempotency keys are for — but a window nothing can
                    # happen inside is as narrow as this half gets.
                    await self._save_session_pointer(topic_id, new_session_id)
                elif isinstance(event, AgentMessage):
                    # Slack-style discrete message: one completed provider
                    # message = one chat block, persisted + broadcast NOW
                    # (mid-turn), not at turn end. The Claude SDK adapter
                    # coalesces its partial AssistantMessages before they reach
                    # this provider-neutral boundary.
                    last_assistant_text = event.text
                    payload = await self._persist_assistant_message(
                        project_id=project_id,
                        topic_id=topic_id,
                        text=event.text,
                        turn_id=turn_id,
                        # The first message threads under the summoning message;
                        # follow-ups stand alone (Slack-style consecutive sends).
                        reply_to=user_block_id if assistant_count == 0 else None,
                        roster=roster,
                        topic_refs=topic_refs,
                        eid=event.eid,
                        eids=event.eids,
                        continuation_id=continuation_id,
                    )
                    if payload is None:
                        # An earlier attempt at this same work already said this
                        # — 已说过的话不再说第二遍 (④). `last_assistant_text` is
                        # deliberately still updated above: the turn's final
                        # text is about what 芝士 said, not about who wrote the
                        # row.
                        continue
                    last_assistant_block_id = (
                        payload.get("block", {}).get("id")
                        if isinstance(payload, dict)
                        else None
                    ) or last_assistant_block_id
                    assistant_count += 1
                    yield {"type": "assistant_block", "block": payload}
                elif isinstance(event, AgentToolUse):
                    # Skip a duplicate delivery (the device drainer re-sends after a
                    # lost ack) — idempotent by the forwarder's event-id, per turn.
                    if event.eid and event.eid in seen_eids:
                        continue
                    if event.eid:
                        seen_eids.add(event.eid)
                    name = event.name.replace("mcp__cheese__", "")
                    args = event.input or {}
                    # Task tools → live working-log todo (process, not 现场).
                    if name in _TASK_TOOLS:
                        if _apply_task_event(todo, name, args):
                            # 进度层: commit the moment it changes, exactly like
                            # 现场 events below — a turn killed mid-flight (the
                            # machine died, the wall clock ran out) must leave
                            # the checklist behind, which is the entire point.
                            await self._persist_progress(topic_id, todo, turn_id)
                            yield {
                                "type": "todo",
                                "items": [dict(t) for t in todo],
                            }
                        continue
                    # Platform vs plain work, decided on the RAW name (prefix
                    # rule) + full command string — before any truncation.
                    platform = _is_platform_tool(event.name, args)
                    # Persist each 施工现场 event the MOMENT it streams in (not
                    # batched to turn-end tx2) so a mid-turn restart/crash never
                    # loses the timeline already produced (durability).
                    await self._persist_tool_event(
                        project_id=project_id,
                        topic_id=topic_id,
                        name=name,
                        tool_input=args,
                        platform=platform,
                        turn_id=turn_id,
                        eid=event.eid,
                    )
                    yield {"type": "tool", "name": event.name, "input": args}
                    # cheese <sub> ran as Bash → tell the UI which panel changed,
                    # so it refreshes mid-turn (doc/decisions/...), quietly.
                    if name == "Bash":
                        resource = _cheese_resource(str(args.get("command", "")))
                        if resource:
                            yield {"type": "state", "resource": resource}
                            if resource in _ACTION_LABEL and resource not in actions:
                                actions.append(resource)
                elif isinstance(event, AgentToolResult):
                    # 分身回吐: same dedup contract as a tool call — the device
                    # drainer re-sends after a lost ack.
                    if event.eid and event.eid in seen_eids:
                        continue
                    if event.eid:
                        seen_eids.add(event.eid)
                    payload = await self._persist_subagent_result(
                        project_id=project_id,
                        topic_id=topic_id,
                        event=event,
                        turn_id=turn_id,
                    )
                    if payload is not None:
                        yield {"type": "event_block", "block": payload}
                elif isinstance(event, AgentResult):
                    final_text = event.text
                    new_session_id = event.session_id
                    usage = event.usage
                    result_error = event.is_error
                    api_error_status = event.api_error_status
                    rate_limit = event.rate_limit
        except BaseException:
            # The turn died mid-stream (error, timeout-cancel, crash). Persist
            # the session pointer FIRST — the partial work lives in that session
            # file, and "再 @ 一次接着做" is only true if the next turn RESUMES
            # it (resume, not replay — replaying repeats side effects).
            if new_session_id and new_session_id != resume_session_id:
                await asyncio.shield(
                    self._save_session_pointer(topic_id, new_session_id)
                )
            raise

        # An all-zero usage report is "unknown", not "free": interactive Claude
        # Code (the hooks backends) reports no usage, and its Stop hook payload
        # decodes to zeros. Recording that as a metered zero-token turn is a lie
        # the table then repeats — normalize to None so the row says unmetered.
        if usage is not None and not (
            usage.input_tokens or usage.output_tokens or usage.cost_usd
        ):
            usage = None

        # L1 (docs/llm-gateway.md): for gateway-routed turns the gateway spend
        # log is the SOLE metering source — hooks backends report no usage at
        # all, and provider-reported numbers for the same tokens would double-
        # bill on the next drain (daily cumulative deltas are exactly-once).
        # Subscription turns are metered by the proxy's own log (ingested
        # separately); native turns keep the SDK's report.
        if self._gateway is not None and route == "gateway":
            usage = await self._drain_gateway_usage(project_id)
            if usage is None:
                # LiteLLM batch-writes spend logs (~10s); the settle retry can
                # still miss. Don't hold the turn hostage — a deferred drain
                # lands the usage row shortly after; if even that misses, the
                # next turn's cumulative drain includes it (exactly-once).
                self._schedule_deferred_drain(project_id, topic_id, turn_id)

        if result_error:
            # Provider/infra failure surfaced as the run's result. NEVER
            # ventriloquize it as 芝士's message — it goes into the 现场 as a
            # system event, platform-worded from STRUCTURED fields (rate-limit
            # resets_at → 北京时间; api_error_status → HTTP code), with the raw
            # provider detail quoted for the record.
            logger.warning(
                "turn %s provider error topic=%s rate_limit=%s api_status=%s",
                turn_id,
                topic_id,
                rate_limit,
                api_error_status,
            )
            detail = final_text.strip()
            platform_failure = classify_platform_failure(detail)
            # The CLI sometimes emits the SAME error string as a final
            # AssistantMessage before the error result — the discrete-message
            # path already persisted it as 芝士's reply. Exact-equality match
            # against the result text identifies that echo; retract it so the
            # error lives ONLY in the system event below.
            if (
                last_assistant_block_id
                and last_assistant_text is not None
                and last_assistant_text.strip() == detail
            ):
                try:
                    async with self._sessions() as session:
                        blocks_repo = BlockRepository(session)
                        blk = await blocks_repo.get(uuid.UUID(last_assistant_block_id))
                        if blk is not None:
                            await session.delete(blk)
                            await session.commit()
                    yield {"type": "retract_block", "block_id": last_assistant_block_id}
                except Exception:  # noqa: BLE001 — retraction is best-effort
                    logger.exception("error-echo retraction failed")
            resume_after_s: float | None = None
            resume_hint_reason = "座位额度已恢复，继续之前的任务"
            swap = NO_SWAP
            fail_meta: dict | None = None
            fail_code: str | None = None
            # 平台提示统一契约 (未分类的那几条): `fail_text` 是房间里那一行，解释性
            # 的话和**服务原话**进 `meta.detail`。以前原话是拼进 `fail_text` 的
            # (`（服务原话：…）`)，于是一条朴素系统行动辄七八行 —— 而且只有
            # `classify_platform_failure()` 命中时才有 meta，最常见的三条（座位
            # 限流 / 余额用尽 / HTTP 错误）恰好都不命中，一个结构化字段都没有。
            fail_hint = ""
            fail_who = WHO_HUMAN
            if platform_failure is not None:
                fail_text = platform_failure.content
                fail_meta = platform_failure.meta
                fail_code = platform_failure.code
                if platform_failure.host_scoped:
                    # Blame the machine, not the turn (#186): count this against
                    # the box and, once it has failed once too often, move the
                    # topic to a healthy one and schedule the continuation. The
                    # platform_failure branch otherwise leaves resume_after_s
                    # unset — correct only while there was nowhere else to go.
                    swap = await handle_host_failure(
                        topic_id=topic_id,
                        project_id=project_id,
                        failure=platform_failure,
                        session_factory=self._sessions,
                        replace_cloud_machine=self._replace_cloud_machine,
                    )
                    if swap.resume_after_s is not None and not is_resume:
                        resume_after_s = swap.resume_after_s
                        resume_hint_reason = swap.resume_reason or resume_hint_reason
            elif (
                rate_limit
                and rate_limit.get("status") == "rejected"
                and rate_limit.get("resets_at")
            ):
                resets = datetime.fromtimestamp(
                    rate_limit["resets_at"], tz=ZoneInfo("Asia/Shanghai")
                )
                recover = "恢复后我会自动接着跑" if not is_resume else "到点再 @ 它"
                fail_text = (
                    f"⚠️ 芝士的 AI 座位额度用完了，北京时间 "
                    f"{resets:%m-%d %H:%M} 恢复，{recover}。"
                )
                if not is_resume:
                    # Resume ~2min after the window opens (clock skew buffer).
                    wait_s = rate_limit["resets_at"] - datetime.now(UTC).timestamp()
                    resume_after_s = max(60.0, wait_s + 120.0)
                    # 平台自己会到点接着跑，没人需要动手。
                    fail_who = WHO_PLATFORM
            elif _is_out_of_credit(detail):
                # A spent balance is not a wait — no amount of retrying refills
                # it, and telling someone to try again later sends them into a
                # loop that cannot succeed. Say what actually has to happen.
                fail_text = "⚠️ 芝士这轮没跑完——AI 中继余额用尽，重试无效，要人充值。"
                fail_hint = (
                    "这不是等一等就能好的，需要有人充值或把机器切到其他 AI 供给；"
                    "重试无效。"
                )
            elif api_error_status:
                fail_text = (
                    f"⚠️ 芝士这轮没跑完——AI 接口错误（HTTP {api_error_status}）。"
                )
                fail_hint = "稍后再 @ 它重试。"
            else:
                # #450 rule 2: an unclassified failure shows the SERVICE'S OWN
                # WORDS in the room line, not a generic label — 「AI 服务返回
                # 错误」 with the reason buried in meta.detail is what sent a
                # whole room hunting a \"mystery bug\" twice in one night
                # (2026-08-16, topic ee17b136: the real text was the delivery
                # timeout all along). One line's worth here; the untruncated
                # original still goes into detail below.
                first_line = detail.splitlines()[0].strip() if detail else ""
                if len(first_line) > 160:
                    first_line = first_line[:160] + "…"
                fail_text = (
                    f"⚠️ 芝士这轮没跑完——{first_line}"
                    if first_line
                    else "⚠️ 芝士这轮没跑完——AI 服务返回错误。"
                )
                fail_hint = "稍后再 @ 它重试。"
            if fail_meta is None:
                # 没被分类的那几条。原话是**唯一**的一份 —— 它没有第二个副本可以
                # 「去别处看」，所以只能原样收进 detail，不截、不摘要。
                fail_meta = notice(
                    EVENT_TURN_FAILED,
                    severity=SEVERITY_ERROR,
                    who=fail_who,
                    detail="\n\n".join(
                        part
                        for part in (
                            fail_hint,
                            f"服务原话：\n{detail}" if detail else "",
                        )
                        if part
                    )
                    or None,
                    detail_label="详细说明",
                )
            async with self._sessions() as session:
                blocks = BlockRepository(session)
                # 施工现场 events were persisted inline as they streamed
                # (durability) — this tx only records usage + the fail card.
                # Record the turn even when its tokens are unknowable (the hooks
                # backends run interactive Claude Code, which reports none). Skipping
                # it left the table empty while real credit drained.
                if usage is None:
                    await UsageRepository(session).add(
                        project_id=project_id,
                        topic_id=topic_id,
                        model=settings.agent_model,
                        input_tokens=0,
                        output_tokens=0,
                        cost_usd=0.0,
                        metered=False,
                        route=route,
                        turn_id=turn_id,
                    )
                if usage is not None:
                    await UsageRepository(session).add(
                        project_id=project_id,
                        topic_id=topic_id,
                        model=usage.model,
                        input_tokens=usage.input_tokens,
                        output_tokens=usage.output_tokens,
                        cost_usd=usage.cost_usd,
                        route=route,
                        turn_id=turn_id,
                    )
                    # Even a failed turn burned tokens: fold them into credits
                    # and deduct from the project's grants (spec §9.1).
                    await ComputeGrantRepository(session).consume(
                        project_id,
                        usage_to_credits(usage, spend_priced=route == "gateway"),
                    )
                fail_block = await blocks.add(
                    project_id=project_id,
                    topic_id=topic_id,
                    author="system",
                    author_type=AuthorType.system,
                    content=fail_text,
                    kind=BlockKind.event,
                    turn_id=turn_id,
                    meta=fail_meta,
                )
                fail_payload = _block_payload(BlockOut.model_validate(fail_block))
                swap_payload = None
                if swap.message:
                    # The move gets its OWN event, right after the failure card:
                    # a topic that changes machines must say so in the room, or
                    # it is the silent drift the pin exists to prevent.
                    swap_block = await blocks.add(
                        project_id=project_id,
                        topic_id=topic_id,
                        author="system",
                        author_type=AuthorType.system,
                        content=swap.message,
                        kind=BlockKind.event,
                        turn_id=turn_id,
                        meta={
                            **(
                                swap.event_meta
                                or {
                                    "event_type": "host_swap",
                                    "from_device": swap.old_device,
                                    "to_device": swap.new_device,
                                }
                            )
                        },
                    )
                    swap_payload = _block_payload(BlockOut.model_validate(swap_block))
                await session.commit()
            if new_session_id and new_session_id != resume_session_id:
                await self._save_session_pointer(topic_id, new_session_id)
            provider.checkpoint(project_id, topic_id)
            yield {"type": "event_block", "block": fail_payload}
            if swap_payload is not None:
                yield {"type": "event_block", "block": swap_payload}
            error_frame = {
                "type": "error",
                "message": fail_text,
                "persisted": True,
            }
            if fail_code is not None:
                error_frame["code"] = fail_code
            yield error_frame
            if resume_after_s is not None:
                # Internal frame: the runner schedules the auto-resume.
                yield {
                    "type": "resume_hint",
                    "after_s": resume_after_s,
                    "reason": resume_hint_reason,
                }
            yield {"type": "done"}
            return

        # --- tx2: persist tool events (施工现场) + usage + session pointer ---
        async with self._sessions() as session:
            topics = TopicRepository(session)
            blocks = BlockRepository(session)

            # 施工现场 events were persisted inline as they streamed (durability);
            # tx2 now only records usage, action cards, and the session pointer.
            # Record the turn even when its tokens are unknowable (the hooks
            # backends run interactive Claude Code, which reports none). Skipping
            # it left the table empty while real credit drained.
            if usage is None:
                await UsageRepository(session).add(
                    project_id=project_id,
                    topic_id=topic_id,
                    model=settings.agent_model,
                    input_tokens=0,
                    output_tokens=0,
                    cost_usd=0.0,
                    metered=False,
                    route=route,
                    turn_id=turn_id,
                )
            if usage is not None:
                await UsageRepository(session).add(
                    project_id=project_id,
                    topic_id=topic_id,
                    model=usage.model,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    cost_usd=usage.cost_usd,
                    route=route,
                    turn_id=turn_id,
                )
                # 用量扣减 (spec §9.1): fold this turn's tokens into credits and
                # deduct from the project's grants, oldest first. A project with
                # no grants (自治项目) deducts nothing — unlimited.
                await ComputeGrantRepository(session).consume(
                    project_id,
                    usage_to_credits(usage, spend_priced=route == "gateway"),
                )

            topic = await topics.get(topic_id)

            # Persistent, clickable action cards for the cheese actions this turn
            # (system events show in the conversation; refs tag the resource).
            action_payloads = []
            acting_agent = await self._agent_handle(session, topic_id)
            # Attribution and memory part ways here, deliberately: the block
            # author is this room's 分身 (who did it), while the pool belongs to
            # the agent working the room (whose memory it is).
            agent_pool = (
                await self._agent_memory_pool(session, topic)
                if topic is not None
                else None
            )
            for resource in actions:
                blk = await blocks.add(
                    project_id=project_id,
                    topic_id=topic_id,
                    author=acting_agent,
                    author_type=AuthorType.system,
                    content=f"芝士 {_ACTION_LABEL[resource]}",
                    kind=BlockKind.event,
                    # Same structured shape as every shared event (meta.action)
                    # — no cheese-private encoding (refs=action:* is legacy,
                    # still rendered for old blocks).
                    turn_id=turn_id,
                    meta={"platform": True, "action": resource},
                )
                action_payloads.append(_block_payload(BlockOut.model_validate(blk)))

            if topic is not None and new_session_id:
                await AgentSessionService(session).remember(
                    topic_id=topic_id,
                    agent_handle=agent.handle,
                    resume_token=new_session_id,
                )

            # 这一轮真的把这些消息交给 agent 跑完了 —— 现在才盖 consumed 戳，下一轮
            # 的窗口从这里往后开。**故意放在这里而不是建 prompt 的 tx1**：轮次崩了 /
            # 超时 / provider 报错的路径都在上面 return 或 raise 掉了，戳没盖上，消息
            # 就留在 pending 里由续跑轮重发。宁可重复，不可丢失 —— 重复看得见，丢失
            # 看不见，而后者正是这次要修的 bug。
            # Mid-turn messages are stamped on their exact lower-layer receipt;
            # this end-of-turn path owns only the batch built into this prompt.
            await blocks.mark_consumed(pending_ids, turn_id)
            await session.commit()

        # A hooks backend can reach here with assistant_count == 0 not because
        # no message was ever shown, but because its MessageDisplay hook lost
        # the race with the turn-ending Stop hook over the network and is only
        # NOW landing in the spool (the container writes it to disk before the
        # live POST even goes out — same root cause as the turn-boundary gap
        # this whole reconcile mechanism exists for). Sweep the spool once more
        # right now — with a real eid, broadcast like any other backfilled
        # block — so the fallback below never re-persists that same text
        # eid-less (which is exactly what left a duplicate: an eid-less block
        # from here, and its eid+backfilled twin from a LATER turn's reconcile
        # that couldn't recognize the two as the same event).
        reconciled_texts: set[str] = set()
        async for frame in self._reconcile_spool(project_id, topic_id, turn_id):
            if frame["type"] == "assistant_block":
                reconciled_texts.add(frame["block"]["content"].strip())
            yield frame

        # Fallback single message: 芝士's messages normally landed one-by-one at
        # each AgentMessage boundary above. A provider that never announced a
        # boundary (plain non-SDK stub turn, an older remote cheesed node) still
        # lands its reply from the final result text — unless the sweep above
        # just landed that exact text from the spool (with a proper eid).
        # Stored content is mention-expanded and the result text is raw, so the
        # comparison happens on the expanded, stripped form of both — an "@名字"
        # or a trailing newline must not defeat the dedup (it did: that exact
        # miss left an eid-less block next to its eid+backfilled twin).
        assistant_payload: dict | None = None
        fallback_text = _expand_mention_names(final_text, roster, topic_refs).strip()
        if (
            assistant_count == 0
            and fallback_text
            and fallback_text not in reconciled_texts
        ):
            assistant_payload = await self._persist_assistant_message(
                project_id=project_id,
                topic_id=topic_id,
                text=final_text,
                turn_id=turn_id,
                reply_to=user_block_id,
                roster=roster,
                topic_refs=topic_refs,
                continuation_id=continuation_id,
            )

        # Snapshot whatever the agent changed in its worktree this turn (native
        # edits → version history). The provider owns this workspace lifecycle
        # step (R2/R9); it's best-effort and never fails the turn.
        provider.checkpoint(project_id, topic_id)

        # 一轮的改动汇总: read AFTER the checkpoint, because the checkpoint is what
        # made this turn's edits into a commit.
        changeset = await self._turn_changeset(
            project_id, topic_id, await known_commits
        )
        change_payload = (
            None
            if changeset is None
            else await self._persist_change_summary(
                project_id=project_id,
                topic_id=topic_id,
                turn_id=turn_id,
                changeset=changeset,
            )
        )

        # 知识沉淀是副产品 (spec §8.4): hand the finished exchange to OpenViking
        # for background memory extraction. The extractor's LLM decides what is
        # memory-worthy (规则4) — fire-and-forget, never delays/fails the turn.
        if not result_error:
            self._schedule_memory_extraction(
                topic_id=topic_id,
                project_id=project_id,
                is_private=is_private,
                private_owner=private_owner,
                agent_pool=agent_pool,
                user_text=prompt_text,
                assistant_text=final_text,
            )

        if assistant_payload is not None:
            yield {"type": "assistant_block", "block": assistant_payload}
        for payload in action_payloads:
            yield {"type": "event_block", "block": payload}
        if change_payload is not None:
            yield {"type": "event_block", "block": change_payload}
        yield {"type": "done"}

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
            # What 芝士 learns in a project is its own, the way a teammate's is.
            # Never the shared pool: two agents in one project would dilute each
            # other's memory, which is the case this split exists for.
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
                team_compute_profile=await _team_compute_profile(session, project),
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
        provider = self._compute.select(provider_id=compute_id)
        final_text = ""
        new_session_id = None
        tools_used: list[str] = []
        async for event in provider.run_turn(
            project_id=project_id,
            topic_id=topic_id,
            prompt=prompt,
            system_prompt=system_prompt,
            resume_session_id=None,
            **(await self._model_kwargs(project_id, provider.name, topic_id))[0],
        ):
            if isinstance(event, AgentToolUse):
                tools_used.append(event.name)
            elif isinstance(event, AgentResult):
                final_text = event.text
                new_session_id = event.session_id

        # --- tx2: persist 芝士's summary + session ---
        async with self._sessions() as session:
            topics = TopicRepository(session)
            blocks = BlockRepository(session)
            await blocks.add(
                project_id=project_id,
                topic_id=topic_id,
                author=await self._agent_handle(session, topic_id),
                author_type=AuthorType.ai,
                content=final_text,
                kind=BlockKind.message,
            )
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
                team_compute_profile=await _team_compute_profile(session, project),
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
        provider = self._compute.select(provider_id=compute_id)
        final_text = ""
        tools_used: list[str] = []
        async for event in provider.run_turn(
            project_id=project_id,
            topic_id=root_topic_id,
            prompt=prompt,
            system_prompt=system_prompt,
            resume_session_id=None,
            **(await self._model_kwargs(project_id, provider.name, root_topic_id))[0],
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
            role = await agents.system_prompt(await agents.for_project(project))
            compute_id = _resolve_compute_id(
                project.settings,
                team_compute_profile=await _team_compute_profile(session, project),
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
            load_skills(["conversation-style"]),
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
        provider = self._compute.select(provider_id=compute_id)
        final_text = ""
        async for event in provider.run_turn(
            project_id=project_id,
            topic_id=project.root_topic_id,
            prompt=prompt,
            system_prompt=system_prompt,
            resume_session_id=None,
            **(
                await self._model_kwargs(
                    project_id, provider.name, project.root_topic_id
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
