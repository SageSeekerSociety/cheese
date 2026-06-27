"""Chat orchestration — ties topic, blocks, memory, and the agent together.

This is the platform "shell" around 芝士: it persists the conversation as
blocks (append-only history, spec H1), injects project memory into the agent's
context (spec §8.4 带记忆回答), streams the reply, and stores the resumable
session id on the topic.

DB writes happen in short transactions around the (long) streaming call so we
never hold a transaction open across the model round-trip.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import settings
from app.core.errors import NotFoundError
from app.domain.agent.roles import role_description
from app.domain.agent.service import (
    AgentDelta,
    AgentResult,
    AgentService,
    AgentToolUse,
)
from app.domain.agent.skills import DEFAULT_CHAT_SKILLS, load_skills
from app.domain.agent.tools import build_cheese_server, tool_names
from app.domain.block.models import AuthorType, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.block.schemas import BlockOut
from app.domain.memory.models import MemoryScope
from app.domain.memory.store import DbMemoryStore
from app.domain.milestone.repositories import MilestoneRepository
from app.domain.project.repositories import ProjectRepository
from app.domain.topic.models import TopicKind
from app.domain.topic.repositories import TopicRepository
from app.domain.usage.repositories import UsageRepository
from app.domain.workspace import service as ws

ACTIVITY_SKILLS = ["conversation-style", "activity-digestion", "doc-form"]
HEARTBEAT_SKILLS = ["heartbeat", "conversation-style"]
PRIVATE_SKILLS = ["private-chat", "conversation-style"]

CHEESE_AUTHOR = "cheese"

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


# Native tools (sandbox mode) → 现场 labels.
_TOOL_VERB.update(
    {"Bash": "执行命令", "Write": "写文件", "Edit": "改文件", "Read": "读文件"}
)
_TOOL_ARG.update(
    {"Bash": "command", "Write": "file_path", "Edit": "file_path", "Read": "file_path"}
)


def _format_tool_event(name: str, args: dict) -> str:
    verb = _TOOL_VERB.get(name, name)
    key = _TOOL_ARG.get(name)
    preview = ""
    if key and isinstance(args, dict) and args.get(key) is not None:
        preview = " ".join(str(args[key]).split())[:120]
    return f"{verb}\n{preview}" if preview else verb




def _build_system_prompt(
    base: str,
    skills: str,
    doc: str | None,
    memories: list[str],
    role: str | None = None,
) -> str:
    parts = [base]
    if role:
        parts.append(f"## 你的专家角色\n{role}")
    if skills:
        parts.append(skills)
    if doc:
        parts.append(
            "## 当前话题的活文档（这是最新状态；用户可能编辑了它，"
            "请按它继续工作，并在状态变化时用 update_doc 工具更新它）\n" + doc
        )
    if memories:
        facts = "\n".join(f"- {m}" for m in memories)
        parts.append(f"## 项目记忆（你已知道的事实，回答时可引用）\n{facts}")
    return "\n\n".join(parts)


def _block_payload(block_out: BlockOut) -> dict:
    return block_out.model_dump(mode="json")


class ChatService:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker,
        agent: AgentService,
        base_system_prompt: str,
        workspace_root: str,
        sandbox_enabled: bool = False,
    ):
        self._sessions = session_factory
        self._agent = agent
        self._base_prompt = base_system_prompt
        self._workspace_root = workspace_root
        self._sandbox_enabled = sandbox_enabled
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
    ) -> AsyncIterator[dict]:
        """Serialize per topic, then run the turn (spec §9.1 串行队列)."""
        async with self._lock_for(topic_id):
            async for frame in self._converse_impl(
                topic_id=topic_id, author=author, content=content, summon=summon
            ):
                yield frame

    def _workspace_for(self, project_id: uuid.UUID) -> str:
        path = Path(self._workspace_root) / str(project_id)
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    async def _converse_impl(
        self,
        *,
        topic_id: uuid.UUID,
        author: str,
        content: str,
        summon: bool = True,
    ) -> AsyncIterator[dict]:
        """Run one chat turn, yielding WS frames as JSON-ready dicts.

        When ``summon`` is False (default human-to-human, spec C3 / §7.1), the
        message is just posted — 芝士 stays quiet (it still ingests it for memory
        on its next summoned turn via the resumed session). When True (@芝士),
        芝士 replies and may use tools.
        """
        # --- tx1: load topic, persist user block, load memory ---
        async with self._sessions() as session:
            topics = TopicRepository(session)
            blocks = BlockRepository(session)
            memory = DbMemoryStore(session)

            topic = await topics.get(topic_id)
            if topic is None:
                raise NotFoundError("Topic not found")

            user_block = await blocks.add(
                project_id=topic.project_id,
                topic_id=topic.id,
                author=author,
                author_type=AuthorType.human,
                content=content,
                kind=BlockKind.message,
            )
            user_payload = _block_payload(BlockOut.model_validate(user_block))

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
                if b.kind == BlockKind.message and b.author_type == AuthorType.human
            ]
            prompt_text = (
                "\n".join(f"[{b.author}]: {b.content}" for b in pending) or content
            )

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
                docs = await blocks.list_docs_for_topic(topic.id)
                doc_text = docs[0].content if docs else None
            project = await ProjectRepository(session).get(topic.project_id)
            role = role_description(project.expert_role if project else None)
            project_id = topic.project_id
            resume_session_id = topic.session_id
            user_block_id = user_block.id
            await session.commit()

        yield {"type": "user_block", "block": user_payload}

        # Default human-to-human: post and stay quiet (spec C3 / §7.1).
        if not summon:
            yield {"type": "done"}
            return

        # --- streaming: no DB transaction held open ---
        skills = load_skills(PRIVATE_SKILLS) if is_private else self._skills
        system_prompt = _build_system_prompt(
            self._base_prompt, skills, doc_text, memories, role
        )
        cwd = self._workspace_for(project_id)
        final_text = ""
        new_session_id = resume_session_id

        # Sandbox mode (spec §9.1): run claude INSIDE a per-topic container with
        # native tools + the `cheese` CLI for platform actions. Private chats stay
        # on the in-process MCP path (remember → user-scoped memory).
        use_sandbox = (
            self._sandbox_enabled and ws.sandbox_available() and not is_private
        )
        stream_kwargs: dict = {}
        if use_sandbox:
            worktree = ws.topic_worktree(project_id, topic_id)
            sess = ws.session_dir(project_id, topic_id)
            cwd = str(worktree)
            stream_kwargs["sandbox"] = {
                "cli_path": str(Path(settings.sandbox_shim).resolve()),
                "allowed_tools": ["Bash", "Read", "Write", "Edit", "Grep", "Glob"],
                "env": {
                    "SBX_IMAGE": settings.sandbox_image,
                    "SBX_WORKTREE": str(worktree),
                    "SBX_SESSION": str(sess),
                    "CHEESE_API": settings.sandbox_api_base,
                    "CHEESE_PROJECT": str(project_id),
                    "CHEESE_TOPIC": str(topic_id),
                    "CHEESE_AUTHOR": CHEESE_AUTHOR,
                },
            }
        else:
            # Give 芝士 its platform tools via in-process MCP. In a private chat,
            # `remember` writes the owner's personal memory (spec §8.4).
            if is_private and private_owner:
                server = build_cheese_server(
                    session_factory=self._sessions,
                    project_id=project_id,
                    topic_id=topic_id,
                    memory_scope=MemoryScope.user,
                    memory_scope_id=private_owner,
                )
            else:
                server = build_cheese_server(
                    session_factory=self._sessions,
                    project_id=project_id,
                    topic_id=topic_id,
                )
            stream_kwargs["mcp_servers"] = {"cheese": server}
            stream_kwargs["allowed_tools"] = tool_names()

        tool_events: list[tuple[str, dict]] = []
        usage = None
        async for event in self._agent.stream_reply(
            prompt=prompt_text,
            system_prompt=system_prompt,
            cwd=cwd,
            resume_session_id=resume_session_id,
            **stream_kwargs,
        ):
            if isinstance(event, AgentDelta):
                yield {"type": "delta", "text": event.text}
            elif isinstance(event, AgentToolUse):
                name = event.name.replace("mcp__cheese__", "")
                tool_events.append((name, event.input or {}))
                yield {"type": "tool", "name": event.name, "input": event.input}
            elif isinstance(event, AgentResult):
                final_text = event.text
                new_session_id = event.session_id
                usage = event.usage

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
            )
            assistant_payload = _block_payload(BlockOut.model_validate(assistant_block))

            topic = await topics.get(topic_id)
            if topic is not None and new_session_id:
                await topics.set_session_id(topic, new_session_id)
            await session.commit()

        # Snapshot whatever the agent changed in its worktree this turn (native
        # edits → version history). Best-effort; never fail the turn on git.
        if use_sandbox:
            try:
                ws.snapshot_worktree(project_id, topic_id)
            except Exception:  # noqa: BLE001
                pass

        yield {"type": "assistant_block", "block": assistant_payload}
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
        cwd = self._workspace_for(project_id)
        prompt = (
            "下面是一条线下活动输入，请按『活动消化』技能把它整理成结构化记录："
            "用 update_doc 写下 做了什么/定了什么/谁负责/下一步；"
            "如果这是个关键节点就用 pin_milestone 钉成里程碑；"
            "需要分派的待办用 notify 通知到人。\n\n---\n" + text
        )
        server = build_cheese_server(
            session_factory=self._sessions,
            project_id=project_id,
            topic_id=topic_id,
        )
        final_text = ""
        new_session_id = None
        tools_used: list[str] = []
        async for event in self._agent.stream_reply(
            prompt=prompt,
            system_prompt=system_prompt,
            cwd=cwd,
            resume_session_id=None,
            mcp_servers={"cheese": server},
            allowed_tools=tool_names(),
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
            "然后只对真正需要的事用 notify 工具发分级通知（level=silent/light/"
            "strong，kind=heartbeat），别骚扰。\n\n" + context
        )
        server = build_cheese_server(
            session_factory=self._sessions,
            project_id=project_id,
            topic_id=root_topic_id,
        )
        final_text = ""
        tools_used: list[str] = []
        async for event in self._agent.stream_reply(
            prompt=prompt,
            system_prompt=system_prompt,
            cwd=self._workspace_for(project_id),
            resume_session_id=None,
            mcp_servers={"cheese": server},
            allowed_tools=tool_names(),
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
        final_text = ""
        async for event in self._agent.stream_reply(
            prompt=prompt,
            system_prompt=system_prompt,
            cwd=self._workspace_for(project_id),
            resume_session_id=None,
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
