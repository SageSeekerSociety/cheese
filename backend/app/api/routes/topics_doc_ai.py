"""Document AI asks and proposals have no writable room execution context."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.auth import ActorResolverDep
from app.api.deps import get_broker, get_chat_service
from app.api.doc_identity import human_operation_actor
from app.api.response import ok
from app.api.routes.topics import DbSession, _actor_in_place
from app.core.errors import ForbiddenError, ValidationError
from app.domain.agent.chat import ChatService
from app.domain.block.doc_selection import DocumentSelections
from app.domain.block.documents import persisted_notice
from app.domain.block.schemas import BlockOut
from app.domain.doc_ai.acceptance import ProposalAcceptance
from app.domain.doc_ai.routing import project_binding
from app.domain.doc_ai.schemas import AcceptIn, RequestIn
from app.domain.doc_ai.services import DocAiService
from app.domain.living_doc.delivery import dispatch_pending
from app.domain.living_doc.services import DocumentJournal
from app.domain.topic import naming
from app.domain.topic.services import TopicService

router = APIRouter(prefix="/topics", tags=["doc-ai"])


async def human_in_room(db, resolver, topic_id):
    place = await TopicService(db).place_or_404(topic_id)
    if topic_id != place.room_id:
        raise ValidationError("文档 AI 只操作房间的 canonical 文档")
    actor = await _actor_in_place(resolver, place)
    identity = await human_operation_actor(db, actor)
    await resolver.authorize_topic(
        actor, project_id=place.project_id, topic_id=place.room_id, enforce=True
    )
    return place, actor, identity


@router.post("/{topic_id}/doc-ai/requests", status_code=202)
async def create_request(
    topic_id: uuid.UUID, body: RequestIn, db: DbSession, resolver: ActorResolverDep
) -> dict:
    place, actor, identity = await human_in_room(db, resolver, topic_id)
    journal = DocumentJournal(db)
    operation = await journal.claim(
        room_id=place.room_id,
        actor=identity,
        action="ai-request",
        operation_id=body.operation_id,
        payload=body.model_dump(mode="json", exclude={"operation_id"}),
    )
    if operation.receipt is not None:
        return operation.receipt
    selection = body.selection.model_dump(mode="json") if body.selection else None
    doc = await DocumentSelections(db).snapshot(
        room_id=place.room_id,
        document_id=body.document_id,
        base_version=body.base_version,
        selection=selection,
    )
    bound = await project_binding(db, place.room_id)
    row = await DocAiService(db).create(
        project_id=place.project_id,
        room_id=place.room_id,
        document_id=doc.id,
        actor=identity,
        kind=body.kind,
        question=body.question,
        base_version=body.base_version,
        source=doc.content,
        selection=selection,
        binding=bound,
    )
    receipt = ok({"request_id": str(row.id), "state": row.state})
    await journal.finish(operation, receipt)
    await db.commit()
    return receipt


@router.get("/{topic_id}/doc-ai/requests")
async def list_requests(
    topic_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    place, _, identity = await human_in_room(db, resolver, topic_id)
    rows = await DocAiService(db).list_requests(place.room_id, identity)
    return ok(
        {
            "requests": [
                {
                    "request_id": str(row.id),
                    "kind": row.kind,
                    "state": row.state,
                    "created_at": row.created_at.isoformat(),
                }
                for row in rows
            ]
        }
    )


@router.get("/{topic_id}/doc-ai/requests/{request_id}")
async def read_request(
    topic_id: uuid.UUID,
    request_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    place, _, identity = await human_in_room(db, resolver, topic_id)
    row = await DocAiService(db).get(place.room_id, request_id)
    if row.actor != identity:
        raise ForbiddenError("只能查看本人发起的文档 AI 请求")
    proposal_id = await DocAiService(db).proposal_id(row.id)
    return ok(
        {
            "proposal_id": str(proposal_id) if proposal_id else None,
            "request_id": str(row.id),
            "kind": row.kind,
            "state": row.state,
            "generation": row.generation,
            "answer": row.answer,
            "error": row.error,
        }
    )


@router.post("/{topic_id}/doc-ai/requests/{request_id}/cancel")
async def cancel_request(
    topic_id: uuid.UUID,
    request_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    place, _, identity = await human_in_room(db, resolver, topic_id)
    service = DocAiService(db)
    row = await service.get(place.room_id, request_id)
    if row.actor != identity:
        raise ForbiddenError("只能取消本人发起的文档 AI 请求")
    row = await service.cancel(place.room_id, request_id)
    await db.commit()
    return ok({"request_id": str(row.id), "state": row.state})


@router.get("/{topic_id}/doc-ai/proposals/{proposal_id}")
async def read_proposal(
    topic_id: uuid.UUID,
    proposal_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    place, _, _ = await human_in_room(db, resolver, topic_id)
    proposal, request = await DocAiService(db).proposal(place.room_id, proposal_id)
    return ok(
        {
            "proposal_id": str(proposal.id),
            "request_id": str(request.id),
            "revision": proposal.revision,
            "state": proposal.state,
            "document_id": str(request.document_id),
            "base_version": request.base_version,
            "selection": request.selection,
            "replacement": proposal.replacement,
            "answer": request.answer,
            "accepted_by": proposal.accepted_by,
            "accepted_version": proposal.accepted_version,
        }
    )


@router.post("/{topic_id}/doc-ai/proposals/{proposal_id}/accept")
async def accept_proposal(
    topic_id: uuid.UUID,
    proposal_id: uuid.UUID,
    body: AcceptIn,
    db: DbSession,
    resolver: ActorResolverDep,
    chat: Annotated[ChatService, Depends(get_chat_service)],
) -> dict:
    place, actor, identity = await human_in_room(db, resolver, topic_id)
    receipt, notice = await ProposalAcceptance(db).apply(
        room_id=place.room_id,
        proposal_id=proposal_id,
        body=body,
        verified_actor=identity,
        author=actor.handle,
    )
    await db.commit()
    if notice is not None:
        await get_broker().publish(
            str(place.room_id),
            {
                "type": "event_block",
                "block": BlockOut.model_validate(notice).model_dump(mode="json"),
            },
        )
        if line := persisted_notice(notice):
            await chat.notify_running_turn(place.room_id, line, blocks=[notice.id])
        naming.nudge(place.room_id, "signal")
    await dispatch_pending(db, get_broker().publish, place.room_id)
    return ok(receipt)
