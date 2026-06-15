"""芝士's platform tools (spec §9.1).

The platform perceives the AI ONLY through structured tool calls — never by
parsing natural-language output. These in-process SDK MCP tools let 芝士 act:
create sub-topics, maintain the living doc, remember facts, send notifications,
hand out accept cards, and return a sub-topic's conclusion.

Each tool opens its own short DB transaction (the tools fire mid-stream, while
no request transaction is held) and is bound to the current project/topic via
closure when the server is built for a turn.
"""

import uuid
from datetime import datetime
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.domain.block.models import AuthorType, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.memory.models import MemoryScope
from app.domain.memory.store import DbMemoryStore
from app.domain.milestone.repositories import MilestoneRepository
from app.domain.notification.models import NotifKind, NotifLevel
from app.domain.notification.repositories import NotificationRepository
from app.domain.review.repositories import AcceptCardRepository
from app.domain.topic.services import TopicService

SERVER_NAME = "cheese"
CHEESE_AUTHOR = "cheese"

_TOOL_NAMES = [
    "create_subtopic",
    "update_doc",
    "remember",
    "notify",
    "request_accept",
    "return_conclusion",
    "pin_milestone",
    "write_file",
    "record_decision",
]


def tool_names() -> list[str]:
    """Fully-qualified allowed-tool names for ClaudeAgentOptions."""
    return [f"mcp__{SERVER_NAME}__{name}" for name in _TOOL_NAMES]


