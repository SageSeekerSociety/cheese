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
import uuid
from collections import Counter
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.errors import GatewayUnavailableError, NotFoundError
from app.core.text import markdown_preview
from app.domain.agent import event_spool
from app.domain.agent.compute import ComputePool
from app.domain.agent.gateway import LlmGateway, drain_new_usage
from app.domain.agent.hook_events import translate_hook
from app.domain.agent.market import subscription_model_alias
from app.domain.agent.platform_failures import classify_platform_failure
from app.domain.agent.profiles import ProfileRegistry
from app.domain.agent.roles import resolve_role_description
from app.domain.agent.service import (
    AgentMessage,
    AgentResult,
    AgentService,
    AgentSessionInfo,
    AgentToolUse,
    AgentUsage,
)
from app.domain.agent.skills import DEFAULT_CHAT_SKILLS, load_scenario, load_skills
from app.domain.agent.stages import resolve_stage, stage_scenario
from app.domain.block.models import AuthorType, Block, BlockKind, consumed_turn
from app.domain.block.repositories import BlockRepository
from app.domain.block.schemas import BlockOut
from app.domain.cx_notification.models import NotifKind, NotifLevel
from app.domain.cx_notification.services import NotificationService
from app.domain.identity.handles import looks_like_agent_handle
from app.domain.memory.models import MemoryScope, agent_project_scope_id
from app.domain.memory.store import RecallResult, memory_store, recall_pools
from app.domain.mentions import expand_mention_names
from app.domain.milestone.repositories import MilestoneRepository
from app.domain.project.repositories import ProjectRepository
from app.domain.review.models import AcceptCard, AcceptStatus
from app.domain.review.repositories import AcceptCardRepository
from app.domain.team.repositories import TeamRepository
from app.domain.topic.models import Topic, TopicKind, TopicStatus
from app.domain.topic.repositories import TopicRepository
from app.domain.topic_membership.services import TopicMemberService
from app.domain.usage.credits import usage_to_credits
from app.domain.usage.repositories import ComputeGrantRepository, UsageRepository
from app.domain.workspace import service as ws

ACTIVITY_SKILLS = ["conversation-style", "activity-digestion", "doc-form"]
HEARTBEAT_SKILLS = ["heartbeat", "conversation-style"]
PRIVATE_SKILLS = ["private-chat", "conversation-style"]

CHEESE_AUTHOR = "cheese"

logger = logging.getLogger(__name__)

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
    "decision": "记录了一条决策",
    "topics": "更新了子话题",
    "milestone": "钉了一个里程碑",
    "accept": "递出了验收卡",
    "notify": "发了一条通知",
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


