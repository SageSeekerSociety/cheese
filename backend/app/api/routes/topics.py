"""Topic routes."""

import shutil
import uuid
from collections.abc import Collection, Mapping
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Query, Response
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import topics_signals
from app.api.auth import ActorResolverDep
from app.api.conditional import conditional_json
from app.api.deps import (
    get_broker,
    get_chat_service,
    get_work_runner,
)
from app.api.doc_access import require_role
from app.api.message_effects import announce_edited_message
from app.api.request_deps import DbSession
from app.api.response import ok, page
from app.api.room_task_rows import rail_tasks, tasks_under_blocks
from app.api.topics_deps import _actor_in_place, _parse_moment, require_person_speaks
from app.api.topics_rows import CardRow, TopicRow
from app.api.write_access import ROUTE_DECIDES, Can, InChannel
from app.core.config import settings
from app.core.errors import ForbiddenError, NotFoundError, ValidationError
from app.core.sentences import say
from app.domain.agent.chat import ChatService, project_refs_text
from app.domain.agent.clone import fork_for as fork_transcript
from app.domain.agent.harness import harness_for
from app.domain.agent.project_feed import TOPICS, tell_project
from app.domain.agent.realtime.activity import WORKING
from app.domain.agent.realtime.broker import InProcessBroker
from app.domain.agent.runtime import AgentWorkRunner, addressed_to_agent
from app.domain.agent.turn.intake.rewrite import text_as_sent
from app.domain.agent_instance.adapters import project_team, room_teammate
from app.domain.agent_session.services import AgentSessionService
from app.domain.block.asked import Awaited
from app.domain.block.editing import edit_message
from app.domain.block.models import (
    CHECKLIST_META_KEY,
    Block,
    BlockKind,
    checklist_text,
)
from app.domain.block.queries import reply_previews
from app.domain.block.repositories import BlockRepository
from app.domain.block.schemas import BlockOut
from app.domain.block.waits import MemberWait, MemberWaits
from app.domain.conversation.services import (
    room_of,
    threads_of_rooms,
    threads_still_threads,
)
from app.domain.delivery.agent import (
    dispatch_pending,
    record_task_instruction,
    redispatch_undelivered,
)
from app.domain.event_author import AuthorType
from app.domain.idempotency import store as idem
from app.domain.idempotency.keys import action_key
from app.domain.identity.actor import Actor
from app.domain.mentions import canonicalize_refs
from app.domain.notification.services import ProjectNotificationService
from app.domain.project.repositories import ProjectRepository
from app.domain.project_permissions import Permission
from app.domain.repository import service as workspace_paths
from app.domain.review.services import AcceptService
from app.domain.room_task import presentation
from app.domain.room_task.place import place_or_404
from app.domain.room_task.services import TaskService
from app.domain.routine import reads as routine_reads
from app.domain.thread import reads as thread_reads
from app.domain.thread.services import onto_rooms, thread_opening
from app.domain.topic.schemas import (
    CheckResultIn,
    MemberActivityOut,
    MemberWaitOut,
    TopicCreate,
    TopicOut,
)
from app.domain.topic.services import (
    SortOrder,
    TopicRelevance,
    TopicService,
    TopicSortField,
)
from app.domain.topic_membership.services import TopicMemberService
from app.domain.usage.repositories import UsageRepository
from app.domain.usage.services import UsageService
from app.domain.usage.windows import _TZ
from app.domain.webhook import service as webhook_service

router = APIRouter(prefix="/topics", tags=["topics"])


@router.post("", dependencies=[ROUTE_DECIDES])
async def create_topic(
    body: TopicCreate, db: DbSession, resolver: ActorResolverDep
) -> dict:
    actor = await resolver.resolve(project_id=body.project_id)
    await resolver.authorize_project(actor, project_id=body.project_id)
    await require_role(db, actor, body.project_id, Permission.create_channels)
    # The creator becomes the topic's roster owner (fusion-design §3), and the
    # creator is whoever the credential names — nobody when there is none.
    topic = await TopicService(db).create(
        project_id=body.project_id,
        title=body.title,
        description=body.description,
        parent_id=body.parent_id,
        created_by=actor.handle if actor.authenticated else None,
        members_only=body.members_only,
    )
    service = TopicService(db)
    relevance = await service.relevance_for_topics(
        [topic],
        _viewer(actor),
        team=project_team,
        cards=AcceptService(db),
        decisions=ProjectNotificationService(db),
    )
    managed = await TopicMemberService(db).managed_topic_ids([topic], actor.handle)
    response = ok(_topic_out(topic, set(), {}, relevance, managed_ids=managed))
    # The caller can configure or enter this room as soon as it gets the ID.
    # Dependency teardown commits after the response, which races that request.
    await db.commit()
    await tell_project(topic.id, TOPICS)
    return response


def _viewer(actor: Actor) -> str | None:
    """The handle 与我的相关性 is computed against, or None for "no one".

    An unidentified caller resolves to the ``anonymous`` actor
    (api/auth/resolver.py),
    which is a placeholder rather than a person: it is in no roster, on no
    card, and @-able by nobody. Handing it to the relevance query would have it
    honestly answer "unrelated to everything" — three round trips to learn what
    the default already says — so it is filtered out here instead.
    """
    return actor.handle if actor.handle != "anonymous" else None


async def _awaiting_an_answer(
    db: AsyncSession,
    room_ids: list[uuid.UUID],
    *,
    threads: Mapping[uuid.UUID, uuid.UUID] | None = None,
    tasks_by_room: Mapping[uuid.UUID, uuid.UUID] | None = None,
) -> dict[uuid.UUID, Awaited]:
    """Which channels wait on an answer, and from whom: their own line's question, or one 芝士 asked in one of their tasks or 支线.

    一个频道那一格认的是它全部的对话 —— 它自己那条线、它的任务、它的支线（决策请求
    走的是同一条规矩，`notification/repositories.decision_topic_ids`）：芝士在一条活
    里问的那道题，题落在那条活自己的线上，可这个频道也在等那个人回答。少了任务这一
    头，人就看得见「有一条待办」而侧栏一处都不亮。

    ``threads``：调用方已经问过的那一份（`topics_signals.at_work` 要的是
    同一个答案）。这里会往上面叠任务，所以先拷一份，不动调用方那份。``tasks_by_room`` 是调用方已经查过的同一批房间的 `tasks_of_rooms`；不传就自己问。
    """
    thread_rooms = await threads_of_rooms(db, room_ids) if threads is None else threads
    inner_rooms = dict(thread_rooms)
    if tasks_by_room is None:
        tasks_by_room = await TaskService(db).tasks_of_rooms(room_ids)
    inner_rooms.update(tasks_by_room)
    found = await BlockRepository(db).awaiting_an_answer([*room_ids, *inner_rooms])
    return {
        place: who
        for place, who in onto_rooms(found, inner_rooms).items()
        if place in room_ids
    }


def _asks_me(asked: Mapping[uuid.UUID, Awaited], viewer: str | None) -> set[uuid.UUID]:
    """这些停在提问上的房间里，哪几个在等 `viewer` 回答。

    芝士问谁，提问那一刻就记在题上（`meta.asked`），跳过了的人不再等
    （`block.asked`）：旁观的人不该被一道不归他答的题点亮。
    """
    if viewer is None:
        return set()
    return {tid for tid, who in asked.items() if viewer in who.waiting()}


