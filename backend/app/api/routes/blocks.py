"""Block routes — editing a message, emoji reactions (Slack semantics), and
skipping a question asked with quick replies or taking back an answer to one."""

import uuid
from datetime import UTC, datetime
from functools import partial
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.auth import ActorResolverDep
from app.api.deps import get_broker, get_chat_service, get_work_runner
from app.api.message_effects import announce_edited_message
from app.api.request_deps import DbSession
from app.api.response import ok, typed_response
from app.api.write_access import ROUTE_DECIDES
from app.core.errors import (
    AuthenticationRequiredError,
    NotFoundError,
    ValidationError,
)
from app.core.sentences import say
from app.domain.agent.announce import finish_answering
from app.domain.agent.ask import (
    publish_answered,
    skip,
    withdraw,
    withdraw_question_by_asker,
)
from app.domain.agent.chat import ChatService
from app.domain.agent.realtime.broker import InProcessBroker
from app.domain.agent.runtime import AgentWorkRunner
from app.domain.agent.turn.intake.rewrite import text_as_sent
from app.domain.block.answer_groups import (
    answers_from,
    group_of,
    record_answers,
    summary_line,
)
from app.domain.block.editing import edit_message
from app.domain.block.repositories import BlockRepository

# `BlockKind` comes from the schemas module, not `block.models`: a route may not
# import a domain's models directly (C2 in `.importlinter`), and `schemas` is
# the module that re-exports the two enums a route writes with.
from app.domain.block.schemas import BlockKind, BlockOut, ReactionToggleIn
from app.domain.conversation.services import room_of
from app.domain.delivery.agent import dispatch_pending, record_task_instruction
from app.domain.event_author import AuthorType

router = APIRouter(prefix="/blocks", tags=["blocks"])


class MessageEditIn(BaseModel):
    # The same ceiling as sending a message (`ChatMessageIn`).
    content: str = Field(min_length=1, max_length=100000)


class AnswerGroupResult(BaseModel):
    """整组提交回了什么：落下的那一条消息，和几道题改完之后的样子。

    和 `block_added` / `block_updated` 两条实时帧带的是同一份东西，发起的页面拿它
    直接换掉，不必再取一次。"""

    block: dict
    questions: list[dict] = []


class WithdrawnAnswer(BaseModel):
    """跳过一道题、或撤回对它的回答（跳过），回的是这道题改完之后的样子。

    和 `block_updated` 那条实时帧带的是同一份东西，发起的页面拿它直接替换这道题，
    不必再取一次。"""

    id: str
    answer_log: list[dict] = []


@router.patch("/{block_id}", dependencies=[ROUTE_DECIDES])
async def edit_block(
    block_id: uuid.UUID,
    body: MessageEditIn,
    db: DbSession,
    resolver: ActorResolverDep,
    broker: Annotated[InProcessBroker, Depends(get_broker)],
    chat: Annotated[ChatService, Depends(get_chat_service)],
    runner: Annotated[AgentWorkRunner, Depends(get_work_runner)],
) -> dict:
    """Edit a message you sent, in a room or on a card: a person with their
    session, an agent with its room credential — one route and one rule for
    both (`domain/block/editing`). Whoever is watching gets the edited message
    as a `block_updated` frame."""
    block = await BlockRepository(db).get(block_id)
    if block is None:
        raise NotFoundError("Message not found")
    actor = await resolver.resolve(
        topic_id=block.conversation_id, project_id=block.project_id
    )
    await resolver.authorize_topic(
        actor,
        project_id=block.project_id,
        topic_id=await room_of(db, block.conversation_id),
        enforce=True,
    )
    payload = await edit_message(
        db,
        broker.publish,
        block_id,
        normalize_text=text_as_sent,
        notify_mentions=announce_edited_message,
        editor=actor.handle,
        content=body.content,
        notify=chat,
        dispatch=partial(
            dispatch_pending, chat.session_factory, chat=chat, runner=runner
        ),
        record_instruction=record_task_instruction,
    )
    return ok(payload)


