"""A room's tasks, and what is done to a task.

`GET|POST /topics/{room}/tasks` list a room's tasks and create one, and
`POST /topics/{conversation}/teammate-tasks` is an AI teammate creating one
from where it is talking: those are the room's. Everything about one task is
addressed by the task's own conversation id — `GET|PATCH /topics/{task}/task`
(the task, handing it over), `POST /topics/{task}/start`, `/close` and
`/reopen`. Talking in a task, naming it and reading its
document go through the same routes as a room's (`/messages`, `/title`,
`/document`), with the task's id.

`_actor_in_place`, `DbSession` and the repositories come from topics.py, so the
import guard and `.importlinter` see no new edges.
"""

import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Query, Response
from pydantic import BaseModel, Field

from app.api.auth import ActorResolverDep
from app.api.conditional import conditional_json
from app.api.deps import get_chat_service, get_work_runner
from app.api.place import task_conversation
from app.api.response import ok, page
from app.api.room_task_rows import named_task, task_rows
from app.api.routes.topics import (
    AcceptCardRepository,
    BlockRepository,
    DbSession,
    ProjectRepository,
    UsageRepository,
)
from app.api.task_instructions import dispatch, teammate_source_text, tell_task
from app.api.task_origin import discussion, materials, materials_text
from app.api.write_access import ROUTE_DECIDES
from app.core.errors import (
    ConflictError,
    ForbiddenError,
    UnprocessableEntityError,
)
from app.core.sentences import say
from app.domain.agent.announce import announce
from app.domain.agent.chat import ChatService
from app.domain.agent.compute_configs import choice_for_owner, works_tasks_of
from app.domain.agent.harness.prompt import task_opening_prompt, task_started_prompt
from app.domain.agent.liveness import running_tasks
from app.domain.agent.opening import opening_content, opening_state
from app.domain.agent.staleness import announce_stale
from app.domain.agent_instance.own import may_work_for
from app.domain.idempotency import store as idem
from app.domain.idempotency.keys import action_key
from app.domain.living_doc.services import Documents
from app.domain.machine import session_work
from app.domain.machine.session_reports import devices_held_by
from app.domain.mentions import canonicalize_refs
from app.domain.review.task_landing import tell_origin
from app.domain.room_task import binding, presentation
from app.domain.room_task.schemas import TaskOut
from app.domain.room_task.services import TaskService, said_title
from app.domain.topic.schemas import ConclusionIn
from app.domain.topic.services import TopicService
from app.domain.topic_membership.services import TopicMemberService

router = APIRouter(prefix="/topics", tags=["topics"])


