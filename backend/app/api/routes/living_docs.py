"""Living-document HTTP boundary, including replay of committed write receipts."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.auth import ActorResolverDep
from app.api.deps import get_broker, get_chat_service
from app.api.doc_identity import operation_actor
from app.api.response import ok
from app.api.routes.topics import DbSession, _actor_in_place
from app.core.errors import AuthenticationRequiredError, NotFoundError
from app.domain.agent.chat import ChatService
from app.domain.block.documents import persisted_notice
from app.domain.block.schemas import BlockOut
from app.domain.living_doc.delivery import dispatch_pending
from app.domain.living_doc.schemas import RestoreIn
from app.domain.living_doc.services import DocumentJournal, content_hash
from app.domain.mentions import canonicalize_refs
from app.domain.topic import naming
from app.domain.topic.doc_checks import living_doc_warnings
from app.domain.topic.schemas import DocEditIn
from app.domain.topic.services import TopicService

router = APIRouter(prefix="/topics", tags=["topics"])


@router.get("/{topic_id}/doc")
async def get_topic_doc(
    topic_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    topics = TopicService(db)
    place = await topics.place_or_404(topic_id)
    await _actor_in_place(resolver, place)
    doc = await topics.get_doc(topic_id)
    if doc is None:
        return ok(None)
    snapshot = BlockOut.model_validate(doc).model_dump(mode="json")
    snapshot["content_hash"] = content_hash(doc.content)
    return ok(snapshot)


@router.put("/{topic_id}/doc")
async def edit_topic_doc(
    topic_id: uuid.UUID,
    body: DocEditIn,
    db: DbSession,
    resolver: ActorResolverDep,
    chat: Annotated[ChatService, Depends(get_chat_service)],
) -> dict:
    topics = TopicService(db)
    place = await topics.place_or_404(topic_id)
    actor = await resolver.resolve(
        fallback_handle=body.author, topic_id=place.room_id, project_id=place.project_id
    )
    await resolver.authorize_topic(
        actor, project_id=place.project_id, topic_id=place.room_id
    )
    journal = DocumentJournal(db)
    operation = None
    if body.operation_id is not None:
        await resolver.authorize_topic(
            actor, project_id=place.project_id, topic_id=place.room_id, enforce=True
        )
        operation = await journal.claim(
            room_id=place.room_id,
            actor=await operation_actor(db, actor),
            action="replace",
            operation_id=body.operation_id,
            payload={
                "content": body.content,
                "expected_version": body.expected_version,
            },
        )
        if operation.receipt is not None:
            return operation.receipt
    content = await canonicalize_refs(
        db, place.project_id, body.content, exclude_topic_id=place.room_id
    )
    doc, notice = await topics.edit_doc(
        topic_id=topic_id,
        content=content,
        author=actor.handle,
        expected_version=body.expected_version,
        operation_id=body.operation_id,
    )
    return await _finish_write(
        db, place, topic_id, doc, notice, operation, body.operation_id, chat
    )


async def _finish_write(
    db, place, topic_id, doc, notice, operation, operation_id, chat
):
    content = doc.content
    snapshot = BlockOut.model_validate(doc).model_dump(mode="json")
    snapshot["content_hash"] = content_hash(doc.content)
    snapshot["operation_id"] = str(operation_id) if operation_id else None
    receipt = ok(snapshot, warnings=living_doc_warnings(content))
    if operation is not None:
        await DocumentJournal(db).finish(operation, receipt)
    await db.commit()
    if notice is not None:
        await get_broker().publish(
            str(place.room_id),
            {
                "type": "event_block",
                "block": BlockOut.model_validate(notice).model_dump(mode="json"),
            },
        )
    await dispatch_pending(db, get_broker().publish, place.room_id)
    if topic_id == place.room_id:
        naming.nudge(place.room_id, "signal")
    if notice is not None and (line := persisted_notice(notice)):
        await chat.notify_running_turn(topic_id, line, blocks=[notice.id])
    return receipt


@router.get("/{topic_id}/doc/history")
async def document_history(
    topic_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    after: int = Query(default=0, ge=0),
) -> dict:
    place = await TopicService(db).place_or_404(topic_id)
    await _actor_in_place(resolver, place)
    rows = await DocumentJournal(db).history(place.room_id, after=after)
    return ok({"versions": rows, "cursor": rows[-1]["version"] if rows else after})


@router.get("/{topic_id}/doc/refreshes")
async def document_refreshes(
    topic_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    after: int = Query(default=0, ge=0),
) -> dict:
    place = await TopicService(db).place_or_404(topic_id)
    await _actor_in_place(resolver, place)
    rows = await DocumentJournal(db).refreshes(place.room_id, after=after)
    return ok({"refreshes": rows, "cursor": rows[-1]["cursor"] if rows else after})


@router.get("/{topic_id}/doc/operations/{operation_id}")
async def document_receipt(
    topic_id: uuid.UUID,
    operation_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    action: str = Query(default="replace", pattern="^(replace|restore)$"),
) -> dict:
    place = await TopicService(db).place_or_404(topic_id)
    actor = await _actor_in_place(resolver, place)
    if not actor.authenticated:
        raise AuthenticationRequiredError("操作回执需要已认证的写入者")
    await resolver.authorize_topic(
        actor, project_id=place.project_id, topic_id=place.room_id, enforce=True
    )
    receipt = await DocumentJournal(db).receipt(
        room_id=place.room_id,
        actor=await operation_actor(db, actor),
        action=action,
        operation_id=operation_id,
    )
    if receipt is None:
        raise NotFoundError("没有这份文档操作回执")
    return receipt


@router.post("/{topic_id}/doc/restore")
async def restore_document(
    topic_id: uuid.UUID,
    body: RestoreIn,
    db: DbSession,
    resolver: ActorResolverDep,
    chat: Annotated[ChatService, Depends(get_chat_service)],
) -> dict:
    topics = TopicService(db)
    place = await topics.place_or_404(topic_id)
    actor = await _actor_in_place(resolver, place)
    if not actor.authenticated:
        raise AuthenticationRequiredError("恢复文档需要已认证的写入者")
    await resolver.authorize_topic(
        actor, project_id=place.project_id, topic_id=place.room_id, enforce=True
    )
    journal = DocumentJournal(db)
    operation = await journal.claim(
        room_id=place.room_id,
        actor=await operation_actor(db, actor),
        action="restore",
        operation_id=body.operation_id,
        payload={"version": body.version, "expected_version": body.expected_version},
    )
    if operation.receipt is not None:
        return operation.receipt
    content = await journal.version_content(place.room_id, body.version)
    if content is None:
        raise NotFoundError("没有这份历史文档版本")
    doc, notice = await topics.edit_doc(
        topic_id=topic_id,
        content=content,
        author=actor.handle,
        expected_version=body.expected_version,
        operation_id=body.operation_id,
    )
    return await _finish_write(
        db, place, topic_id, doc, notice, operation, body.operation_id, chat
    )
