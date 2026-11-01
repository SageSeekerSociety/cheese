"""一条活怎么从一间房里长出来：谁来做它，它在房间里怎么说。

这批函数从前是 `TopicService` 的方法（`topic/services.py`），搬到这里来是因为它们
写的、读的、报的都是 `tasks` 那一行 —— 那是本包的行，第 2 层的 `topic` 够上来问
一次是一次向上的边（层表在 `docs/manual/dev/architecture.md`）。搬过来的是**动作**，
不是状态：`TopicService` 仍旧管房间自己的行，房间还是房间。

三个口子由调用方递进来，答案在上面：`SeatProject`（这一席落在哪个项目上）、
`ProjectTeam`（这个项目名下的队友）、`RoomThread`（这条会话的根消息是哪一条）。

名字里的「支线」和「活」是同一件事的两面：一条活挂在一间房里，一条支线挂在一条
消息下面；`room_task/place.py` 说 id 怎么同时指到这两样。
"""

import uuid

from sqlalchemy import Uuid, column, select, table
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    ForbiddenError,
    NotFoundError,
    UnprocessableEntityError,
    ValidationError,
)
from app.core.sentences import say
from app.domain.block.models import Block, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.conversation.numbering import lock_conversations
from app.domain.conversation.services import room_of
from app.domain.event_about import EventAbout, landing
from app.domain.event_author import AuthorType
from app.domain.identity.actor import acted_by
from app.domain.identity.handles import names_a_person, recipient_seat
from app.domain.project.services import ProjectService
from app.domain.room_task import heard
from app.domain.room_task.models import (
    PLACEHOLDER_TITLE,
    Task,
    TaskTitleSource,
)
from app.domain.room_task.place import PlaceResolver
from app.domain.room_task.services import TaskService, said_title
from app.domain.topic.models import Topic, TopicStatus, is_private_chat
from app.domain.topic.owner import resolve_owner
from app.domain.topic.ports import ProjectTeam, RoomThread, SeatProject
from app.domain.topic.services import TopicService
from app.domain.topic_membership.services import TopicMemberService

#: The 支线 a task may be made from, as a bare table: `thread` is read by
#: `topic`, not this package (`RoomThread`).
_THREADS = table("threads", column("id", Uuid), column("root_block_id", Uuid))


async def upgrade_block_to_place(
    session: AsyncSession,
    *,
    block_id: uuid.UUID,
    created_by: str | None = None,
    seat_project: SeatProject,
    team: ProjectTeam,
    main_line: RoomThread,
) -> tuple[Topic, Task]:
    """转为任务: a message in a channel becomes a task in that channel,
    owned by whoever turned it. A message, or the 支线 under it, can become
    any number of tasks; each hangs under the message in the main line.
    Its agent drafts the task's document from the message and what was
    said around it (the caller hands it that). A private chat's messages
    stay where they are."""
    block = await BlockRepository(session).get(block_id)
    if block is None:
        raise NotFoundError("Block not found")
    parent = await TopicService(session).get(
        await room_of(session, block.conversation_id)
    )
    if parent is None:
        raise NotFoundError("Parent topic not found")
    # 归档后工作面冻结 (spec §6.3) — consistent with dispatch/edit_doc.
    if parent.status == TopicStatus.archived:
        raise ValidationError(say("topicArchivedFrozen"))
    # Opened unnamed: the platform names it (`room_task/naming.py`).
    task = await create_task(
        session,
        room_id=parent.id,
        created_by=created_by,
        teammate=recipient_seat((block.meta or {}).get("agent_recipient")),
        origin_block_id=await _main_line_message(session, block, thread=main_line),
        source_block_id=block.id,
        seat_project=seat_project,
        team=team,
    )
    return parent, task


async def _main_line_message(
    session: AsyncSession, block: Block, *, thread: RoomThread
) -> uuid.UUID:
    """The main-line message ``block`` is under: itself, or the message its
    支线 hangs under. A task from either shows under that message.

    「这条会话是不是支线」由 `thread` 那一头回答（`RoomThread`）：支线是
    第 4 层的行，本包也在第 4 层，但读它是 `topic` 的事，所以这一次查问由调用方
    递进来。"""

    root = await thread(session, block.conversation_id)
    return root if root is not None else block.id