@router.get("/{topic_id}/tasks", response_model=None)
async def list_room_tasks(
    topic_id: uuid.UUID,
    db: DbSession,
    chat: Annotated[ChatService, Depends(get_chat_service)],
    resolver: ActorResolverDep,
    limit: Annotated[int | None, Query(ge=0, le=500)] = None,
    status: Annotated[Literal["open", "closed"] | None, Query()] = None,
    ids: Annotated[list[uuid.UUID] | None, Query()] = None,
    blocks: Annotated[list[uuid.UUID] | None, Query(max_length=200)] = None,
    branch: bool = False,
    latest: Annotated[int | None, Query(ge=1, le=50)] = None,
    if_none_match: Annotated[str | None, Header()] = None,
) -> Response:
    """This room's threads — every piece of work in it, each with its own
    conversation.

    Narrowed, a page reads only the tasks it draws instead of the whole room:

    - `status=open|closed`;
    - `ids`: these tasks (the ones a message names);
    - `blocks`: the tasks these blocks carry — made from one of them, or named
      by one of them as the task that began there (`task_created`, `split`);
    - `branch=true`: tasks with a branch of their own;
    - `latest=N`: the newest N, newest first — by when they closed with
      `status=closed`, by when they were created otherwise.

    `ids` and `blocks` together return a task matching either.

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

    `limit=0` is the roster shape: every thread, none of their conversation. A
    client drawing a rail or an overview wants the threads themselves and never
    reads a block; asking for `limit=1` made it download one message per thread
    to throw away. 0 is spelled rather than inferred from a separate flag
    because it is the same knob — "newest zero blocks each" — and it skips the
    block query entirely instead of filtering its result.

    条件请求：侧栏画一次 rail 就要整份清单，而一个房间这里有 ~1317 条活、`limit=0`
    也有 1 MB 上下。`ETag` 由整份信封的规范化 JSON 算出，`If-None-Match` 命中就回
    304、空 body —— 切页面时「没有新东西」不再重传这一份。
    """
    topic = await TopicService(db).get_or_404(topic_id)
    actor = await resolver.resolve(topic_id=topic_id, project_id=topic.project_id)
    await resolver.authorize_topic(
        actor, project_id=topic.project_id, topic_id=topic_id
    )
    named: set[uuid.UUID] | None = set(ids) if ids is not None else None
    if blocks is not None:
        lines = await BlockRepository(db).many(blocks)
        named = (named or set()) | {
            task_id for line in lines if (task_id := named_task(line)) is not None
        }
    threads = await TaskService(db).threads_for_room(
        topic_id,
        limit=limit,
        status=status,
        ids=named,
        origins=blocks,
        with_branch=branch,
        latest=latest,
    )
    items = await task_rows(db, chat, actor, topic.project_id, threads)
    return conditional_json(ok(page(items, len(items))), if_none_match)


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
    out["opening"] = await _opening(db, task)
    return ok(out)


async def _opening(db, task) -> str | None:
    """Where the task's first turn is, while its document is still empty
    (`agent/opening.py`); None once the document says something."""
    if task.document_id is not None:
        doc = await Documents(db).get(task.document_id)
        if doc is not None and doc.content.strip():
            return None
    return await opening_state(db, task.id)


