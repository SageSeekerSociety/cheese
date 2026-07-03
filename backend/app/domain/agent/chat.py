"""Chat orchestration — ties topic, blocks, memory, and the agent together.

This is the platform "shell" around 芝士: it persists the conversation as
blocks (append-only history, spec H1), injects project memory into the agent's
context (spec §8.4 带记忆回答), streams the reply, and stores the resumable
session id on the topic.

DB writes happen in short transactions around the (long) streaming call so we
never hold a transaction open across the model round-trip.
"""

import asyncio
import logging
import re
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.errors import NotFoundError
from app.core.text import markdown_preview
from app.domain.agent.compute import ComputePool
from app.domain.agent.profiles import ProfileRegistry
from app.domain.agent.roles import role_description
from app.domain.agent.service import (
    AgentDelta,
    AgentResult,
    AgentService,
    AgentSessionInfo,
    AgentToolUse,
)
from app.domain.agent.skills import DEFAULT_CHAT_SKILLS, load_skills
from app.domain.block.models import AuthorType, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.block.schemas import BlockOut
from app.domain.memory.models import MemoryScope
from app.domain.memory.store import DbMemoryStore
from app.domain.milestone.repositories import MilestoneRepository
from app.domain.notification.models import NotifKind, NotifLevel
from app.domain.notification.services import NotificationService
from app.domain.project.repositories import ProjectRepository
from app.domain.topic.models import TopicKind
from app.domain.topic.repositories import TopicRepository
from app.domain.usage.repositories import UsageRepository

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


def _format_tool_event(name: str, args: dict) -> str:
    verb = _TOOL_VERB.get(name, name)
    key = _TOOL_ARG.get(name)
    preview = ""
    if key and isinstance(args, dict) and args.get(key) is not None:
        preview = " ".join(str(args[key]).split())[:120]
    return f"{verb}\n{preview}" if preview else verb


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
_ACTION_LABEL = {
    "decision": "📌 记录了一条决策",
    "doc": "📄 更新了活文档",
    "topics": "🌿 更新了子话题",
    "milestone": "📌 钉了一个里程碑",
    "accept": "✅ 递出了验收卡",
    "notify": "🔔 发了一条通知",
}


# HTTP statuses worth an automatic re-run: timeouts, throttling, server-side
# blips. Anything else (or a rejected seat rate-limit) surfaces immediately.
_TRANSIENT_HTTP = {408, 429, 500, 502, 503, 504, 529}


def _transient_provider_error(result: AgentResult) -> bool:
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




