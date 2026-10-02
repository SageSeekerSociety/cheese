"""What a person's 芝士 may look up, as that person.

Every tool reads what the person may already see, and none of them changes
anything; the first write tool will arrive with a confirmation step (#2285).
The session reaches them with its personal credential (``/assistant/tools``),
so each runs as the person that credential names and nobody else.

``SPECS`` is what the session is told about them, in the shape every harness's
tool table has (name, description, JSON schema).
"""

import json

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.docs_site import library
from app.domain.task.services import TaskService

SPECS: list[dict] = [
    {
        "name": "my_tasks",
        "description": "我参与的题：自己领的个人题，和所在团队领的团队题。含截止时间。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "search_docs",
        "description": (
            "按关键词检索知是的使用文档（怎么领题、建项目、验收、额度……），返回相关的"
            "页、小节、链接和摘录。关键词检索，没命中就换个说法再试。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "关键词"}},
            "required": ["query"],
        },
    },
    {
        "name": "read_doc",
        "description": (
            "读知是使用文档里的一页（search_docs 给出的 url），返回它的 Markdown 原文。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "page": {"type": "string", "description": "search_docs 给出的 url"}
            },
            "required": ["page"],
        },
    },
]

NAMES = frozenset(spec["name"] for spec in SPECS)


class UnknownTool(Exception):
    pass


def _when(task_deadline) -> str:
    return task_deadline.strftime("%Y-%m-%d") if task_deadline else "不限"


async def my_tasks(sessions: async_sessionmaker[AsyncSession], user_id: int) -> list:
    async with sessions() as session:
        tasks = await TaskService.of(session).list_joined(user_id)
    return [
        {
            "id": t.id,
            "title": t.name,
            "intro": t.intro,
            "deadline": _when(t.deadline),
            "ended": t.ended_at is not None,
        }
        for t in tasks
    ]


async def search_docs(query: str) -> list | str:
    found = await library.search(query, dev=False)
    if found is None:
        return "文档暂时读不到。"
    return [
        {"title": f.title, "heading": f.heading, "url": f.url, "excerpt": f.excerpt}
        for f in found
    ]


async def read_doc(page: str) -> str:
    try:
        text = await library.read_page(page, dev=False)
    except (library.DevDocsForbidden, ValueError):
        return "没有这一页。"
    return text or "没有这一页。"


async def run(
    name: str,
    arguments: dict,
    *,
    user_id: int,
    sessions: async_sessionmaker[AsyncSession],
) -> str:
    """One call, as ``user_id``; what the model reads back."""
    if name == "my_tasks":
        result: list | str = await my_tasks(sessions, user_id)
    elif name == "search_docs":
        result = await search_docs(str(arguments.get("query") or ""))
    elif name == "read_doc":
        result = await read_doc(str(arguments.get("page") or ""))
    else:
        raise UnknownTool(name)
    return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