async def say_thread_title(
    session: AsyncSession, *, room_id: uuid.UUID, title: str, by: str
) -> uuid.UUID:
    """把「开一条支线」的那句话说到频道主线里，返回它的 id (清单第 4 条)。

    支线今天只挂在频道主线里的**一条消息**下面（`thread/services.py` 的
    `open_thread` 只认 `kind == message` 的根），所以「填个标题就开出来」落地
    成：以创建者自己的名义在主线说一句标题，支线开在这句话下。这样支线永远有
    根，`threads.root_block_id` 不必改成可空，读它的每一处（只有人回复时才开
    支线的那条路）也都不用动。

    说这句话的是频道域；开支线那一步由调用方做（`thread` 在本包之上，反向
    import 它是 C5 层表里的向上依赖）。同一个请求共用一个 session，两步
    之间没有提交，所以不存在只说出话、支线没开出来的一刻。

    私聊没有主线，也就没有支线；归档的频道冻结，和别处一样。
    """
    place = await PlaceResolver(session).resolve(room_id)
    if place is None:
        raise NotFoundError(say("topicNotFound"))
    room = place.room
    if room.status == TopicStatus.archived:
        raise UnprocessableEntityError(say("topicArchivedFrozen"))
    if _in_private(room):
        raise UnprocessableEntityError(say("privateChatHasNoThreads"))
    named = title.strip()[:300]
    if not named:
        raise UnprocessableEntityError(say("threadTitleRequired"))
    landed = landing(EventAbout.room, project_id=room.project_id, room_id=room.id)
    block = await BlockRepository(session).add(
        project_id=landed.project_id,
        conversation_id=landed.conversation_id,
        author=by,
        author_type=AuthorType.participant,
        content=named,
        kind=BlockKind.message,
        # 这句话是支线的名字，不是谁说的话：界面读这个标记，把它画成「开了一条
        # 支线」而不是一条发言。
        meta={"opened_thread": True},
    )
    return block.id


async def _card_block(
    session: AsyncSession, room: Topic, task: Task, *, actor: str
) -> Block:
    """The room's timeline says a task was created here, and whose it is.

    The task id rides in `meta`, and the STATUS does not: a block is a fixed
    record of a moment, and a piece of work that reads `running` forever
    after it finished is worse than no status at all. Whoever renders this
    reads the live row (`GET /topics/{id}/tasks`) for that.
    """
    # The ROOM's main line: the whole point is that the room sees the task.
    landed = landing(EventAbout.room, project_id=room.project_id, room_id=room.id)
    return await BlockRepository(session).add(
        project_id=landed.project_id,
        conversation_id=landed.conversation_id,
        author="system",
        author_type=AuthorType.platform,
        content=say(
            "taskCreated",
            actor=acted_by(actor, by_assistant=task.created_by_assistant),
            title=said_title(task),
            owner=f"<@{task.owner_handle}>" if task.owner_handle else "",
        ),
        kind=BlockKind.event,
        meta={"platform": True, "action": "task_created", "task_id": str(task.id)},
    )


def _in_private(room: Topic) -> bool:
    """这间房是不是名册两席的私聊（结论 19）。

    整个文件只在这里问 ``is_private_chat``：任务和支线都不是两个人之间的事，两处
    拒绝问的是同一句，就问一次。
    """
    return is_private_chat(room)


def refuse_tasks_in_private(room: Topic) -> None:
    """A task is the project's: everyone in it can see it. A private chat is
    two people's, so nothing said there becomes one, nor is proposed to."""
    if _in_private(room):
        raise ValidationError(say("privateChatHasNoTasks"))


