"""A room's work: its threads, one card with its conversation, and closing it.

Eighth slice of `app/api/routes/topics.py` (arch review C-backend.md section
3.3), after `topics_attachments.py` (#2171), `topics_documents.py` +
`topics_preview.py` (#2175), `topics_side_routes.py` (#2190),
`topics_compute.py` (#2197), `topics_title.py` (#2201) and `topics_shown.py`
(#2207). topics.py is 2,753 lines against a 1,500-line cap that only ratchets
down.

What moves, verbatim: `GET /topics/{topic_id}/tasks` (every thread of work in
the room, each with its own conversation), `GET /topics/{topic_id}/tasks/{task_id}`
(one card, in the same shape) and the three things the room says about one --
`POST .../messages` (say it under the card; the room is woken to relay it),
`POST .../title` (name or rename the thread) and `POST .../close` (the work is
over). They are one group because they are one subject read one way: the list,
one entry, and the room's three acts on it.

What stays behind, and why. `_actor_in_place` (identity, then the room's roster
-- the shape `topics_preview.py` and `topics_shown.py` import it in in),
`DbSession`, and the five repositories these handlers read (`BlockRepository`,
`AcceptCardRepository`, `ProjectRepository`, `TaskRepository`, `UsageRepository`)
are imported from topics.py rather than from `app.domain.*.repositories`: every
one of them is still read by handlers that stay, and the guard in
`tests/unit/test_domain_import_guard.py` ratchets (route module, repository
module) pairs, so importing them from the package that already owns those edges
adds no exemption. `AuthorType` and `BlockKind` come from topics.py for the same
reason, and there it is the C2 baseline that ratchets them: importing
`app.domain.block.models` here would add an edge to `.importlinter`.

One edge does follow the code. `say_on_task` is the only reader of
`NotificationType` in topics.py (imported inside the function, where the
delivery notice that uses it is built), so moving it moves the frozen C2 edge
`app.api.routes.topics -> app.domain.notification.models` to
`app.api.routes.topics_tasks` -- the same debt with a new importer, not new debt
(the shape #2196 took for `app.domain.review`). Nothing else in the frozen block
moves, and no contract grows.

Ordering. This module sorts after `topics.py` and after every other `topics_*`
module (`_` > `.`, and `tasks` sits between `shown` and `title`), so its five
paths mount later in the route table than they did inside topics.py. Every one
of them is literal where `tasks` sits, and no route registered in between has a
parameter there, so none loses its first full match; resolving every path in the
table confirms each still reaches the handler it did before, now under
`app.api.routes.topics_tasks`. OpenAPI is byte-identical apart from the moved
paths' position in the paths object.

The new module mounts itself: `app.main._discover_routers` includes every
module-level `APIRouter` under `app.api.routes`, so the declaration below, with
the same prefix and tags, is all it takes.
"""

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.api.auth import ActorResolverDep
from app.api.deps import get_broker, get_chat_service, get_work_runner
from app.api.response import ok, page
from app.api.routes.topics import (
    AcceptCardRepository,
    BlockRepository,
    DbSession,
    ProjectRepository,
    UsageRepository,
    _actor_in_place,
)
from app.api.task_instructions import dispatch, tell_task
from app.core.errors import ForbiddenError, NotFoundError, ValidationError
from app.core.sentences import say
from app.domain.agent.announce import announce
from app.domain.agent.chat import ChatService, project_refs_text
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
    asked = await BlockRepository(db).tasks_awaiting_an_answer(thread_ids)
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


