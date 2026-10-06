"""A room's tasks, and what is done to a task.

`GET|POST /topics/{room}/tasks` list a room's tasks and create one, and the
`task-proposals` routes are what an AI teammate proposes there: those are the
room's. Everything about one task is addressed by the task's own conversation
id — `GET|PATCH /topics/{task}/task` (the task, handing it over), `POST
/topics/{task}/start` and `/close`. Talking in a task, naming it and reading its
document go through the same routes as a room's (`/messages`, `/title`,
`/document`), with the task's id.

`_actor_in_place`, `DbSession` and the repositories come from topics.py, so the
import guard and `.importlinter` see no new edges.
"""

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.api.auth import ActorResolverDep
from app.api.deps import get_chat_service, get_work_runner
from app.api.place import task_conversation
from app.api.response import ok, page
from app.api.routes.topics import (
    AcceptCardRepository,
    BlockRepository,
    DbSession,
    ProjectRepository,
    UsageRepository,
)
from app.api.task_instructions import dispatch, proposal_source_text, tell_task
from app.core.errors import ForbiddenError, NotFoundError, ValidationError
from app.core.sentences import say
from app.domain.agent.announce import announce
from app.domain.agent.chat import ChatService
from app.domain.agent.harness.prompt import task_opening_prompt, task_started_prompt
from app.domain.agent.liveness import running_tasks
from app.domain.agent.runtime import announce_stale
from app.domain.block.schemas import BlockOut
from app.domain.idempotency import store as idem
from app.domain.idempotency.keys import action_key
from app.domain.mentions import canonicalize_refs
from app.domain.room_task import binding, presentation
from app.domain.room_task.proposals import ProposalState, TaskProposals
from app.domain.room_task.schemas import TaskOut
from app.domain.room_task.services import TaskService
from app.domain.topic import naming
from app.domain.topic.schemas import ConclusionIn
from app.domain.topic.services import TopicService
from app.domain.topic_membership.services import TopicMemberService

router = APIRouter(prefix="/topics", tags=["topics"])


@router.get("/{topic_id}/tasks")
async def list_room_tasks(
    topic_id: uuid.UUID,
    db: DbSession,
    chat: Annotated[ChatService, Depends(get_chat_service)],
    resolver: ActorResolverDep,
    limit: Annotated[int | None, Query(ge=1, le=500)] = None,
) -> dict:
    """This room's threads — every piece of work in it, each with its own
    conversation.

    The room's own line is `/blocks` beside this; nothing appears in both, and
    together they are everything said in the room. That separation is the whole
    reason a task no longer needs a room of its own: the thread is a key on the
    block, not a second row in `topics`.

    Authorized exactly like `/blocks`, and for the same reason — this carries
    conversation, so holding a topic id must not be enough to read it.

    `limit` caps EACH thread at its newest N blocks; with none, every thread
    comes back whole (agents read this to review history, and a silent default
    window would truncate them with no way to notice).

    That default is inherited from `/blocks`, and it costs more here: this fans
    out over a room's whole history of work, and a long-lived room already
    holds close to two hundred of them. No cap is imposed because an invented
    number truncates silently — the exact failure the neighbouring default
    exists to avoid — but a caller rendering a room should be passing `limit`,
    and whoever builds that view should decide what it is.
    """
    topic = await TopicService(db).get_or_404(topic_id)
    actor = await resolver.resolve(topic_id=topic_id, project_id=topic.project_id)
    await resolver.authorize_topic(
        actor, project_id=topic.project_id, topic_id=topic_id
    )
    threads = await TaskService(db).threads_for_room(topic_id, limit=limit)
    # The card each thread rides on, in ONE query for the whole room (the same
    # batched loader the project rail uses). Without it "在跑 / 闲着" and "等着
    # 人验收" are indistinguishable on screen — both are quiet — and the room
    # overview would have to ask per thread to tell them apart.
    thread_ids = [t.id for t, _ in threads]
    cards = await AcceptCardRepository(db).latest_by_task(thread_ids)
    asked = await BlockRepository(db).awaiting_an_answer(thread_ids)
    # 每条活最后一次花钱花在哪个模型上，一次查完 —— 卡上的模型是从这里算的，
    # `tasks` 上没有一列存它。
    spent = await UsageRepository(db).last_model_by_task(thread_ids)
    # 能用哪些模型，按项目算一次，整屏卡共用 —— 每张卡各算一次就是同一个答案
    # 构造几百遍。
    project = await ProjectRepository(db).get(topic.project_id)
    choices = binding.catalog(project.settings if project else None)
    running = await running_tasks(chat, db, [t for t, _ in threads])
    now = datetime.now(UTC)
    items = []
    for task, blocks in threads:
        card = cards.get(task.id)
        items.append(
            {
                **TaskOut.model_validate(task).model_dump(mode="json"),
                # 用哪个模型。花过就是它真花的那个，没花过就是它绑的那个。
                "model": presentation.card_model(
                    task, spent=spent.get(task.id), choices=choices
                ),
                # 同一个函数算的那一格，和项目级列表、和这条活自己的头一模一样。
                "presentation": presentation.task_presentation(
                    presentation.facts_for_task(
                        task,
                        card,
                        running=task.id in running,
                        awaiting_answer=task.id in asked,
                    ),
                    now=now,
                ).as_dict(),
                "blocks": [
                    BlockOut.model_validate(b).model_dump(mode="json") for b in blocks
                ],
                "card": None
                if card is None
                else {
                    "id": str(card.id),
                    "status": str(card.status),
                    "pr_number": card.pr_number,
                    "pr_url": card.pr_url,
                },
            }
        )
    return ok(page(items, len(items)))