async def _unresolved_failures(
    db: AsyncSession,
    room_ids: list[uuid.UUID],
    tasks_of_room: Mapping[uuid.UUID, list[uuid.UUID]],
) -> tuple[dict[uuid.UUID, datetime], dict[uuid.UUID, datetime]]:
    """尚未恢复的失败从什么时候开始：频道那一行，和它名下各件活。

    返回 `(频道 → 时刻, 活 → 时刻)`。频道那一行问它**自己名下、此刻还没关**的东西：
    房间自己那条线、还在当支线用的支线（`threads_still_threads`）、它名下的每一件
    活（`tasks_of_room`，同一判据「开着、还没进收尾窗口」）。频道行取最早的一条——
    名下一件活报错，上面的房间就是在出问题；一件活的恢复只清它自己，不清整间房：
    多件活各算各的，谁都没恢复就谁都在。

    `tasks_of_room` 只列**还没关**的活——关了的活自己的错误不再点亮任何一行；原地
    转成任务的支线已按任务算，不折回频道（否则旧错误点亮它、它的话清掉它的失败）。
    拖过三天的失败也不再点亮（`waits.FAILURE_GIVES_UP_AFTER`），这条线自己那条线
    也是 —— 否则没人接手的频道仍然永远红。
    """
    if not room_ids:
        return {}, {}
    threads = await threads_still_threads(db, room_ids)
    units: dict[uuid.UUID, list[uuid.UUID]] = {}
    # 频道那一条 = 它自己那条线 + 它的支线。支线说的就是这个房间的事，`_last_said`
    # 本来也把支线折回房间，所以两段一起问、答案落在频道这个键上。
    for room in room_ids:
        units.setdefault(room, [room])
    for thread, room in threads.items():
        if room in units:
            units[room].append(thread)
    # 每一件活自己一条：活的失败只点亮它自己那一行，直到它自己恢复 —— 别的活恢复
    # 了不会把它清掉，它也不会被并进频道那一条里（那样一件活恢复就会误清整间房的线）。
    for task_id in (t for ids in tasks_of_room.values() for t in ids):
        units.setdefault(task_id, [task_id])
    found = await MemberWaits(db).unresolved_failures(units)
    tasks = {
        task_id: found[task_id]
        for task_ids in tasks_of_room.values()
        for task_id in task_ids
        if task_id in found
    }
    # 频道那一行取最早的一条：它自己/支线的，和名下各件活的。合在这里而不是在
    # `units` 里，正是为了让「一件活的恢复只清它自己」——上面每一件活是分开问的。
    rooms: dict[uuid.UUID, datetime] = {}
    for room in room_ids:
        if room in found:
            rooms[room] = found[room]
    for room, task_ids in tasks_of_room.items():
        for task_id in task_ids:
            at = found.get(task_id)
            if at is None:
                continue
            if room not in rooms or at < rooms[room]:
                rooms[room] = at
    return rooms, tasks


def _topic_out(
    topic: TopicRow,
    running_ids: set[uuid.UUID],
    last_activity: dict[uuid.UUID, datetime],
    relevance: dict[uuid.UUID, TopicRelevance] | None = None,
    cards: Mapping[uuid.UUID, CardRow] | None = None,
    now: datetime | None = None,
    managed_ids: set[uuid.UUID] | None = None,
    asked: Mapping[uuid.UUID, Awaited] | None = None,
    asks_me: set[uuid.UUID] | None = None,
    activity: list[dict] | None = None,
    waits: list[MemberWait] | None = None,
    working_in_threads: set[str] | None = None,
    unseen: Collection[uuid.UUID] | None = None,
    answered: Collection[uuid.UUID] | None = None,
    incomplete: Mapping[uuid.UUID, datetime] | None = None,
    moving: Collection[uuid.UUID] | None = None,
    seats: Mapping[uuid.UUID, str | None] | None = None,
) -> dict:
    """TopicOut plus the signals the ORM row cannot carry: 最后活动时间, which is
    derived from the topic's blocks; who is busy in it right now and whom it is
    waiting on (`activity` / `waits` — about members, never about the room); and
    与我的相关性, which depends on WHO is asking and so cannot live on the row
    at all.

    `moving` is 「这间房此刻在不在往前走」—— 侧栏的呼吸绿：`moving` 是调用方已经
    聚合好的「房间里、支线里、名下的活里有一处在跑」，房间自己那张卡走在交付链路上
    也在这里补上（`presentation.room_presentation` 那一列）。

    `presentation` is the last of them: which column of the board this room is
    in and the one phrase to print on it, derived from the same facts the row
    already carries plus its live card (`room_task/presentation.py`)."""
    out = TopicOut.model_validate(topic)
    out.can_manage = topic.id in (managed_ids or set())
    # Assign before dumping so the instant is serialized by the same schema as
    # created_at/updated_at — a hand-rolled isoformat() here rendered "+00:00"
    # where every other timestamp in the payload says "Z".
    out.last_activity_at = last_activity.get(topic.id)
    # 这个频道名下有没有「交付完了、而这个人还没点开看过」的活（侧栏的长绿）。
    # `unseen` 一次算一整页，这里只查表；没有登录的人拿到的是空集，也就是不亮。
    out.unseen_delivery = topic.id in (unseen or ())
    # 这间房里还没恢复的失败从什么时候开始（侧栏红灯的另一半，`TopicOut.
    # incomplete_since`）：这间房自己那条线、它的支线，或者它名下一件**还没关**的活
    # （`_unresolved_failures` 那一份）。算不出来就是 `None`，不亮。
    out.incomplete_since = (incomplete or {}).get(topic.id)
    # 这间房的 AI 队友坐在哪一席（`TopicOut.agent_handle`）：`seats` 一次算一整页。
    out.agent_handle = (seats or {}).get(topic.id)
    # 有没有人回复了我、而我还没处理（侧栏黄灯的另一半，`TopicOut.replied_to_me`）：
    # 同一页一次算完，这里只查表；没有登录的人拿到空集，也就是不亮。
    out.replied_to_me = topic.id in (answered or ())
    # 只在干活的队友，不要打字的人：这份列表的读者是侧栏，而侧栏只画在干活的队友，
    # 打字的人不画（`useTopicRail`）—— 这份列表三十秒才读一次，打字五秒就过去了，
    # 画出来多半已经不是真的。打字条目还带一个每次都变的 `expires_in`（还差几秒过期，
    # 每次请求现算），只要它在 body 里，只要有人在打字，整份清单就再也命中不了 ETag、
    # 每三十秒原样重传一遍（`list_topics` 的条件请求）。
    out.activity = [
        MemberActivityOut(**entry)
        for entry in activity or []
        if entry["kind"] == WORKING
    ]
    # A member working here right now is not one the room is waiting on: it is
    # the one handling it. 在支线里跑的轮次同样算「在管这间房」：芝士在这条频道
    # 没有自己那条线，它答一条主线上的消息就是在那条消息的支线里说
    # （`topics_signals.answering`），只认主线的话，一条跑了五分钟的支线轮次会让它上面这间房
    # 显示成「没人管」（`useTopicRail` 的 REPLY_STALL_MS）。`activity` 本身照旧只放
    # 主线 —— 侧栏那一格画的是这间房自己那条线。
    working = {a.member for a in out.activity if a.kind == WORKING}
    working.update(working_in_threads or ())
    out.waits = [
        MemberWaitOut(member=w.member, reason=w.reason, since=w.since, pr=w.pr)
        for w in waits or []
        if w.member is None or w.member not in working
    ]
    mine = (relevance or {}).get(topic.id, TopicRelevance())
    # 芝士停在一道只有我能回答的问题上，同样是「在等我」——而且比一张卡更急：卡是
    # 一轮结束后的状态，提问是一轮**停在半路**。它也蕴含参与，理由同上。
    asking_me = topic.id in (asks_me or set())
    out.joined = mine.joined
    out.awaits_me = mine.awaits_me or asking_me
    facts = presentation.facts_for_room(
        topic,
        running_ids,
        (cards or {}).get(topic.id),
        awaiting_answer=topic.id in (asked or {}),
    )
    shown = presentation.room_presentation(facts, now=now or datetime.now(UTC))
    # 侧栏的呼吸绿：房间里、支线里、名下的活里有一处在跑（调用方聚合好传进来），
    # 或者房间自己那张卡走在交付链路上。和常亮绿是两档，见 `TopicOut.moving`。
    out.moving = (
        topic.id in (moving or ()) or shown.column is presentation.Column.delivering
    )
    data = out.model_dump(mode="json")
    data["presentation"] = shown.as_dict()
    return data