@router.get("/{topic_id}/tasks/{task_id}")
async def get_room_task(
    topic_id: uuid.UUID,
    task_id: uuid.UUID,
    db: DbSession,
    chat: Annotated[ChatService, Depends(get_chat_service)],
    resolver: ActorResolverDep,
    limit: Annotated[int | None, Query(ge=1, le=500)] = None,
    through: uuid.UUID | None = None,
) -> dict:
    """One card, with its conversation — the same shape `/tasks` lists.

    Through the room, because a card is not a place: `GET /topics/{card}` is a
    404 by construction, and the person reading a card is standing in the room
    it belongs to anyway.

    `limit` caps the timeline at its newest N blocks; with none it comes back
    whole. Same default as `/blocks` and for the same reason — an invented
    window truncates an agent reading history with no way to notice.
    `through=<block_id>` stretches that window back to the named block (a card
    opened at one of its messages); a block of another conversation is a 404.
    """
    place = await TopicService(db).place_or_404(topic_id)
    await _actor_in_place(resolver, place)
    resolver.require_task_scope(task_id)
    tasks = TaskService(db)
    task = await tasks.get(task_id)
    if task is None or task.room_id != place.room_id:
        raise NotFoundError(say("taskNotInRoom"))
    blocks = await tasks.blocks_for_thread(task_id, limit=limit, through=through)
    if blocks is None:
        raise NotFoundError(say("messageNotInTask"))
    cards = await AcceptCardRepository(db).latest_by_task([task.id])
    running = await running_tasks(chat, db, [task])
    out = TaskOut.model_validate(task).model_dump(mode="json")
    # 看板那一格，和它在列表里显示的是同一句话——同一个函数算的，所以深链接进来
    # 和从看板点进来不可能给出两种说法。
    out["presentation"] = presentation.task_presentation(
        presentation.facts_for_task(
            task,
            cards.get(task.id),
            running=task.id in running,
            awaiting_answer=bool(
                await BlockRepository(db).tasks_awaiting_an_answer([task.id])
            ),
        ),
        now=datetime.now(UTC),
    ).as_dict()
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
    out["blocks"] = [BlockOut.model_validate(b).model_dump(mode="json") for b in blocks]
    return ok(out)


class TaskMessageIn(BaseModel):
    content: str = Field(min_length=1, max_length=100000)
    #: The sender's own id for this send: a retry with the same id returns the
    #: message already stored.
    request_id: uuid.UUID | None = None
    reply_to: uuid.UUID | None = None


@router.post("/{topic_id}/tasks/{task_id}/messages")
async def say_on_task(
    topic_id: uuid.UUID,
    task_id: uuid.UUID,
    body: TaskMessageIn,
    db: DbSession,
    resolver: ActorResolverDep,
    chat: Annotated[ChatService, Depends(get_chat_service)],
) -> dict:
    """在任务里说话 —— 任务自己的对话里只有负责人和做它的 AI 队友。

    From the task's owner it is what the task's session answers: the message
    lands in the task's conversation and the session is woken, or handed it
    mid-turn. From the task's own session (its credential names this task) it is
    a publication in its own turn. Anyone else is refused: what others have to
    say about a task they say in the room.
    """
    place = await TopicService(db).place_or_404(topic_id)
    task = await TaskService(db).get(task_id)
    if task is None or task.room_id != place.room_id:
        raise NotFoundError(say("taskNotInRoom"))
    content = body.content.strip()
    if not content:
        raise ValidationError(say("messageEmpty"))
    actor = await resolver.resolve(topic_id=place.room_id, project_id=place.project_id)
    await resolver.authorize_topic(
        actor, project_id=place.project_id, topic_id=place.room_id
    )
    if actor.via == "cheese":
        if resolver.task_scope() != task.id:
            raise ForbiddenError(say("taskOwnerOnly"))
        return ok(await _publish_in_task(chat, db, place, task, body, actor.handle))
    if not actor.authenticated or actor.handle != task.owner_handle:
        raise ForbiddenError(say("taskOwnerOnly"))
    TaskService.require_open(task)
    anchor_id = await get_broker().receive_message(
        chat,
        task.id,
        author=actor.handle,
        content=content,
        reply_to=str(body.reply_to) if body.reply_to else None,
        provision_actor=actor,
        client_id=str(body.request_id) if body.request_id else None,
    )
    stored = await BlockRepository(db).get(anchor_id)
    return ok(BlockOut.model_validate(stored).model_dump(mode="json"))


async def _publish_in_task(
    chat: ChatService, db, place, task, body, author
) -> dict | None:
    """The task's session speaking in its own conversation, inside its turn."""
    content = await project_refs_text(
        db, place.project_id, place.room_id, body.content.strip()
    )
    runner = get_work_runner()
    work = runner.live_work_for_topic(task.id)
    turn_id = uuid.UUID(work["turn_id"]) if work is not None else None
    payload = await chat._persist_assistant_message(
        project_id=place.project_id,
        topic_id=place.room_id,
        task_id=task.id,
        text=content,
        turn_id=turn_id,
        reply_to=body.reply_to,
        roster=None,
        topic_refs=[],
        publish=True,
        author=author,
        publication_id=str(body.request_id) if body.request_id else None,
    )
    await get_broker().publish(
        str(task.id), {"type": "assistant_block", "block": payload}
    )
    if turn_id is not None:
        runner.note_session_output(turn_id, tool=False)
    return payload