@router.post("/{block_id}/reactions", dependencies=[ROUTE_DECIDES])
async def toggle_reaction(
    block_id: uuid.UUID,
    body: ReactionToggleIn,
    db: DbSession,
    resolver: ActorResolverDep,
    broker: Annotated[InProcessBroker, Depends(get_broker)],
) -> dict:
    """Slack-style toggle: add the (emoji, caller) reaction, or remove it when
    the same caller reacts with the same emoji again. Broadcasts the block's
    fresh aggregate to the topic channel so every open client updates live.

    A reaction is a write somebody does in a room, so it takes what any write
    there takes: a verified caller who can reach the block's room. The
    credential is judged against that room, because an agent's is bound to its
    project and answers only for a route that names one.

    The reaction lands under the caller the credential names — a session
    token's person or an agent credential's seat — and nobody else: the body
    carries no author. So the global sandbox token alone (the trusted dev
    credential, gate-only, naming nobody) gets a 401 here, since there is no one
    to put the reaction under."""
    repo = BlockRepository(db)
    block = await repo.get(block_id)
    if block is None:
        raise NotFoundError("Block not found")
    actor = await resolver.resolve(
        topic_id=block.conversation_id, project_id=block.project_id
    )
    await resolver.authorize_topic(
        actor,
        project_id=block.project_id,
        topic_id=await room_of(db, block.conversation_id),
        enforce=True,
    )
    if not actor.authenticated:
        raise AuthenticationRequiredError("Login required to react")
    added = await repo.toggle_reaction(block_id, body.emoji, actor.handle)
    reactions = await repo.reactions_for_block(block_id)
    # Commit before broadcasting so a client that refetches on the frame
    # never reads stale state.
    await db.commit()
    await broker.publish(
        str(block.conversation_id),
        {"type": "reaction", "block_id": str(block_id), "reactions": reactions},
    )
    return ok({"toggled": "added" if added else "removed", "reactions": reactions})


@router.post(
    "/{block_id}/skip",
    dependencies=[ROUTE_DECIDES],
    **typed_response(WithdrawnAnswer),
)
async def skip_question(
    block_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    broker: Annotated[InProcessBroker, Depends(get_broker)],
    chat: Annotated[ChatService, Depends(get_chat_service)],
    runner: Annotated[AgentWorkRunner, Depends(get_work_runner)],
) -> dict:
    """Skip a question asked with quick replies: you will not answer it.

    Only someone who may answer it can (`block.asked.may_answer`, the rule an
    answer by option or in words is held to); anyone else is refused, and so
    is a question that already has its answer. Nothing is said in the room.
    The question stops waiting, as an answer would make it: it leaves that
    person's 待你回答 list, and its notice is settled. The agent that asked
    runs another turn told that the question was skipped, so it stops waiting
    for an answer that is not coming. ``DELETE /blocks/{block_id}/answer`` undoes it."""
    return await _respond(block_id, db, resolver, broker, chat, runner, act=skip)


@router.delete(
    "/{block_id}/answer",
    dependencies=[ROUTE_DECIDES],
    **typed_response(WithdrawnAnswer),
)
async def withdraw_answer(
    block_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    broker: Annotated[InProcessBroker, Depends(get_broker)],
    chat: Annotated[ChatService, Depends(get_chat_service)],
    runner: Annotated[AgentWorkRunner, Depends(get_work_runner)],
) -> dict:
    """Take back your own answer to a question asked with quick replies, or
    your skip of it, so the question is unanswered again and its options come
    back for everyone.

    ``block_id`` is the question, not the answer: an answer is an ordinary
    message, and what is withdrawn is the line it left on the question
    (`domain/block/questions`). Only whoever answered it can — anyone else gets
    a refusal, not a way to erase somebody's answer. The message the answerer
    sent stays in the room; its words are not what this changes.

    Whoever was watching gets the question as a `block_updated` frame, and the
    agent that asked it runs another turn, because the turn it had been going
    on was decided on an answer that no longer counts."""
    return await _respond(block_id, db, resolver, broker, chat, runner, act=withdraw)


@router.delete(
    "/{block_id}/question",
    dependencies=[ROUTE_DECIDES],
    **typed_response(WithdrawnAnswer),
)
async def withdraw_own_question(
    block_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    broker: Annotated[InProcessBroker, Depends(get_broker)],
) -> dict:
    """Take back your own question, before anyone answers it.

    Only the asking agent may (`block.questions.withdraw_question` checks the
    block's author) and only while nobody has answered: an answer belongs to
    whoever gave it, and taking that back is their own route
    (``DELETE /blocks/{block_id}/answer``). Nothing is said in the room — the
    question's own message stays where it was — and no turn is run: the agent
    is the one acting, so there is nobody to tell.

    The question stops waiting: it leaves the 待你回答 lists, its notice is
    settled, and the channel's yellow light goes out. Where it is drawn its
    options are hidden and it says it was taken back."""
    repo = BlockRepository(db)
    question = await repo.get(block_id)
    if question is None:
        raise NotFoundError(say("cursorMessageNotFound"))
    actor = await resolver.resolve(
        topic_id=question.conversation_id, project_id=question.project_id
    )
    room_id = await room_of(db, question.conversation_id)
    await resolver.authorize_topic(
        actor,
        project_id=question.project_id,
        topic_id=room_id,
        enforce=True,
    )
    if not actor.authenticated:
        raise AuthenticationRequiredError(say("signInRequired"))
    await withdraw_question_by_asker(
        db, block=question, room_id=room_id, handle=actor.handle
    )
    payload = BlockOut.model_validate(question).model_dump(mode="json")
    payload["reactions"] = await repo.reactions_for_block(block_id)
    await db.commit()
    await broker.publish(
        str(question.conversation_id), {"type": "block_updated", "block": payload}
    )
    return ok(payload)