@router.get("", response_model=None)
async def list_topics(
    project_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    sort: TopicSortField | None = None,
    order: SortOrder = "asc",
    active_since: datetime | None = None,
    if_none_match: Annotated[str | None, Header()] = None,
) -> Response:
    """The project's topics.

    `sort=last_activity_at` orders by when something last HAPPENED in each topic
    (its newest block), and `active_since=<ISO instant>` keeps only the topics
    active at or after it — "最近活跃的话题". Neither reads `updated_at`, which
    only moves when the topic's own fields change.

    Every row also carries what it is to the caller (`joined`/`awaits_me`) —
    this is the endpoint the sidebar lists from — and the tasks the sidebar
    hangs under it (`my_tasks`, see `room_task_rows.rail_tasks`), so the sidebar
    never reads the whole project's task list to find the few rows it draws.

    条件请求：`ETag` 由整份信封的规范化 JSON 算出（`conditional_json`），`If-None-Match`
    命中就回 304、空 body。清单里每一行都是「数据库 + 在跑的会话」推出来的：一个房间的
    徽章会因为成员刚被拉进来、一张验收卡刚落地、某个队友刚开始干活而变，而这些都不动
    `updated_at`，所以「有没有变」只能靠整份 body 的指纹来判，不能靠某一列的时间戳。
    没命中那条路仍然走 `ok()` 的信封，前端 `request()` 靠 `{code,message,data}` 解包。
    """
    actor = await resolver.resolve(project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    service = TopicService(db)
    topics, last_activity, total = await service.list_for_project(
        project_id,
        viewer=_viewer(actor),
        sort=sort,
        order=order,
        active_since=active_since,
    )
    # 这一页的房间支线、名下的活，下面几处都要（见 `get_topic` 同一行）：各问一次。
    room_ids = [t.id for t in topics]
    room_threads = await threads_of_rooms(db, room_ids)
    tasks_by_room = await TaskService(db).tasks_of_rooms(room_ids)
    asked = await _awaiting_an_answer(
        db, room_ids, threads=room_threads, tasks_by_room=tasks_by_room
    )
    asks_me = _asks_me(asked, _viewer(actor))
    # 这一页里谁在干活：每间房自己那条线上、和它的支线里，runner 报着在干活的
    # 会话（`session_states`）。`presentation` 那一格读的是房间自己那条线：看板
    # 一格是房间自己的格子，它名下的活有它们自己的格子
    # （`test_a_rooms_board_cell_does_not_follow_a_task_running_under_it`）。
    working = await topics_signals.at_work(
        db, room_ids, threads=room_threads, tasks=tasks_by_room
    )
    running_ids = set(working.rooms)
    relevance = await service.relevance_for_topics(
        topics,
        _viewer(actor),
        team=project_team,
        cards=AcceptService(db),
        decisions=ProjectNotificationService(db),
    )
    live = await topics_signals.live_cards(db, [t.id for t in topics])
    cards = topics_signals.own_cards(live)
    # 侧栏的呼吸绿：房间里、支线里、名下的活里，任意一处有 runner 报着在跑的会话
    # （`moving`，`TopicOut.moving`）。三处都从 `at_work` 那一次读里来，不为这一页
    # 多付一条查询。
    moving_ids = set(working.rooms) | set(working.threads)
    moving_ids |= {tasks_by_room[task_id] for task_id in working.tasks}
    # 走在交付链路上的活也算：房间自己那条线闲着，名下的活在等 CI / 换基 / 修检查。
    moving_ids |= topics_signals.delivering_rooms(live)
    managed = (
        await TopicMemberService(db).managed_topic_ids(topics, actor.handle)
        if actor.authenticated
        else set()
    )
    # 一次，给整页用同一个「现在几点」——见 `list_project_tasks` 里同一行的理由。
    now = datetime.now(UTC)
    # 这间房名下的活（开着、还没进收尾窗口）—— 先查未恢复失败以复用失败块缓存。
    tasks_of_room: dict[uuid.UUID, list[uuid.UUID]] = {}
    for task_id, room_id in tasks_by_room.items():
        tasks_of_room.setdefault(room_id, []).append(task_id)
    incomplete, incomplete_tasks = await _unresolved_failures(
        db, [t.id for t in topics], tasks_of_room
    )
    waits = await MemberWaits(db).for_rooms(
        [t.id for t in topics],
        now=now,
        stuck_rooms=topics_signals.stuck_on_checks(live),
        threads=room_threads,
    )
    # 哪几间房里有「交付完了、而这个人还没点开看过」的活——侧栏的长绿靠它。一次
    # 查一整页（`room_task/repositories.unseen_deliveries`），频道行和它下面的活行
    # 都从这一份里查表。
    viewer = _viewer(actor)
    unseen = (
        await TaskService(db).unseen_deliveries([t.id for t in topics], viewer)
        if viewer
        else set()
    )
    # 哪几间房里有人回复了我、而我还没处理 —— 侧栏那盏黄灯的另一半。一样一次查一
    # 整页，`_topic_out` 只查表。
    answered = (
        await service.replied_to_me([t.id for t in topics], viewer) if viewer else set()
    )
    seats = await TopicMemberService(db).addressable_agent_handles(project_id, room_ids)
    rails = await rail_tasks(
        db,
        actor,
        [t.id for t in topics],
        delivered_of=unseen.__contains__,
        incomplete_of=incomplete_tasks.get,
        running_of=working.tasks.__contains__,
    )
    items = [
        {
            **_topic_out(
                t,
                running_ids,
                last_activity,
                relevance,
                cards,
                now,
                managed,
                asked,
                asks_me,
                activity=working.rooms.get(t.id),
                waits=waits.get(t.id),
                working_in_threads=working.threads.get(t.id),
                unseen=unseen,
                answered=answered,
                incomplete=incomplete,
                moving=moving_ids,
                seats=seats,
            ),
            "my_tasks": rails[t.id],
        }
        for t in topics
    ]
    return await conditional_json(ok(page(items, total)), if_none_match)


@router.get("/names")
async def list_topic_names(db: DbSession, resolver: ActorResolverDep) -> dict:
    """The names of the topics in every project the caller can see.

    The command palette matches topic names locally (pinyin initials included),
    so leaving the current project it needs the candidates themselves, not a
    search. Names only: the sidebar's per-row state is computed per project and
    stays on ``GET /topics``.

    The projects are those ``GET /projects`` lists, and the topics per project
    are those ``GET /topics`` lists, so this never names a room the caller could
    not already find in a sidebar.
    """
    who = await resolver.resolve()
    if not who.authenticated:
        # Same answer as ``GET /projects``: a failed credential is told so, and
        # nobody at all is owed nothing.
        resolver.reject_failed_credential(who)
        return ok({"topics": []})
    projects = await ProjectRepository(db).list_visible_to(
        handle=who.handle, user_id=who.user_id
    )
    topics = await TopicService(db).names_in_projects(
        [p.id for p in projects], viewer=who.handle
    )
    return ok(
        {
            "topics": [
                {
                    "id": str(t.id),
                    "project_id": str(t.project_id),
                    "title": t.title,
                    "kind": t.kind,
                    "status": t.status,
                    "members_only": t.members_only,
                }
                for t in topics
            ]
        }
    )


@router.get("/{topic_id}")
async def get_topic(
    topic_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """One room's header.

    A card's id is not an address — it is read through its room
    (`GET /topics/{room}/tasks/{card}`), which is where the person reading it
    already is.

    Authorized like the routes beside it (`/comments`, `/doc`). It was not, and
    the sibling routes' having been is what made that a gap rather than a
    policy: a logged-in caller from another project could read any topic's title
    just by holding its id. Measured, not inferred.

    Carries 与我的相关性 too, for the same reason it carries `last_activity_at`
    and `activity`: this route and `list_topics` are the pair that fill the
    derived fields, and a header opened directly (deep link, refresh) would
    otherwise report `awaits_me: false` on a topic that IS waiting on you.
    Every OTHER endpoint returning a TopicOut leaves them at their default.
    """
    service = TopicService(db)
    place = await place_or_404(db, topic_id)
    if place.task is not None:
        raise NotFoundError(say("topicNotFound"))
    topic = place.room
    await TopicMemberService(db)._repo.list_for_topics([topic.id])
    actor = await _actor_in_place(resolver, place)
    # 这几处的房间是同一批，问的也是同一件事：一次问完再传下去，别让它们各问一
    # 遍（`_awaiting_an_answer`、`MemberWaits.for_rooms`、
    # `topics_signals.at_work`）。
    room_threads = await threads_of_rooms(db, [topic.id])
    last_activity = await service.last_activity_for_topics([topic.id])
    relevance = await service.relevance_for_topics(
        [topic],
        _viewer(actor),
        team=project_team,
        cards=AcceptService(db),
        decisions=ProjectNotificationService(db),
    )
    seats = await TopicMemberService(db).addressable_agent_handles(
        topic.project_id, [topic.id]
    )
    live = await topics_signals.live_cards(db, [topic.id])
    cards = topics_signals.own_cards(live)
    managed = (
        await TopicMemberService(db).managed_topic_ids([topic], actor.handle)
        if actor.authenticated
        else set()
    )
    asked = await _awaiting_an_answer(db, [topic.id], threads=room_threads)
    # 同 `list_topics`：谁在这间房自己那条线上、和它的支线里干活；名下的活也交给同一次读。
    underway = await TaskService(db).tasks_of_rooms([topic.id])
    tasks_of_room: dict[uuid.UUID, list[uuid.UUID]] = {}
    for task_id, room_id in underway.items():
        tasks_of_room.setdefault(room_id, []).append(task_id)
    incomplete, incomplete_tasks = await _unresolved_failures(
        db, [topic.id], tasks_of_room
    )
    waits = await MemberWaits(db).for_rooms(
        [topic.id],
        now=datetime.now(UTC),
        stuck_rooms=topics_signals.stuck_on_checks(live),
        threads=room_threads,
    )
    working = await topics_signals.at_work(
        db, [topic.id], threads=room_threads, tasks=underway
    )
    running_ids = set(working.rooms)
    viewer = _viewer(actor)
    unseen = (
        await TaskService(db).unseen_deliveries([topic.id], viewer) if viewer else set()
    )
    # 同 `list_topics`：这一间房里有没有人回复了我、而我还没处理（侧栏黄灯）。
    answered = await service.replied_to_me([topic.id], viewer) if viewer else set()
    # 同 `list_topics`：侧栏的呼吸绿问的是这间房、它的支线、它名下的活里有没有会话在跑。
    moving_ids = set(working.rooms) | set(working.threads)
    moving_ids |= {underway[task_id] for task_id in working.tasks}
    moving_ids |= topics_signals.delivering_rooms(live)
    rails = await rail_tasks(
        db,
        actor,
        [topic.id],
        delivered_of=unseen.__contains__,
        incomplete_of=incomplete_tasks.get,
        running_of=working.tasks.__contains__,
    )
    return ok(
        {
            **_topic_out(
                topic,
                running_ids,
                last_activity,
                relevance,
                cards,
                managed_ids=managed,
                asked=asked,
                asks_me=_asks_me(asked, viewer),
                activity=working.rooms.get(topic.id),
                waits=waits.get(topic.id),
                working_in_threads=working.threads.get(topic.id),
                unseen=unseen,
                answered=answered,
                incomplete=incomplete,
                moving=moving_ids,
                seats=seats,
            ),
            "my_tasks": rails[topic.id],
        }
    )


@router.get("/{topic_id}/blocks")
async def list_topic_blocks(
    topic_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    limit: Annotated[int | None, Query(ge=1, le=500)] = None,
    before: uuid.UUID | None = None,
    after: uuid.UUID | None = None,
    around: uuid.UUID | None = None,
    kind: Annotated[list[BlockKind] | None, Query()] = None,
    author: Annotated[str | None, Query(min_length=1, max_length=120)] = None,
    shown: bool | None = None,
    stored_after: Annotated[int | None, Query(ge=0)] = None,
) -> dict:
    """The topic's conversation timeline, oldest-first.

    Authorized, like `/comments` and `/doc` beside it. This one carries the
    conversation ITSELF, and it was the only unguarded route of the three that
    did: measured on a test server, a logged-in caller belonging to no part of
    the project read another team's messages verbatim by holding a topic id.

    Paging is OPT-IN: with no `limit` this returns the whole timeline, exactly
    as it always has. That default is deliberate — agents read this endpoint to
    review history (`platform_request GET /topics/{id}/blocks`), and a default window
    would silently truncate them with no way to notice. Callers that DO page get
    `has_more` / `oldest_id` (older blocks exist above) and `has_newer` /
    `newest_id` (newer ones below), and can walk either way.

    - `?limit=N`                  → the newest N blocks (chat is bottom-anchored)
    - `?limit=N&before=<block_id>` → the N blocks immediately older than that one
    - `?limit=N&after=<block_id>`  → the N blocks immediately newer than that one
    - `?limit=N&around=<block_id>` → that block with about N/2 on each side: a
      conversation opened at one message (a search hit, a quoted reply)
    - `?limit=N&stored_after=<seq>` → the N blocks stored next after the one
      numbered so, in the order they were stored: what a page holding
      everything up to that number missed (the rooms socket's `newest`).
      `has_newer` says more were stored after them.

    Filters narrow the timeline inside the paging, so a page holds `limit` rows
    of what was asked for and `has_more` counts the same set (filtering a page
    afterwards returns fewer rows and pages through holes):

    - `kind` (repeatable): only these kinds of block;
    - `author`: only blocks signed by this handle;
    - `shown=true`: only what the room's conversation shows
      (`indexed_rows.SHOWN_ROWS`), the way the room reads it; `shown=false`, only
      what it keeps out of the room (notices for an agent, unresolved-reference
      lines). Session output is not here: it is read from `/session-records`.

    Cursors still name any block of the conversation, shown or not.

    Each block carries what a reader draws with it, so the page is whole on its
    own: `reactions`, the 支线 under a main-line message (`thread`), a routine's
    run (`routine_run`), and `tasks` — the tasks made from a message, under it,
    and the task a room-line row says began there (`room_task_rows`), as the
    room's task list has them.
    """
    if sum(c is not None for c in (before, after, around, stored_after)) > 1:
        raise ValidationError(say("cursorOneAnchor"))
    if limit is None and (
        after is not None or around is not None or stored_after is not None
    ):
        raise ValidationError(say("cursorNeedsLimit"))
    place = await place_or_404(db, topic_id)
    actor = await _actor_in_place(resolver, place)
    repo = BlockRepository(db)

    async def cursor_of(block_id: uuid.UUID | None) -> Block | None:
        if block_id is None:
            return None
        cursor = await repo.get(block_id)
        # An unknown cursor must not silently degrade into "newest N" — that
        # would hand the caller a duplicate page it can't distinguish. A cursor
        # from another conversation in this room is as wrong as one from
        # another room.
        if cursor is None or cursor.conversation_id != place.conversation_id:
            raise NotFoundError(say("cursorMessageNotFound"))
        return cursor

    older_than = await cursor_of(before)
    newer_than = await cursor_of(after)
    centre = await cursor_of(around)
    narrow = {"kinds": kind, "author": author, "shown": shown}
    if limit is None:
        blocks = await repo.list_for_topic(place.conversation_id, **narrow)
        if older_than is not None:
            blocks = [
                b
                for b in blocks
                if (b.created_at, b.id) < (older_than.created_at, older_than.id)
            ]
        has_more = has_newer = False
    elif centre is not None:
        above = await repo.page_for_topic(
            place.conversation_id, limit=limit // 2, before=centre, **narrow
        )
        below = await repo.page_for_topic(
            place.conversation_id, limit=limit - limit // 2, after=centre, **narrow
        )
        # The block it opens at is in the window only if the filters keep it.
        kept = [centre] if await repo.holds(centre, **narrow) else []
        blocks = [*above.items, *kept, *below.items]
        has_more, has_newer = above.has_more, below.has_more
    elif stored_after is not None:
        page_result = await repo.page_for_topic(
            place.conversation_id, limit=limit, stored_after=stored_after, **narrow
        )
        blocks = page_result.items
        has_more, has_newer = True, page_result.has_more
    elif newer_than is not None:
        page_result = await repo.page_for_topic(
            place.conversation_id, limit=limit, after=newer_than, **narrow
        )
        blocks = page_result.items
        # Paging down from a block means that block is above this page.
        has_more, has_newer = True, page_result.has_more
    else:
        page_result = await repo.page_for_topic(
            place.conversation_id, limit=limit, before=older_than, **narrow
        )
        blocks = page_result.items
        # Paging up from a block means that block is below this page.
        has_more, has_newer = page_result.has_more, older_than is not None
    total = await repo.count_for_topic(place.conversation_id, **narrow)
    # Emoji reactions ride the same payload — ONE batch query, no per-block N+1.
    by_id = {b.id: b for b in blocks}
    block_ids = list(by_id)
    reactions = await repo.reactions_for_blocks(block_ids)
    # A routine's runs are messages of its teammate's: how each went.
    runs = (
        await routine_reads.runs_under(db, block_ids) if place.inner_id is None else {}
    )
    if place.inner_id is None:
        # The AI teammates at work in each 支线 right now (`topics_signals`).
        seats = await topics_signals.answering(db)
        threads = await thread_reads.under_messages(
            db,
            block_ids,
            replying=lambda thread: seats.get(thread, []),
            roots=by_id,
        )
    else:
        threads = {}
    # The tasks made from these messages, and the ones these rows say began:
    # the page draws their cards without reading the room's whole task list.
    carried = await tasks_under_blocks(
        db,
        actor,
        room_id=place.room_id,
        project_id=place.project_id,
        blocks=blocks,
    )
    items = []
    for b in blocks:
        item = BlockOut.model_validate(b).model_dump(mode="json")
        if b.id in carried:
            item["tasks"] = carried[b.id]
        if b.id in reactions:
            item["reactions"] = reactions[b.id]
        if b.id in threads:
            item["thread"] = threads[b.id]
        if b.id in runs:
            item["routine_run"] = runs[b.id]
        items.append(item)
    return ok(
        {
            **page(items, total),
            "has_more": has_more,
            # Feed this back as `before` to fetch the next older page.
            "oldest_id": str(blocks[0].id) if blocks else None,
            "has_newer": has_newer,
            # Feed this back as `after` to fetch the next newer page.
            "newest_id": str(blocks[-1].id) if blocks else None,
        }
    )


async def _opening_ids(db: AsyncSession, conversation_id: uuid.UUID) -> list[uuid.UUID]:
    """The blocks a 支线 reads besides its own: the message it hangs under and
    the files sent with it. They stay in the main line and they are the 支线's
    own first input, so reading the 支线 has to reach them — the agent tools
    read this endpoint, and the session is held to answer what is here.

    Empty for a channel's main line and for a task's line.
    """
    return [block.id for block in await thread_opening(db, conversation_id)]


async def _history_block(
    repo: BlockRepository,
    conversation_id: uuid.UUID,
    block_id: uuid.UUID,
    also: Collection[uuid.UUID] = (),
) -> Block:
    block = await repo.get(block_id)
    if (
        block is None
        or (block.conversation_id != conversation_id and block.id not in also)
        or not await repo.holds(block)
    ):
        raise NotFoundError("Message not found in this conversation")
    return block


async def _channel_block(
    db: AsyncSession, repo: BlockRepository, room_id: uuid.UUID, block_id: uuid.UUID
) -> Block:
    block = await repo.get(block_id)
    if block is None or await room_of(db, block.conversation_id) != room_id:
        raise NotFoundError("Message not found in this channel")
    return block


@router.get("/{topic_id}/history")
async def read_chat_history(
    topic_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    before: uuid.UUID | None = None,
    after: uuid.UUID | None = None,
    reply_to: uuid.UUID | None = None,
    q: Annotated[str | None, Query(min_length=1, max_length=1000)] = None,
    kind: BlockKind | None = None,
    author: str | None = None,
    channel: bool = False,
) -> dict:
    """Read stored chat, including structured events and reactions.

    One conversation's: a room's own line, a task's or a 支线's. With
    ``channel`` it is every conversation of the channel at once — its main
    line, its 支线 and its tasks — which is what a search for something settled
    elsewhere in the channel needs. Replies are direct children; follow their
    IDs for nested replies. Search is literal, case-insensitive substring
    matching over content, metadata and quoted document text.
    """
    place = await place_or_404(db, topic_id)
    await _actor_in_place(resolver, place)
    if before is not None and after is not None:
        raise ValidationError("Use before or after, not both")
    repo = BlockRepository(db)
    # The channel-wide read has the main line in scope already.
    also = [] if channel else await _opening_ids(db, place.conversation_id)
    parent = None
    if reply_to is not None:
        parent = await _history_block(repo, place.conversation_id, reply_to, also)
    cursor = None
    if cursor_id := before or after:
        cursor = (
            await _channel_block(db, repo, place.room_id, cursor_id)
            if channel
            else await _history_block(repo, place.conversation_id, cursor_id, also)
        )
    result = await repo.page_for_topic(
        place.conversation_id,
        whole_room=place.room_id if channel else None,
        also=also,
        limit=limit,
        before=cursor if before else None,
        after=cursor if after else None,
        query=q,
        reply_to=reply_to,
        author=author,
        kinds=[kind] if kind else list(BlockKind),
    )
    included = [*result.items, *([parent] if parent else [])]
    reactions = await repo.reactions_for_blocks([b.id for b in included])
    serialized = {
        b.id: {
            **BlockOut.model_validate(b).model_dump(mode="json"),
            "reactions": reactions.get(b.id, []),
        }
        for b in included
    }
    # A reply whose parent is on an older page carries one line of it, so the
    # cue does not vanish just because the reader has not paged back that far.
    # Parents already in this page are left to the full block; the parent itself
    # is never added to the page.
    previews = await reply_previews(db, result.items)
    page_ids = {b.id for b in result.items}
    for b in included:
        preview = previews.get(b.id)
        if preview is not None and b.reply_to not in page_ids:
            serialized[b.id]["reply_preview"] = preview
    return ok(
        {
            "data": [serialized[b.id] for b in result.items],
            "has_more": result.has_more,
            "oldest_id": str(result.items[0].id) if result.items else None,
            "newest_id": str(result.items[-1].id) if result.items else None,
            "direction": "after" if after else "before",
            "reply_to": serialized[parent.id] if parent else None,
        }
    )


@router.get("/{topic_id}/history/{block_id}")
async def read_chat_message(
    topic_id: uuid.UUID,
    block_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """Read one block of any kind, including a task-card comment or doc node."""
    place = await place_or_404(db, topic_id)
    await _actor_in_place(resolver, place)
    repo = BlockRepository(db)
    also = await _opening_ids(db, place.conversation_id)
    block = await _history_block(repo, place.conversation_id, block_id, also)
    item = BlockOut.model_validate(block).model_dump(mode="json")
    preview = (await reply_previews(db, [block])).get(block.id)
    if preview is not None:
        item["reply_preview"] = preview
    item["reactions"] = await repo.reactions_for_block(block_id)
    return ok(item)


@router.get("/{topic_id}/usage")
async def topic_usage(
    topic_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """资源用量 (spec §9.1): token/cost for this room.

    The room's whole bill, its tasks included: a task's sessions spend under
    the room as well as under the task.
    """
    place = await place_or_404(db, topic_id)
    await _actor_in_place(resolver, place)
    return ok(await UsageRepository(db).for_topic(place.room_id))


# The agent-facing gate-output slice: enough to read the failure, small enough
# for a prompt. The full output is already capped at persist time (GATE_TAIL).
_GATE_OUTPUT_TAIL = 2000


def _card_snapshot(card: CardRow, reviewers: list[str]) -> dict:
    return {
        "id": str(card.id),
        "status": str(card.status),
        "reviewer": "、".join(reviewers),
        "decided_by": card.decided_by,
        "decided_at": card.decided_at.isoformat() if card.decided_at else None,
        "note": card.note,
        "gate_passed_at": (
            card.gate_passed_at.isoformat() if card.gate_passed_at else None
        ),
        "gate_output_tail": card.gate_output[-_GATE_OUTPUT_TAIL:],
        # PR-based accept (#188 §5.1): the agent checks its PR's CI itself
        # (`gh api`) — the snapshot carries the pointer.
        "pr_number": card.pr_number,
        "pr_url": card.pr_url,
        "created_at": card.created_at.isoformat(),
    }


def _disk_snapshot(root: str) -> dict | None:
    try:
        du = shutil.disk_usage(root)
    except OSError:
        return None
    used_pct = round((du.total - du.free) * 100 / du.total) if du.total else None
    return {
        "free_gb": round(du.free / 2**30, 1),
        "total_gb": round(du.total / 2**30, 1),
        "used_pct": used_pct,
    }


def _credit_reset_text(at: datetime) -> str:
    """When credits come back, as the page would say it: 「11月1日 00:00」."""
    local = at.astimezone(_TZ)
    return f"{local.month}月{local.day}日 {local.strftime('%H:%M')}"


@router.get("/{topic_id}/status")
async def topic_status(
    topic_id: uuid.UUID,
    db: DbSession,
    runner: Annotated[AgentWorkRunner, Depends(get_work_runner)],
    broker: Annotated[InProcessBroker, Depends(get_broker)],
    chat_service: Annotated[ChatService, Depends(get_chat_service)],
    resolver: ActorResolverDep,
) -> dict:
    """盲飞防护: one snapshot of "what is going on" for this topic — accept
    cards with their gate output, the current/last turn's state, and the
    platform waterlines (disk/queue/credits) — so an agent (via `cheese
    status`) or a debugging human doesn't have to poll several endpoints and
    guess. Read path, open like the rest of the MVP read surface.

    ``stall`` answers the question nothing here could answer before: is a
    session here stopped in the middle of a call? ``turn`` cannot — it is a
    ring buffer of what turns did, so it is empty after a restart and says
    `running` about a turn killed with the process. See
    ``TopicService.stall_signal``."""
    topics = TopicService(db)
    place = await place_or_404(db, topic_id)
    await _actor_in_place(resolver, place)
    filed, _ = await AcceptService(db).list_for_topic(place.room_id)
    cards = [
        card for card in filed if place.task is None or card.task_id == place.task.id
    ]
    card_reviewers = await AcceptService(db).reviewer_handles_for_cards(
        [card.id for card in cards]
    )
    credits = await UsageService(db).project_status_credits(place.project_id)
    reset = credits.pop("resets_at_dt", None)
    credits["resets_at"] = None if reset is None else _credit_reset_text(reset)
    turn = runner.topic_work(topic_id)
    stall = await topics.stall_signal(
        place.room_id, alive_for_s=broker.alive_for_s(str(place.room_id))
    )
    return ok(
        {
            "topic": {
                "id": str(place.room_id),
                "title": place.title,
                "status": str(place.room.status),
            },
            "turn": turn,
            "stall": stall,
            "cards": [_card_snapshot(c, card_reviewers.get(c.id, [])) for c in cards],
            "platform": {
                "active_turns": runner.active_work_count(),
                "queued_turns": await runner.project_queue_depth(place.project_id),
                "disk": _disk_snapshot(settings.workspace_root),
                "credits": {
                    "unlimited": credits["unlimited"],
                    "remaining": credits["credits_remaining"],
                    "total": credits.get("total"),
                    "resets_at": credits.get("resets_at"),
                    "project_remaining": credits.get("project_remaining"),
                },
            },
        }
    )


@router.get("/{topic_id}/children")
async def list_topic_children(
    topic_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    topic = await TopicService(db).get_or_404(topic_id)
    actor = await resolver.resolve(topic_id=topic_id, project_id=topic.project_id)
    await resolver.authorize_topic(
        actor, project_id=topic.project_id, topic_id=topic_id, room=topic
    )
    children = await TopicService(db).list_children(topic_id)
    # 综合 is every channel's parent: a private one is listed to its people only.
    seen = await resolver.readable_topic_ids(
        actor,
        project_id=topic.project_id,
        topics=[t for t in children if t.members_only],
    )
    items = [
        TopicOut.model_validate(t).model_dump(mode="json")
        for t in children
        if not t.members_only or t.id in seen
    ]
    return ok(page(items, len(items)))


@router.get("/{topic_id}/progress")
async def get_topic_progress(
    topic_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """进度层 (#187): 芝士's checklist for this conversation — a room's, or a
    task's, written by its own session — as of the last turn to touch it. Read
    on open: between turns there is no WS stream to carry it, and "做到哪了" has
    to be visible without summoning anyone."""
    place = await place_or_404(db, topic_id)
    await _actor_in_place(resolver, place)
    items, updated_at = await TopicService(db).get_progress(place.conversation_id)
    return ok(
        {
            "items": items,
            "updated_at": updated_at.isoformat() if updated_at else None,
        }
    )


class TodoIn(BaseModel):
    # 200 / 30: the limits `todo_write` states to the model (`sandbox/cheese`,
    # TODO_MAX_CHARS / TODO_MAX_ITEMS). The CLI ships to machines on its own and
    # cannot import them, so they are written twice.
    content: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
    ]
    status: Literal["pending", "in_progress", "completed"]


class ProgressIn(BaseModel):
    todos: list[TodoIn] = Field(min_length=1, max_length=30)
    # Post a new checklist message instead of editing the current one. Which
    # request a list belongs to is the agent's call: it sets this when someone
    # brings it a new one.
    new: bool = False
    # Edit this checklist of the writer's rather than their newest: a person
    # ticks a step on whichever of their lists they are looking at.
    message: uuid.UUID | None = None
    # One line on what landed, written when the work is done; shown under the
    # list in the same message. Same ceiling as an item.
    result: (
        Annotated[
            str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
        ]
        | None
    ) = None


@router.put("/{topic_id}/progress", dependencies=[ROUTE_DECIDES])
async def write_topic_progress(
    topic_id: uuid.UUID,
    body: ProgressIn,
    db: DbSession,
    resolver: ActorResolverDep,
    chat: Annotated[ChatService, Depends(get_chat_service)],
) -> dict:
    """A member's whole checklist as their message: `todo_write` for an agent,
    the composer's checklist for a person. The first write posts it, later
    ones edit the writer's own current list (or the one ``message`` names)
    through the edit every author has; ``new`` posts another. Whole-list
    replace, so the message says exactly what its writer last said.

    Only a writer seated as one of the room's agents also stores the list as
    the room's progress (进度层): that is the plan the room's next turn is
    handed back as its own, and what 总览 shows. A person's list stored there
    would hand the agent somebody else's plan. With ``task`` a task's session
    is writing its task's list: stored under the task, pushed on the task's
    channel, and nothing posted in the room.
    """
    place = await place_or_404(db, topic_id)
    actor = await resolver.resolve(
        topic_id=place.conversation_id, project_id=place.project_id
    )
    inner_id = place.task_id
    if inner_id is None:
        await TopicMemberService(db).list_for_topic(place.room_id)
    await resolver.authorize_topic(
        actor,
        project_id=place.project_id,
        topic_id=place.room_id,
        room=place.room,
        enforce=True,
    )
    if not actor.authenticated:
        # The global development token is nobody, and a message needs an author.
        raise ForbiddenError("Sign in to write a checklist")
    # A task's checklist is its own session's, shown as its progress; a room's or
    # a 支线's is a seated agent's, or a person's said like any message of theirs.
    plans_the_turn = (
        resolver.credential_conversation() == inner_id
        if inner_id is not None
        else await TopicMemberService(db).holds_an_agent_seat(place.room, actor.handle)
    )
    if inner_id is not None and not plans_the_turn:
        raise ForbiddenError("A task's checklist is written by its session")
    if body.new and body.message is not None:
        raise ValidationError("message and new cannot both be given")
    current = None
    if inner_id is None and not body.new:
        current = await BlockRepository(db).current_checklist(
            place.conversation_id, actor.handle, message=body.message
        )
        if current is None and body.message is not None:
            raise NotFoundError("Checklist not found in this room")
        if current is not None and current.author != actor.handle:
            raise ForbiddenError("Only the author can edit this message")
    if not plans_the_turn and current is None:  # Posting is speaking; editing is not.
        await require_person_speaks(db, actor, place)
    items = [
        {"id": str(number), "subject": todo.content, "status": todo.status}
        for number, todo in enumerate(body.todos, start=1)
    ]
    runner = get_work_runner()
    turn_id = None
    if plans_the_turn:
        work = runner.live_work_for_topic(place.conversation_id)
        turn_id = uuid.UUID(work["turn_id"]) if work is not None else None
        await TopicService(db).save_progress(
            place.conversation_id, items, turn_id=turn_id
        )
        await db.commit()
    if inner_id is not None:
        await get_broker().publish(str(inner_id), {"type": "todo", "items": items})
        return ok({"items": items})
    text = checklist_text(items, body.result)
    checklist = {"items": items, "result": body.result}
    if current is not None:
        message = await edit_message(
            db,
            get_broker().publish,
            current.id,
            normalize_text=text_as_sent,
            notify_mentions=announce_edited_message,
            editor=actor.handle,
            content=text,
            notify=chat,
            dispatch=partial(
                dispatch_pending, chat.session_factory, chat=chat, runner=runner
            ),
            record_instruction=record_task_instruction,
            checklist=checklist,
        )
        return ok({"items": items, "message_id": message["id"], "posted": False})
    message = await chat.messages.persist_assistant_message(
        project_id=place.project_id,
        topic_id=place.room_id,
        room=place.room,
        inner_id=place.thread_id,
        text=await project_refs_text(db, place.project_id, place.room_id, text),
        turn_id=turn_id,
        reply_to=None,
        roster=None,
        topic_refs=[],
        author=actor.handle,
        # An agent's list is its own output; a person's is said to the room and
        # waits to be read like anything else they post.
        own_output=plans_the_turn,
        extra_meta={CHECKLIST_META_KEY: checklist},
    )
    assert message is not None  # a publication with no eid never deduplicates
    await get_broker().publish(
        str(place.conversation_id), {"type": "assistant_block", "block": message}
    )
    if turn_id is not None:
        runner.note_session_output(turn_id, tool=False)
    return ok({"items": items, "message_id": message["id"], "posted": True})


@router.post("/{topic_id}/summon")
async def summon_agent(
    at: Annotated[InChannel, Can(Permission.ask_agent, at="topic_id")],
    body: dict,
    db: DbSession,
    chat: Annotated[ChatService, Depends(get_chat_service)],
    runner: Annotated[AgentWorkRunner, Depends(get_work_runner)],
) -> dict:
    """叫芝士读一遍还没交到它手上的消息 —— 忘了 @ 的那一条的补救。

    没 @ 的消息从来不会丢：它在待读窗口里等着下一轮把它捎上（没 @ 不等于没说）。
    但「等下一轮」在一个安静的房间里等于永远，而房间安静恰恰是忘了 @ 之后的常态。
    这个接口只做一件事：现在就开那一轮。它**不再发一条消息**，因为那条消息已经
    在时间线上了——补发一条一模一样的，读的人要自己分辨哪条是真的。

    两种情况下它什么都不做，并如实说明是哪一种：房间已经在干活（正在跑的那一轮
    会自己把没 @ 的消息接过去），或者根本没有待读的东西，也没有欠着的输入。两种
    都不是错误，只是这一下不需要花钱。
    """
    place, actor = at.place, at.actor
    # The conversation the button is in: a 支线's or a task's retry runs there.
    if await chat.has_running_turn(place.conversation_id):
        return ok({"started": False, "reason": "working"})
    if not await chat.has_unread_input(place.conversation_id):
        # 房间里没有待读的消息，不等于这一下没事可做：平台自己欠这个频道的输入
        # （任务的开始指令那类）也在账本上等着，等的是退避时间。点「重试」的意思
        # 就是「现在」—— 不然那句话只能等这个项目里碰巧发生的下一件事把它捎上。
        if await redispatch_undelivered(
            chat.session_factory,
            conversation_id=place.conversation_id,
            chat=chat,
            runner=runner,
        ):
            return ok({"started": True})
        return ok({"started": False, "reason": "nothing_pending"})
    # content 在有待读消息时会被待读窗口取代（_converse_impl 的 backlog 分支），
    # 这里正是要那个结果：芝士收到的东西和「当时就 @ 了它」一模一样。这句只在
    # 待读窗口刚好被别人清空的缝隙里当兜底。
    #
    # 交给谁：**这批消息点名交给谁，就交给谁**。房间里坐着不止一位 AI 队友时，
    # 「房间的默认席位」是另一个答案 —— 取它的话，另一位队友的轮次失败之后一点
    # 重试就换成默认芝士来接，而默认芝士那一轮的待读窗口里根本没有点名给那位队友
    # 的消息（`_addressed_to` 按收件人过滤），于是它接了一轮却读不到真正找它的那
    # 句话。没人被点名（没 @ 不等于没说），或者被点名的那位已经不在名册上（被请出
    # 房间），才回落到默认席位。
    members = TopicMemberService(db)
    seat = await chat.pending_seat(place.conversation_id)
    if seat is None or seat not in await members.agent_handles(place.room_id):
        seat = await members.addressable_agent_handle(place.room_id)
    runner.submit(
        chat,
        place.conversation_id,
        author="system",
        content="有人请你看一下这里还没读到的消息，照常处理。",
        # 点名的是按下这个按钮的人，不是平台：他指名这个房间的芝士，寻址结果里
        # 因此恰好有它一个，这一轮才跑得起来。
        addressed=addressed_to_agent(seat),
        nudge_event=(
            say("pendingHandedTo", actor=f"<@{actor.handle}>", seat=f"<@{seat}>")
            if seat
            else say("pendingHandedOver", actor=f"<@{actor.handle}>")
        ),
    )
    return ok({"started": True})


@router.post("/{topic_id}/webhook-token")
async def mint_webhook_token(
    at: Annotated[InChannel, Can(Permission.mint_webhook_token, at="topic_id")],
    db: DbSession,
) -> dict:
    """Mint (or rotate) this place's webhook credential — used by the `cheese`
    CLI to hand a caller a token for POST /webhooks/{topic_id}. Rotating
    invalidates every previously-minted token for this place; the raw value is
    returned once and never recoverable afterwards.

    The token is a write credential for this room: whoever holds it can post
    into the room's timeline as any source, and rotating it locks out every
    token minted before. So only the room's own AI teammate mints it, which
    no person's role grants."""
    place = at.place
    # Scoped to the place, because the webhook wakes the place: a thread's
    # credential minted against the room would deliver into the room instead.
    token = await webhook_service.mint(
        db, topic_id=place.conversation_id, project_id=place.project_id
    )
    await db.commit()
    return ok({"token": token})


def _weekly_window(body: dict) -> tuple[datetime, datetime]:
    """这份周报讲的是哪一段。略过就是「截止到现在的一周」。"""
    until = _parse_moment(body.get("until")) or datetime.now(UTC)
    since = _parse_moment(body.get("since")) or until - timedelta(days=7)
    if since > until:
        raise ValidationError(say("sinceAfterUntil"))
    return since, until


@router.post("/{topic_id}/weekly", dependencies=[ROUTE_DECIDES])
async def record_weekly(
    topic_id: uuid.UUID,
    body: dict,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """记一份周报到周报集 (spec §7.1) —— 项目文档页的「周报集」就是从这条读的。

    一份周报讲的是一段已经过去的时间，不是项目此刻的状态（那是任务和它们的实况
    文档的事），所以它带一个窗口：`since`/`until`。窗口存在 `meta` 上而不是新开一
    列 —— 它是这一条记录的属性，没有第二处会读它。

    `refs` 指向它写在哪：周报集里那一行的「来自话题」靠它跳回去。
    """
    place = await place_or_404(db, topic_id)
    actor = await _actor_in_place(resolver, place)
    report = (body.get("body") or "").strip()
    if not report:
        raise ValidationError(say("bodyRequired"))
    since, until = _weekly_window(body)
    report = await canonicalize_refs(
        db,
        place.project_id,
        report,
        team=project_team,
        exclude_topic_id=place.room_id,
    )
    # 重发幂等 (④)：一轮重新送达时，同一份正文是同一份
    # 周报，不能垒出第二行。轮次之外（人在界面上点）没有 continuation，也就没有
    # 去重 —— 点两次就是两次。
    continuation = get_work_runner().continuation_for(topic_id)
    key = action_key(continuation, "weekly", report) if continuation else None
    if key is not None and not await idem.claim(
        db, key, action="weekly", scope_id=str(topic_id)
    ):
        prior = await idem.stored_result(db, key)
        return ok(prior or {"skipped": True})
    block = await BlockRepository(db).add(
        project_id=place.project_id,
        conversation_id=place.conversation_id,
        author=(
            actor.handle
            if actor.authenticated
            else await TopicMemberService(db).resolve_agent_handle(
                topic_id, room_id=place.room_id
            )
        ),
        author_type=AuthorType.participant,
        content=report,
        kind=BlockKind.weekly,
        refs=[str(topic_id)],
        meta={"since": since.isoformat(), "until": until.isoformat()},
    )
    out = BlockOut.model_validate(block).model_dump(mode="json")
    if key is not None:
        await idem.record_result(db, key, out)
    return ok(out)


@router.post("/{topic_id}/check-result", dependencies=[ROUTE_DECIDES])
async def record_check_result(
    topic_id: uuid.UUID,
    body: CheckResultIn,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """Record what the quick check said about this place's tree.

    It gates nothing. #296 settled that a card is a view of a PR and the real
    CI on that PR decides — a platform-side check voting on delivery is the
    thing that was retired, and this does not bring it back. What it brings
    back is the other half: a red check that the person about to accept can
    SEE. A check whose result goes nowhere is a check nobody bothers to run.

    A timeout is a failure, deliberately. A quick check has a time budget
    because its value IS the speed; one that quietly grew past the budget and
    got reported as "inconclusive" would rot into a second full CI.
    """
    place = await place_or_404(db, topic_id)
    await _actor_in_place(resolver, place)
    if place.task is None:
        raise NotFoundError(say("taskNotFound"))
    task = place.task
    await TaskService(db).record_check(task, ok=body.ok, detail=body.detail)
    await db.commit()
    return ok({"recorded": True, "task_id": str(task.id)})


@router.post("/{topic_id}/clone-from", dependencies=[ROUTE_DECIDES])
async def clone_topic_from(
    topic_id: uuid.UUID,
    body: dict,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """Clone (transcript-fork) another topic's Claude conversation onto THIS
    topic (fusion-design §6 clone — 「复制自」/并行探索, not 分身).

    Auth: the actor must have access to BOTH the target (where the copy lands)
    and the SOURCE (whose conversation is being read out). A token alone is
    necessary-not-sufficient — access is checked per actor on each topic (§4).
    Only meaningful on backends with real session files (tmux/device); the sdk
    backend returns a clear 422 (degrade to dispatching fresh work)."""
    source_raw = (body.get("source_topic_id") or "").strip()
    if not source_raw:
        raise ValidationError(say("sourceTopicRequired"))
    try:
        source_id = uuid.UUID(source_raw)
    except ValueError as exc:
        raise ValidationError(say("sourceTopicInvalid")) from exc
    service = TopicService(db)
    target = await service.get_or_404(topic_id)
    source = await service.get_or_404(source_id)
    actor = await resolver.resolve(topic_id=topic_id, project_id=target.project_id)
    # Must be allowed on BOTH ends: reading the source's session is as sensitive
    # as writing into the target.
    await resolver.authorize_topic(
        actor, project_id=target.project_id, topic_id=topic_id
    )
    await resolver.authorize_topic(
        actor, project_id=source.project_id, topic_id=source_id
    )
    topic = await service.clone_from(
        target_topic_id=topic_id,
        source_topic_id=source_id,
        room_teammate=room_teammate,
        fork=fork_transcript,
        project_harness=harness_for,
        paths=workspace_paths,
        sessions=AgentSessionService(db),
    )
    await db.commit()
    return ok(TopicOut.model_validate(topic).model_dump(mode="json"))