def _text(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": message}]}


def build_cheese_server(
    *,
    session_factory: async_sessionmaker,
    project_id: uuid.UUID,
    topic_id: uuid.UUID,
    memory_scope: MemoryScope = MemoryScope.project,
    memory_scope_id: str | None = None,
):
    """Build an in-process MCP server bound to one project/topic context.

    memory_scope/_id route the `remember` tool: project memory by default, or a
    user's personal memory in a private chat (spec §8.4)."""
    mem_scope = memory_scope
    mem_scope_id = memory_scope_id or str(project_id)

    @tool(
        "create_subtopic",
        "把当前话题里一件值得独立追踪的事拆成一个子话题，你的分身会在里面专注完成它。"
        "传入子话题标题。",
        {"title": str},
    )
    async def create_subtopic(args: dict[str, Any]) -> dict[str, Any]:
        async with session_factory() as s:
            topic = await TopicService(s).split_to_subtopic(
                parent_topic_id=topic_id,
                title=args["title"],
                created_by=CHEESE_AUTHOR,
            )
            await s.commit()
            return _text(f"已创建子话题「{topic.title}」（id={topic.id}）。")

    @tool(
        "update_doc",
        "维护当前话题的活文档（状态摘要）。传入完整的 markdown 文档内容，"
        "会覆盖式更新——文档永远反映最新状态，不是流水账。",
        {"content": str},
    )
    async def update_doc(args: dict[str, Any]) -> dict[str, Any]:
        async with session_factory() as s:
            repo = BlockRepository(s)
            docs = await repo.list_docs_for_topic(topic_id)
            if docs:
                await repo.update_content(docs[0], args["content"])
                action = "更新"
            else:
                await repo.add(
                    project_id=project_id,
                    topic_id=topic_id,
                    author=CHEESE_AUTHOR,
                    author_type=AuthorType.ai,
                    content=args["content"],
                    kind=BlockKind.doc,
                )
                action = "创建"
            await s.commit()
            return _text(f"已{action}话题文档。")

    @tool(
        "remember",
        "把一个值得长期记住的项目事实写入项目记忆（章程/决策/进展），"
        "之后任何话题都能引用。传入一句话事实。",
        {"fact": str},
    )
    async def remember(args: dict[str, Any]) -> dict[str, Any]:
        async with session_factory() as s:
            await DbMemoryStore(s).remember(mem_scope, mem_scope_id, args["fact"])
            await s.commit()
            where = "个人记忆" if mem_scope == MemoryScope.user else "项目记忆"
            return _text(f"已记入{where}。")

    @tool(
        "notify",
        "给某个人发一条通知。level=silent/light/strong（按打扰程度），"
        "kind=change_alert(变更提醒)/decision_request(决策请求)。"
        "决策请求要让人能拍板。",
        {
            "title": str,
            "body": str,
            "level": str,
            "kind": str,
            "target_handle": str,
        },
    )
    async def notify(args: dict[str, Any]) -> dict[str, Any]:
        try:
            level = NotifLevel(args.get("level", "light"))
            kind = NotifKind(args.get("kind", "change_alert"))
        except ValueError:
            return _text("level/kind 取值无效。")
        async with session_factory() as s:
            await NotificationRepository(s).add(
                project_id=project_id,
                level=level,
                kind=kind,
                title=args["title"],
                body=args.get("body", ""),
                target_handle=args.get("target_handle") or None,
                topic_id=topic_id,
            )
            await s.commit()
            return _text(f"已发送通知给 {args.get('target_handle') or '所有人'}。")

    @tool(
        "request_accept",
        "当前话题的成果做完了，把验收卡递给一个具体的人（不是广播）。"
        "传入 reviewer_handle 和推荐理由（为什么是这个人）。",
        {"reviewer_handle": str, "routing_reason": str},
    )
    async def request_accept(args: dict[str, Any]) -> dict[str, Any]:
        async with session_factory() as s:
            await AcceptCardRepository(s).add(
                topic_id=topic_id,
                reviewer_handle=args["reviewer_handle"],
                routing_reason=args.get("routing_reason", ""),
            )
            await s.commit()
            return _text(f"已把验收卡递给 {args['reviewer_handle']}。")

    @tool(
        "return_conclusion",
        "子话题做完了，把结论回流到父话题。传入结论摘要。仅在当前话题是子话题时有效。",
        {"conclusion": str},
    )
    async def return_conclusion(args: dict[str, Any]) -> dict[str, Any]:
        async with session_factory() as s:
            try:
                await TopicService(s).return_conclusion(
                    subtopic_id=topic_id, conclusion=args["conclusion"]
                )
            except Exception as exc:  # no parent / not found
                return _text(f"无法回流结论：{exc}")
            await s.commit()
            return _text("已把结论回流到父话题。")

    @tool(
        "pin_milestone",
        "把一个关键节点钉成里程碑（立项过了/中期评审/结题等），它会排进日历并冒泡到"
        "机构看板。传入标题，可选 due_date（ISO 日期字符串）和描述。",
        {"title": str, "due_date": str, "description": str},
    )
    async def pin_milestone(args: dict[str, Any]) -> dict[str, Any]:
        due_raw = (args.get("due_date") or "").strip()
        due = None
        if due_raw:
            try:
                due = datetime.fromisoformat(due_raw.replace("Z", "+00:00"))
            except ValueError:
                due = None
        async with session_factory() as s:
            await MilestoneRepository(s).add(
                project_id=project_id,
                title=args["title"],
                description=args.get("description", ""),
                due_date=due,
                source_topic_id=topic_id,
                auto_pinned=True,
            )
            await s.commit()
            return _text(f"已把「{args['title']}」钉为里程碑。")

    @tool(
        "write_file",
        "把产出写成项目仓库里的文件（代码/报告/数据都行，spec：所有产出都是 git）。"
        "每次写入都会提交一次版本，可在 Git/文件面板查看。传入相对路径和完整内容。",
        {"path": str, "content": str},
    )
    async def write_file(args: dict[str, Any]) -> dict[str, Any]:
        from app.domain.workspace import service as ws

        try:
            res = ws.write_file(project_id, path=args["path"], content=args["content"])
        except Exception as exc:
            return _text(f"写文件失败：{exc}")
        return _text(f"已写入 {res['path']}（{res['bytes']} 字节）并提交版本。")

    @tool(
        "record_decision",
        "记录一条关键决策到项目的『决策记录』（每条会关联到当前话题，可追溯来源）。"
        "传入一句话的决策内容。",
        {"decision": str},
    )
    async def record_decision(args: dict[str, Any]) -> dict[str, Any]:
        async with session_factory() as s:
            await BlockRepository(s).add(
                project_id=project_id,
                topic_id=topic_id,
                author=CHEESE_AUTHOR,
                author_type=AuthorType.ai,
                content=args["decision"],
                kind=BlockKind.decision,
                refs=[str(topic_id)],
            )
            await s.commit()
            return _text("已记入决策记录。")

    server = create_sdk_mcp_server(
        name=SERVER_NAME,
        tools=[
            create_subtopic,
            update_doc,
            remember,
            notify,
            request_accept,
            return_conclusion,
            pin_milestone,
            write_file,
            record_decision,
        ],
    )
    return server