@router.get("/{topic_id}/task")
async def get_task(
    topic_id: uuid.UUID,
    db: DbSession,
    chat: Annotated[ChatService, Depends(get_chat_service)],
    resolver: ActorResolverDep,
) -> dict:
    """The task this conversation is — the same shape a room's `/tasks` lists.
    404 for a room's own conversation. What is said in it is read like any
    conversation's (`/topics/{task}/blocks`)."""
    place, _actor, task = await task_conversation(db, resolver, topic_id)
    cards = await AcceptCardRepository(db).latest_by_task([task.id])
    out = await _task_out(db, chat, task, cards.get(task.id))
    # 用哪个模型：花过就是它真花的那个（`usage` 里这条活最后一行），一分钱没花过
    # 就是它绑的那个。和列表里显示的是同一个函数算的。
    project = await ProjectRepository(db).get(place.project_id)
    out["model"] = presentation.card_model(
        task,
        spent=(await UsageRepository(db).last_model_by_task([task.id])).get(task.id),
        choices=binding.catalog(project.settings if project else None),
    )
    card = cards.get(task.id)
    out["card"] = (
        None
        if card is None
        else {
            "id": str(card.id),
            "status": str(card.status),
            "pr_number": card.pr_number,
            "pr_url": card.pr_url,
        }
    )
    return ok(out)


async def _task_out(db, chat: ChatService, task, card=None) -> dict:
    """The task with its board cell — what every route that hands a task back
    to its page returns, so the page never holds one without it. The same
    function the list uses, so a deep link and the board cannot disagree."""
    if card is None:
        card = (await AcceptCardRepository(db).latest_by_task([task.id])).get(task.id)
    running = await running_tasks(chat, db, [task])
    out = TaskOut.model_validate(task).model_dump(mode="json")
    out["presentation"] = presentation.task_presentation(
        presentation.facts_for_task(
            task,
            card,
            running=task.id in running,
            awaiting_answer=bool(
                await BlockRepository(db).awaiting_an_answer([task.id])
            ),
        ),
        now=datetime.now(UTC),
    ).as_dict()
    return out


