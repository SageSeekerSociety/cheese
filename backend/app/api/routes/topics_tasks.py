"""A room's tasks, and what is done to a task.

`GET|POST /topics/{room}/tasks` list a room's tasks and create one, and
`POST /topics/{conversation}/teammate-tasks` is an AI teammate creating one
from where it is talking: those are the room's. Everything about one task is
addressed by the task's own conversation id — `GET|PATCH /topics/{task}/task`
(the task, handing it over), `PUT /topics/{task}/task/progress` (its AI
teammate saying where the work stands), `POST /topics/{task}/close` and
`/reopen`.
Talking in a task, naming it and reading its document go through the same
routes as a room's (`/messages`, `/title`, `/document`), with the task's id.

`_actor_in_place`, `DbSession` and the block/project/usage repositories come from
`app.api.topics_deps` (they lived in `topics.py` until that module's siblings
stopped importing it), so the import guard and `.importlinter` see no new edges;
the accept card is read through `AcceptService`, not a repository re-export.
Handing one task back to its page is `app.api.room_task_rows.task_out` — the same
row the room's list builds — so the title route asks for it there, not here (C4
`routes-independent`). The rows a room's list and a task's page answer with
(`room_task_items`, `task_page_row`, `_opening`) and the 相关 of a task
(`related_of`, in `app.api.task_origin`) live there too, for the same reason:
the room snapshot reads them and is not a route.
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
from app.api.request_deps import DbSession
from app.api.response import ok, page, typed_response
from app.api.room_task_rows import (
    _opening,
    room_task_items,
    task_out,
    task_page_row,
    with_session_running,
)
from app.api.task_instructions import (
    dispatch,
    teammate_source_text,
    tell_task,
)
from app.api.task_origin import (
    asked_in,
    discussion,
    materials,
    materials_text,
    related_of,
)
from app.api.thread_task import turn_into_task
from app.api.topics_deps import (
    BlockRepository,
    ProjectRepository,
)
from app.api.write_access import ROUTE_DECIDES, Can, InChannel
from app.auth.project_roles import may
from app.core.errors import (
    ConflictError,
    ForbiddenError,
    UnprocessableEntityError,
)
from app.core.sentences import say
from app.domain.agent import checkout_sweep
from app.domain.agent.announce import announce
from app.domain.agent.chat import ChatService
from app.domain.agent.environment import default_environment
from app.domain.agent.harness.prompt import task_opening_prompt
from app.domain.agent.opening import opening_content
from app.domain.agent.own_calls import ask_owner_to_join
from app.domain.agent.staleness import announce_stale
from app.domain.agent_instance.adapters import (
    project_team,
    seat_project,
    task_teammate,
)
from app.domain.agent_session.services import AgentSessionService
from app.domain.delivery.ledger import settle_card
from app.domain.idempotency import store as idem
from app.domain.idempotency.keys import action_key
from app.domain.machine import session_work
from app.domain.machine.environments import default_for, usable_by_owner
from app.domain.mentions import canonicalize_refs
from app.domain.project_permissions import Permission
from app.domain.review.archive import OPEN_CARD_STATUSES, close_cards_for_closed_task
from app.domain.review.services import AcceptService
from app.domain.review.task_landing import tell_origin
from app.domain.review.task_pr import close_pr_after_commit
from app.domain.room_task import dispatch as task_dispatch
from app.domain.room_task import heard
from app.domain.room_task.closing import close_task
from app.domain.room_task.place import place_or_404
from app.domain.room_task.progress import RUN_HEADER, parse_run, set_progress
from app.domain.room_task.schemas import ProgressIn, TaskOut
from app.domain.room_task.services import TaskService, said_title
from app.domain.room_task.title_width import TITLE_WIDTH, title_fits, title_width
from app.domain.topic.schemas import ConclusionIn
from app.domain.topic.services import TopicService
from app.domain.topic_membership.services import TopicMemberService

router = APIRouter(prefix="/topics", tags=["topics"])


@router.get("/{topic_id}/tasks", response_model=None)
async def list_room_tasks(
    topic_id: uuid.UUID,
    db: DbSession,
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

    Each row says whether its AI teammate's session is running right now
    (`session_running`, `room_task_rows.with_session_running`).

    条件请求：侧栏画一次 rail 就要整份清单，而一个房间这里有 ~1317 条活、`limit=0`
    也有 1 MB 上下。`ETag` 由整份信封的规范化 JSON 算出，`If-None-Match` 命中就回
    304、空 body —— 切页面时「没有新东西」不再重传这一份。
    """
    topic = await TopicService(db).get_or_404(topic_id)
    actor = await resolver.resolve(topic_id=topic_id, project_id=topic.project_id)
    await resolver.authorize_topic(
        actor, project_id=topic.project_id, topic_id=topic_id
    )
    items = await room_task_items(
        db,
        actor,
        topic,
        limit=limit,
        status=status,
        ids=ids,
        blocks=blocks,
        branch=branch,
        latest=latest,
    )
    items = await with_session_running(db, items)
    return await conditional_json(ok(page(items, len(items))), if_none_match)


