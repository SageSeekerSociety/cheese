"""What a document's 芝士 may look up in its project while it answers
(`doc_agent`): the project searched, the team's memory read, an attachment read.

They read and change nothing. The search reaches the rooms the person who asked
may read and no others: the answer is posted where that person's question was,
and a session that searched as the agent could quote a room the person cannot
open.

``SPECS`` is what the session is told about them (name, description, JSON
schema), beside the document's own two tools.
"""

import json
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolver
from app.api.routes.project_context import search_everything
from app.core.errors import NotFoundError, ValidationError
from app.domain.identity.actor import Actor
from app.domain.library import service as library
from app.domain.memory.files import INDEX_NAME, MemoryFileError, MemoryFileScope
from app.domain.memory.files_store import MemoryFileStore
from app.domain.search import bm25
from app.domain.topic.models import Topic

#: How many of each kind of hit a search lists.
SEARCH_LIMIT = 8
#: The most of one attachment's text handed back.
ATTACHMENT_CHARS = 30_000

SPECS: list[dict] = [
    {
        "name": "search_project",
        "description": (
            "按关键词搜这个项目：频道名、消息、文档段落、评论、周报、任务卡、资料库文件名、"
            "产物。只搜提问人能看的频道。返回每条命中在哪个频道、谁写的和一段摘录。"
            "每个词都要命中，没找到就换个说法或少几个词再搜。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "关键词"}},
            "required": ["query"],
        },
    },
    {
        "name": "read_memory",
        "description": (
            "读项目共享记忆里的一条正文：给出索引里那一条链接的文件名（如 deploy.md）。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "记忆的文件名"}},
            "required": ["name"],
        },
    },
    {
        "name": "read_attachment",
        "description": (
            "读消息里附带的文件或资料库里的文件（消息里写着的地址，资料库的以 library/ "
            "开头）。只读得了文本；二进制的文件只返回大小。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "文件的地址"}},
            "required": ["path"],
        },
    },
]

NAMES = frozenset(spec["name"] for spec in SPECS)


async def search_project(
    db: AsyncSession, project_id: uuid.UUID, asker: str, query: str
) -> str:
    q = query.strip()
    if not q:
        return "要搜的关键词不能是空的。"
    rooms = list(await db.scalars(select(Topic).where(Topic.project_id == project_id)))
    readable_ids = await ActorResolver(
        session=db, bearer=None, cheese_token=""
    ).readable_topic_ids(
        Actor(handle=asker, user_id=None, via="token"),
        project_id=project_id,
        topics=rooms,
    )
    readable = {room.id: room for room in rooms if room.id in readable_ids}
    terms = bm25.words(q)
    await bm25.serial_scans(db)
    hits = await search_everything(db, project_id, q, terms, readable, SEARCH_LIMIT)
    if not any(hits.values()):
        return "没有找到。换个说法或少几个词再搜。"
    return json.dumps(hits, ensure_ascii=False)


async def read_memory(db: AsyncSession, project_id: uuid.UUID, name: str) -> str:
    path = name.strip().removeprefix("team/")
    try:
        row = await MemoryFileStore(db).get(
            project_id, MemoryFileScope.team, None, path
        )
    except MemoryFileError as exc:
        return str(exc)
    if row is None or path == INDEX_NAME:
        return "没有这一条记忆。"
    return row.content


def read_attachment(project_id: uuid.UUID, room_id: uuid.UUID, path: str) -> str:
    try:
        read = library.read_attachment_text(project_id, room_id, path.strip())
    except (ValidationError, NotFoundError, ValueError, OSError):
        return "没有这个文件。"
    content = read.get("content")
    if content is None:
        return f"这个文件不是文本，读不了（{read.get('bytes')} 字节）。"
    if len(content) > ATTACHMENT_CHARS:
        return content[:ATTACHMENT_CHARS] + f"\n\n（只给了前 {ATTACHMENT_CHARS} 字）"
    return content


async def run(
    db: AsyncSession,
    name: str,
    arguments: dict,
    *,
    project_id: uuid.UUID,
    room_id: uuid.UUID,
    asker: str,
) -> str:
    """One call, for ``asker``; what the session reads back."""
    if name == "search_project":
        return await search_project(
            db, project_id, asker, str(arguments.get("query") or "")
        )
    if name == "read_memory":
        return await read_memory(db, project_id, str(arguments.get("name") or ""))
    return read_attachment(project_id, room_id, str(arguments.get("path") or ""))
