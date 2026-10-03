"""The agent-created question group and its atomic human submission.

A group settles through `POST /topics/asks/{group_id}/settle`. A single option
question an older ask left behind keeps its own route, `POST
/topics/blocks/{block_id}/answers`, and lives here too: it writes the same
versioned answer log and the same kind of wake.
"""

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
from app.core.errors import ForbiddenError, NotFoundError, ValidationError
from app.domain.agent.announce import notify_question
from app.domain.agent.ask_origin import ask_origin
from app.domain.agent.chat import ChatService
from app.domain.agent.runtime import AgentWorkRunner
from app.domain.block.answer_submission import add_answer_wake, submit_answer
from app.domain.block.ask_groups import AskGroups, parse_questions, required_text
from app.domain.block.notice_text import say
from app.domain.delivery.agent import dispatch_pending
from app.domain.delivery.ask_receipts import ask_receipt
from app.domain.delivery.ask_wake import (
    record_ask_wake,
    record_single_answer_wake,
    single_answer_wake,
)
from app.domain.delivery.ledger import settle as settle_delivery
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
    actor = await resolver.resolve(project_id=place.project_id, topic_id=place.room_id)
    if not actor.authenticated:
        raise ForbiddenError(say("askSignIn"))
    await resolver.authorize_topic(
        actor, project_id=place.project_id, topic_id=place.room_id, enforce=True
    )
    return actor


@router.post("/{topic_id}/asks")
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
        raise ForbiddenError(say("askNativeSessionOnly"))
    questions = parse_questions(body)
    origin = await ask_origin(chat, place.project_id, place.room_id, actor.handle)
    if origin is None:
        raise ForbiddenError(say("askOriginUnknown"))
    group_id = body.get("ask_group")
    if group_id is None:
        group_id = str(uuid.uuid4())
    group_id = required_text(group_id, "ask_group")
    if len(group_id) > 128:
        raise ValidationError(say("askGroupIdTooLong"))
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
        raise ValidationError(say("askTopicIdInvalid")) from exc
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
            "answer_to": str(rows[0].id),
            "v": settlement["v"],
            "block_ids": [str(row.id) for row in rows],
        },
    )
    meta: dict = {
        "answer_group": group_id,
        "answer_to": str(rows[0].id),
        "delivery_event_id": str(event_id),
    }
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
    # 结掉等回答的那条通知：人已交卷，它不该还躺着说「待你回答」。整组只发过一条
    # 通知（`notify_question` 收 `rows[0]`），这里就结那一条，记下生效答案。
    first_log = (rows[0].meta or {}).get("answer_log") or []
    await settle_delivery(
        db,
        rows[0].id,
        {
            "answered": (
                first_log[-1].get("option")
                or first_log[-1].get("note")
                or first_log[-1].get("kind")
            )
        }
        if first_log
        else {},
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


@router.post("/blocks/{block_id}/answers")
async def submit_versioned_answer(
    block_id: uuid.UUID,
    body: dict,
    db: DbSession,
    resolver: ActorResolverDep,
    chat: Annotated[ChatService, Depends(get_chat_service)],
    runner: Annotated[AgentWorkRunner, Depends(get_work_runner)],
) -> dict:
    """Answer a single option question with an option, note or rejection.

    Submit client_op_id and expect_version with kind (option / note / reject),
    plus option or note as appropriate. The answer, timeline wake and delivery
    are authorized and persisted atomically.

    Authorization precedes replay lookup. Block owns answer rules and timeline
    writes; delivery owns addressing and intent. Commit before publication or
    dispatch. Calling receive_message here would duplicate the durable wake.

    This is the versioned answer path (`answer_log`): `POST
    /topics/blocks/{block_id}/answers`. A question group settles atomically
    through `POST /topics/asks/{group_id}/settle` instead — one member at a
    time is refused here. Rows an older question left behind keep their shape
    and are answered through this same route.
    """
    blk = await BlockRepository(db).get(block_id)
    if blk is None:
        raise NotFoundError(say("optionQuestionNotFound"))

    # Identity comes only from the credential; the body never names the caller.
    actor = await resolver.resolve(topic_id=blk.topic_id, project_id=blk.project_id)
    await resolver.authorize_topic(
        actor, project_id=blk.project_id, topic_id=blk.topic_id
    )
    author = actor.handle

    answer = await submit_answer(db, block_id=block_id, author=author, body=body)
    if answer.replay:
        return ok(answer.updated)
    wake = await single_answer_wake(
        db,
        project_id=answer.project_id,
        topic_id=answer.topic_id,
        block_id=block_id,
        asked_by=answer.asked_by,
        entry=answer.entry,
    )
    answer_out = await add_answer_wake(
        db,
        project_id=answer.project_id,
        topic_id=answer.topic_id,
        author=author,
        content=wake.content,
        meta=wake.meta,
    )
    await record_single_answer_wake(
        db,
        topic_id=answer.topic_id,
        block_id=block_id,
        version=answer.entry["v"],
        wake=wake,
    )
    # Publish only committed state; failed publication is recoverable by GET.
    await db.commit()
    await get_broker().publish(
        str(answer.topic_id), {"type": "block_updated", "block": answer.updated}
    )
    await get_broker().publish(
        str(blk.topic_id), {"type": "block_added", "block": answer_out}
    )
    # Committed pending intent survives a crash before dispatch.
    await dispatch_pending(chat.session_factory, chat=chat, runner=runner)
    return ok(answer.updated)