def _build_system_prompt(
    base: str,
    skills: str,
    doc: str | None,
    memories: list[str],
    role: str | None = None,
    roster: list[dict] | None = None,
    topics: list[dict] | None = None,
    untitled: bool = False,
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
            "`cheese title \"<标题>\"` 起个 ≤12 字简短标题，然后再照常回应、干活。"
            "这条优先于「先回应，再干活」：起标题只是一条命令，几乎不花时间。"
            "（只起一次，定了别反复改。）"
        )
    if role:
        parts.append(f"## 你的专家角色\n{role}")
    if skills:
        parts.append(skills)
    if topics:
        lines = "\n".join(f"- {t['title']}" for t in topics)
        parts.append(
            "## 项目话题（交叉引用某个话题/它的文档时，在标题前加 @，如 "
            "`@搭建推荐算法原型`——会渲染成可点的「#标题」链接）\n" + lines
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
    if memories:
        facts = "\n".join(f"- {m}" for m in memories)
        parts.append(f"## 项目记忆（你已知道的事实，回答时可引用）\n{facts}")
    return "\n\n".join(parts)


# Mentions are an ENCODED token, not guessed-from-prose: 芝士 (and the composer)
# emit `<@handle>`, which the platform resolves deterministically and the UI
# renders as a chip showing the member's name. A literal "@name" is just text.
_MENTION_RE = re.compile(r"<@([\w-]+)>")


_TOPIC_REF_RE = re.compile(r"<#([0-9a-fA-F-]{8,})>")

# A topic created from the rail's + has no human-typed title ("新话题"); 芝士 names
# it via `cheese title` (titles are AI-generated, never deterministically derived
# from human input or the agent's output — see CLAUDE.md).
PLACEHOLDER_TITLE = "新话题"

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


def conclusion_digest_prompt(conclusion_message: str) -> str:
    """The parent's wake-up instruction when a sub-topic returns its conclusion
    (结论回流唤醒父话题 — the return leg of the subagent loop: in Claude Code
    the parent resumes when the Task tool result arrives). Prompt-only; the
    conclusion text is copied verbatim, nothing is derived from it."""
    return (
        "一个子话题刚回流了结论（原文如下，也已织进本话题活文档末尾）。"
        "请消化它：\n"
        "1. 把活文档整理成最新状态——结论的要点合并进对应章节，"
        "别让「子话题结论」堆在文档末尾。\n"
        "2. 判断下一步：这个结论解锁了什么？需要继续拆活就拆（split 带 --brief），"
        "需要人拍板/验收就发通知或验收卡，整件事收尾了就说明结论。\n"
        "3. 在对话里用一两句话向大家报信（结论已在文档里，别复述全文）。\n\n"
        f"---\n{conclusion_message}"
    )


def _topic_refs(text: str) -> list[str]:
    """`<#topicId>` reference tokens in a message → topic refs (for linkage)."""
    return [f"topic:{tid}" for tid in dict.fromkeys(_TOPIC_REF_RE.findall(text or ""))]


# After an ASCII-word-ending @name/@handle, the next char must not continue the
# word — so roster handle "andy" never eats the front of a literal "@andyl".
# ASCII-only on purpose: Python's \w matches CJK, and "@张衡来负责" must still
# resolve 张衡 even though 来 follows without a space.
_ASCII_WORD = re.compile(r"[A-Za-z0-9_-]$")
_ASCII_BOUNDARY = r"(?![A-Za-z0-9_-])"


def _expand_mention_names(
    text: str, roster: list[dict], topics: list[dict] | None = None
) -> str:
    """Canonicalize a friendly "@名字 / @handle / @话题名" into the structured
    token (<@handle> / <#id>) — deterministic exact-match against the
    roster/topics, longest first. Both the display name AND the handle work:
    in chat people are labeled by handle, so "@andyl" must resolve even when
    andyl's display name differs. Tokens already present are untouched (they
    don't match the @name patterns)."""
    subs: list[tuple[str, str]] = []
    for m in roster:
        tok = f"<@{m['handle']}>"
        for key in (m.get("name"), m.get("handle")):
            if key:  # an empty pattern ("@") would swallow every @ in the text
                subs.append((f"@{key}", tok))
    subs += [(f"@{t['title']}", f"<#{t['id']}>") for t in (topics or []) if t["title"]]
    subs.sort(key=lambda s: len(s[0]), reverse=True)
    seen: set[str] = set()
    for pat, tok in subs:
        if pat in seen:  # name == handle yields the same pattern twice
            continue
        seen.add(pat)
        boundary = _ASCII_BOUNDARY if _ASCII_WORD.search(pat) else ""
        # (?<!<) keeps already-encoded tokens intact: the "@handle" inside a
        # produced "<@handle>" must not be re-wrapped by a later pattern.
        # bind tok per-iteration (B023): a bare closure would see the last tok
        text = re.sub(
            r"(?<!<)" + re.escape(pat) + boundary, lambda _m, t=tok: t, text
        )
    return text


def _resolve_mentions(text: str, roster: list[dict]) -> tuple[list[str], list[str]]:
    """Resolve <@handle> mention tokens against the roster. Returns
    (resolved_handles, unresolved_handles); unresolved = a token whose handle is
    not a member (a hallucinated handle → the platform flags it)."""
    resolved: list[str] = []
    unresolved: list[str] = []
    if not text:
        return resolved, unresolved
    handles = {m["handle"] for m in roster}
    for h in dict.fromkeys(_MENTION_RE.findall(text)):
        (resolved if h in handles else unresolved).append(h)
    return resolved, unresolved


def _block_payload(block_out: BlockOut) -> dict:
    return block_out.model_dump(mode="json")


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
    return f"[{b.author}]: {b.content}"


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
    ):
        self._sessions = session_factory
        self._base_prompt = base_system_prompt
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
        # Load the conversation skills once (spec §8.3 product "soul").
        self._skills = load_skills(DEFAULT_CHAT_SKILLS)
        # Per-topic serial queue (spec §9.1): one agent turn per topic at a
        # time, so concurrent messages to the same topic don't race.
        self._topic_locks: dict[uuid.UUID, asyncio.Lock] = {}

    @property
    def session_factory(self) -> async_sessionmaker:
        return self._sessions

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
        self, topic_id: uuid.UUID, content: str, turn_id: uuid.UUID | None = None
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
            )
            payload = _block_payload(BlockOut.model_validate(block))
            await session.commit()
        return payload

    async def _save_session_pointer(
        self, topic_id: uuid.UUID, session_id: str
    ) -> None:
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
                roster = (
                    []
                    if topic.is_private
                    else await ProjectRepository(session).list_members(
                        topic.project_id
                    )
                )
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

    async def _model_kwargs(self, project_id: uuid.UUID) -> dict:
        """Per-turn overrides for the agent call, resolved from project.settings:
        the ExecutionProfile → model+env (design §2), and the sandbox image (spec
        §9.1 environment — a project can run on cheesex-dev for dogfooding). model
        is skipped when no registry is configured (the agent uses its default); the
        image is resolved regardless (it's independent of the AI profile)."""
        async with self._sessions() as session:
            project = await ProjectRepository(session).get(project_id)
        kwargs: dict = {}
        image = (project.settings or {}).get("sandbox_image") if project else None
        if image:
            kwargs["sandbox_image"] = image
        if self._profiles is not None:
            profile = self._profiles.resolve(
                project.settings if project else None,
                project.owner_handle if project else None,
            )
            kwargs["model"] = profile.model
            kwargs["env"] = profile.full_env()
        return kwargs

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
        (spec §7: @人 = strong). Returns (resolved_handles, unresolved_names) so the
        caller can set refs and flag the wrong ones."""
        resolved, unresolved = _resolve_mentions(text, roster)
        targets = [h for h in resolved if h not in (author, CHEESE_AUTHOR)]
        if targets:
            notifs = NotificationService(session)
            preview = markdown_preview(text, 200)
            who = "芝士" if author == CHEESE_AUTHOR else author
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
        # --- tx1: load topic + history, load memory ---
        async with self._sessions() as session:
            topics = TopicRepository(session)
            blocks = BlockRepository(session)
            memory = DbMemoryStore(session)

            topic = await topics.get(topic_id)
            if topic is None:
                raise NotFoundError("Topic not found")

            # Speaker-labelled prompt covering every human message since 芝士's
            # last reply — so messages posted without @芝士 are still seen on the
            # next summon (spec §7.1 所有消息 AI 都会收到), each tagged with who
            # said it so 芝士 can tell people apart in a group topic (§8.4).
            history = await blocks.list_for_topic(topic_id)
            last_ai = -1
            for i, b in enumerate(history):
                if b.author_type == AuthorType.ai and b.kind == BlockKind.message:
                    last_ai = i
            pending = [
                b
                for b in history[last_ai + 1 :]
                if b.kind in (BlockKind.message, BlockKind.attachment)
                and b.author_type == AuthorType.human
            ]
            prompt_text = (
                "\n".join(_prompt_line(b) for b in pending) or content
            )
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
            if is_private and private_owner:
                # Private chat: the owner's cross-project personal memory.
                memories = await memory.recall(MemoryScope.user, private_owner)
                doc_text = None
            else:
                memories = await memory.recall(
                    MemoryScope.project, str(topic.project_id)
                )
                doc_root = await blocks.doc_root(topic.id)
                doc_text = doc_root.content if doc_root else None
            projects_repo = ProjectRepository(session)
            project = await projects_repo.get(topic.project_id)
            role = role_description(project.expert_role if project else None)
            # Roster so 芝士 can @ real teammates (not just name them in prose).
            roster = (
                []
                if is_private
                else await projects_repo.list_members(topic.project_id)
            )
            # Topic list so 芝士 can cross-reference topics with <#id> tokens.
            topic_refs = []
            if not is_private:
                topic_refs = [
                    {"id": str(t.id), "title": t.title}
                    for t in await topics.list_for_project(topic.project_id)
                    if t.id != topic.id and t.kind != TopicKind.root
                ]
            project_id = topic.project_id
            resume_session_id = topic.session_id
            untitled = not is_private and topic.title == PLACEHOLDER_TITLE

        # --- streaming: no DB transaction held open ---
        skills = load_skills(PRIVATE_SKILLS) if is_private else self._skills
        system_prompt = _build_system_prompt(
            self._base_prompt,
            skills,
            doc_text,
            memories,
            role,
            roster,
            topic_refs,
            untitled,
        )
        final_text = ""
        new_session_id = resume_session_id
        result_error = False
        api_error_status: int | None = None
        rate_limit: dict | None = None

        # Compute: a provider owns the per-topic sandbox + execution (spec §9.1).
        # In a private chat, `cheese remember` targets the owner's personal memory
        # (spec §8.4). The provider runs a plain model turn when no Docker (tests).
        provider = self._compute.select()
        model_kwargs = await self._model_kwargs(project_id)

        tool_events: list[tuple[str, dict]] = []
        todo: list[dict] = []
        actions: list[str] = []  # cheese-action resources this turn (→ persisted cards)
        usage = None
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
                elif isinstance(event, AgentDelta):
                    yield {"type": "delta", "text": event.text}
                elif isinstance(event, AgentToolUse):
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
                    tool_events.append((name, args))
                    yield {"type": "tool", "name": event.name, "input": args}
                    # cheese <sub> ran as Bash → tell the UI which panel changed,
                    # so it refreshes mid-turn (doc/decisions/...), quietly.
                    if name == "Bash":
                        resource = _cheese_resource(str(args.get("command", "")))
                        if resource:
                            yield {"type": "state", "resource": resource}
                            if (
                                resource in _ACTION_LABEL
                                and resource not in actions
                            ):
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

        if result_error:
            # Provider/infra failure surfaced as the run's result. NEVER
            # ventriloquize it as 芝士's message — it goes into the 现场 as a
            # system event, platform-worded from STRUCTURED fields (rate-limit
            # resets_at → 北京时间; api_error_status → HTTP code), with the raw
            # provider detail quoted for the record.
            logger.warning(
                "turn %s provider error topic=%s rate_limit=%s api_status=%s",
                turn_id, topic_id, rate_limit, api_error_status,
            )
            detail = final_text.strip()
            quoted = f"（服务原话：{detail}）" if detail else ""
            resume_after_s: float | None = None
            if (
                rate_limit
                and rate_limit.get("status") == "rejected"
                and rate_limit.get("resets_at")
            ):
                resets = datetime.fromtimestamp(
                    rate_limit["resets_at"], tz=ZoneInfo("Asia/Shanghai")
                )
                recover = (
                    "恢复后我会自动接着跑" if not is_resume else "到点再 @ 它"
                )
                fail_text = (
                    f"⚠️ 芝士的 AI 座位额度用完了，北京时间 "
                    f"{resets:%m-%d %H:%M} 恢复，{recover}。{quoted}"
                )
                if not is_resume:
                    # Resume ~2min after the window opens (clock skew buffer).
                    wait_s = rate_limit["resets_at"] - datetime.now(UTC).timestamp()
                    resume_after_s = max(60.0, wait_s + 120.0)
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
                for name, tool_input in tool_events:
                    await blocks.add(
                        project_id=project_id,
                        topic_id=topic_id,
                        author=CHEESE_AUTHOR,
                        author_type=AuthorType.ai,
                        content=_format_tool_event(name, tool_input),
                        kind=BlockKind.event,
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
                    )
                fail_block = await blocks.add(
                    project_id=project_id,
                    topic_id=topic_id,
                    author="system",
                    author_type=AuthorType.system,
                    content=fail_text,
                    kind=BlockKind.event,
                    turn_id=turn_id,
                )
                fail_payload = _block_payload(BlockOut.model_validate(fail_block))
                await session.commit()
            if new_session_id and new_session_id != resume_session_id:
                await self._save_session_pointer(topic_id, new_session_id)
            provider.checkpoint(project_id, topic_id)
            yield {"type": "event_block", "block": fail_payload}
            yield {"type": "error", "message": fail_text, "persisted": True}
            if resume_after_s is not None:
                # Internal frame: the runner schedules the auto-resume.
                yield {
                    "type": "resume_hint",
                    "after_s": resume_after_s,
                    "reason": "座位额度已恢复，继续之前的任务",
                }
            yield {"type": "done"}
            return

        # Canonicalize friendly "@名字 / @话题名" → tokens so they render as chips
        # and notify, even when 芝士 didn't emit the exact <@handle> form.
        final_text = _expand_mention_names(final_text, roster, topic_refs)

        # --- tx2: persist tool events (施工现场) + assistant block + usage ---
        async with self._sessions() as session:
            topics = TopicRepository(session)
            blocks = BlockRepository(session)

            for name, tool_input in tool_events:
                await blocks.add(
                    project_id=project_id,
                    topic_id=topic_id,
                    author=CHEESE_AUTHOR,
                    author_type=AuthorType.ai,
                    content=_format_tool_event(name, tool_input),
                    kind=BlockKind.event,
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
                )

            assistant_block = await blocks.add(
                project_id=project_id,
                topic_id=topic_id,
                author=CHEESE_AUTHOR,
                author_type=AuthorType.ai,
                content=final_text,
                kind=BlockKind.message,
                reply_to=user_block_id,
                turn_id=turn_id,
            )
            topic = await topics.get(topic_id)
            # <@handle> mentions in 芝士's reply → strong notify (the token is the
            # single source of truth: what's shown = who's notified). Hallucinated
            # handles get flagged in 现场 so a wrong @ never silently no-ops.
            if topic is not None:
                resolved, unresolved = await self._notify_mentions(
                    session, topic, CHEESE_AUTHOR, final_text, roster
                )
                refs = [f"user:{h}" for h in resolved] + _topic_refs(final_text)
                if refs:
                    assistant_block.refs = refs
                for bad in unresolved:
                    await blocks.add(
                        project_id=project_id,
                        topic_id=topic_id,
                        author=CHEESE_AUTHOR,
                        author_type=AuthorType.ai,
                        content=f"⚠️ @了 <@{bad}>，但项目里没有这个成员，没能通知到",
                        kind=BlockKind.event,
                        turn_id=turn_id,
                    )
            assistant_payload = _block_payload(BlockOut.model_validate(assistant_block))

            # Persistent, clickable action cards for the cheese actions this turn
            # (system events show in the conversation; refs tag the resource).
            action_payloads = []
            for resource in actions:
                blk = await blocks.add(
                    project_id=project_id,
                    topic_id=topic_id,
                    author=CHEESE_AUTHOR,
                    author_type=AuthorType.system,
                    content=_ACTION_LABEL[resource],
                    kind=BlockKind.event,
                    refs=[f"action:{resource}"],
                    turn_id=turn_id,
                )
                action_payloads.append(_block_payload(BlockOut.model_validate(blk)))

            if topic is not None and new_session_id:
                await topics.set_session_id(topic, new_session_id)
            await session.commit()

        # Snapshot whatever the agent changed in its worktree this turn (native
        # edits → version history). The provider owns this workspace lifecycle
        # step (R2/R9); it's best-effort and never fails the turn.
        provider.checkpoint(project_id, topic_id)

        yield {"type": "assistant_block", "block": assistant_payload}
        for payload in action_payloads:
            yield {"type": "event_block", "block": payload}
        yield {"type": "done"}

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
            memory = DbMemoryStore(session)

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
            memories = await memory.recall(MemoryScope.project, str(project_id))
            topic_id = topic.id
            await session.commit()

        # --- run 芝士 with the activity-digestion skill + tools ---
        system_prompt = _build_system_prompt(
            self._base_prompt, load_skills(ACTIVITY_SKILLS), None, memories
        )
        prompt = (
            "下面是一条线下活动输入，请按『活动消化』技能把它整理成结构化记录："
            "用 cheese 把 做了什么/定了什么/谁负责/下一步 设为本话题活文档；"
            "如果这是个关键节点就用 cheese 钉成里程碑；"
            "需要分派的待办用 cheese 通知到人。\n\n---\n" + text
        )
        provider = self._compute.select()
        final_text = ""
        new_session_id = None
        tools_used: list[str] = []
        async for event in provider.run_turn(
            project_id=project_id,
            topic_id=topic_id,
            prompt=prompt,
            system_prompt=system_prompt,
            resume_session_id=None,
            **(await self._model_kwargs(project_id)),
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
                author=CHEESE_AUTHOR,
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
            return f"{m.due_date.date().isoformat()}（剩 {d} 天）" if d >= 0 else (
                f"{m.due_date.date().isoformat()}（已逾期 {-d} 天）"
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
        provider = self._compute.select()
        final_text = ""
        tools_used: list[str] = []
        async for event in provider.run_turn(
            project_id=project_id,
            topic_id=root_topic_id,
            prompt=prompt,
            system_prompt=system_prompt,
            resume_session_id=None,
            **(await self._model_kwargs(project_id)),
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
                author=CHEESE_AUTHOR,
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
            memory = DbMemoryStore(session)

            project = await projects.get(project_id)
            if project is None:
                raise NotFoundError("Project not found")
            all_topics = await topics.list_for_project(project_id)
            upcoming = await milestones.list_calendar(project_id)
            memories = await memory.recall(MemoryScope.project, str(project_id))

        topic_lines = "\n".join(
            f"- {t.title} [{t.status.value}]"
            for t in all_topics
            if t.kind != TopicKind.root
        )
        ms_lines = "\n".join(
            f"- {m.title} 截止 {m.due_date.isoformat() if m.due_date else '未定'}"
            for m in upcoming
        )
        mem_lines = "\n".join(f"- {m}" for m in memories)
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
            role_description(project.expert_role),
        )
        prompt = (
            "请基于下面的项目状态，写一份『一页纸总结』：3-5 句话，让老师 30 秒读懂"
            "这个团队在做什么、到哪了、下一步和风险。说人话、不堆术语、不要列工具调用，"
            "直接给总结正文。\n\n" + context
        )
        # Pure text generation (no platform actions) — still runs through the
        # provider (root-topic sandbox when present) for a single execution path;
        # topic_id None (no root topic) degrades to a plain model turn.
        provider = self._compute.select()
        final_text = ""
        async for event in provider.run_turn(
            project_id=project_id,
            topic_id=project.root_topic_id,
            prompt=prompt,
            system_prompt=system_prompt,
            resume_session_id=None,
            **(await self._model_kwargs(project_id)),
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