@router.post("/{topic_id}/close")
async def conclude_task(
    topic_id: uuid.UUID,
    body: ConclusionIn,
    db: DbSession,
    resolver: ActorResolverDep,
    chat: Annotated[ChatService, Depends(get_chat_service)],
) -> dict:
    """关闭任务：its owner or its own session says it is over. With a
    conclusion it is done — the work did not end in a merge (research, a
    decision); without one it was put down. Delivered work closes by itself
    when its change is accepted.
    """
    place, actor, task = await task_conversation(db, resolver, topic_id)
    tasks = TaskService(db)
    # The owner closes the task, or its own session (`cheese_close_task`).
    if actor.via == "cheese":
        if resolver.credential_conversation() != task.id:
            raise ForbiddenError(say("taskOwnerOnly"))
    elif actor.handle != task.owner_handle:
        raise ForbiddenError(say("taskOwnerOnly"))
    # Friendly "@名字/@话题名" → structured tokens, same as every other write
    # path that lands text a person will read.
    text = (body.conclusion or "").strip()
    conclusion = (
        await canonicalize_refs(
            db, place.project_id, text, exclude_topic_id=place.room_id
        )
        if text
        else None
    )
    if {"reporter_handle", "contributor_handles"} & body.model_fields_set:
        await tasks.set_credits(
            task,
            reporter_handle=(
                body.reporter_handle
                if "reporter_handle" in body.model_fields_set
                else task.reporter_handle
            ),
            contributor_handles=(
                body.contributor_handles
                if body.contributor_handles is not None
                else task.contributor_handles
            ),
        )
    task = await tasks.close_thread(task, conclusion=conclusion)
    # The room hears how it ended: done with a conclusion, or put down.
    await announce(
        db,
        place_id=place.room_id,
        content=(
            say("taskCompleted", title=task.title, conclusion=task.conclusion)
            if task.conclusion
            else say("taskClosed", title=task.title)
        ),
        meta={"platform": True, "action": "task_closed", "task_id": str(task.id)},
    )
    out = await _task_out(db, chat, task)
    await db.commit()
    return ok(out)


# —— 创建、开始、转交 ——————————————————————————————————————————————————


class TaskCreateIn(BaseModel):
    title: str | None = Field(default=None, max_length=300)


class TaskStartIn(BaseModel):
    #: Who reviews the task's changes, when the project names nobody by default.
    reviewer_handle: str | None = Field(default=None, max_length=64)


class TaskUpdateIn(BaseModel):
    owner_handle: str | None = Field(default=None, max_length=64)
    agent_handle: str | None = Field(default=None, max_length=64)


class TaskProposalIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    #: What the task is for, in the agent's words: the start of its document.
    #: Required: a task created from a proposal has nothing else of the
    #: discussion but this and the messages just before it.
    summary: str = Field(min_length=1, max_length=20000)