@router.get("/{topic_id}/task")
async def get_task(
    topic_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """The task this conversation is — the same shape a room's `/tasks` lists.
    404 for a room's own conversation. What is said in it is read like any
    conversation's (`/topics/{task}/blocks`)."""
    place, actor, task = await task_conversation(db, resolver, topic_id)
    return ok(await task_page_row(db, place, task, me=actor.handle or ""))


@router.get("/{topic_id}/related")
async def task_related(
    topic_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """Where the task came from and what was put on the table there
    (`task_origin.py`): what its page shows as 相关."""
    _place, _actor, task = await task_conversation(db, resolver, topic_id)
    return ok(await related_of(db, task))


@router.post("/{topic_id}/opening", dependencies=[ROUTE_DECIDES])
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


@router.post("/{topic_id}/close", dependencies=[ROUTE_DECIDES])
async def conclude_task(
    topic_id: uuid.UUID,
    body: ConclusionIn,
    db: DbSession,
    resolver: ActorResolverDep,
    chat: Annotated[ChatService, Depends(get_chat_service)],
) -> dict:
    """关闭任务：someone who works it — its owner or a contributor — or its
    own session says it is over. With a conclusion it is done — the work did
    not end in a merge (research, a decision); without one it was put down.
    Delivered work closes by itself when its change is accepted.

    Closing is not undone by reopening (its open cards are voided, its pull
    request closed), so it is kept to the people whose word the task's
    teammate acts on (`TaskService.takes_part`); reopening is wider.
    """
    place, actor, task = await task_conversation(db, resolver, topic_id)
    tasks = TaskService(db)
    if actor.via == "cheese":
        if resolver.credential_conversation() != task.id:
            raise ForbiddenError(say("taskOwnSessionCloses"))
    elif not TaskService.takes_part(task, actor.handle):
        raise ForbiddenError(say("taskOwnSessionCloses"))
    credits = {"reporter_handle", "contributor_handles"} & body.model_fields_set
    # Who reported it and who works it is the owner's to change (`update_task`);
    # a contributor closing the task does not get to rewrite that.
    if credits and actor.via != "cheese" and actor.handle != task.owner_handle:
        raise ForbiddenError(say("taskOwnerOnly"))
    # Friendly "@名字/@话题名" → structured tokens, same as every other write
    # path that lands text a person will read.
    text = (body.conclusion or "").strip()
    conclusion = (
        await canonicalize_refs(
            db,
            place.project_id,
            text,
            team=project_team,
            exclude_topic_id=place.room_id,
        )
        if text
        else None
    )
    if credits:
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
    # Its own session closing it is the turn that would be stopped, and it
    # ends by itself.
    task = await close_task(
        db,
        task,
        None if actor.via == "cheese" else chat,
        conclusion=conclusion,
        machines=checkout_sweep.sweep,
    )
    close_pr_after_commit(db, task)
    # 活关掉了，它还没结算的卡跟着作废：卡不能比它挂着的活活得久。判据和归档
    # 那条路共用（`review.archive`）；PR 怎么处置是上面 `task_pr` 的事，这里
    # 只让审阅停下来。
    await close_cards_for_closed_task(
        db, task_id=task.id, by=actor.handle, settle_card=settle_card
    )
    # The task's own conversation hears how it ended, and so does the
    # discussion it came from; the channel's card for it updates in place.
    ended = (
        say("taskCompleted", title=said_title(task), conclusion=task.conclusion)
        if task.conclusion
        else say("taskClosed", title=said_title(task))
    )
    closed = {"platform": True, "action": "task_closed", "task_id": str(task.id)}
    await announce(
        db,
        place_id=place.room_id,
        task_id=task.id,
        content=ended,
        # Its own session closing it read that in the tool's reply.
        meta=closed
        if actor.via == "cheese"
        else heard.informing(
            closed, heard.task_closed(by=actor.handle, conclusion=task.conclusion)
        ),
    )
    # The 支线 line under the message the task was made from is said by its AI
    # teammate, so whose teammate it is comes in here too.
    await tell_origin(db, task, ended, task_teammate=task_teammate)
    out = await task_out(db, task, me=actor.handle or "")
    await db.commit()
    return ok(out)


@router.put(
    "/{topic_id}/task/progress",
    dependencies=[ROUTE_DECIDES],
    **typed_response(TaskOut),
)
async def report_progress(
    topic_id: uuid.UUID,
    body: ProgressIn,
    db: DbSession,
    resolver: ActorResolverDep,
    run: str | None = Header(default=None, alias=RUN_HEADER),
) -> dict:
    """The task's AI teammate says where its work stands (`cheese_task(action=state)`):
    what the board shows for it until it says again, or until its session
    runs again in a run it has not reported in (`presentation.task_presentation`).
    Only the task's own session reports; a person reads it, and says what they
    need in the task.

    Refused while a card waits on the task's review: the card says where the
    task stands then (等审), and a report would either change nothing on the
    board while the teammate believes it did, or take the task out of the
    reviewer's hands. It reports again once the card is returned or voided."""
    _place, actor, task = await task_conversation(db, resolver, topic_id)
    if actor.via != "cheese" or resolver.credential_conversation() != task.id:
        raise ForbiddenError(say("taskOwnSessionReportsProgress"))
    TaskService.require_open(task)
    card = (await AcceptService(db).latest_by_task([task.id])).get(task.id)
    if card is not None and card.status in OPEN_CARD_STATUSES:
        raise ConflictError(say("taskProgressWhileInReview"))
    by = actor.handle or ""
    set_progress(
        task,
        body.state,
        note=body.note,
        by=by,
        at=datetime.now(UTC),
        # Which run of its session the call was made in, as its runner
        # names it (`executor_transport.PlatformHost.run`).
        run=parse_run(run),
    )
    await db.flush()
    out = await task_out(db, task, me=actor.handle or "")
    await db.commit()
    return ok(out)


@router.post("/{topic_id}/reopen", dependencies=[ROUTE_DECIDES])
async def reopen_task(
    topic_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """重新打开：anyone in the project who works tasks (`Permission.tasks`)
    takes a closed task up again, or its own session does, so a teammate that
    closed its task too early can take that back. Wider than who closes it:
    reopening is undone by closing again and is announced with who did it,
    while closing voids cards and closes the pull request for good. Its AI
    teammate goes on from the project's latest code when it has landed
    something."""
    place, actor, task = await task_conversation(db, resolver, topic_id)
    if actor.via == "cheese":
        if resolver.credential_conversation() != task.id:
            raise ForbiddenError(say("taskOwnSessionReopens"))
    elif not actor.authenticated or not await may(
        db, place.project_id, actor.handle, Permission.tasks
    ):
        raise ForbiddenError(say("taskOwnSessionReopens"))
    # The same test `reopen` moves a task onto a new branch by.
    new_branch = task.branch_name is not None and task.delivered_head is not None
    task = await TaskService(db).reopen(task)
    await announce(
        db,
        place_id=place.room_id,
        task_id=task.id,
        content=say("taskReopened", actor=f"<@{actor.handle}>", title=said_title(task)),
        meta=heard.informing(
            {"platform": True, "action": "task_reopened", "task_id": str(task.id)},
            heard.task_reopened(
                by=actor.handle, task_id=task.id, new_branch=new_branch
            ),
        ),
    )
    out = await task_out(db, task, me=actor.handle or "")
    await db.commit()
    return ok(out)


# —— 创建、转交 ——————————————————————————————————————————————————


class TaskCreateIn(BaseModel):
    title: str | None = Field(default=None, max_length=300)


class TaskUpdateIn(BaseModel):
    owner_handle: str | None = Field(default=None, max_length=64)
    agent_handle: str | None = Field(default=None, max_length=64)
    #: Everyone who works the task beside its owner, as the whole new list.
    contributor_handles: list[str] | None = Field(default=None, max_length=50)


@router.post("/{topic_id}/tasks")
async def create_task(
    at: Annotated[InChannel, Can(Permission.tasks, at="topic_id")],
    body: TaskCreateIn,
    db: DbSession,
) -> dict:
    """新建任务：a task owned by whoever creates it, empty until they say what
    it is for. Its agent starts on the owner's first message; its owner
    reviews its changes (`TaskService.default_reviewer`)."""
    place, actor = at.place, at.actor
    if not actor.authenticated or actor.via == "cheese":
        raise ForbiddenError(say("taskCreatedByPerson"))
    task = await task_dispatch.create_task(
        db,
        room_id=place.room_id,
        created_by=actor.handle,
        title=body.title,
        seat_project=seat_project,
        team=project_team,
        by_assistant=actor.by_assistant,
    )
    out = TaskOut.model_validate(task).model_dump(mode="json")
    await db.commit()
    return ok(out)


@router.patch("/{topic_id}/task", dependencies=[ROUTE_DECIDES])
async def update_task(
    topic_id: uuid.UUID,
    body: TaskUpdateIn,
    db: DbSession,
    resolver: ActorResolverDep,
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
        await heard.inform(
            db,
            task,
            heard.contributors_changed(
                by=actor.handle, added=[], removed=[actor.handle]
            ),
        )
        out = await task_out(db, task, me=actor.handle or "")
        await db.commit()
        return ok(out)
    members = TopicMemberService(db)
    if "owner_handle" in body.model_fields_set and body.owner_handle:
        # Work is handed to anyone in the project, and whoever takes it is in
        # its channel from then on.
        if body.owner_handle not in await members.project_people(
            place.project_id, team=project_team
        ):
            raise UnprocessableEntityError(say("taskOwnerNotInProject"))
        await members.take_in(place.room_id, body.owner_handle)
        await _move_off_former_owners_computer(
            db, actor, place, task, body.owner_handle
        )
        if body.owner_handle != task.owner_handle:
            await heard.inform(
                db,
                task,
                heard.owner_changed(by=actor.handle, owner=body.owner_handle),
            )
        await tasks.hand_over(task, owner_handle=body.owner_handle)
    if "agent_handle" in body.model_fields_set:
        given = (body.agent_handle or "").strip() or None
        seated = await members.agent_handles(place.room_id) if given else []
        await task_dispatch.give_task_teammate(
            db,
            task,
            given,
            by=actor.handle,
            seat_project=seat_project,
            team=project_team,
        )
        if given and given not in seated:
            await ask_owner_to_join(db, place.room, given)
    if body.contributor_handles is not None:
        people = await members.project_people(place.project_id, team=project_team)
        wanted = list(dict.fromkeys(body.contributor_handles))
        if any(h not in people for h in wanted):
            raise UnprocessableEntityError(say("contributorNotInProject"))
        for handle in wanted:
            await members.take_in(place.room_id, handle)
        before = list(task.contributor_handles or [])
        after = [h for h in wanted if h != task.owner_handle]
        await tasks.set_contributors(task, after)
        added = [h for h in after if h not in before]
        removed = [h for h in before if h not in after]
        if added or removed:
            await heard.inform(
                db,
                task,
                heard.contributors_changed(
                    by=actor.handle, added=added, removed=removed
                ),
            )
    out = await task_out(db, task, me=actor.handle or "")
    await db.commit()
    return ok(out)


async def _move_off_former_owners_computer(db, actor, place, task, owner) -> None:
    """A person's own computer works only that person's tasks: before a task
    changes hands, each agent in it whose environment is on someone else's own
    computer moves to one its new owner may use, pushing its work first, and a
    default naming such a computer is cleared. A push that fails leaves the
    task with its owner and says why."""

    room = await TopicService(db).get_or_404(place.room_id)
    project = await ProjectRepository(db).get(place.project_id)
    if not await usable_by_owner(db, task.default_environment, project, owner):
        task.default_environment = None
    sessions = AgentSessionService(db)
    for handle, chosen in (await sessions.environments_in(task.id)).items():
        if await usable_by_owner(db, chosen.environment, project, owner):
            continue
        row = await sessions.latest(task.id, handle)
        if row is None:
            continue
        target = default_for(room, task, project.settings if project else None)
        environment = (
            target.environment
            if await usable_by_owner(
                db, target.environment.model_dump(), project, owner
            )
            else default_environment()
        )
        await session_work.switch_environment(
            db,
            topic_id=place.room_id,
            session_id=row.id,
            actor=actor,
            environment=environment,
        )


class TeammateTaskIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    #: What the task is for, in the teammate's words: what its session starts
    #: from. Not asked for a 支线 turned into the task (``here``), whose
    #: session carries the conversation on.
    summary: str = Field(default="", max_length=20000)
    #: The person the task is for. Defaults to the person whose message the
    #: teammate is answering — who asked for it, or who answered 「转为任务」 —
    #: and failing that whoever wrote the message the 支线 hangs under.
    owner_handle: str | None = Field(default=None, max_length=64)
    #: The 支线 the teammate is in becomes the task, in place, rather than a
    #: new task being made from it.
    here: bool = False
    source_message_id: uuid.UUID | None = None


@router.post("/{topic_id}/teammate-tasks", dependencies=[ROUTE_DECIDES])
async def create_teammate_task(
    topic_id: uuid.UUID,
    body: TeammateTaskIn,
    db: DbSession,
    resolver: ActorResolverDep,
    chat: Annotated[ChatService, Depends(get_chat_service)],
) -> dict:
    """An AI teammate makes a task (`cheese_task(action=open)`) on someone's word, from the
    conversation it is in: the 支线 itself (``here``), or a new task that hangs
    under the message the 支线 is under. A teammate whose own idea it is asks
    first (`cheese_ask`), and the person who answers is who it does it for."""
    place = await place_or_404(db, topic_id)
    actor = await resolver.resolve(
        topic_id=place.conversation_id, project_id=place.project_id
    )
    await resolver.authorize_topic(
        actor, project_id=place.project_id, topic_id=place.room_id
    )
    if actor.via != "cheese":
        raise ForbiddenError(say("teammateTaskByTeammate"))
    source = (
        await BlockRepository(db).get(body.source_message_id)
        if body.source_message_id
        else None
    )
    if source is not None and source.project_id != place.project_id:
        raise ForbiddenError(say("resourceNotFound"))
    task_dispatch.refuse_tasks_in_private(place.room)
    if body.here and place.thread is None and place.task is not None:
        # 已经在任务里了，而队友说「就在这儿开工」。那不是「这里不能开」，
        # 是「你要的地方就是这里」：把这条任务交回去，并说明它在。以前这句
        # 回的是「只有支线可以原地转为任务」——只说了这不是什么，没说人在哪，
        # 于是队友转头另开了一条，同一件事被两个 AI 干了两遍（2026-10-21）。
        out = {
            **TaskOut.model_validate(place.task).model_dump(mode="json"),
            "here": True,
            "already": True,
        }
        return ok(out)
    if body.here and place.thread is None:
        raise UnprocessableEntityError(say("teammateTaskHereOnlyInThread"))
    if not body.here and not body.summary.strip():
        raise UnprocessableEntityError(say("teammateTaskSummaryRequired"))
    # A title nobody can read is not a title. Refused here, with the limit in
    # the words, so the teammate writes a shorter one and calls again — cutting
    # it instead would leave half a phrase in the sidebar and no one the wiser.
    # Checked before the idempotency claim: a refused call leaves no record.
    if not title_fits(body.title):
        raise UnprocessableEntityError(
            say("taskTitleTooLong", limit=TITLE_WIDTH, now=title_width(body.title))
        )
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
        place.project_id, team=project_team
    ):
        raise UnprocessableEntityError(say("taskOwnerNotInProject"))
    # What the person said that this turn answers is where the task came from;
    # in a 支线 that is usually a reply, not the message the 支线 hangs under.
    # The owner's word may have been given in their private chat with the
    # agent; it is the task's source only when said where the task is made,
    # since the source is quoted to everyone who opens the task.
    cited = (
        source
        if source is not None and source.conversation_id == place.conversation_id
        else None
    )
    asked = cited or await asked_in(
        db,
        place.conversation_id,
        actor.handle,
        main_line=place.thread is None and place.task is None,
    )
    owner = (
        named
        or (source.author if source is not None else None)
        or (asked.author if asked is not None else None)
        or (origin.author if origin is not None else None)
    )
    if body.here:
        assert place.thread is not None
        task, _ = await turn_into_task(
            db,
            place.thread,
            owner=owner,
            by=actor.handle,
            title=body.title,
            named_by_teammate=True,
            source_block_id=asked.id if asked is not None else None,
        )
        out = {**TaskOut.model_validate(task).model_dump(mode="json"), "here": True}
        if key is not None:
            await idem.record_result(db, key, out)
        await db.commit()
        await announce_stale(place.room_id, "topics", id=place.room_id)
        return ok(out)
    task = await task_dispatch.create_task(
        db,
        room_id=place.room_id,
        created_by=actor.handle,
        title=body.title,
        owner_handle=owner,
        teammate=actor.handle,
        named_by_teammate=True,
        origin_block_id=origin.id if origin is not None else None,
        source_block_id=asked.id if asked is not None else None,
        seat_project=seat_project,
        team=project_team,
    )
    blocks = (await discussion(db, task))[1]
    await tell_task(
        db,
        task,
        task_opening_prompt(
            title=task.title,
            owner=task.owner_handle,
            source=teammate_source_text(actor.handle, body.summary, blocks),
            materials=materials_text(materials(blocks)),
            reviewer=task.reviewer_handle,
        ),
        opening=True,
    )
    out = {**TaskOut.model_validate(task).model_dump(mode="json"), "here": False}
    if key is not None:
        await idem.record_result(db, key, out)
    await db.commit()
    await dispatch(chat)
    return ok(out)
