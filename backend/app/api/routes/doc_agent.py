"""The tools of a document thread's session (``app.api.doc_agent``): the
document's own two, and what it may look up in the project
(``app.api.doc_agent_tools``).

The session presents the credential it was started with, which names the
project, the room, the agent and the thread; nothing else opens these. A tool
works only while a question of that thread is being answered, and what it
changes in the document is recorded as done for the person who asked it, and
noted under the answer's work id for whoever is waiting on the answer. A
question that may only be answered changes nothing.
"""

import uuid

from fastapi import APIRouter, Request

from app.api import doc_agent, doc_agent_tools
from app.api.response import ok
from app.api.routes.topics import DbSession
from app.core.errors import (
    AuthenticationRequiredError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    UnprocessableEntityError,
)
from app.core.redis import get_redis_client
from app.core.sandbox_auth import scoped_token_claims
from app.domain.living_doc import collab
from app.domain.topic.services import TopicService

router = APIRouter(prefix="/doc-agent", tags=["doc-agent"])


@router.post("/tools/{name}", include_in_schema=False)
async def doc_agent_tool(name: str, request: Request, db: DbSession) -> dict:
    claims = scoped_token_claims(request.headers.get("x-cheese-token") or "")
    if claims is None or not all(claims.get(k) for k in ("p", "t", "a", "r")):
        raise AuthenticationRequiredError("A document thread's credential is required")
    if name not in doc_agent.TOOL_NAMES:
        raise NotFoundError.for_resource("tool", name)
    redis = get_redis_client()
    held = await doc_agent.asking(redis, claims["r"]) if redis is not None else None
    room_id = uuid.UUID(claims["t"])
    if held is None or held.room_id != room_id or held.seat != claims["a"]:
        raise ForbiddenError("No question of this thread is being answered")
    try:
        arguments = await request.json()
    except ValueError:
        arguments = {}
    arguments = arguments if isinstance(arguments, dict) else {}

    if name in doc_agent_tools.NAMES:
        text = await doc_agent_tools.run(
            db,
            name,
            arguments,
            project_id=uuid.UUID(claims["p"]),
            room_id=room_id,
            asker=held.asker,
        )
        return ok({"text": text})
    topics = TopicService(db)
    doc = await topics.doc_of_room(room_id)
    if name == "read_document":
        return ok(
            {
                "text": doc.content
                if doc is not None and doc.content
                else "（文档还是空的）"
            }
        )

    edits = [
        {"old": str(e.get("old") or ""), "new": str(e.get("new") or "")}
        for e in arguments.get("edits") or []
        if isinstance(e, dict)
    ]
    if not held.may_edit:
        return ok({"text": "这次只回答，不改文档；文档没有变。"})
    if not edits:
        return ok({"text": "没有给出要改的地方，文档没有变。"})
    if doc is None:
        return ok({"text": "这个话题还没有实况文档，没有可改的文字。"})
    place = await topics.place_or_404(room_id)
    topics.require_doc_writable(place)
    await db.commit()
    try:
        result = await collab.edit(
            room_id,
            edits=edits,
            actor=held.seat,
            requested_by=held.asker,
            mode="direct",
            reason=str(arguments.get("reason") or "") or None,
        )
    except (UnprocessableEntityError, ConflictError) as exc:
        return ok(
            {
                "text": f"一处都没改，文档没有变：{str(exc).rstrip('。')}。"
                "先用 read_document 读最新的全文，照读到的原文改好再试。"
            }
        )
    if redis is not None:
        await doc_agent.record_edits(redis, held.work, edits)
    version = ((result.get("stored") or {}).get("data") or {}).get("doc_version")
    done = f"已改了 {len(result.get('edits') or edits)} 处"
    return ok({"text": f"{done}（第 {version} 版）。" if version else f"{done}。"})