@router.post("/{topic_id}/tasks")
async def create_task(
    topic_id: uuid.UUID,
    body: TaskCreateIn,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """新建任务：a task owned by whoever creates it, empty until they say what
    it is for. Its agent starts on the owner's first message."""
    place = await TopicService(db).place_or_404(topic_id)
    actor = await resolver.resolve(
        topic_id=place.conversation_id, project_id=place.project_id
    )
    await resolver.authorize_topic(
        actor, project_id=place.project_id, topic_id=place.room_id
    )
    if not actor.authenticated or actor.via == "cheese":
        raise ForbiddenError(say("taskCreatedByPerson"))
    task = await TopicService(db).create_task(
        room_id=place.room_id, created_by=actor.handle, title=body.title
    )
    out = TaskOut.model_validate(task).model_dump(mode="json")
    await db.commit()
    await announce_stale(place.room_id, "topics")
    # A task opened in a room is a sign of where the room is going.
    naming.nudge(place.room_id, "signal")
    return ok(out)


@router.post("/{topic_id}/start")
async def start_task(
    topic_id: uuid.UUID,
    body: TaskStartIn,
    db: DbSession,
    resolver: ActorResolverDep,
    chat: Annotated[ChatService, Depends(get_chat_service)],
) -> dict:
    """开始：the owner says the task is discussed enough. From here its agent may
    change the project, and what the document says now is what its changes are
    reviewed against."""
    place, actor, task = await task_conversation(db, resolver, topic_id)
    if not actor.authenticated or actor.handle != task.owner_handle:
        raise ForbiddenError(say("taskOwnerOnly"))
    TaskService.require_open(task)
    task = await TaskService(db).start(
        task, by=actor.handle, reviewer_handle=body.reviewer_handle
    )
    await announce(
        db,
        place_id=place.room_id,
        content=say("taskStarted", actor=f"<@{actor.handle}>", title=task.title),
        meta={"platform": True, "action": "task_started", "task_id": str(task.id)},
    )
    await tell_task(db, task, task_started_prompt(title=task.title, actor=actor.handle))
    out = await _task_out(db, chat, task)
    await db.commit()
    await announce_stale(place.room_id, "topics")
    await dispatch(chat)
    return ok(out)


@router.patch("/{topic_id}/task")
async def update_task(
    topic_id: uuid.UUID,
    body: TaskUpdateIn,
    db: DbSession,
    resolver: ActorResolverDep,
    chat: Annotated[ChatService, Depends(get_chat_service)],
) -> dict:
    """转交：the owner hands the task to another member, or to another AI
    teammate."""
    place, actor, task = await task_conversation(db, resolver, topic_id)
    if not actor.authenticated or actor.handle != task.owner_handle:
        raise ForbiddenError(say("taskOwnerOnly"))
    TaskService.require_open(task)
    tasks = TaskService(db)
    if "owner_handle" in body.model_fields_set and body.owner_handle:
        if body.owner_handle not in await TopicMemberService(db).people_handles(
            place.room_id
        ):
            raise ValidationError(say("taskOwnerNotInRoom"))
        await _move_off_former_owners_computer(
            db, actor, place, task, body.owner_handle
        )
        await tasks.hand_over(task, owner_handle=body.owner_handle)
    if "agent_handle" in body.model_fields_set:
        await tasks.give_agent(task, agent_handle=body.agent_handle)
    out = await _task_out(db, chat, task)
    await db.commit()
    await announce_stale(place.room_id, "topics")
    return ok(out)


async def _move_off_former_owners_computer(db, actor, place, task, owner) -> None:
    """A person's own computer works only that person's tasks: before a task
    changes hands, it moves to a computer its new owner may use, pushing its
    work first. A push that fails leaves the task with its owner and says why."""
    from app.domain.agent.compute_configs import choice_for_owner, works_tasks_of
    from app.domain.machine import session_work
    from app.domain.machine.session_reports import devices_held_by

    room = await TopicService(db).get_or_404(place.room_id)
    project = await ProjectRepository(db).get(place.project_id)
    target = await choice_for_owner(db, room, task, project, owner)
    held = await devices_held_by(db, task.id)
    stays = not task.compute_config or target.model_dump() == task.compute_config
    if stays and all(
        [await works_tasks_of(db, device, project, owner) for device in held]
    ):
        return
    await session_work.request_choice(
        db, topic_id=place.room_id, actor=actor, choice=target, task=task
    )


def _proposal_out(proposal) -> dict:
    return {
        "id": str(proposal.id),
        "room_id": str(proposal.room_id),
        "title": proposal.title,
        "summary": proposal.summary,
        "proposed_by": proposal.proposed_by,
        "state": proposal.state.value,
        "task_id": str(proposal.task_id) if proposal.task_id else None,
        "created_at": proposal.created_at.isoformat(),
    }


async def _room_actor(db, resolver, topic_id: uuid.UUID):
    place = await TopicService(db).place_or_404(topic_id)
    actor = await resolver.resolve(
        topic_id=place.conversation_id, project_id=place.project_id
    )
    await resolver.authorize_topic(
        actor, project_id=place.project_id, topic_id=place.room_id
    )
    return place, actor


@router.post("/{topic_id}/task-proposals")
async def propose_task(
    topic_id: uuid.UUID,
    body: TaskProposalIn,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """An AI teammate proposes a task (`cheese_task`): a card in the room that a
    person creates or puts aside. A teammate never creates one itself."""
    place, actor = await _room_actor(db, resolver, topic_id)
    # A turn sent again proposes the same task again: one card, not two.
    continuation = get_work_runner().continuation_for(topic_id)
    key = (
        action_key(continuation, "task-proposal", body.title, body.summary)
        if continuation
        else None
    )
    if key is not None and not await idem.claim(
        db, key, action="task-proposal", scope_id=str(topic_id)
    ):
        prior = await idem.stored_result(db, key)
        return ok(prior or {"skipped": True})
    proposal = await TaskProposals(db).propose(
        project_id=place.project_id,
        room_id=place.room_id,
        title=body.title,
        summary=body.summary,
        proposed_by=actor.handle,
    )
    out = _proposal_out(proposal)
    if key is not None:
        await idem.record_result(db, key, out)
    await db.commit()
    await announce_stale(place.room_id, "task-proposals")
    return ok(out)


@router.get("/{topic_id}/task-proposals")
async def list_task_proposals(
    topic_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """The proposals in this room still waiting for someone."""
    place, _actor = await _room_actor(db, resolver, topic_id)
    rows = await TaskProposals(db).open_in_room(place.room_id)
    return ok([_proposal_out(p) for p in rows])


async def _open_proposal(db, room_id: uuid.UUID, proposal_id: uuid.UUID):
    proposal = await TaskProposals(db).lock(room_id, proposal_id)
    if proposal is None:
        raise NotFoundError(say("taskProposalNotFound"))
    if proposal.state != ProposalState.open:
        raise ValidationError(say("taskProposalDecided"))
    return proposal


@router.post("/{topic_id}/task-proposals/{proposal_id}/accept")
async def accept_task_proposal(
    topic_id: uuid.UUID,
    proposal_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    chat: Annotated[ChatService, Depends(get_chat_service)],
) -> dict:
    """创建任务 from a teammate's proposal: the person who creates it owns it."""
    place, actor = await _room_actor(db, resolver, topic_id)
    if not actor.authenticated or actor.via == "cheese":
        raise ForbiddenError(say("taskCreatedByPerson"))
    proposal = await _open_proposal(db, place.room_id, proposal_id)
    task = await TopicService(db).create_task(
        room_id=place.room_id, created_by=actor.handle, title=proposal.title
    )
    TaskProposals.decide(
        proposal, ProposalState.accepted, by=actor.handle, task_id=task.id
    )
    await tell_task(
        db,
        task,
        task_opening_prompt(
            title=task.title,
            owner=task.owner_handle,
            source=await proposal_source_text(db, proposal),
        ),
    )
    out = TaskOut.model_validate(task).model_dump(mode="json")
    await db.commit()
    await announce_stale(place.room_id, "task-proposals")
    await announce_stale(place.room_id, "topics")
    naming.nudge(place.room_id, "signal")
    await dispatch(chat)
    return ok(out)


@router.post("/{topic_id}/task-proposals/{proposal_id}/dismiss")
async def dismiss_task_proposal(
    topic_id: uuid.UUID,
    proposal_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """Put a teammate's proposal aside."""
    place, actor = await _room_actor(db, resolver, topic_id)
    if not actor.authenticated or actor.via == "cheese":
        raise ForbiddenError(say("taskCreatedByPerson"))
    proposal = await _open_proposal(db, place.room_id, proposal_id)
    TaskProposals.decide(proposal, ProposalState.dismissed, by=actor.handle)
    out = _proposal_out(proposal)
    await db.commit()
    await announce_stale(place.room_id, "task-proposals")
    return ok(out)