def _turn_meta_lines(
    *,
    budget_s: float,
    activity_aware: bool,
    is_resume: bool,
    disk: tuple[int, int] | None,
    open_cards: list[AcceptCard] | None,
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
            "## 当前话题的活文档（这是最新状态；用户可能编辑了它，"
            "请按它继续工作，并在状态变化时用 update_doc 工具更新它）\n" + doc
        )
    if memories or memories_omitted:
        facts = "\n".join(f"- {_chipify_paths(m)}" for m in memories)
        block = f"## 项目记忆（你已知道的事实，回答时可引用）\n{facts}"
        if memories_omitted:
            # 溢出必须可见: what does not fit is stated, never dropped in
            # silence. A reader who cannot tell "nothing was stored" from
            # "the oldest fell off the end" stops trusting memory entirely —
            # and stops asking for the part it can still get.
            block += (
                f"\n\n> ⚠️ 上面只是最近的 {len(memories)} 条，另有 **{memories_omitted} "
                "条更早的记忆没放进来**（放不下，不是不存在）。**没列出来 ≠ 不存在**——"
                '要用到早期约定/踩过的坑时，用 `cheese recall "<关键词>"` 现查；'
                "一次没查到也不等于没有，换个说法、用更短的词再试一次。"
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
    "「当前话题的活文档」里：拆分意图（或被升级的那段讨论）+ 父话题文档快照。"
    "现在开工：\n"
    "1. 先发开场白：一两句复述你理解的任务、说明打算怎么推进（给人纠偏的机会）；"
    "简报信息不足就明确列出缺什么、@ 拆分发起人补充。\n"
    "2. 把活文档改写成你自己的状态摘要（目标/约束/下一步），别留着简报原文不动。\n"
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
        "一个子话题刚回流了结论（原文如下，也已织进本话题活文档末尾）。"
        "请消化它：\n"
        "1. 把活文档整理成最新状态——结论的要点合并进对应章节，"
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


def _prompt_line(b) -> str:
    """One speaker-labelled prompt line per pending human block. An attachment
    block is a worktree image — embedded NATIVELY in this turn's user message
    (base64 image block, see service.build_query_input), so the line just says
    who sent it and where the file lives."""
    if b.kind == BlockKind.attachment:
        return (
            f"[{b.author}] 发来一张图片（图片内容已附在本条消息里；"
            f"它同时存在你工作目录的 {b.content}）"
        )
    return f"[{b.author}]: {_strip_platform_notice(b.content)}"


def _pending_human_blocks(history: list[Block]) -> list[Block]:
    """The human messages/attachments no agent turn has read into a prompt yet.

    轮次边界按**归属**划，不按位置划：一条在轮次运行中到达的人类消息，created_at
    排在那轮 AI 回复之前，所以"最后一条 AI 消息之后"这个窗口会把它切掉 —— 而且
    切掉就再也捡不回来了（那个下标只会往前走）。这里改成挑「没被任何一轮盖过
    consumed 戳」的块，戳由跑完的轮次自己盖上（BlockRepository.mark_consumed）。

    `history` 已按 created_at 升序。
    """
    watermark = -1  # 最后一个已被消费的人类块的位置
    for i, b in enumerate(history):
        if _is_human_input(b) and consumed_turn(b) is not None:
            watermark = i
    if watermark < 0:
        # 兼容老数据：戳是这次改动才有的，改动之前的块一个都没有。一个戳都没有
        # 的话题退回旧语义（最后一条 AI 消息之前的算已读），否则上线后每个老话题
        # 的第一轮都会把整段历史重放一遍。盖过一次戳之后就永远走上面那条线。
        for i, b in enumerate(history):
            if b.author_type == AuthorType.ai and b.kind == BlockKind.message:
                watermark = i
    return [b for b in history[watermark + 1 :] if _is_human_input(b)]


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
        # Per-project ExecutionProfile (model + provider). None → always the
        # agent's built-in default (tests / single-profile deploys).
        self._profiles = profiles
        # LiteLLM gateway ADMIN client (docs/llm-gateway.md L1/L2). None = off.
        # The lock serializes key-mint and usage-drain read-modify-writes on
        # project.settings (single-process reality, like the topic locks).
        self._gateway = gateway
        self._gateway_lock = asyncio.Lock()
        # Load the conversation skills once (spec §8.3 product "soul").
        self._skills = load_skills(DEFAULT_CHAT_SKILLS)
        # Per-topic serial queue (spec §9.1): one agent turn per topic at a
        # time, so concurrent messages to the same topic don't race.
        self._topic_locks: dict[uuid.UUID, asyncio.Lock] = {}
        # Strong refs to in-flight post-turn memory-extraction tasks (asyncio
        # only keeps weak refs; without this a pending commit could be GC'd).
        self._memory_tasks: set[asyncio.Task] = set()

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
    ) -> AsyncIterator[dict]:
        """Post the human message instantly, then (if summoned) run the agent
        turn serialized per topic (spec §9.1 串行队列). 现场必须实时: the human
        block persists + broadcasts BEFORE the lock, so a post never queues
        behind a running agent turn. turn_id groups this turn's blocks (R4);
        reply_to threads this message under another (B3); attachments are
        uploaded worktree images this message carries (图片输入)."""
        turn_id = turn_id or uuid.uuid4()
        if is_resume or nudge_event:
            # System-initiated turn (自动续跑 / 评论叫醒 / 冲突调度…): no human
            # spoke — the opener is a SYSTEM event in the 现场, and the
            # instruction goes straight to the agent as the prompt.
            if not nudge_event:
                why = resume_reason or "从上一轮的断点继续"
                nudge_event = f"⏯️ 自动续跑：{why}"
            payload = await self.post_system_event(topic_id, nudge_event, turn_id)
            if payload is None:
                raise NotFoundError("Topic not found")
            yield {"type": "event_block", "block": payload}
            user_block_id = None
        else:
            user_payloads, user_block_id = await self._post_user_message(
                topic_id,
                author=author,
                content=content,
                turn_id=turn_id,
                reply_to=reply_to,
                attachments=attachments,
            )
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
            ack = await self._ack_summon(user_block_id, topic_id)
            if ack is not None:
                yield {"type": "reaction", **ack}

        async with self._lock_for(topic_id):
            async for frame in self._converse_impl(
                topic_id=topic_id,
                content=content,
                turn_id=turn_id,
                user_block_id=user_block_id,
                is_resume=is_resume,
            ):
                yield frame

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
        async with self._lock_for(topic_id):
            async for frame in self._converse_impl(
                topic_id=topic_id,
                content=prompt or KICKOFF_PROMPT,
                turn_id=turn_id,
                user_block_id=None,
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

    async def turn_policy(self, topic_id: uuid.UUID) -> dict | None:
        """Admission facts the TurnRunner gates on BEFORE running a turn
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

    async def _save_session_pointer(self, topic_id: uuid.UUID, session_id: str) -> None:
        """Best-effort: point the topic at the (possibly partial) session so the
        next summon resumes it. Never raises — used on failure paths."""
        try:
            async with self._sessions() as session:
                topics = TopicRepository(session)
                topic = await topics.get(topic_id)
                if topic is not None:
                    await topics.set_session_id(topic, session_id)
                    await session.commit()
        except Exception:  # noqa: BLE001 — never mask the original failure
            logger.exception("failed to save session pointer for %s", topic_id)

    async def _post_user_message(
        self,
        topic_id: uuid.UUID,
        *,
        author: str,
        content: str,
        turn_id: uuid.UUID,
        reply_to: str | None,
        attachments: list[dict] | None = None,
    ) -> tuple[list[dict], uuid.UUID]:
        """Persist the human message (+ its image attachment blocks) and the
        @mention notifications in one short transaction, outside any turn lock.
        Returns (payloads, anchor_block_id) — the anchor is what 芝士's reply
        threads under (the text block, or the first attachment when image-only)."""
        async with self._sessions() as session:
            topics = TopicRepository(session)
            blocks = BlockRepository(session)
            topic = await topics.get(topic_id)
            if topic is None:
                raise NotFoundError("Topic not found")
            payloads: list[dict] = []
            anchor_id: uuid.UUID | None = None
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
                    reply_to=_parse_uuid(reply_to),  # B3: thread under another
                )
                # Resolve <@handle> mentions in the human message → strong notify.
                resolved, _unresolved = await self._notify_mentions(
                    session, topic, author, content, roster
                )
                refs = [f"user:{h}" for h in resolved] + _topic_refs(content)
                if refs:
                    user_block.refs = refs
                anchor_id = user_block.id
                payloads.append(_block_payload(BlockOut.model_validate(user_block)))
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
                    turn_id=turn_id,
                    # An image-only send still honors the reply thread (B3).
                    reply_to=None if content else _parse_uuid(reply_to),
                )
                if anchor_id is None:
                    anchor_id = att_block.id
                payloads.append(_block_payload(BlockOut.model_validate(att_block)))
            if anchor_id is None:  # guarded by the route, but never crash a turn
                raise NotFoundError("empty message")
            await session.commit()
        return payloads, anchor_id

    async def _ack_summon(
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

    async def _recall_agent_memories(
        self,
        memory,
        session: AsyncSession,
        *,
        project_id: uuid.UUID,
        agent_handle: str,
    ) -> RecallResult:
        """What this 芝士 remembers inside this project.

        Reads its own per-agent scope first, then the legacy shared ``project``
        pool. Writes only ever go to the per-agent scope, so the pool is a
        read-only tail of what was learned before memory was split per agent —
        rooms that accumulated it keep it, and nothing new lands there.

        Returns what did *not* fit alongside what did: both pools are capped,
        and a cap nobody is told about is how memory quietly stops existing.
        """
        return await recall_pools(
            memory,
            [
                (
                    MemoryScope.agent_project,
                    agent_project_scope_id(project_id, agent_handle),
                ),
                (MemoryScope.project, str(project_id)),
            ],
        )

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
        backfilled: bool = False,
    ) -> dict:
        """Persist ONE discrete 芝士 message (Slack-style): committed the moment
        the SDK reports the AssistantMessage complete, so a turn lands as
        several complete messages instead of one growing streamed bubble.
        Handles the same mention canonicalization / notify / refs as before.
        ``eid`` (hooks path) is stamped into meta so the spool reconcile can
        dedup a backfilled copy against this live one."""
        meta: dict | None = None
        if eid:
            meta = {"eid": eid}
        if backfilled:
            meta = {**(meta or {}), "backfilled": True}
        async with self._sessions() as session:
            blocks = BlockRepository(session)
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
    ) -> dict:
        """Persist ONE 施工现场 event the moment it streams in, not batched to the
        turn-end tx2. Mirrors _persist_assistant_message's commit-now contract so
        a mid-turn restart/crash never loses the 现场 timeline already produced.
        ``eid`` (the hook forwarder's event id) is stamped into meta so the durable
        spool reconcile can dedup a backfilled copy against this live one. Returns
        the persisted block payload so a caller (live path or spool reconcile) can
        broadcast it as a WS frame."""
        meta = _tool_event_meta(name, tool_input, platform=platform)
        if eid:
            meta = {**meta, "eid": eid}
        if backfilled:
            meta = {**meta, "backfilled": True}
        async with self._sessions() as session:
            block = await BlockRepository(session).add(
                project_id=project_id,
                topic_id=topic_id,
                author=await self._agent_handle(session, topic_id),
                author_type=AuthorType.ai,
                content=_format_tool_event(name, tool_input),
                kind=BlockKind.event,
                turn_id=turn_id,
                meta=meta,
            )
            payload = _block_payload(BlockOut.model_validate(block))
            await session.commit()
        return payload

    async def _reconcile_spool(
        self, project_id: uuid.UUID, topic_id: uuid.UUID, turn_id: uuid.UUID | None
    ) -> AsyncIterator[dict]:
        """Backfill 现场 events the live hook path missed (backend down / no listener
        during a prior turn) from the durable spool WAL — idempotent by event-id.
        No-op for the sdk backend (no spool dir) and an empty spool. Best-effort: a
        reconcile failure never blocks the turn.

        Scope: 现场 tool events AND 芝士 chat messages (MessageDisplay) — both are
        idempotent by event-id, so a copy the live path already persisted is
        skipped. Backfilled messages skip mention-notify (the moment passed), but
        they DO yield a WS frame like the live path — a hook that missed its turn's
        listening window must still reach the frontend, just without threading or
        an @-notify (bug: it was landing as a silent DB row nobody saw)."""
        try:
            entries = event_spool.spool_entries(ws.spool_dir(project_id, topic_id))
            if not entries:
                return
            # Dedup against everything the live path already persisted (this +
            # prior turns): event-ids stamped on this topic's blocks (any kind).
            async with self._sessions() as session:
                blocks = await BlockRepository(session).list_for_topic(topic_id)
            seen = {
                b.meta["eid"]
                for b in blocks
                if isinstance(b.meta, dict) and isinstance(b.meta.get("eid"), str)
            }
            recovered = 0
            for _path, eid, payload in entries:
                if payload is None or eid in seen:
                    continue
                payload["_eid"] = eid
                event = translate_hook(payload)
                if isinstance(event, AgentMessage):
                    # A chat message whose live delivery was lost — land it as
                    # history (no reply threading, no notify: the moment passed)
                    # but still broadcast it, exactly like the live path does.
                    block_payload = await self._persist_assistant_message(
                        project_id=project_id,
                        topic_id=topic_id,
                        text=event.text,
                        turn_id=turn_id,
                        reply_to=None,
                        roster=None,
                        topic_refs=[],
                        eid=eid,
                        backfilled=True,
                    )
                    seen.add(eid)
                    recovered += 1
                    yield {"type": "assistant_block", "block": block_payload}
                    continue
                if not isinstance(event, AgentToolUse):
                    continue  # SessionStart/Stop have no historical counterpart
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
                recovered += 1
                yield {"type": "event_block", "block": block_payload}
            event_spool.remove(path for path, _eid, _payload in entries)
            if recovered:
                logger.info(
                    "reconciled %d spooled 现场 event(s) for topic %s",
                    recovered,
                    topic_id,
                )
        except Exception:  # noqa: BLE001 — reconcile is best-effort, never fail a turn
            logger.exception("spool reconcile failed for topic %s", topic_id)

    async def _model_kwargs(
        self, project_id: uuid.UUID, provider_name: str
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

        The route is a fact about the PROVIDER executing the turn, not about the
        deployment. `subscription_enabled` used to force every turn onto the
        subscription branch, which mis-labeled device turns — their traffic goes
        through /llm → gateway regardless — so their spend sat in the gateway
        log and was never drained into the books."""
        async with self._sessions() as session:
            project = await ProjectRepository(session).get(project_id)
        kwargs: dict = {}
        image = (project.settings or {}).get("sandbox_image") if project else None
        if image:
            kwargs["sandbox_image"] = image
        if provider_name == "device":
            # A machine's credentials are the device provider's own affair: it
            # gets the backend's /llm route + its scoped token, and the backend
            # swaps in the project's virtual key per request (routes/llm_proxy).
            # Handing it this box's profile env would put a box-local URL and a
            # raw provider key on hardware the platform does not control.
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
            choice = (
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
            notifs = NotificationService(session)
            preview = markdown_preview(text, 200)
            who = "芝士" if looks_like_agent_handle(author) else author
            for h in targets:
                await notifs.create(
                    project_id=topic.project_id,
                    level=NotifLevel.strong,
                    kind=NotifKind.mention,
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
    ) -> AsyncIterator[dict]:
        """Run the AGENT part of a turn (the human block was already posted by
        _post_user_message), yielding WS frames as JSON-ready dicts. Runs under
        the per-topic lock; the prompt is built from history at lock time so a
        queued turn picks up every message posted while it waited."""
        # 正在思考 for EVERYONE: with discrete messages there are no deltas to
        # make a running turn visible, so the working indicator is announced
        # explicitly to every open client (not just the submitter / late
        # re-connectors, who get it from the WS-connect in_flight check).
        yield {"type": "turn_active"}
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
            # No pending human block ⇒ nobody spoke: this is a resume nudge,
            # a kickoff or a returned conclusion. Say so, rather than handing
            # 芝士 bare text that looks like a person's message.
            prompt_text = "\n".join(
                _prompt_line(b) for b in pending
            ) or platform_prompt(content)
            # 图片输入: every pending image rides this turn's user message as a
            # NATIVE base64 image block (Claude Code native image input) — the
            # provider side that has the file does the embedding.
            turn_images = [
                {"path": b.content, "media_type": b.mime_type or "image/png"}
                for b in pending
                if b.kind == BlockKind.attachment and b.content
            ]

            is_private = topic.is_private
            private_owner = topic.private_owner
            acting_agent = await self._agent_handle(session, topic.id)
            if is_private and private_owner:
                # Private chat: the owner's cross-project personal memory.
                memories = await recall_pools(
                    memory, [(MemoryScope.user, private_owner)]
                )
                doc_text = None
            else:
                memories = await self._recall_agent_memories(
                    memory,
                    session,
                    project_id=topic.project_id,
                    agent_handle=acting_agent,
                )
                doc_root = await blocks.doc_root(topic.id)
                doc_text = doc_root.content if doc_root else None
            projects_repo = ProjectRepository(session)
            project = await projects_repo.get(topic.project_id)
            role = await resolve_role_description(
                session, project.expert_role if project else None
            )
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
            resume_session_id = topic.session_id
            untitled = not is_private and topic.title == PLACEHOLDER_TITLE
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
            # turn 活跃度检测: only the tmux backend has a real activity signal
            # (hooks_substrate's idle-suspect + hard-ceiling loop) — SDK/device
            # emit nothing here and TurnRunner's outer wall-clock wrap (runtime.py)
            # keeps its unchanged `agent_turn_timeout_s` default for them.
            from app.domain.agent.tmux_provider import TmuxHooksProvider

            is_activity_aware_backend = isinstance(provider, TmuxHooksProvider)
            if topic.compute_profile is None:
                # v4 affinity red line: materialize the effective target BEFORE
                # the first provider call. A later team-default/sticky change must
                # never move an existing work tree or resumable Claude session.
                topic.compute_profile = provider.name
                await session.commit()

        # --- streaming: no DB transaction held open ---
        if is_activity_aware_backend:
            # Tells TurnRunner's outer wall-clock wrap (runtime.py) to reschedule
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
            turn_meta=_turn_meta_lines(
                budget_s=settings.agent_turn_timeout_s,
                activity_aware=is_activity_aware_backend,
                is_resume=is_resume,
                disk=_workspace_disk(self._workspace_root),
                open_cards=open_cards,
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
        model_kwargs, route = await self._model_kwargs(project_id, provider.name)

        # Backfill any 现场 events the live hook path missed (backend down / no
        # listener during a prior turn) from the durable spool WAL — idempotent by
        # event-id. No-op for the sdk backend and an empty spool.
        async for frame in self._reconcile_spool(project_id, topic_id, turn_id):
            yield frame

        todo: list[dict] = []
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
                    # Announced early so even a failed turn persists it below.
                    new_session_id = event.session_id
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
                    )
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
            quoted = f"（服务原话：{detail}）" if detail else ""
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
            fail_meta: dict | None = None
            fail_code: str | None = None
            if platform_failure is not None:
                fail_text = platform_failure.content
                fail_meta = platform_failure.meta
                fail_code = platform_failure.code
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
                    f"{resets:%m-%d %H:%M} 恢复，{recover}。{quoted}"
                )
                if not is_resume:
                    # Resume ~2min after the window opens (clock skew buffer).
                    wait_s = rate_limit["resets_at"] - datetime.now(UTC).timestamp()
                    resume_after_s = max(60.0, wait_s + 120.0)
            elif _is_out_of_credit(detail):
                # A spent balance is not a wait — no amount of retrying refills
                # it, and telling someone to try again later sends them into a
                # loop that cannot succeed. Say what actually has to happen.
                fail_text = (
                    "⚠️ 芝士这轮没能完成——AI 中继的余额用尽了。"
                    "这不是等一等就能好的，需要有人充值或把机器切到其他 AI 供给；"
                    f"重试无效。{quoted}"
                )
            elif api_error_status:
                fail_text = (
                    f"⚠️ 芝士这轮没能完成——AI 接口错误（HTTP {api_error_status}）。"
                    f"稍后再 @ 它重试。{quoted}"
                )
            else:
                fail_text = (
                    "⚠️ 芝士这轮没能完成——AI 服务返回错误"
                    + (f"：{detail}" if detail else "")
                    + "。稍后再 @ 它重试。"
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
                await session.commit()
            if new_session_id and new_session_id != resume_session_id:
                await self._save_session_pointer(topic_id, new_session_id)
            provider.checkpoint(project_id, topic_id)
            yield {"type": "event_block", "block": fail_payload}
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
                    "reason": "座位额度已恢复，继续之前的任务",
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
                await topics.set_session_id(topic, new_session_id)

            # 这一轮真的把这些消息交给 agent 跑完了 —— 现在才盖 consumed 戳，下一轮
            # 的窗口从这里往后开。**故意放在这里而不是建 prompt 的 tx1**：轮次崩了 /
            # 超时 / provider 报错的路径都在上面 return 或 raise 掉了，戳没盖上，消息
            # 就留在 pending 里由续跑轮重发。宁可重复，不可丢失 —— 重复看得见，丢失
            # 看不见，而后者正是这次要修的 bug。
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
                reconciled_texts.add(frame["block"]["content"])
            yield frame

        # Fallback single message: 芝士's messages normally landed one-by-one at
        # each AgentMessage boundary above. A provider that never announced a
        # boundary (plain non-SDK stub turn, an older remote cheesed node) still
        # lands its reply from the final result text — unless the sweep above
        # just landed that exact text from the spool (with a proper eid).
        assistant_payload: dict | None = None
        if (
            assistant_count == 0
            and final_text.strip()
            and final_text.strip() not in reconciled_texts
        ):
            assistant_payload = await self._persist_assistant_message(
                project_id=project_id,
                topic_id=topic_id,
                text=final_text,
                turn_id=turn_id,
                reply_to=user_block_id,
                roster=roster,
                topic_refs=topic_refs,
            )

        # Snapshot whatever the agent changed in its worktree this turn (native
        # edits → version history). The provider owns this workspace lifecycle
        # step (R2/R9); it's best-effort and never fails the turn.
        provider.checkpoint(project_id, topic_id)

        # 知识沉淀是副产品 (spec §8.4): hand the finished exchange to OpenViking
        # for background memory extraction. The extractor's LLM decides what is
        # memory-worthy (规则4) — fire-and-forget, never delays/fails the turn.
        if not result_error:
            self._schedule_memory_extraction(
                topic_id=topic_id,
                project_id=project_id,
                is_private=is_private,
                private_owner=private_owner,
                agent_handle=acting_agent,
                user_text=prompt_text,
                assistant_text=final_text,
            )

        if assistant_payload is not None:
            yield {"type": "assistant_block", "block": assistant_payload}
        for payload in action_payloads:
            yield {"type": "event_block", "block": payload}
        yield {"type": "done"}

    def _schedule_memory_extraction(
        self,
        *,
        topic_id: uuid.UUID,
        project_id: uuid.UUID,
        is_private: bool,
        private_owner: str | None,
        agent_handle: str,
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
        else:
            # What 芝士 learns in a project is its own, the way a teammate's is.
            # Never the shared pool: two agents in one project would dilute each
            # other's memory, which is the case this split exists for.
            scope, scope_id = (
                MemoryScope.agent_project,
                agent_project_scope_id(project_id, agent_handle),
            )

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
                project_id=project_id,
                agent_handle=await self._agent_handle(session, topic.id),
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
        )
        prompt = (
            "下面是一条线下活动输入，请按『活动消化』技能把它整理成结构化记录："
            "用 cheese 把 做了什么/定了什么/谁负责/下一步 设为本话题活文档；"
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
            **(await self._model_kwargs(project_id, provider.name))[0],
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
                await topics.set_session_id(topic, new_session_id)
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
            **(await self._model_kwargs(project_id, provider.name))[0],
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
                memory, [(MemoryScope.project, str(project_id))]
            )
            role = await resolve_role_description(session, project.expert_role)
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
            mem_lines += f"\n- （另有 {memories.omitted} 条更早的记忆未列出）"
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
            **(await self._model_kwargs(project_id, provider.name))[0],
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
