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
    AuthorType,
    BlockKind,
    BlockRepository,
    DbSession,
    ProjectRepository,
    TaskRepository,
    UsageRepository,
    _actor_in_place,
)
from app.core.errors import NotFoundError, ValidationError
from app.core.sentences import say
from app.domain.agent.chat import ChatService, project_refs_text
from app.domain.agent.harness.prompt import thread_relay_prompt
from app.domain.agent.liveness import task_liveness
from app.domain.agent.runtime import AgentWorkRunner
from app.domain.block.schemas import BlockOut
from app.domain.mentions import canonicalize_refs
from app.domain.room_task import binding, presentation
from app.domain.room_task.schemas import TaskOut
from app.domain.room_task.services import TaskService
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
    beats = await TaskRepository(db).last_block_at_for_tasks(thread_ids)
    asked = await BlockRepository(db).tasks_awaiting_an_answer(thread_ids)
    # 每条活最后一次花钱花在哪个模型上，一次查完 —— 卡上的模型是从这里算的，
    # `tasks` 上没有一列存它。
    spent = await UsageRepository(db).last_model_by_task(thread_ids)
    # 能用哪些模型，按项目算一次，整屏卡共用 —— 每张卡各算一次就是同一个答案
    # 构造几百遍。
    project = await ProjectRepository(db).get(topic.project_id)
    choices = binding.catalog(project.settings if project else None)
    # One answer for the whole room: every thread's worker lives in this room's
    # one session, so the screen is alive for all of them or for none.
    live = await task_liveness(chat, db, [t for t, _ in threads])
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
                        beats.get(task.id),
                        room_screen_live=live[task.id].screen,
                        # 每条活的分身是它自己的，所以逐条答；上面一次问完。
                        worker_live=live[task.id].worker,
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
    tasks = TaskService(db)
    task = await tasks.get(task_id)
    if task is None or task.room_id != place.room_id:
        raise NotFoundError(say("taskNotInRoom"))
    blocks = await tasks.blocks_for_thread(task_id, limit=limit, through=through)
    if blocks is None:
        raise NotFoundError(say("messageNotInTask"))
    cards = await AcceptCardRepository(db).latest_by_task([task.id])
    beats = await TaskRepository(db).last_block_at_for_tasks([task.id])
    live = await task_liveness(chat, db, [task])
    out = TaskOut.model_validate(task).model_dump(mode="json")
    # 看板那一格，和它在列表里显示的是同一句话——同一个函数算的，所以深链接进来
    # 和从看板点进来不可能给出两种说法。
    out["presentation"] = presentation.task_presentation(
        presentation.facts_for_task(
            task,
            cards.get(task.id),
            beats.get(task.id),
            # 两位当下事实见 `agent.liveness`：屏幕先看，屏幕没了分身也没了。
            room_screen_live=live[task.id].screen,
            worker_live=live[task.id].worker,
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


#: How long a note a room's agent leaves on one of its threads may be. A note
#: adds a requirement; anything longer belongs in the living document, which
#: both the room and the worker read.
AGENT_NOTE_CHARS = 4000


class TaskMessageIn(BaseModel):
    content: str = Field(min_length=1, max_length=100000)


@router.post("/{topic_id}/tasks/{task_id}/messages")
async def say_on_task(
    topic_id: uuid.UUID,
    task_id: uuid.UUID,
    body: TaskMessageIn,
    db: DbSession,
    resolver: ActorResolverDep,
    chat: Annotated[ChatService, Depends(get_chat_service)],
    runner: Annotated[AgentWorkRunner, Depends(get_work_runner)],
) -> dict:
    """在一张卡下面说话 —— 落在这条活的时间线上。人和房间的 AI 队友走这同一条路由
    （`cheese_tell`）；谁在说，决定之后发生什么。

    A person watching a card cannot reach the 分身 doing it: that worker lives
    inside the room's session and only the room's 芝士 can pass it a message.
    So a person's message lands WHERE THE WORK IS, and wakes the ROOM to act on
    it. One of the room's agents writing here is that room already: its message
    is recorded on the card and nobody is woken, because the worker is in its own
    session and it reaches it directly. Nothing is woken on the card — there is
    no session there to wake.

    Through the room's id for the same reason `/conclude` and `/title` are: a card
    is not a place, so it has no address of its own and no token scoped to it.
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
    members = TopicMemberService(db)
    relay_to_parent = not await members.holds_an_agent_seat(place.room, actor.handle)
    if not relay_to_parent and len(content) > AGENT_NOTE_CHARS:
        raise ValidationError(
            say("noteTooLong", length=len(content), limit=AGENT_NOTE_CHARS)
        )
    content = await project_refs_text(db, place.project_id, place.room_id, content)
    block = await BlockRepository(db).add(
        project_id=place.project_id,
        topic_id=place.room_id,
        task_id=task.id,
        author=actor.handle,
        author_type=AuthorType.participant,
        content=content,
        kind=BlockKind.message,
    )
    payload = BlockOut.model_validate(block).model_dump(mode="json")
    if relay_to_parent:
        from app.domain.delivery.agent import record_task_instruction
        from app.domain.delivery.ledger import DeliveryEvent
        from app.domain.notification.models import NotificationType

        await record_task_instruction(
            db,
            DeliveryEvent(
                id=block.id,
                type=NotificationType.ROOM_NOTICE,
                payload={
                    "projectId": str(place.project_id),
                    "topicId": str(place.room_id),
                },
                occurred_at=block.created_at,
            ),
            task=task,
            content=thread_relay_prompt(
                task_id=task.id,
                task_title=task.title,
                author=actor.handle,
                message=f"说：{content}",
            ),
        )
    # Visible before the turn that reads it — same ordering as the doc comment.
    await db.commit()
    # 卡下的实时帧走这条活自己的频道，因为块落在这条活上：发给房间的话，看着房间
    # 的人会看见一条刷新之后就搬走了的消息。
    await get_broker().publish(
        str(task.id), {"type": "assistant_block", "block": payload}
    )
    if relay_to_parent:
        from app.domain.delivery.agent import dispatch_pending

        await dispatch_pending(chat.session_factory, chat=chat, runner=runner)
    return ok(payload)


@router.post("/{topic_id}/tasks/{task_id}/title")
async def set_task_title(
    topic_id: uuid.UUID,
    task_id: uuid.UUID,
    body: dict,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """给房间里的一条活起/改标题, said by the ROOM.

    Naming is the room's because nothing else is left to do it: a card
    dispatched by /split is named by whoever dispatched it, but one upgraded
    out of a message starts untitled, and the session that used to name itself
    on its first turn is exactly what 任务=分身 removed.
    """
    place = await TopicService(db).place_or_404(topic_id)
    await _actor_in_place(resolver, place)
    task = await TaskService(db).get(task_id)
    if task is None or task.room_id != place.room_id:
        raise NotFoundError(say("taskNotInRoom"))
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
    """收卡, said by the ROOM about one of its threads.

    The worker's conclusion is already on the card: the platform writes it there
    on every `SubagentStop` whose label names this card. This is the other half
    — the room saying the work is over — and it is deliberately a separate act,
    done by hand.

    It has to be. A worker reports finished more than once (parking a long
    command in its own background counts as finishing), and stops arrive from
    sub-threads the harness started for its own purposes — measured on 2.1.224:
    after the session's own Stop, with an unknown id, an empty label and a
    fragment of a prompt as their closing message. Closing on either of those
    would collapse work that is still going. The room decides when work has
    ended; code acceptance merges the task's branch and closes it through the
    separate acceptance flow.

    `conclusion` is optional: given, it overwrites the worker's last word (which
    is sometimes the fragment above); omitted, that last word stands.
    """
    place = await TopicService(db).place_or_404(topic_id)
    await _actor_in_place(resolver, place)
    tasks = TaskService(db)
    task = await tasks.get(task_id)
    if task is None or task.room_id != place.room_id:
        raise NotFoundError(say("taskNotInRoom"))
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
    out = TaskOut.model_validate(task).model_dump(mode="json")
    await db.commit()
    return ok(out)
