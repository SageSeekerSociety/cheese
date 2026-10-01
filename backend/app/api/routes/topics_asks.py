"""The agent-created question group and its atomic human submission."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.auth import ActorResolverDep
from app.api.deps import get_broker, get_chat_service, get_work_runner
from app.api.response import ok
from app.api.routes.topics import (
    AuthorType,
    BlockKind,
    BlockOut,
    BlockRepository,
    DbSession,
)
from app.core.errors import ForbiddenError, ValidationError
from app.domain.agent.announce import notify_question
from app.domain.agent.chat import ChatService
from app.domain.agent.runtime import AgentWorkRunner
from app.domain.block.ask_groups import AskGroups, parse_questions, required_text
from app.domain.delivery.agent import dispatch_pending
from app.domain.delivery.ask_receipts import ask_receipt
from app.domain.delivery.ask_wake import record_ask_wake
from app.domain.topic.services import TopicService
from app.domain.topic_membership.services import TopicMemberService

router = APIRouter(prefix="/topics", tags=["topics"])


async def group_data(db, rows, *, operation=None):
    first = rows[0]
    group = first.meta["ask_group"]
    # A POST replay confirms that operation, while blocks remain current.
    # GET has no operation argument and projects only the latest settlement.
    settlement = operation or first.meta.get("group_settle")
    receipt = None
    if settlement:
        receipt = await ask_receipt(
            db,
            event_id=settlement["delivery_event_id"],
            project_id=first.project_id,
            topic_id=first.topic_id,
            recipient=first.meta["ask_origin"]["recipient_handle"],
        )
    return {
        "group": {
            "topic_id": str(first.topic_id),
            "asked_by": group["asked_by"],
            "id": group["id"],
            "members": group["members"],
            "total": group["total"],
        },
        "blocks": [
            BlockOut.model_validate(row).model_dump(mode="json") for row in rows
        ],
        "settlement": settlement,
        "receipt": receipt,
    }


async def authorize_group(resolver, place):
    actor = await resolver.resolve(
        fallback_handle=None, project_id=place.project_id, topic_id=place.room_id
    )
    if not actor.authenticated:
        raise ForbiddenError("要登录才能读取或提交问题组")
    await resolver.authorize_topic(
        actor, project_id=place.project_id, topic_id=place.room_id, enforce=True
    )
    return actor


@router.post("/{topic_id}/ask")
async def create_ask_group(
    topic_id: uuid.UUID,
    body: dict,
    db: DbSession,
    resolver: ActorResolverDep,
    chat: Annotated[ChatService, Depends(get_chat_service)],
):
    place = await TopicService(db).place_or_404(topic_id)
    actor = await authorize_group(resolver, place)
    if actor.via != "cheese" or not await TopicMemberService(db).holds_an_agent_seat(
        place.room, actor.handle
    ):
        raise ForbiddenError("只有真实 agent 会话可以创建问题")
    questions = parse_questions(body)
    origin = await chat.ask_origin(place.project_id, place.room_id, actor.handle)
    if origin is None:
        raise ForbiddenError("无法确认原生提问会话和执行区间")
    group_id = body.get("ask_group")
    if group_id is None:
        group_id = str(uuid.uuid4())
    group_id = required_text(group_id, "ask_group")
    if len(group_id) > 128:
        raise ValidationError("ask_group 最多 128 字")
    asked = origin.get("asked")
    rows = await AskGroups(db).create(
        project_id=place.project_id,
        topic_id=place.room_id,
        asked_by=actor.handle,
        group_id=group_id,
        questions=questions,
        asked=asked,
        origin=origin,
    )
    # One human notice for the group; question members persist atomically.
    await notify_question(
        db,
        place=place,
        block=rows[0],
        question=rows[0].content,
        asker=actor.handle,
        asked=asked,
    )
    data = await group_data(db, rows)
    await db.commit()
    for block in data["blocks"]:
        await get_broker().publish(
            str(place.room_id), {"type": "assistant_block", "block": block}
        )
    return ok(data)


@router.get("/asks/{group_id}")
async def read_ask_group(
    group_id: str,
    topic_id: uuid.UUID,
    asked_by: str,
    db: DbSession,
    resolver: ActorResolverDep,
):
    place = await TopicService(db).place_or_404(topic_id)
    await authorize_group(resolver, place)
    rows = await AskGroups(db).read(place.room_id, asked_by, group_id)
    return ok(await group_data(db, rows))


def settlement_line(rows, settlement, asked_by):
    lines = [
        f"<@{asked_by}> 问题组已提交：{len(settlement['answered'])}/{len(rows)} 已答"
    ]
    for index, row in enumerate(rows):
        key = str(row.id)
        answer = (row.meta.get("answer_log") or [None])[-1]
        if key in settlement["answered"] and answer:
            value = (
                answer.get("option")
                if answer["kind"] == "option"
                else "以上都不是"
                if answer["kind"] == "reject"
                else answer.get("note")
            )
            note = answer.get("note") if answer["kind"] != "note" else None
            lines.append(
                f"{index + 1}. {row.content}：{value}" + (f"（{note}）" if note else "")
            )
        elif key in settlement["later"]:
            lines.append(f"{index + 1}. {row.content}：稍后回答")
        else:
            lines.append(f"{index + 1}. {row.content}：未回答")
    return "\n".join(lines)


@router.post("/asks/{group_id}/settle")
async def settle_ask_group(
    group_id: str,
    body: dict,
    db: DbSession,
    resolver: ActorResolverDep,
    chat: Annotated[ChatService, Depends(get_chat_service)],
    runner: Annotated[AgentWorkRunner, Depends(get_work_runner)],
):
    try:
        topic_id = uuid.UUID(required_text(body.get("topic_id"), "topic_id"))
    except ValueError as exc:
        raise ValidationError("topic_id 必须是 UUID") from exc
    asked_by = required_text(body.get("asked_by"), "asked_by")
    place = await TopicService(db).place_or_404(topic_id)
    actor = await authorize_group(resolver, place)
    rows, settlement, replay = await AskGroups(db).settle(
        topic_id=place.room_id,
        asked_by=asked_by,
        group_id=group_id,
        body=body,
        author=actor.handle,
    )
    if replay:
        return ok(await group_data(db, rows, operation=settlement))
    text = settlement_line(rows, settlement, asked_by)
    event_id = uuid.UUID(settlement["delivery_event_id"])
    recipient = await record_ask_wake(
        db,
        project_id=place.project_id,
        topic_id=place.room_id,
        origin=rows[0].meta["ask_origin"],
        event_id=event_id,
        content=text,
        payload={
            "ask_group": group_id,
            "v": settlement["v"],
            "block_ids": [str(row.id) for row in rows],
        },
    )
    meta = {"answer_group": group_id, "delivery_event_id": str(event_id)}
    if recipient:
        meta["agent_recipient"] = recipient
    wake = await BlockRepository(db).add(
        project_id=place.project_id,
        topic_id=place.room_id,
        author=actor.handle,
        author_type=AuthorType.participant,
        content=text,
        kind=BlockKind.message,
        meta=meta,
    )
    data = await group_data(db, rows)
    wake_out = BlockOut.model_validate(wake).model_dump(mode="json")
    await db.commit()
    for block in data["blocks"]:
        await get_broker().publish(
            str(place.room_id), {"type": "block_updated", "block": block}
        )
    await get_broker().publish(
        str(place.room_id), {"type": "block_added", "block": wake_out}
    )
    await dispatch_pending(chat.session_factory, chat=chat, runner=runner)
    return ok(data)