@router.get("/{topic_id}/related")
async def task_related(
    topic_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """Where the task came from and what was put on the table there
    (`task_origin.py`): what its page shows as 相关."""
    _place, _actor, task = await task_conversation(db, resolver, topic_id)
    origin, blocks = await discussion(db, task)
    return ok({"origin": origin, "materials": materials(blocks)})


@router.post("/{topic_id}/opening")
async def retry_opening(
    topic_id: uuid.UUID,
    db: DbSession,
    chat: Annotated[ChatService, Depends(get_chat_service)],
    resolver: ActorResolverDep,
) -> dict:
    """Give a task's first instruction again, after it failed: someone working
    the task asks its AI teammate to draft the document once more."""
    _place, actor, task = await task_conversation(db, resolver, topic_id)
    if not actor.authenticated or not TaskService.takes_part(task, actor.handle):
        raise ForbiddenError(say("taskParticipantsOnly"))
    TaskService.require_open(task)
    content = await opening_content(db, task.id)
    if content is None or await _opening(db, task) != "failed":
        raise ConflictError(say("taskOpeningNotFailed"))
    await tell_task(db, task, content, opening=True)
    await db.commit()
    await dispatch(chat)
    return ok({"opening": "drafting"})


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
    # The task's own conversation hears how it ended, and so does the
    # discussion it came from; the channel's card for it updates in place.
    ended = (
        say("taskCompleted", title=said_title(task), conclusion=task.conclusion)
        if task.conclusion
        else say("taskClosed", title=said_title(task))
    )
    await announce(
        db,
        place_id=place.room_id,
        task_id=task.id,
        content=ended,
        meta={"platform": True, "action": "task_closed", "task_id": str(task.id)},
    )
    await tell_origin(db, task, ended)
    out = await _task_out(db, chat, task)
    await db.commit()
    await announce_stale(place.room_id, "topics", id=place.room_id)
    return ok(out)


@router.post("/{topic_id}/reopen", dependencies=[ROUTE_DECIDES])
async def reopen_task(
    topic_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    chat: Annotated[ChatService, Depends(get_chat_service)],
) -> dict:
    """重新打开：the owner takes a closed task up again. Its AI teammate goes on
    from the project's latest code when it has landed something."""
    place, actor, task = await task_conversation(db, resolver, topic_id)
    if not actor.authenticated or actor.handle != task.owner_handle:
        raise ForbiddenError(say("taskOwnerOnly"))
    task = await TaskService(db).reopen(task)
    await announce(
        db,
        place_id=place.room_id,
        task_id=task.id,
        content=say("taskReopened", actor=f"<@{actor.handle}>", title=said_title(task)),
        meta={"platform": True, "action": "task_reopened", "task_id": str(task.id)},
    )
    out = await _task_out(db, chat, task)
    await db.commit()
    await announce_stale(place.room_id, "topics", id=place.room_id)
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
    #: Everyone who works the task beside its owner, as the whole new list.
    contributor_handles: list[str] | None = Field(default=None, max_length=50)


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
    await announce_stale(place.room_id, "topics", id=place.room_id)
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
        task_id=task.id,
        content=say("taskStarted", actor=f"<@{actor.handle}>", title=said_title(task)),
        meta={"platform": True, "action": "task_started", "task_id": str(task.id)},
    )
    await tell_task(db, task, task_started_prompt(title=task.title, actor=actor.handle))
    out = await _task_out(db, chat, task)
    await db.commit()
    await announce_stale(place.room_id, "topics", id=place.room_id)
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
    teammate, and brings collaborators in or lets them go. A collaborator may
    only take themself off the list."""
    place, actor, task = await task_conversation(db, resolver, topic_id)
    if not actor.authenticated:
        raise ForbiddenError(say("taskOwnerOnly"))
    TaskService.require_open(task)
    tasks = TaskService(db)
    if actor.handle != task.owner_handle:
        leaving = [h for h in task.contributor_handles or [] if h != actor.handle]
        if (
            body.model_fields_set != {"contributor_handles"}
            or actor.handle not in (task.contributor_handles or [])
            or body.contributor_handles != leaving
        ):
            raise ForbiddenError(say("taskOwnerOnly"))
        await tasks.set_contributors(task, leaving)
        out = await _task_out(db, chat, task)
        await db.commit()
        await announce_stale(place.room_id, "topics", id=place.room_id)
        return ok(out)
    members = TopicMemberService(db)
    if "owner_handle" in body.model_fields_set and body.owner_handle:
        # Work is handed to anyone in the project, and whoever takes it is in
        # its channel from then on.
        if body.owner_handle not in await members.project_people(place.project_id):
            raise UnprocessableEntityError(say("taskOwnerNotInProject"))
        await members.take_in(place.room_id, body.owner_handle)
        await _move_off_former_owners_computer(
            db, actor, place, task, body.owner_handle
        )
        await tasks.hand_over(task, owner_handle=body.owner_handle)
    project_now = await ProjectRepository(db).get(place.project_id)
    settings_now = project_now.settings if project_now is not None else None
    if "agent_handle" in body.model_fields_set:
        given = (body.agent_handle or "").strip() or None
        if not await may_work_for(
            db, place.project_id, given, task.owner_handle, settings_now
        ):
            raise ForbiddenError(say("ownAgentOtherOwnersTask"))
        await TopicService(db).give_task_teammate(task, given)
    elif not await may_work_for(
        db, place.project_id, task.agent_handle, task.owner_handle, settings_now
    ):
        # Handed to someone whose own agent it is not (#2991): the task goes
        # back to the project's teammate, and its new owner may give it theirs.
        await TopicService(db).give_task_teammate(task, None)
    if body.contributor_handles is not None:
        people = await members.project_people(place.project_id)
        wanted = list(dict.fromkeys(body.contributor_handles))
        if any(h not in people for h in wanted):
            raise UnprocessableEntityError(say("contributorNotInProject"))
        for handle in wanted:
            await members.take_in(place.room_id, handle)
        await tasks.set_contributors(
            task, [h for h in wanted if h != task.owner_handle]
        )
    out = await _task_out(db, chat, task)
    await db.commit()
    await announce_stale(place.room_id, "topics", id=place.room_id)
    return ok(out)


async def _move_off_former_owners_computer(db, actor, place, task, owner) -> None:
    """A person's own computer works only that person's tasks: before a task
    changes hands, it moves to a computer its new owner may use, pushing its
    work first. A push that fails leaves the task with its owner and says why."""

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


class TeammateTaskIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    #: What the task is for, in the teammate's words: the start of its document.
    summary: str = Field(min_length=1, max_length=20000)
    #: The person the task is for: who asked for it. Defaults to whoever wrote
    #: the message the 支线 hangs under.
    owner_handle: str | None = Field(default=None, max_length=64)
    #: Someone asked for it to be done: start it as well. A teammate's own idea
    #: stays in discussion until its owner starts it.
    start: bool = False


@router.post("/{topic_id}/teammate-tasks", dependencies=[ROUTE_DECIDES])
async def create_teammate_task(
    topic_id: uuid.UUID,
    body: TeammateTaskIn,
    db: DbSession,
    resolver: ActorResolverDep,
    chat: Annotated[ChatService, Depends(get_chat_service)],
) -> dict:
    """An AI teammate creates a task (`cheese_task`) from the conversation it
    is in. Made in a 支线, the task hangs under the message the 支线 is under.
    Started at once when ``start`` says someone asked for it; otherwise it
    waits for its owner, whom the teammate asks."""
    place = await TopicService(db).place_or_404(topic_id)
    actor = await resolver.resolve(
        topic_id=place.conversation_id, project_id=place.project_id
    )
    await resolver.authorize_topic(
        actor, project_id=place.project_id, topic_id=place.room_id
    )
    if actor.via != "cheese":
        raise ForbiddenError(say("teammateTaskByTeammate"))
    TopicService.refuse_tasks_in_private(place.room)
    # A turn sent again creates the same task again: one task, not two.
    continuation = get_work_runner().continuation_for(topic_id)
    key = (
        action_key(continuation, "teammate-task", body.title, body.summary)
        if continuation
        else None
    )
    if key is not None and not await idem.claim(
        db, key, action="teammate-task", scope_id=str(topic_id)
    ):
        prior = await idem.stored_result(db, key)
        return ok(prior or {"skipped": True})
    origin = (
        await BlockRepository(db).get(place.thread.root_block_id)
        if place.thread is not None
        else None
    )
    named = (body.owner_handle or "").strip().lstrip("@")
    if named and named not in await TopicMemberService(db).project_people(
        place.project_id
    ):
        raise UnprocessableEntityError(say("taskOwnerNotInProject"))
    owner = named or (origin.author if origin is not None else None)
    task = await TopicService(db).create_task(
        room_id=place.room_id,
        created_by=actor.handle,
        title=body.title,
        owner_handle=owner,
        teammate=actor.handle,
        named_by_teammate=True,
        origin_block_id=origin.id if origin is not None else None,
    )
    blocks = (await discussion(db, task))[1]
    started, why_not = False, None
    if body.start and task.owner_handle:
        try:
            await TaskService(db).start(task, by=task.owner_handle)
            started = True
        except UnprocessableEntityError as exc:
            why_not = str(exc)
    await tell_task(
        db,
        task,
        task_opening_prompt(
            title=task.title,
            owner=task.owner_handle,
            source=teammate_source_text(actor.handle, body.summary, blocks),
            materials=materials_text(materials(blocks)),
            started=started,
        ),
        opening=True,
    )
    out = {
        **TaskOut.model_validate(task).model_dump(mode="json"),
        "started": started,
        "not_started_because": why_not,
    }
    if key is not None:
        await idem.record_result(db, key, out)
    await db.commit()
    await announce_stale(place.room_id, "topics", id=place.room_id)
    await dispatch(chat)
    return ok(out)