@router.post(
    "/{block_id}/answers",
    dependencies=[ROUTE_DECIDES],
    **typed_response(AnswerGroupResult),
)
async def answer_group(
    block_id: uuid.UUID,
    body: dict,
    db: DbSession,
    resolver: ActorResolverDep,
    broker: Annotated[InProcessBroker, Depends(get_broker)],
    chat: Annotated[ChatService, Depends(get_chat_service)],
    runner: Annotated[AgentWorkRunner, Depends(get_work_runner)],
) -> dict:
    """一次答掉整组题：`block_id` 是其中任意一道，`body["answers"]` 是题 id 到回答。

    回答是 `{"options": ["..."], "note": "..."}`，两个都空表示这题没答——允许留白。
    提交后房间里只多一条消息，正文是逐题的答案；提问的那位读的就是它。

    整组一次写、一次结，任何一道题不合规（不在等他、已经有人答过、他跳过的那题）
    就整组拒绝，不留半组答案。
    """
    return await _answer_group(block_id, body, db, resolver, broker, chat, runner)


async def _answer_group(block_id, body, db, resolver, broker, chat, runner) -> dict:
    repo = BlockRepository(db)
    question = await repo.get(block_id)
    if question is None:
        raise NotFoundError(say("cursorMessageNotFound"))
    actor = await resolver.resolve(
        topic_id=question.conversation_id, project_id=question.project_id
    )
    room_id = await room_of(db, question.conversation_id)
    await resolver.authorize_topic(
        actor, project_id=question.project_id, topic_id=room_id, enforce=True
    )
    if not actor.authenticated:
        raise AuthenticationRequiredError(say("signInRequired"))

    questions = await group_of(db, question)
    answers = answers_from(body)
    known = {q.id for q in questions}
    unknown = [str(qid) for qid in answers if qid not in known]
    if unknown:
        raise ValidationError(say("askAnswerNotInGroup", ids=", ".join(unknown)))

    touched = record_answers(questions, answers, by=actor.handle, at=datetime.now(UTC))
    note = summary_line(questions, answers)
    reply = await repo.add(
        project_id=question.project_id,
        conversation_id=question.conversation_id,
        author=actor.handle,
        author_type=AuthorType.participant,
        content=note,
        kind=BlockKind.message,
    )
    # 这条消息自己就是那几道题的答案：把它指给提问的席位，并结掉那一组通知。
    recipient: dict = {"handle": None, "instance_id": None, "mentioned": False}
    await finish_answering(
        db, reply=reply, questions=touched, said=note, recipient=recipient
    )
    payload = BlockOut.model_validate(reply).model_dump(mode="json")
    payload["reactions"] = await repo.reactions_for_block(reply.id)
    updated = [BlockOut.model_validate(q).model_dump(mode="json") for q in questions]
    await db.commit()
    await broker.publish(
        str(question.conversation_id), {"type": "block_added", "block": payload}
    )
    for block in updated:
        await broker.publish(
            str(question.conversation_id), {"type": "block_updated", "block": block}
        )
    await publish_answered(question.conversation_id, questions)
    await dispatch_pending(chat.session_factory, chat=chat, runner=runner)
    return ok({"block": payload, "questions": updated})


async def _respond(block_id, db, resolver, broker, chat, runner, *, act) -> dict:
    """Run ``act`` (`agent.ask.skip` / `withdraw`) as the caller on the question
    ``block_id``, then show the room the question and run the asking agent."""
    repo = BlockRepository(db)
    question = await repo.get(block_id)
    if question is None:
        raise NotFoundError(say("cursorMessageNotFound"))
    actor = await resolver.resolve(
        topic_id=question.conversation_id, project_id=question.project_id
    )
    room_id = await room_of(db, question.conversation_id)
    await resolver.authorize_topic(
        actor,
        project_id=question.project_id,
        topic_id=room_id,
        enforce=True,
    )
    # A credential that names nobody (the gate-only sandbox token) has no answer
    # to take back: refuse it as what it is rather than as the wrong person.
    if not actor.authenticated:
        raise AuthenticationRequiredError(say("signInRequired"))
    await act(
        db,
        block=question,
        project_id=question.project_id,
        room_id=room_id,
        handle=actor.handle,
    )
    payload = BlockOut.model_validate(question).model_dump(mode="json")
    payload["reactions"] = await repo.reactions_for_block(block_id)
    # Commit before broadcasting, as reactions do: a client that refetches on
    # the frame must not read stale state.
    await db.commit()
    await broker.publish(
        str(question.conversation_id), {"type": "block_updated", "block": payload}
    )
    # The asking agent's turn is recorded with the change (`agent.ask`); this
    # is what runs it.
    await dispatch_pending(chat.session_factory, chat=chat, runner=runner)
    return ok(payload)