@router.post("/{topic_id}/tasks/{task_id}/title")
async def set_task_title(
    topic_id: uuid.UUID,
    task_id: uuid.UUID,
    body: dict,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """给任务起名或改名：its owner, or its own session (`cheese_title`), which
    names a task that was created without a title."""
    place = await TopicService(db).place_or_404(topic_id)
    actor = await _actor_in_place(resolver, place)
    resolver.require_task_scope(task_id)
    task = await TaskService(db).get(task_id)
    if task is None or task.room_id != place.room_id:
        raise NotFoundError(say("taskNotInRoom"))
    if actor.via == "cheese":
        if resolver.task_scope() != task.id:
            raise ForbiddenError(say("taskOwnerOnly"))
    elif actor.handle != task.owner_handle:
        raise ForbiddenError(say("taskOwnerOnly"))
    title = (body.get("title") or "").strip()
    if not title:
        raise ValidationError(say("titleRequired"))
    TaskService.rename(task, title[:80])
    out = TaskOut.model_validate(task).model_dump(mode="json")
    await db.commit()
    return ok(out)


@router.post("/{topic_id}/tasks/{task_id}/close")
async def conclude_task(
    topic_id: uuid.UUID,
    task_id: uuid.UUID,
    body: ConclusionIn,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """关闭任务：its owner or its own session says it is over. With a
    conclusion it is done — the work did not end in a merge (research, a
    decision); without one it was put down. Delivered work closes by itself
    when its change is accepted.
    """
    place = await TopicService(db).place_or_404(topic_id)
    actor = await _actor_in_place(resolver, place)
    resolver.require_task_scope(task_id)
    tasks = TaskService(db)
    task = await tasks.get(task_id)
    if task is None or task.room_id != place.room_id:
        raise NotFoundError(say("taskNotInRoom"))
    # The owner closes the task, or its own session (`cheese_close_task`).
    if actor.via == "cheese":
        if resolver.task_scope() != task.id:
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
    out = TaskOut.model_validate(task).model_dump(mode="json")
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
    summary: str = Field(default="", max_length=20000)


async def _task_in_room(db, resolver, topic_id: uuid.UUID, task_id: uuid.UUID):
    place = await TopicService(db).place_or_404(topic_id)
    actor = await resolver.resolve(topic_id=place.room_id, project_id=place.project_id)
    await resolver.authorize_topic(
        actor, project_id=place.project_id, topic_id=place.room_id
    )
    task = await TaskService(db).get(task_id)
    if task is None or task.room_id != place.room_id:
        raise NotFoundError(say("taskNotInRoom"))
    return place, actor, task


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
    actor = await resolver.resolve(topic_id=place.room_id, project_id=place.project_id)
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


@router.post("/{topic_id}/tasks/{task_id}/start")
async def start_task(
    topic_id: uuid.UUID,
    task_id: uuid.UUID,
    body: TaskStartIn,
    db: DbSession,
    resolver: ActorResolverDep,
    chat: Annotated[ChatService, Depends(get_chat_service)],
) -> dict:
    """开始：the owner says the task is discussed enough. From here its agent may
    change the project, and what the document says now is what its changes are
    reviewed against."""
    place, actor, task = await _task_in_room(db, resolver, topic_id, task_id)
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
    out = TaskOut.model_validate(task).model_dump(mode="json")
    await db.commit()
    await announce_stale(place.room_id, "topics")
    await dispatch(chat)
    return ok(out)


@router.patch("/{topic_id}/tasks/{task_id}")
async def update_task(
    topic_id: uuid.UUID,
    task_id: uuid.UUID,
    body: TaskUpdateIn,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """转交：the owner hands the task to another member, or to another AI
    teammate."""
    place, actor, task = await _task_in_room(db, resolver, topic_id, task_id)
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
    out = TaskOut.model_validate(task).model_dump(mode="json")
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
    actor = await resolver.resolve(topic_id=place.room_id, project_id=place.project_id)
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
            title=task.title, owner=task.owner_handle, source=proposal.summary
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