async def create_task(
    session: AsyncSession,
    *,
    room_id: uuid.UUID,
    created_by: str | None,
    title: str | None = None,
    owner_handle: str | None = None,
    teammate: str | None = None,
    named_by_teammate: bool = False,
    origin_block_id: uuid.UUID | None = None,
    source_block_id: uuid.UUID | None = None,
    thread_id: uuid.UUID | None = None,
    seat_project: SeatProject,
    team: ProjectTeam,
    by_assistant: bool = False,
) -> Task:
    """Open a task in a room: a conversation of its own, owned by one person,
    with an empty living document for its agent to write.

    ``by_assistant``: ``created_by``'s own 芝士 opens it for them
    (`Actor.by_assistant`); the task and the line announcing it say so.

    ``thread_id`` turns a 支线 into the task, in place: the task takes its
    id, so its conversation and its AI teammate's session go on as they
    were, and it shows under the message the 支线 hangs under
    (``origin_block_id``, which the caller passes as that message).

    ``teammate`` is the seat of the AI teammate the work came from — the one
    that created it, or the one a message turned into it was addressed to.
    The task is worked by that teammate rather than the room's, so whoever
    was asked keeps the work, and it is seated in the room if it is not
    (refused when ``created_by`` could not add it); a seat that is no saved
    teammate of the project is ignored.

    A title typed by a person is final. A task opened unnamed is named by
    the platform (`room_task/naming.py`). A title the AI teammate wrote
    when it created the task (``named_by_teammate``) is the platform's
    too: kept unless the task changes direction.

    ``origin_block_id`` is the main-line message the task was made from;
    the task shows under it. A task made on its own is announced in the
    main line instead. ``source_block_id`` is the message whose words it
    came from, wherever that is; the task's page quotes it.

    The owner is the person named, else the person creating it, else — when
    no person is identifiable — the room's owner, then the project's. A task
    belongs to someone; a task nobody owns is one nobody can start or answer
    for. Who reviews its changes is its owner, or the project's default
    reviewer when the owner cannot (`TaskService.default_reviewer`).

    ``seat_project`` 是递进来的口子，落座那一步用它挡住别家的队友；本包读不出
    「这一席落在哪个项目上」（`app.domain.topic.ports.SeatProject`）。
    """
    # Everything creating it says something in, before anything is written
    # (`conversation.numbering`): the room, the 支线 it turns into, and the
    # 支线 under the message it is made from.
    cached_origin_threads = (
        [
            tid
            for obj in session.sync_session.identity_map.values()
            if getattr(type(obj), "__tablename__", None) == "threads"
            and getattr(obj, "root_block_id", None) == origin_block_id
            and (tid := getattr(obj, "id", None)) is not None
        ]
        if origin_block_id is not None and isinstance(session, AsyncSession)
        else []
    )
    await lock_conversations(
        session,
        [
            room_id,
            *([thread_id] if thread_id is not None else []),
            *(
                cached_origin_threads
                if cached_origin_threads
                else (
                    await session.scalars(
                        select(_THREADS.c.id).where(
                            _THREADS.c.root_block_id == origin_block_id
                        )
                    )
                    if origin_block_id is not None
                    else []
                )
            ),
        ],
    )
    place = await PlaceResolver(session).resolve(room_id)
    if place is None:
        raise NotFoundError(say("topicNotFound"))
    room = place.room
    # 归档后工作面冻结 (spec §6.3): no new work in a frozen room.
    if room.status == TopicStatus.archived:
        raise ValidationError(say("topicArchivedFrozen"))
    refuse_tasks_in_private(room)
    projects = ProjectService(session)
    project = await projects.get(room.project_id)
    owner = (
        owner_handle
        if names_a_person(owner_handle)
        else created_by
        if names_a_person(created_by)
        else await resolve_owner(
            session,
            None,
            parent_id=room.id,
            project_owner=project.owner_handle if project else None,
            project_team=project.team_id if project else None,
        )
    )
    tasks = TaskService(session)
    named = (title or "").strip()[:300]
    assigned_agent: str | None = None
    if (
        teammate
        and project is not None
        and await seat_project(session, teammate) == project.id
    ):
        await _seat_teammate(
            session,
            room.id,
            teammate,
            by=created_by,
            seat_project=seat_project,
            team=team,
        )
        assigned_agent = (teammate or "").strip() or None
    if owner:
        # Whoever the work is handed to is in its channel from then on.
        await TopicMemberService(session).take_in(room.id, owner)
    task = await tasks.open_thread(
        project_id=room.project_id,
        room_id=room.id,
        title=named or PLACEHOLDER_TITLE,
        title_source=TaskTitleSource.placeholder
        if not named
        else TaskTitleSource.auto
        if named_by_teammate
        else TaskTitleSource.human,
        owner_handle=owner,
        created_by=created_by,
        task_id=thread_id,
        flush=False,
    )
    task.upgraded_from_block_id = origin_block_id
    task.source_block_id = source_block_id
    task.created_by_assistant = by_assistant
    if named and named_by_teammate:
        task.title_calibrated = True
    if assigned_agent is not None:
        task.agent_handle = assigned_agent
    await session.flush()
    if named and named_by_teammate:
        tasks.record_title(task, reason="teammate", by=teammate)
    await tasks.ensure_document(task)
    if origin_block_id is None:
        await _card_block(session, room, task, actor=created_by or "system")
    await session.flush()
    return task


async def give_task_teammate(
    session: AsyncSession,
    task: Task,
    seat: str | None,
    *,
    by: str,
    seat_project: SeatProject,
    team: ProjectTeam,
) -> None:
    """Another AI teammate works ``task`` from its next turn: one saved in
    the task's project, named by its seat; None gives it back to its
    room's. ``by`` is who gives it, who must be able to seat it.

    ``seat_project`` 递到落座那一步（`SeatProject`）。"""
    if seat is not None:
        project = await ProjectService(session).get(task.project_id)
        if project is None or await seat_project(session, seat) != project.id:
            raise ValidationError(say("teammateHandleNotFound", handle=repr(seat)))
        await _seat_teammate(
            session,
            task.room_id,
            seat,
            by=by,
            seat_project=seat_project,
            team=team,
        )
    if (seat or None) != task.agent_handle:
        # Read by whichever teammate works it next: the one it was given to.
        await heard.inform(session, task, heard.teammate_changed(by=by, seat=seat))
    await TaskService(session).give_agent(task, agent_handle=seat)


async def _seat_teammate(
    session: AsyncSession,
    room_id: uuid.UUID,
    seat: str,
    *,
    by: str | None,
    seat_project: SeatProject,
    team: ProjectTeam,
) -> None:
    """Seat the teammate a task is given in the task's room, so that it
    works and speaks there as itself (`queries._seat_of`).

    Seated the way ``by`` would add it to the room by hand, with the same
    permission: someone who could not add it cannot give it the task
    either, and is told so. 那条落座的路要认「这一席是哪个项目的」，口子从上面
    递下来（`SeatProject`）。"""
    members = TopicMemberService(session)
    if seat in await members.agent_handles(room_id):
        return
    try:
        await members.add(
            topic_id=room_id,
            handle=seat,
            actor=by or "",
            seat_project=seat_project,
            team=team,
        )
    except ForbiddenError:
        raise ForbiddenError(say("taskTeammateNeedsSeat")) from None
