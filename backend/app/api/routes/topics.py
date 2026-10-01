"""Topic routes."""

import shutil
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolver, ActorResolverDep
from app.api.deps import (
    get_broker,
    get_chat_service,
    get_work_runner,
)
from app.api.place import project_reader
from app.api.response import ok, page
from app.core.config import settings
from app.core.db import get_db
from app.core.errors import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ValidationError,
)
from app.domain.agent.chat import ChatService, project_refs_text
from app.domain.agent.liveness import task_liveness
from app.domain.agent.runtime import (
    AgentWorkRunner,
    addressed_to_agent,
    announce_stale,
)
from app.domain.block.editing import edit_message
from app.domain.block.models import (
    CHECKLIST_META_KEY,
    AuthorType,
    Block,
    BlockKind,
    agent_notice,
)
from app.domain.block.repositories import BlockRepository, ReplyWait, StuckCard
from app.domain.block.schemas import BlockOut
from app.domain.delivery.agent import instance_for_seat, record_agent
from app.domain.delivery.ledger import DeliveryEvent, event_id_for
from app.domain.idempotency import store as idem
from app.domain.idempotency.keys import action_key
from app.domain.identity.actor import Actor
from app.domain.library import service as library
from app.domain.mentions import canonicalize_refs
from app.domain.notification.models import NotificationType
from app.domain.project.repositories import ProjectRepository
from app.domain.review import archive
from app.domain.review.models import AcceptCard
from app.domain.review.repositories import AcceptCardRepository
from app.domain.room_task import presentation
from app.domain.room_task.models import LockKind
from app.domain.room_task.place import Place
from app.domain.room_task.repositories import TaskRepository
from app.domain.room_task.schemas import TaskOut
from app.domain.room_task.services import (
    RoomLockService,
    TaskService,
)
from app.domain.topic import naming
from app.domain.topic.doc_checks import living_doc_warnings
from app.domain.topic.models import Topic, TopicKind
from app.domain.topic.relay import TopicRelayService
from app.domain.topic.repositories import (
    SortOrder,
    TopicProgressRepository,
    TopicRepository,
    TopicSortField,
)
from app.domain.topic.schemas import (
    CheckResultIn,
    DocEditIn,
    LockIn,
    RelayIn,
    SplitIn,
    TopicCreate,
    TopicOut,
)
from app.domain.topic.services import TopicRelevance, TopicService
from app.domain.topic_membership.services import TopicMemberService
from app.domain.usage.repositories import ComputeGrantRepository, UsageRepository
from app.domain.webhook import service as webhook_service

router = APIRouter(prefix="/topics", tags=["topics"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


async def _actor_in_place(resolver: ActorResolver, place: Place) -> Actor:
    """Who is calling here, and whether they may be — identity, then the
    room's roster."""
    actor = await resolver.resolve(
        fallback_handle=None, topic_id=place.room_id, project_id=place.project_id
    )
    await resolver.authorize_topic(
        actor, project_id=place.project_id, topic_id=place.room_id
    )
    return actor


@router.post("")
async def create_topic(
    body: TopicCreate, db: DbSession, resolver: ActorResolverDep
) -> dict:
    actor = await resolver.resolve(fallback_handle=None, project_id=body.project_id)
    await resolver.authorize_project(actor, project_id=body.project_id)
    # The creator becomes the topic's roster owner (fusion-design §3). Resolve
    # them at the trust boundary (P1): the token's actor wins over any body
    # value, so the roster owner is who's really logged in — and body.created_by
    # stays a Phase-0 fallback for token-less callers.
    actor = await resolver.resolve(
        fallback_handle=body.created_by, project_id=body.project_id
    )
    topic = await TopicService(db).create(
        project_id=body.project_id,
        title=body.title,
        parent_id=body.parent_id,
        created_by=actor.handle if actor.handle != "anonymous" else body.created_by,
    )
    service = TopicService(db)
    relevance = await service.relevance_for_topics([topic], _viewer(actor))
    managed = await TopicMemberService(db).managed_topic_ids([topic.id], actor.handle)
    response = ok(_topic_out(topic, set(), {}, relevance, managed_ids=managed))
    # The caller can configure or enter this room as soon as it gets the ID.
    # Dependency teardown commits after the response, which races that request.
    await db.commit()
    return response


def _viewer(actor: Actor) -> str | None:
    """The handle 与我的相关性 is computed against, or None for "no one".

    An unidentified caller resolves to the ``anonymous`` actor (api/auth.py),
    which is a placeholder rather than a person: it is in no roster, on no
    card, and @-able by nobody. Handing it to the relevance query would have it
    honestly answer "unrelated to everything" — three round trips to learn what
    the default already says — so it is filtered out here instead.
    """
    return actor.handle if actor.handle != "anonymous" else None


def _asks_me(
    asked: Mapping[uuid.UUID, str | None], viewer: str | None
) -> set[uuid.UUID]:
    """这些停在提问上的房间里，哪几个在等 `viewer` 回答。

    一道待确认问题只有**发起那一轮的人**能回答，提问那一刻就记在题上
    （`meta.asked`）：旁观的人不该被一道不归他答的题点亮。
    """
    if viewer is None:
        return set()
    return {tid for tid, who in asked.items() if who == viewer}


async def _live_cards(db: AsyncSession, room_ids: list[uuid.UUID]) -> list[AcceptCard]:
    """这些房间（连同名下的活）上还没结算的验收卡，一次查完。

    房间自己那张（`_own_cards`）和「卡停在检查红上」（`_stuck_on_checks`）都从
    这一批里读，不各查一遍。只要没结算的：一张已经决议的卡对看板没有话说
    （`presentation` 读到它会让位给别的判据），所以拉全量只是白读。哪些状态算
    「还没结算」不在这里数——那张表是 `review/archive.py` 维护的，抄第二份就是
    让它们走散。

    """
    if not room_ids:
        return []
    # 结算过的也带上最近一周的：「退回了还没重递」要靠它看出来（`_stuck_on_checks`）。
    return await AcceptCardRepository(db).list_recent_for_places(
        room_ids,
        open_statuses=archive.OPEN_CARD_STATUSES,
        settled_since=datetime.now(UTC) - BlockRepository.REPLY_LOOKBACK,
    )


def _own_cards(cards: list[AcceptCard]) -> dict[uuid.UUID, AcceptCard]:
    """每个房间**自己**那张还没结算的验收卡。

    `task_id is None` 才是房间自己的卡：一条活递的卡把房间记在 `topic_id` 上，不
    过滤的话，一条活在等验收会让它上面那个房间也显示成等验收。
    """
    # 按 created_at 升序回来，所以同一个房间后写的覆盖先写的 = 留下最新那张。
    return {
        c.topic_id: c
        for c in cards
        if c.task_id is None and c.status in archive.OPEN_CARD_STATUSES
    }


def _latest_per_place(cards: list[AcceptCard]) -> dict[tuple, AcceptCard]:
    """同一个地方（房间自己，或某一条活）只留最新那张：旧卡被新卡顶掉后可能还没
    结算，它说的不代表现在。"""
    return {(c.topic_id, c.task_id): c for c in cards}


def _merging(cards: list[AcceptCard]) -> set[uuid.UUID]:
    """名下有一张已采纳、在等检查 / 合并队列的卡的房间（`card_is_merging`）。"""
    return {
        room
        for (room, _), card in _latest_per_place(cards).items()
        if presentation.card_is_merging(card)
    }


async def _rooms_with_running_work(
    db: AsyncSession, chat: ChatService, room_ids: list[uuid.UUID], now: datetime
) -> set[uuid.UUID]:
    """名下有一条活正在「运行中」的房间 —— 一次查完。

    房间自己那一轮结束了，它派出去的分身可能还在干：只看 `running_topic_ids()`
    的话，侧栏的绿点在主 agent 收尾那一刻就灭了，人以为没人在做事。判据和看板
    同一个函数（`presentation.task_is_running`），所以两边不会说出两种话。

    卡不喂进去：「运行中」排在卡前面判，卡对这个答案没有影响。
    """
    tasks = await TaskRepository(db).list_worked_open_for_rooms(room_ids)
    if not tasks:
        return set()
    task_ids = [t.id for t in tasks]
    beats = await TaskRepository(db).last_block_at_for_tasks(task_ids)
    asked = await BlockRepository(db).tasks_awaiting_an_answer(task_ids)
    live = await task_liveness(chat, db, tasks)
    return {
        t.room_id
        for t in tasks
        if presentation.task_is_running(
            presentation.facts_for_task(
                t,
                None,
                beats.get(t.id),
                room_screen_live=live[t.id].screen,
                worker_live=live[t.id].worker,
                awaiting_answer=t.id in asked,
            ),
            now=now,
        )
    }


def _stuck_on_checks(cards: list[AcceptCard]) -> dict[uuid.UUID, StuckCard]:
    """房间（连同名下的活）里，最新那张卡停在「检查红 / 冲突，要 AI 修」上的。

    同一个地方（房间自己，或某一条活）只看最新那张：旧卡被新卡顶掉后可能还没结算，
    它的红不代表现在。判据和看板同一个（`presentation.card_needs_agent_fix`）。
    """
    stuck: dict[uuid.UUID, StuckCard] = {}
    for (room, _), card in _latest_per_place(cards).items():
        kind = presentation.agent_fix_kind(card)
        if kind is not None and room not in stuck:
            stuck[room] = StuckCard(kind=kind, pr=card.pr_number)
    return stuck


def _topic_out(
    topic: Topic,
    running_ids: set[uuid.UUID],
    last_activity: dict[uuid.UUID, datetime],
    relevance: dict[uuid.UUID, TopicRelevance] | None = None,
    cards: dict[uuid.UUID, AcceptCard] | None = None,
    now: datetime | None = None,
    managed_ids: set[uuid.UUID] | None = None,
    asked: Mapping[uuid.UUID, str | None] | None = None,
    asks_me: set[uuid.UUID] | None = None,
    working_ids: set[uuid.UUID] | None = None,
    merging_ids: set[uuid.UUID] | None = None,
    waiting: Mapping[uuid.UUID, ReplyWait] | None = None,
    failed: Mapping[uuid.UUID, datetime] | None = None,
) -> dict:
    """TopicOut plus the signals the ORM row cannot carry: the in-memory
    turn-running flag (separate from `status`/归档 — see TopicOut.running: a
    topic can be active-and-idle or active-and-mid-turn, and only this tells
    them apart), 最后活动时间, which is derived from the topic's blocks, and
    与我的相关性, which depends on WHO is asking and so cannot live on the row
    at all.

    `presentation` is the last of them: which column of the board this room is
    in and the one phrase to print on it, derived from the same facts the row
    already carries plus its live card (`room_task/presentation.py`)."""
    out = TopicOut.model_validate(topic)
    out.can_archive = topic.kind != TopicKind.root and topic.id in (
        managed_ids or set()
    )
    # Assign before dumping so the instant is serialized by the same schema as
    # created_at/updated_at — a hand-rolled isoformat() here rendered "+00:00"
    # where every other timestamp in the payload says "Z".
    out.last_activity_at = last_activity.get(topic.id)
    wait = (waiting or {}).get(topic.id)
    # 检查红了但 AI 此刻正在这个房间（或名下的活）里干活：它就是在处理，不算没人管。
    if (
        wait is not None
        and wait.source == "card"
        and (topic.id in running_ids or topic.id in (working_ids or set()))
    ):
        wait = None
    out.awaiting_reply_since = wait.since if wait else None
    out.reply_wait_reason = wait.reason if wait else None
    out.reply_wait_pr = wait.pr if wait else None
    out.turn_failed_at = (failed or {}).get(topic.id)
    mine = (relevance or {}).get(topic.id, TopicRelevance())
    # 芝士停在一道只有我能回答的问题上，同样是「在等我」——而且比一张卡更急：卡是
    # 一轮结束后的状态，提问是一轮**停在半路**。它也蕴含参与，理由同上。
    asking_me = topic.id in (asks_me or set())
    out.i_participate = mine.i_participate or asking_me
    out.awaits_me = mine.awaits_me or asking_me
    data = out.model_dump(mode="json")
    # 侧栏的绿点：房间自己那一轮在跑，或它名下有一条活在跑。看板那一格
    # （下面的 facts_for_room）仍只看房间自己——活在看板上有自己的一格。
    data["running"] = topic.id in running_ids or topic.id in (working_ids or set())
    # 绿灯常亮：已采纳、在等合并落地。有 AI 在干活时让位给「在跑」（闪）。
    data["merging"] = not data["running"] and topic.id in (merging_ids or set())
    facts = presentation.facts_for_room(
        topic,
        running_ids,
        (cards or {}).get(topic.id),
        awaiting_answer=topic.id in (asked or {}),
    )
    data["presentation"] = presentation.room_presentation(
        facts, now=now or datetime.now(UTC)
    ).as_dict()
    return data


@router.get("")
async def list_topics(
    project_id: uuid.UUID,
    db: DbSession,
    runner: Annotated[AgentWorkRunner, Depends(get_work_runner)],
    chat: Annotated[ChatService, Depends(get_chat_service)],
    resolver: ActorResolverDep,
    sort: TopicSortField | None = None,
    order: SortOrder = "asc",
    active_since: datetime | None = None,
    topic: str = "",
) -> dict:
    """The project's topics.

    `sort=last_activity_at` orders by when something last HAPPENED in each topic
    (its newest block), and `active_since=<ISO instant>` keeps only the topics
    active at or after it — "最近活跃的话题". Neither reads `updated_at`, which
    only moves when the topic's own fields change.

    Every row also carries 与我的相关性 (`i_participate`/`awaits_me`) for the
    caller — this is the endpoint the sidebar groups from.
    """
    actor = await project_reader(db, resolver, project_id, topic)
    service = TopicService(db)
    topics, last_activity, total = await service.list_for_project(
        project_id, sort=sort, order=order, active_since=active_since
    )
    running_ids = runner.running_topic_ids()
    relevance = await service.relevance_for_topics(topics, _viewer(actor))
    live = await _live_cards(db, [t.id for t in topics])
    cards = _own_cards(live)
    managed = (
        await TopicMemberService(db).managed_topic_ids(
            [t.id for t in topics], actor.handle
        )
        if actor.authenticated
        else set()
    )
    # 哪几个房间停在一个未回答的提问上（房间自己那条线）——一次查完。
    asked = await BlockRepository(db).rooms_awaiting_an_answer([t.id for t in topics])
    asks_me = _asks_me(asked, _viewer(actor))
    # 一次，给整页用同一个「现在几点」——见 list_project_tasks 里同一行的理由。
    now = datetime.now(UTC)
    working = await _rooms_with_running_work(db, chat, [t.id for t in topics], now)
    # 侧栏红灯的两个来源，各一次查完：有人点了 AI 的名还没人接；最近一轮报错了。
    waiting = await BlockRepository(db).rooms_awaiting_a_reply(
        [t.id for t in topics],
        now=now,
        stuck_rooms=_stuck_on_checks(live),
    )
    failed = await BlockRepository(db).rooms_with_a_failed_turn(
        [t.id for t in topics], now=now
    )
    items = [
        _topic_out(
            t,
            running_ids,
            last_activity,
            relevance,
            cards,
            now,
            managed,
            asked,
            asks_me,
            working,
            merging_ids=_merging(live),
            waiting=waiting,
            failed=failed,
        )
        for t in topics
    ]
    return ok(page(items, total))


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
    who = await resolver.resolve(fallback_handle=None)
    if not who.authenticated:
        # Same answer as ``GET /projects``: a failed credential is told so, and
        # nobody at all is owed nothing.
        resolver.reject_failed_credential(who)
        return ok({"topics": []})
    projects = await ProjectRepository(db).list_visible_to(
        handle=who.handle, user_id=who.user_id
    )
    topics = await TopicRepository(db).names_in_projects([p.id for p in projects])
    return ok(
        {
            "topics": [
                {
                    "id": str(t.id),
                    "project_id": str(t.project_id),
                    "title": t.title,
                    "kind": t.kind,
                    "status": t.status,
                }
                for t in topics
            ]
        }
    )


@router.get("/{topic_id}")
async def get_topic(
    topic_id: uuid.UUID,
    db: DbSession,
    runner: Annotated[AgentWorkRunner, Depends(get_work_runner)],
    chat: Annotated[ChatService, Depends(get_chat_service)],
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
    and `running`: this route and `list_topics` are the pair that fill the
    derived fields, and a header opened directly (deep link, refresh) would
    otherwise report `awaits_me: false` on a topic that IS waiting on you.
    Every OTHER endpoint returning a TopicOut leaves them at their default.
    """
    service = TopicService(db)
    place = await service.place_or_404(topic_id)
    topic = place.room
    actor = await _actor_in_place(resolver, place)
    last_activity = await service.last_activity_for_topics([topic.id])
    relevance = await service.relevance_for_topics([topic], _viewer(actor))
    live = await _live_cards(db, [topic.id])
    cards = _own_cards(live)
    managed = (
        await TopicMemberService(db).managed_topic_ids([topic.id], actor.handle)
        if actor.authenticated
        else set()
    )
    asked = await BlockRepository(db).rooms_awaiting_an_answer([topic.id])
    working = await _rooms_with_running_work(db, chat, [topic.id], datetime.now(UTC))
    return ok(
        _topic_out(
            topic,
            runner.running_topic_ids(),
            last_activity,
            relevance,
            cards,
            managed_ids=managed,
            asked=asked,
            asks_me=_asks_me(asked, _viewer(actor)),
            working_ids=working,
            merging_ids=_merging(live),
            waiting=await BlockRepository(db).rooms_awaiting_a_reply(
                [topic.id],
                now=datetime.now(UTC),
                stuck_rooms=_stuck_on_checks(live),
            ),
            failed=await BlockRepository(db).rooms_with_a_failed_turn(
                [topic.id], now=datetime.now(UTC)
            ),
        )
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
    """
    if sum(c is not None for c in (before, after, around)) > 1:
        raise ValidationError("before、after、around 一次只能用一个")
    if limit is None and (after is not None or around is not None):
        raise ValidationError("after 和 around 要和 limit 一起用")
    place = await TopicService(db).place_or_404(topic_id)
    await _actor_in_place(resolver, place)
    repo = BlockRepository(db)

    async def cursor_of(block_id: uuid.UUID | None) -> Block | None:
        if block_id is None:
            return None
        cursor = await repo.get(block_id)
        # An unknown cursor must not silently degrade into "newest N" — that
        # would hand the caller a duplicate page it can't distinguish. A cursor
        # from one of this room's CARDS is just as wrong as one from another
        # room: the card's timeline is read through the card. A document node
        # or margin comment is not on the timeline at all.
        if (
            cursor is None
            or cursor.topic_id != place.room_id
            or cursor.task_id is not None
            or cursor.kind in BlockRepository.NON_TIMELINE
        ):
            raise NotFoundError("游标消息不存在")
        return cursor

    older_than = await cursor_of(before)
    newer_than = await cursor_of(after)
    centre = await cursor_of(around)
    if limit is None:
        blocks = await repo.list_for_topic(place.room_id)
        if older_than is not None:
            blocks = [
                b
                for b in blocks
                if (b.created_at, b.id) < (older_than.created_at, older_than.id)
            ]
        has_more = has_newer = False
    elif centre is not None:
        above = await repo.page_for_topic(
            place.room_id, limit=limit // 2, before=centre
        )
        below = await repo.page_for_topic(
            place.room_id, limit=limit - limit // 2, after=centre
        )
        blocks = [*above.items, centre, *below.items]
        has_more, has_newer = above.has_more, below.has_more
    elif newer_than is not None:
        page_result = await repo.page_for_topic(
            place.room_id, limit=limit, after=newer_than
        )
        blocks = page_result.items
        # Paging down from a block means that block is above this page.
        has_more, has_newer = True, page_result.has_more
    else:
        page_result = await repo.page_for_topic(
            place.room_id, limit=limit, before=older_than
        )
        blocks = page_result.items
        # Paging up from a block means that block is below this page.
        has_more, has_newer = page_result.has_more, older_than is not None
    total = await repo.count_for_topic(topic_id)
    # Emoji reactions ride the same payload — ONE batch query, no per-block N+1.
    # Scoped to THIS page's ids, so paging saves the database work too, not just
    # the bytes on the wire.
    reactions = await repo.reactions_for_blocks([b.id for b in blocks])
    items = []
    for b in blocks:
        item = BlockOut.model_validate(b).model_dump(mode="json")
        if b.id in reactions:
            item["reactions"] = reactions[b.id]
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


async def _history_block(
    repo: BlockRepository, room_id: uuid.UUID, block_id: uuid.UUID
) -> Block:
    block = await repo.get(block_id)
    if block is None or block.topic_id != room_id:
        raise NotFoundError("Message not found in this room")
    return block


@router.get("/{topic_id}/history")
async def read_chat_history(
    topic_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    before: uuid.UUID | None = None,
    after: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
    reply_to: uuid.UUID | None = None,
    q: Annotated[str | None, Query(min_length=1, max_length=1000)] = None,
    kind: BlockKind | None = None,
    author: str | None = None,
) -> dict:
    """Read stored chat, including structured events and reactions.

    Replies are direct children; follow their IDs for nested replies. A reply
    query inherits its parent's task scope. Search is literal, case-insensitive
    substring matching over content, metadata and quoted document text.
    """
    place = await TopicService(db).place_or_404(topic_id)
    await _actor_in_place(resolver, place)
    if before is not None and after is not None:
        raise ValidationError("Use before or after, not both")
    repo = BlockRepository(db)
    parent = None
    if reply_to is not None:
        parent = await _history_block(repo, place.room_id, reply_to)
        if task_id is not None and parent.task_id != task_id:
            raise NotFoundError("Message not found in this task")
        task_id = parent.task_id
    if task_id is not None:
        task = await TaskRepository(db).get(task_id)
        if task is None or task.room_id != place.room_id:
            raise NotFoundError("Task not found in this room")
    cursor = None
    if cursor_id := before or after:
        cursor = await _history_block(repo, place.room_id, cursor_id)
        if cursor.task_id != task_id:
            raise NotFoundError("Cursor not found in this conversation")
    result = await repo.page_for_topic(
        place.room_id,
        task_id=task_id,
        limit=limit,
        before=cursor if before else None,
        after=cursor if after else None,
        query=q,
        reply_to=reply_to,
        author=author,
        # Document nodes have their own tree. Comments and preview pointers
        # remain discoverable here; --kind doc_node reads the nodes explicitly.
        kinds=[kind] if kind else [k for k in BlockKind if k != BlockKind.doc_node],
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
    place = await TopicService(db).place_or_404(topic_id)
    await _actor_in_place(resolver, place)
    repo = BlockRepository(db)
    block = await _history_block(repo, place.room_id, block_id)
    item = BlockOut.model_validate(block).model_dump(mode="json")
    item["reactions"] = await repo.reactions_for_block(block_id)
    return ok(item)


@router.get("/{topic_id}/usage")
async def topic_usage(
    topic_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """资源用量 (spec §9.1): token/cost for this room.

    The room's whole bill, cards included. There is no second meter to read:
    every 分身 in the room spends through the room's one session, so a per-card
    figure would be an invented split of one number.
    """
    place = await TopicService(db).place_or_404(topic_id)
    await _actor_in_place(resolver, place)
    return ok(await UsageRepository(db).for_topic(place.room_id))


# The agent-facing gate-output slice: enough to read the failure, small enough
# for a prompt. The full output is already capped at persist time (GATE_TAIL).
_GATE_OUTPUT_TAIL = 2000


def _card_snapshot(card: AcceptCard) -> dict:
    return {
        "id": str(card.id),
        "status": str(card.status),
        "reviewer": card.reviewer_handle,
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


@router.get("/{topic_id}/status")
async def topic_status(
    topic_id: uuid.UUID,
    db: DbSession,
    runner: Annotated[AgentWorkRunner, Depends(get_work_runner)],
    chat_service: Annotated[ChatService, Depends(get_chat_service)],
    resolver: ActorResolverDep,
) -> dict:
    """盲飞防护: one snapshot of "what is going on" for this topic — accept
    cards with their gate output, the current/last turn's state, and the
    platform waterlines (disk/queue/credits) — so an agent (via `cheese
    status`) or a debugging human doesn't have to poll several endpoints and
    guess. Read path, open like the rest of the MVP read surface.

    ``stall`` answers the question nothing here could answer before: did a turn
    die on this topic? ``turn`` cannot — it is a ring buffer of what turns did,
    so it is empty after a restart and says `running` about a turn killed with
    the process. See ``TopicService.stall_signal``."""
    topics = TopicService(db)
    place = await topics.place_or_404(topic_id)
    await _actor_in_place(resolver, place)
    cards = await AcceptCardRepository(db).list_for_topic(topic_id)
    credits = await ComputeGrantRepository(db).summary(place.project_id)
    turn = runner.topic_work(topic_id)
    stall = await topics.stall_signal(
        topic_id,
        live_turn=runner.live_work_for_topic(topic_id),
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
            "cards": [_card_snapshot(c) for c in cards],
            "platform": {
                "active_turns": runner.active_work_count(),
                "queued_turns": runner.project_queue_depth(place.project_id),
                "disk": _disk_snapshot(settings.workspace_root),
                "credits": {
                    "unlimited": credits["unlimited"],
                    "remaining": credits["credits_remaining"],
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
    actor = await resolver.resolve(
        fallback_handle=None, topic_id=topic_id, project_id=topic.project_id
    )
    await resolver.authorize_topic(
        actor, project_id=topic.project_id, topic_id=topic_id
    )
    children = await TopicService(db).list_children(topic_id)
    items = [TopicOut.model_validate(t).model_dump(mode="json") for t in children]
    return ok(page(items, len(items)))


@router.get("/{topic_id}/progress")
async def get_topic_progress(
    topic_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    task: Annotated[uuid.UUID | None, Query()] = None,
) -> dict:
    """进度层 (#187): 芝士's checklist for this topic, as of the last turn to
    touch it. Read on topic open — between turns there is no WS stream to carry
    it, and "做到哪了" has to be visible without summoning anyone. With ``task``,
    that card's list — the one its 分身 wrote — instead of the room's."""
    place = await TopicService(db).place_or_404(topic_id)
    await _actor_in_place(resolver, place)
    items, updated_at = await TopicService(db).get_progress(topic_id, task_id=task)
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
    # The card whose 分身 is writing. A 分身 runs inside the room's session, on
    # the room's credentials, so the call cannot tell it apart from the room's
    # own agent: it says so, the way `cheese_lock` names its task.
    task: uuid.UUID | None = None
    # Post a new checklist message instead of editing the current one. Which
    # request a list belongs to is the agent's call: it sets this when someone
    # brings it a new one.
    new: bool = False
    # One line on what landed, written when the work is done; shown under the
    # list in the same message. Same ceiling as an item.
    result: (
        Annotated[
            str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
        ]
        | None
    ) = None


# The step markers of the checklist message's text. The room draws its own
# icons from `meta.checklist`; this text is what every other reader gets — the
# agent reading the history, a copy, a notification preview.
_CHECKLIST_MARK = {"completed": "✓", "in_progress": "✱", "pending": "○"}


def _checklist_text(items: list[dict], result: str | None) -> str:
    """The checklist as the message's text: one line per step, and the result
    line under it once there is one."""
    lines = [f"{_CHECKLIST_MARK[item['status']]} {item['subject']}" for item in items]
    if result:
        lines += ["", f"✅ {result}"]
    return "\n".join(lines)


@router.put("/{topic_id}/progress")
async def write_topic_progress(
    topic_id: uuid.UUID,
    body: ProgressIn,
    db: DbSession,
    resolver: ActorResolverDep,
    chat: Annotated[ChatService, Depends(get_chat_service)],
) -> dict:
    """`todo_write`: the agent's whole checklist (进度层), and its message.

    Whole-list replace, so what is stored is exactly what the agent last said,
    never a merge of two plans. The stored list is what 总览 shows and what the
    room's next turn is handed back.

    In the room the list is an ordinary message by the agent. The first call
    posts it; later calls edit the agent's current checklist message through
    the same edit every author has (`domain/block/editing`); ``new`` posts a
    fresh one.

    With ``task`` it is that card's 分身 writing, and the list is the card's:
    stored under the card and pushed on the card's channel, the same channel its
    attributed events go to (`chat.py`), leaving the room's own list and
    conversation alone.

    A platform tool, so it reaches here the same way from every harness.
    """
    place = await TopicService(db).place_or_404(topic_id)
    actor = await resolver.resolve(
        fallback_handle=None, topic_id=place.room_id, project_id=place.project_id
    )
    if not actor.authenticated:
        raise ForbiddenError("An authenticated agent must write this checklist")
    await resolver.authorize_topic(
        actor, project_id=place.project_id, topic_id=place.room_id, enforce=True
    )
    if not await TopicMemberService(db).holds_an_agent_seat(place.room, actor.handle):
        raise ForbiddenError("An authenticated agent must write this checklist")
    if body.task is not None:
        task = await TaskRepository(db).get(body.task)
        if task is None or task.room_id != place.room_id:
            raise NotFoundError("Task not found in this room")
    items = [
        {"id": str(number), "subject": todo.content, "status": todo.status}
        for number, todo in enumerate(body.todos, start=1)
    ]
    runner = get_work_runner()
    work = runner.live_work_for_topic(place.room_id)
    turn_id = uuid.UUID(work["turn_id"]) if work is not None else None
    await TopicProgressRepository(db).save(
        place.room_id, items, task_id=body.task, turn_id=turn_id
    )
    await db.commit()
    if body.task is not None:
        await get_broker().publish(str(body.task), {"type": "todo", "items": items})
        return ok({"items": items})
    text = _checklist_text(items, body.result)
    checklist = {"items": items, "result": body.result}
    current = (
        None
        if body.new
        else await BlockRepository(db).current_checklist(place.room_id, actor.handle)
    )
    if current is not None:
        message = await edit_message(
            db,
            get_broker(),
            current.id,
            editor=actor.handle,
            content=text,
            chat=chat,
            runner=runner,
            checklist=checklist,
        )
        return ok({"items": items, "message_id": message["id"], "posted": False})
    message = await chat._persist_assistant_message(
        project_id=place.project_id,
        topic_id=place.room_id,
        text=await project_refs_text(db, place.project_id, place.room_id, text),
        turn_id=turn_id,
        reply_to=None,
        roster=None,
        topic_refs=[],
        publish=True,
        author=actor.handle,
        own_output=True,
        extra_meta={CHECKLIST_META_KEY: checklist},
    )
    assert message is not None  # a publication with no eid never deduplicates
    await get_broker().publish(
        str(place.room_id), {"type": "assistant_block", "block": message}
    )
    if turn_id is not None:
        runner.note_session_output(turn_id, tool=False)
    return ok({"items": items, "message_id": message["id"], "posted": True})


@router.get("/{topic_id}/doc")
async def get_topic_doc(
    topic_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """This place's single living doc (spec §2.2 docs-out).

    Two levels exist and both are real: a room's document is the shared picture,
    a thread's is that one piece of work's brief and then its status. They are
    told apart by the id in the path.
    """
    place = await TopicService(db).place_or_404(topic_id)
    await _actor_in_place(resolver, place)
    doc = await TopicService(db).get_doc(topic_id)
    if doc is None:
        return ok(None)
    return ok(BlockOut.model_validate(doc).model_dump(mode="json"))


@router.get("/{topic_id}/overview")
async def get_topic_overview(
    topic_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """总览房间的自动区（#1889）：②~⑤，结构化，给文档面板正文下方那一栏。

    总览文档是五块：① 写在文档正文里，②~⑤ 由平台现拼。注入 agent 提示词的
    那一份是同一批数据的 markdown 排版（`topic/overview.py`），这里给的是能
    逐个点击的结构化条目。

    授权和读文档那一份完全一样：先认出「谁在这儿」，再看他在不在这个房间的
    名册上。只有根话题有总览，别处 404（见 `TopicService.overview_auto`）。
    """
    topics = TopicService(db)
    place = await topics.place_or_404(topic_id)
    await _actor_in_place(resolver, place)
    blocks = await topics.overview_auto(topic_id)
    return ok({"root_topic_id": str(place.room_id), "blocks": blocks})


@router.put("/{topic_id}/doc")
async def edit_topic_doc(
    topic_id: uuid.UUID,
    body: DocEditIn,
    db: DbSession,
    resolver: ActorResolverDep,
    chat: Annotated[ChatService, Depends(get_chat_service)],
) -> dict:
    """改文档即指令 (eval B2): edit the living doc; emits a conversation event.

    Conditional on ``expected_version``: this doc has no partial write, so a
    save based on a version that is no longer current is refused with 409
    rather than quietly erasing whatever landed in between.

    A person's edit is also pushed into whatever turn is running right now.
    改文档即指令 has always been true of the NEXT turn — the doc is read at the
    top of one — and false of the turn already in progress, which went on
    working from the version it started with and would then set that version
    back. What gets pushed is the version number and a line about what moved,
    never the text: the doc is one `cheese_doc_get` away, and a document
    injected mid-turn displaces the work instead of informing it."""
    # A thread has a doc of its own — its brief, and then how the work is
    # going — and `edit_doc` has always written by place. Only this handler
    # still refused to name one, so `cheese_doc_set` 404ed for every 分身 doing
    # the work while `cheese_doc_get` right above answered fine.
    place = await TopicService(db).place_or_404(topic_id)
    # actor 在信任边界注入: prefer the verified token, fall back to body.author.
    # The token is scoped to the place; the roster is the room's.
    actor = await resolver.resolve(
        fallback_handle=body.author,
        topic_id=place.room_id,
        project_id=place.project_id,
    )
    await resolver.authorize_topic(
        actor, project_id=place.project_id, topic_id=place.room_id
    )
    # Same backstop as chat replies: friendly "@名字 / @话题名" → structured
    # token, so refs in the doc render as clickable chips (docs used to skip
    # this and stayed plain text).
    content = await canonicalize_refs(
        db, place.project_id, body.content, exclude_topic_id=place.room_id
    )
    doc, notice = await TopicService(db).edit_doc(
        topic_id=topic_id,
        content=content,
        author=actor.handle,
        expected_version=body.expected_version,
        author_type=AuthorType.participant,
    )
    # Publish only committed edits: connected teammates can immediately read
    # the new document and the same persisted contribution record.
    await db.commit()
    broker = get_broker()
    if notice is not None:
        await broker.publish(
            str(place.room_id),
            {
                "type": "event_block",
                "block": BlockOut.model_validate(notice).model_dump(mode="json"),
            },
        )
    await announce_stale(place.room_id, "doc")
    if topic_id == place.room_id:
        # A rewritten goal is the clearest sign a room changed direction.
        naming.nudge(place.room_id, "signal")
    if notice is not None and (line := agent_notice(notice)):
        # The notice tells 芝士 to go re-read the doc, so the doc has to BE the
        # new one by the time it does — same ordering as the comment route.
        # What it says was written where the document moved (`edit_doc`), so the
        # running turn and the next one are told the same thing; naming `notice`
        # is what lets the receipt stamp it consumed instead of it being said
        # twice.
        await chat.notify_running_turn(topic_id, line, blocks=[notice.id])
    # 写入检查（#1889 第 3 条）：**照样写入**，只把「哪里不像状态」跟着响应
    # 带回去，让写它的人当场改。拦下来是错的——让人先猜格式再写字，比一条警告
    # 贵得多；而只写进日志的警告等于没写（没人读日志，写它的人也不在那儿）。
    return ok(
        BlockOut.model_validate(doc).model_dump(mode="json"),
        warnings=living_doc_warnings(content),
    )


@router.post("/{topic_id}/deliveries")
async def ask_for_a_delivery(
    topic_id: uuid.UUID, body: dict, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """定时投递（结论 17）：请平台在某个时刻把这条递给请求者自己。

    收件人不在正文里，因为这条原语只有一个收件人规则——**就是请求它的那个参与者**。
    给别人设闹钟是另一件事，而那件事没有人要过。
    """
    from app.domain.delivery.timer import deliver_at

    place = await TopicService(db).place_or_404(topic_id)
    actor = await _actor_in_place(resolver, place)
    recipient = actor.handle
    if not actor.authenticated:
        recipient = await TopicMemberService(db).resolve_agent_handle(
            topic_id, room_id=place.room_id
        )
    raw = (body.get("at") or "").strip()
    try:
        when = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise ValidationError("at 要是一个 ISO-8601 时刻") from None
    row = await deliver_at(
        db,
        when=when,
        event=body.get("content") or "",
        recipient=recipient,
        topic_id=topic_id,
        project_id=place.project_id,
    )
    return ok({"id": str(row.id), "at": when.isoformat(), "to": recipient})


@router.post("/{topic_id}/summon")
async def summon_agent(
    topic_id: uuid.UUID,
    body: dict,
    db: DbSession,
    resolver: ActorResolverDep,
    chat: Annotated[ChatService, Depends(get_chat_service)],
    runner: Annotated[AgentWorkRunner, Depends(get_work_runner)],
) -> dict:
    """叫芝士读一遍还没交到它手上的消息 —— 忘了 @ 的那一条的补救。

    没 @ 的消息从来不会丢：它在待读窗口里等着下一轮把它捎上（没 @ 不等于没说）。
    但「等下一轮」在一个安静的房间里等于永远，而房间安静恰恰是忘了 @ 之后的常态。
    这个接口只做一件事：现在就开那一轮。它**不再发一条消息**，因为那条消息已经
    在时间线上了——补发一条一模一样的，读的人要自己分辨哪条是真的。

    两种情况下它什么都不做，并如实说明是哪一种：房间已经在干活（正在跑的那一轮
    会自己把没 @ 的消息接过去），或者根本没有待读的东西（有人先 @ 过了）。两种
    都不是错误，只是这一下不需要花钱。
    """
    place = await TopicService(db).place_or_404(topic_id)
    actor = await resolver.resolve(
        fallback_handle=body.get("author"),
        topic_id=place.room_id,
        project_id=place.project_id,
    )
    await resolver.authorize_topic(
        actor, project_id=place.project_id, topic_id=place.room_id
    )
    if chat.has_running_turn(place.room_id):
        return ok({"started": False, "reason": "working"})
    if not await chat.has_unread_input(place.room_id):
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
    # 房间），才回落到默认席位 —— 和 `answer_options` 同一条规矩。
    members = TopicMemberService(db)
    seat = await chat.pending_seat(place.room_id)
    if seat is None or seat not in await members.agent_handles(place.room_id):
        seat = await members.addressable_agent_handle(place.room_id)
    runner.submit(
        chat,
        place.room_id,
        author="system",
        content="有人请你看一下房间里还没读到的消息，照常处理。",
        # 点名的是按下这个按钮的人，不是平台：他指名这个房间的芝士，寻址结果里
        # 因此恰好有它一个，这一轮才跑得起来。
        addressed=addressed_to_agent(seat),
        nudge_event=(
            f"<@{actor.handle}> 把之前的消息交给了 <@{seat}>"
            if seat
            else f"<@{actor.handle}> 交出了之前的消息"
        ),
        provision_actor=actor,
    )
    return ok({"started": True})


# 一次作答/更正的全部状态都写在 `meta` 上，形状见 ask-ux-preview/contract.md §4.1。
# 处理顺序是有讲究的（§4.4）：先鉴权，再查重，再校验内容，最后才比版本 —— 颠倒
# 任何一对都会留下一个可以拿来绕过的口子。
_ANSWER_KINDS = ("option", "note", "reject")


def _option_texts(meta: dict) -> list[str]:
    """选项的文字。顺序是选项列表唯一的意思，所以按原数组顺序返回。"""
    out: list[str] = []
    for entry in meta.get("options") or []:
        out.append(entry["text"] if isinstance(entry, dict) else str(entry))
    return out


def _answer_payload(kind: str, option: str, note: str) -> tuple[str, str, str]:
    """幂等键要比的那份「内容」：换一个字就是另一次操作。"""
    return kind, option, note


@router.post("/blocks/{block_id}/answer")
async def answer_options(
    block_id: uuid.UUID,
    body: dict,
    db: DbSession,
    resolver: ActorResolverDep,
    chat: Annotated[ChatService, Depends(get_chat_service)],
    runner: Annotated[AgentWorkRunner, Depends(get_work_runner)],
) -> dict:
    """Answer an option question, or correct this answerer's own earlier answer.

    The answer is an append-only log; the last entry is the one in force. A
    correction keeps the entry it replaces, so the room can still see what was
    originally chosen and who chose it.

    Two invariants worth naming, both of which a naive rewrite breaks:

    * **Authentication happens before anything else.** The idempotency lookup
      reads the question's state; returning "already answered" to somebody who
      has not proven they belong here is a leak, and it is also how an outsider
      would probe for `client_op_id` values. So `resolve` + `authorize_topic`
      come first, and every other branch is behind them.
    * **The wake is one durable delivery, not a second timeline message.**
      `broker.receive_message` both persists a row and starts a turn; running it
      here alongside `record_agent` wakes the seat twice and writes the answer
      text twice. Only `record_agent` runs — and because `_run` does not persist
      anything (`Human message already persisted by `receive_message``), the
      answer text is written here, once, carrying the same `delivery_event_id`
      as the delivery.
    """
    repo = BlockRepository(db)
    blk = await repo.get(block_id)
    if blk is None:
        raise NotFoundError("问题不存在")

    # 1. 先鉴权。`body["author"]` 只是寻址的退路，不参与授权 —— 见
    # `ActorResolver` 的 docstring：legacy authorship fallback does not
    # authenticate.
    actor = await resolver.resolve(
        fallback_handle=body.get("author"),
        topic_id=blk.topic_id,
        project_id=blk.project_id,
    )
    await resolver.authorize_topic(
        actor, project_id=blk.project_id, topic_id=blk.topic_id
    )
    author = actor.handle

    kind = (body.get("kind") or "option").strip()
    option = (body.get("option") or "").strip()
    note = (body.get("note") or "").strip()
    client_op_id = (body.get("client_op_id") or "").strip()
    expect_version = body.get("expect_version")

    if kind not in _ANSWER_KINDS:
        raise ValidationError("kind 要是 option / note / reject")
    if not client_op_id:
        raise ValidationError("client_op_id 必带")
    if not isinstance(expect_version, int) or isinstance(expect_version, bool):
        raise ValidationError("expect_version 必带：初答 0，之后是 answer_log 末项的 v")
    if len(note) > 2000:
        raise ValidationError("note 最多 2000 字")
    if author == "anonymous":
        raise ValidationError("要登录才能作答")

    # 2. 行锁。幂等查重、内容校验、版本比对读的是同一份 `answer_log`，不锁住就
    # 会出现「两个并发都没看见对方」的假幂等 —— 重试的那一次会拿到 409 而不是
    # 已经存好的那一版。锁在这里而不是在 CAS 那一行，是为了让查重也在锁内。
    # repo.get 已将块放进 identity map；拿到锁也不会自动覆盖缓存属性。
    # 等前一答提交后，必须用锁内读到的版本替换初读的 meta。
    locked = await db.execute(
        select(Block)
        .where(Block.id == block_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    blk = locked.scalar_one()
    meta = dict(blk.meta or {})
    log: list[dict] = list(meta.get("answer_log") or [])

    # 3. 幂等：同一个 (块, 人, 操作) 只记一次。**在版本拒绝之前** —— 重试的人手上
    # 的 expect_version 可能已经过期，但他那一次操作确实已经落库了，这时该拿 200
    # 而不是 409。
    for entry in log:
        if entry.get("by") != author or entry.get("client_op_id") != client_op_id:
            continue
        stored = _answer_payload(
            entry.get("kind") or "option",
            entry.get("option") or "",
            entry.get("note") or "",
        )
        if stored != _answer_payload(kind, option, note):
            raise ConflictError("同一个 client_op_id 换了内容")
        return ok(BlockOut.model_validate(blk).model_dump(mode="json"))

    # 4. 内容校验。`allow_other` 读建题 meta（持久化的那一份），不是读这次请求。
    texts = _option_texts(meta)
    if kind == "option":
        if not option:
            raise ValidationError("kind=option 要给 option")
        if option not in texts:
            raise ValidationError("不在选项里")
    else:
        # reject / note 都**不伪造合法项**：不写进 options，也不编一个 option 出来。
        if option:
            raise ValidationError(f"kind={kind} 不给 option")
        if kind == "note" and not note:
            raise ValidationError("kind=note 要给 note")
        if kind == "note" and not meta.get("allow_other"):
            raise ValidationError("这道题不接受自由输入")
    if kind == "reject" and not (meta.get("reject_option") or meta.get("allow_other")):
        # 老题没有这两个键：界面没补「以上都不是」，就不该有一条 API 能替它补。
        raise ValidationError("这道题不接受「以上都不是」")

    # 5. 更正闸门：只有原答者能改自己的答案。这是房间里的规则，不是「你不是成员」
    # —— 后者才是 403（ForbiddenError），所以这里用 422。
    if log:
        last = log[-1]
        if author != last.get("by"):
            raise ValidationError("只有原答者能更正")

    # 6. CAS：旧日志长度等于 expect_version。初答 expect_version=0、长度 0 → 成立
    # → 写第 1 版；第一次更正 expect_version=1、长度 1 → 成立 → 写 v=2。
    # 不是 `== expect_version + 1` —— 那个式子在初答时要求 0 == 1，会把第一次作答
    # 整个拒掉。落败的那个拿 409。
    if len(log) != expect_version:
        raise ConflictError("版本不对，请重取这一题后再作答")

    now_iso = datetime.now(UTC).isoformat()
    entry = {
        "v": expect_version + 1,
        "kind": kind,
        "option": option if kind == "option" else None,
        "note": note or None,
        "by": author,
        "at": now_iso,
        "client_op_id": client_op_id,
    }
    log.append(entry)
    meta["answer_log"] = log

    members = TopicMemberService(db)
    if blk.author in await members.agent_handles(blk.topic_id):
        seat: str | None = blk.author
    else:
        seat = await members.addressable_agent_handle(blk.topic_id)

    instance = (
        await instance_for_seat(db, blk.project_id, seat) if seat is not None else None
    )
    answer_meta = {"answer_to": str(block_id)}
    if instance is not None:
        answer_meta["agent_recipient"] = {
            "instance_id": str(instance.id),
            "handle": instance.handle,
            "mentioned": True,
        }
    event_id = event_id_for(NotificationType.MENTION, f"{block_id}:{entry['v']}")
    answer_meta["delivery_event_id"] = str(event_id)
    text = _answer_line(seat, entry)

    # 7. 同一件事一个事务里写完：答案、那条给人看的文本、那条给席位的投递意图。
    blk.meta = meta
    answer_block = await repo.add(
        project_id=blk.project_id,
        topic_id=blk.topic_id,
        author=author,
        author_type=AuthorType.participant,
        content=text,
        kind=BlockKind.message,
        meta=answer_meta,
    )
    if instance is not None:
        await record_agent(
            db,
            DeliveryEvent(
                id=event_id,
                type=NotificationType.MENTION,
                payload={"answer_to": str(block_id), "v": entry["v"]},
                occurred_at=datetime.now(UTC),
            ),
            topic_id=blk.topic_id,
            instance_id=instance.id,
            content=text,
        )

    updated = BlockOut.model_validate(blk).model_dump(mode="json")
    answer_out = BlockOut.model_validate(answer_block).model_dump(mode="json")

    # 8. 提交。落库之后再广播 —— 广播是尽力而为，状态已经在库里，前端可以重取。
    await db.commit()
    await get_broker().publish(
        str(blk.topic_id), {"type": "block_updated", "block": updated}
    )
    await get_broker().publish(
        str(blk.topic_id), {"type": "block_added", "block": answer_out}
    )
    # 投递意图已经落库，接着把它交出去。这一步不在事务里：意图先持久，投递后发生，
    # 中间崩了也只是留一条 `pending`，由 `dispatch_pending` / 定时补送接上，而不是
    # 把答案写两遍。
    from app.domain.delivery.agent import dispatch_pending

    await dispatch_pending(chat.session_factory, chat=chat, runner=runner)
    return ok(updated)


def _answer_line(seat: str | None, entry: dict) -> str:
    """时间线上那一行的正文。@ 写进正文（不是帧上另置一位），读的人才看得出叫了谁。"""
    kind = entry.get("kind")
    if kind == "option":
        what = entry["option"] or ""
    elif kind == "reject":
        what = "以上都不是"
    else:
        what = entry.get("note") or ""
    suffix = ""
    if kind == "option" and entry.get("note"):
        suffix = f"（{entry['note']}）"
    elif kind == "reject" and entry.get("note"):
        suffix = f"（{entry['note']}）"
    return f"<@{seat}> {what}{suffix}" if seat else f"{what}{suffix}"


@router.post("/{topic_id}/webhook-token")
async def mint_webhook_token(
    topic_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """Mint (or rotate) this place's webhook credential — used by the `cheese`
    CLI to hand a caller a token for POST /webhooks/{topic_id}. Rotating
    invalidates every previously-minted token for this place; the raw value is
    returned once and never recoverable afterwards.

    The token is a write credential for this room: whoever holds it can post
    into the room's timeline as any source, and rotating it locks out every
    token minted before. So minting takes the same door as writing to the room
    — project membership — rather than being handed to whoever knows the id."""
    place = await TopicService(db).place_or_404(topic_id)
    actor = await resolver.resolve(
        fallback_handle=None, project_id=place.project_id, topic_id=place.room_id
    )
    await resolver.authorize_topic(
        actor, project_id=place.project_id, topic_id=place.room_id
    )
    # Scoped to the place, because the webhook wakes the place: a thread's
    # credential minted against the room would deliver into the room instead.
    token = await webhook_service.mint(
        db, topic_id=topic_id, project_id=place.project_id
    )
    await db.commit()
    return ok({"token": token})


@router.post("/{topic_id}/decision")
async def record_decision(
    topic_id: uuid.UUID,
    body: dict,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """记录关键决策到决策记录 (spec §7.1) — used by the `cheese_decision` tool."""
    place = await TopicService(db).place_or_404(topic_id)
    actor = await _actor_in_place(resolver, place)
    decision = (body.get("decision") or "").strip()
    if not decision:
        raise ValidationError("decision 不能为空")
    decision = await canonicalize_refs(
        db, place.project_id, decision, exclude_topic_id=place.room_id
    )
    # 重发幂等 (④): inside a re-sent turn, the same decision text is the
    # same decision — a re-sent 芝士 re-recording it must not stack a second
    # 决策记录 row. Outside a turn (a human in the UI) there is no continuation
    # and no dedup: pressing the button twice means it twice.
    continuation = get_work_runner().continuation_for(topic_id)
    key = action_key(continuation, "decision", decision) if continuation else None
    if key is not None and not await idem.claim(
        db, key, action="decision", scope_id=str(topic_id)
    ):
        prior = await idem.stored_result(db, key)
        return ok(prior or {"skipped": True})
    block = await BlockRepository(db).add(
        project_id=place.project_id,
        topic_id=topic_id,  # the place; `add` splits it
        author=(
            actor.handle
            if actor.authenticated
            else await TopicMemberService(db).resolve_agent_handle(
                topic_id, room_id=place.room_id
            )
        ),
        author_type=AuthorType.participant,
        content=decision,
        kind=BlockKind.decision,
        refs=[str(topic_id)],
    )
    out = BlockOut.model_validate(block).model_dump(mode="json")
    if key is not None:
        await idem.record_result(db, key, out)
    await db.commit()
    await announce_stale(place.room_id, "decision")
    return ok(out)


def _parse_moment(raw: object) -> datetime | None:
    """一个可选的 ISO-8601 时刻；空串和缺席是一回事。"""
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        moment = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        raise ValidationError("since/until 要是一个 ISO-8601 时刻") from None
    # 不带时区的按 UTC 读：否则它和 now() 相减时 naive/aware 直接抛，而调用方
    # 只写了个「2026-09-06」也得能用。
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _weekly_window(body: dict) -> tuple[datetime, datetime]:
    """这份周报讲的是哪一段。略过就是「截止到现在的一周」。"""
    until = _parse_moment(body.get("until")) or datetime.now(UTC)
    since = _parse_moment(body.get("since")) or until - timedelta(days=7)
    if since > until:
        raise ValidationError("since 不能晚于 until")
    return since, until


@router.post("/{topic_id}/weekly")
async def record_weekly(
    topic_id: uuid.UUID,
    body: dict,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """记一份周报到周报集 (spec §7.1) —— 项目文档页的「周报集」就是从这条读的。

    一份周报讲的是一段已经过去的时间，不是项目此刻的状态（那是章程和话题文档
    的事），所以它带一个窗口：`since`/`until`。窗口存在 `meta` 上而不是新开一
    列 —— 它是这一条记录的属性，没有第二处会读它。

    `refs` 指向它写在哪：周报集里那一行的「来自话题」靠它跳回去，和决策记录一样。
    """
    place = await TopicService(db).place_or_404(topic_id)
    actor = await _actor_in_place(resolver, place)
    report = (body.get("body") or "").strip()
    if not report:
        raise ValidationError("body 不能为空")
    since, until = _weekly_window(body)
    report = await canonicalize_refs(
        db, place.project_id, report, exclude_topic_id=place.room_id
    )
    # 重发幂等 (④)，和决策记录同一个道理：一轮重新送达时，同一份正文是同一份
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
        topic_id=topic_id,  # the place; `add` splits it
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


@router.post("/{topic_id}/read")
async def mark_topic_read(
    topic_id: uuid.UUID, body: dict, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """话题级已读位: bump the caller's read cursor (opening a topic clears its
    unread badge, Feishu-style).

    The cursor is per person, so whose it is comes from the verified
    credential — ``handle`` in the body is only an assertion checked against
    it (it used to BE the identity, letting anyone move anyone's cursor)."""
    topic = await TopicService(db).get_or_404(topic_id)
    actor = await resolver.resolve(
        fallback_handle=None, topic_id=topic_id, project_id=topic.project_id
    )
    await resolver.authorize_topic(
        actor, project_id=topic.project_id, topic_id=topic_id
    )
    handle = await resolver.resolve_recipient(
        requested=(body.get("handle") or "").strip() or None,
        project_id=await resolver.project_of_topic(topic_id),
        allow_anonymous=False,
    )
    await TopicService(db).mark_read(topic_id, handle)
    return ok({"topic_id": str(topic_id), "handle": handle})


@router.post("/{topic_id}/archive")
async def archive_topic(
    topic_id: uuid.UUID,
    body: dict,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """手动归档 (归档去向): explicit archive, independent of 采纳."""
    topic = await TopicService(db).get_or_404(topic_id)
    actor = await resolver.resolve(
        fallback_handle=None, topic_id=topic_id, project_id=topic.project_id
    )
    await resolver.authorize_topic(
        actor, project_id=topic.project_id, topic_id=topic_id
    )
    if not actor.authenticated:
        raise ForbiddenError("归档需要登录")
    await TopicMemberService(db).require_archive_manager(topic_id, actor.handle)
    topic = await TopicService(db).archive(topic_id, by=actor.handle)
    return ok(TopicOut.model_validate(topic).model_dump(mode="json"))


@router.get("/{topic_id}/cleanup")
async def cleanup_status(
    topic_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    from app.domain.topic.models import RoomCleanup

    topic = await TopicService(db).get_or_404(topic_id)
    actor = await resolver.resolve(
        fallback_handle=None, topic_id=topic_id, project_id=topic.project_id
    )
    await resolver.authorize_topic(
        actor, project_id=topic.project_id, topic_id=topic_id, enforce=True
    )
    operation = (
        await db.get(RoomCleanup, topic.cleanup_id) if topic.cleanup_id else None
    )
    if operation is None:
        return ok({"state": "not_scheduled"})
    return ok(
        {
            "state": operation.state,
            "due_at": operation.due_at.isoformat(),
            "reason": operation.last_error,
            "resources": len(operation.resources),
            "removed": sum(bool(entry.get("removed")) for entry in operation.resources),
        }
    )


@router.post("/{topic_id}/unarchive")
async def unarchive_topic(
    topic_id: uuid.UUID,
    body: dict,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """取消归档: bring an archived topic back to active."""
    topic = await TopicService(db).get_or_404(topic_id)
    actor = await resolver.resolve(
        fallback_handle=None, topic_id=topic_id, project_id=topic.project_id
    )
    await resolver.authorize_topic(
        actor, project_id=topic.project_id, topic_id=topic_id
    )
    if not actor.authenticated:
        raise ForbiddenError("取消归档需要登录")
    await TopicMemberService(db).require_archive_manager(topic_id, actor.handle)
    topic = await TopicService(db).unarchive(topic_id, by=actor.handle)
    return ok(TopicOut.model_validate(topic).model_dump(mode="json"))


@router.post("/{topic_id}/split")
async def split_topic(
    topic_id: uuid.UUID,
    body: SplitIn,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """从上往下拆解：dispatch a todo as a card of work in this room (eval A2).

    Writes the card and stops there. The WORKER is the caller's to start: it
    spawns one inside its own session carrying this card's thread label, and the
    platform reads whose work each event is off that label. The platform used to
    raise a whole second container per piece of work — its own screen, its own
    home, its own clone of the repository — to run something that is a second
    worker in a session the room already has.

    So a card returned from here has no worker yet, and that is a normal state
    rather than a half-finished dispatch: nothing here reports a worker, because
    the id it would carry does not exist until the worker does."""
    service = TopicService(db)
    parent_place = await service.place_or_404(topic_id)
    # actor 在信任边界注入 (同 edit_topic_doc): prefer the verified token, fall
    # back to body.created_by, and require the caller actually have access to
    # the ROOM — a body-trusted `created_by` let anyone dispatch work in anyone
    # else's room and name an arbitrary owner.
    actor = await resolver.resolve(
        fallback_handle=body.created_by,
        topic_id=parent_place.room_id,
        project_id=parent_place.project_id,
    )
    await resolver.authorize_topic(
        actor, project_id=parent_place.project_id, topic_id=parent_place.room_id
    )
    # 重发幂等 (④): a re-sent turn re-splitting would leave the room holding two
    # threads on one brief, and whoever reads the room cannot tell which of them
    # the work is actually happening in.
    runner = get_work_runner()
    continuation = runner.continuation_for(topic_id)
    key = (
        action_key(continuation, "split", topic_id, body.title)
        if continuation
        else None
    )
    if key is not None and not await idem.claim(
        db, key, action="split", scope_id=str(topic_id)
    ):
        prior = await idem.stored_result(db, key)
        return ok(prior or {"skipped": True})
    task = await service.dispatch_task(
        place_id=topic_id,
        title=body.title,
        created_by=actor.handle if actor.handle != "anonymous" else body.created_by,
        brief=body.brief,
        base_task_id=body.base_task_id,
        # 显式指定优先，没指定就用项目默认验收人 (#718 设置表)。The resolution is
        # in the service because it needs the project row; the route only says
        # whether anybody named somebody.
        reviewer_handle=body.reviewer_handle,
        reporter_handle=body.reporter_handle,
        contributor_handles=body.contributor_handles,
        # 归属跟推进者走: who is DRIVING this room right now. 芝士 splits under her
        # own handle, so `created_by` names the robot and the person who asked for
        # the split is nowhere in the request — the runner is the only place that
        # answer exists. None whenever no person is identifiable (a
        # platform-initiated turn, or one this room's agent started itself off a
        # worker's completion notice), and the ladder in the service then
        # behaves exactly as it did before.
        triggered_by=runner.turn_author_for(topic_id),
    )
    out = TaskOut.model_validate(task).model_dump(mode="json")
    if key is not None:
        await idem.record_result(db, key, out)
    # The thread and its idempotency key commit together, so a crash here cannot
    # produce a second thread on resume — and the caller must see the row and
    # its brief doc, and the thread label on it, before it can put a worker on
    # them.
    await db.commit()
    await announce_stale(parent_place.room_id, "topics")
    # Work split out of a room is a sign of where the room is going.
    naming.nudge(parent_place.room_id, "signal")
    return ok(out)


@router.post("/{topic_id}/tasks/{task_id}/check-result")
async def record_check_result(
    topic_id: uuid.UUID,
    task_id: uuid.UUID,
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
    place = await TopicService(db).place_or_404(topic_id)
    await _actor_in_place(resolver, place)
    tasks = TaskService(db)
    task = await tasks.require_in_room(place.room_id, task_id)
    await tasks.record_check(task, ok=body.ok, detail=body.detail)
    await db.commit()
    return ok({"recorded": True, "task_id": str(task.id)})


@router.post("/{topic_id}/lock")
async def take_room_lock(
    topic_id: uuid.UUID,
    body: LockIn,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """Take one of the room's two locks, or be told who has it.

    Never waits: blocking would spend a whole turn's compute standing still,
    and the caller has better answers available — write a different file, use
    `Edit` instead of a whole-file write, come back next turn.
    """
    place = await TopicService(db).place_or_404(topic_id)
    await _actor_in_place(resolver, place)
    await TaskService(db).require_in_room(topic_id, body.task_id)
    acquired, reason = await RoomLockService(db).acquire(
        room_id=place.room_id,
        kind=LockKind(body.kind),
        resource=body.resource or "",
        holder_task_id=body.task_id,
    )
    await db.commit()
    return ok({"acquired": acquired, "reason": reason})


@router.post("/{topic_id}/unlock")
async def release_room_lock(
    topic_id: uuid.UUID,
    body: LockIn,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    place = await TopicService(db).place_or_404(topic_id)
    await _actor_in_place(resolver, place)
    await TaskService(db).require_in_room(topic_id, body.task_id)
    released = await RoomLockService(db).release(
        room_id=place.room_id,
        kind=LockKind(body.kind),
        resource=body.resource or "",
        holder_task_id=body.task_id,
    )
    await db.commit()
    return ok({"released": released})


@router.post("/{topic_id}/clone-from")
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
        raise ValidationError("source_topic_id 必填")
    try:
        source_id = uuid.UUID(source_raw)
    except ValueError as exc:
        raise ValidationError("source_topic_id 不是合法的话题 id") from exc
    service = TopicService(db)
    target = await service.get_or_404(topic_id)
    source = await service.get_or_404(source_id)
    actor = await resolver.resolve(
        fallback_handle=body.get("by"),
        topic_id=topic_id,
        project_id=target.project_id,
    )
    # Must be allowed on BOTH ends: reading the source's session is as sensitive
    # as writing into the target.
    await resolver.authorize_topic(
        actor, project_id=target.project_id, topic_id=topic_id
    )
    await resolver.authorize_topic(
        actor, project_id=source.project_id, topic_id=source_id
    )
    topic = await service.clone_from(
        target_topic_id=topic_id, source_topic_id=source_id
    )
    await db.commit()
    return ok(TopicOut.model_validate(topic).model_dump(mode="json"))


@router.post("/{topic_id}/tell")
async def tell_topic(
    topic_id: uuid.UUID,
    body: RelayIn,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """留话给一条活: write one message onto a thread this room dispatched
    (`cheese_tell`). See `app.domain.topic.relay` for why the comments endpoint
    could not be this channel, and why nothing is woken.

    `topic_id` is the SENDER — the place whose turn is speaking, which is what
    the per-turn token in `_CHEESE_WRITE_PATHS` is scoped to. The receiver rides
    in the body and is resolved against the threads that sender dispatched: an
    id in the URL says "who is talking", never "which resource is this".
    """
    sender = await TopicService(db).place_or_404(topic_id)
    await _actor_in_place(resolver, sender)
    service = TopicRelayService(db)
    target = await service.resolve_target(sender=sender, target=body.target)
    # Friendly "@名字 / @话题名" → structured tokens BEFORE the message lands on
    # the thread, so chips render and @mentions notify over there.
    content = await canonicalize_refs(
        db, sender.project_id, body.content, exclude_topic_id=target.id
    )
    block = await service.relay(sender=sender, target=target, content=content)
    out = BlockOut.model_validate(block).model_dump(mode="json")
    await db.commit()
    # The room is where a person is watching; a thread's message shows up there
    # too, under its thread.
    await get_broker().publish(
        str(target.room_id), {"type": "assistant_block", "block": out}
    )
    return ok(
        {
            "block": out,
            "target_topic_id": str(target.id),
            "target_title": target.title,
        }
    )


# 芝士 → UI rendering (spec §9.1): an artifact is a file the AI explicitly points
# at + how to render it. The type comes from the tool call, never from parsing
# prose. MVP renders html/svg in the preview window; more types are additive.
_ARTIFACT_MIME = {
    "html": "text/html",
    "svg": "image/svg+xml",
    # A deliverable is not always a web page. A room that writes a report, a
    # budget or a deck produces one of these, and until the platform accepted
    # them the only way to hand one over was to describe where it sat in the
    # worktree — which the person in the room cannot open.
    "pdf": "application/pdf",
    "docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    "pptx": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    ),
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "md": "text/markdown",
    "csv": "text/csv",
    "png": "image/png",
    "jpg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    # 运行环境预览: the artifact is a RUNNING app on the machine this place's turn
    # lives on, reached over the preview tunnel that machine dialled out. HOW to
    # run it — and on which port — is the agent's judgment; the platform only
    # carries what answers there.
    "app": "application/x-cheesex-app",
}

#: Which artifact kind a filename implies, when the caller named none.
#:
#: Asking the agent to restate in a flag what the extension already says is a
#: rule it can get wrong, and the wrong answer here is silent: `report.docx`
#: declared as html reaches the panel as a mis-typed blob rather than an error.
#: An unknown extension still falls back to html, which is what every caller
#: predating this table sent.
_ARTIFACT_KIND_BY_SUFFIX = {
    ".html": "html",
    ".htm": "html",
    ".svg": "svg",
    ".pdf": "pdf",
    ".docx": "docx",
    ".pptx": "pptx",
    ".xlsx": "xlsx",
    ".md": "md",
    ".markdown": "md",
    ".csv": "csv",
    ".png": "png",
    ".jpg": "jpg",
    ".jpeg": "jpg",
    ".gif": "gif",
    ".webp": "webp",
}

#: Ceiling on a published artifact, matching the chat attachment limit below —
#: both are "a file a person will open in this room", and a report that is too
#: big to send as an attachment is too big to publish as a deliverable.
MAX_ARTIFACT_BYTES = 10 * 1024 * 1024


def artifact_kind_for(path: str) -> str:
    """The kind `path`'s extension implies; `html` when it implies none."""
    suffix = path.rsplit("/", 1)[-1]
    dot = suffix.rfind(".")
    return _ARTIFACT_KIND_BY_SUFFIX.get(suffix[dot:].lower() if dot > 0 else "", "html")


def _clean_artifact_path(raw: str) -> str:
    """A workspace-relative pointer — reject absolute paths, traversal, and .git.
    The file itself is read later via the guarded workspace reader."""
    path = (raw or "").strip()
    if not path:
        raise ValidationError("path 不能为空")
    parts = path.split("/")
    if path.startswith("/") or ".." in parts or ".git" in parts:
        raise ValidationError("path 必须是工作区相对路径")
    return path


async def _bind_source_task(
    db: AsyncSession, room_id: uuid.UUID, task: uuid.UUID
) -> None:
    """Refuse a card that is not this room's: a source is not a free-form id."""
    work = await TaskRepository(db).get(task)
    if work is None or work.room_id != room_id or work.branch_name is None:
        raise NotFoundError("Task not found")


async def _source_bytes(
    db: AsyncSession,
    project_id: uuid.UUID,
    room_id: uuid.UUID,
    path: str,
    task: uuid.UUID | None,
    source: Literal["live", "committed"] = "live",
) -> bytes:
    """One of this room's files, from whichever store holds it.

    A room keeps what it delivered outside git; a card keeps what it is still
    writing, on its own branch; the project's 资料库 keeps what someone gave it,
    and `library/<名字>` says so in the path itself. All three are 「这个房间的
    文件」 to a reader, so the viewers take the source as a parameter instead of
    each being wired to one store — that wiring is why a document on a branch
    had no view but a raw binary diff.
    """
    if task is not None or source == "committed":
        if library.library_name(path) is not None:
            raise ValidationError("资料库里的文件不属于某个任务分支")
        from app.domain.repository.forge_files import ProjectFiles

        data, _ = await ProjectFiles(db, project_id, task).raw(path, source)
        return data
    return library.read_attachment(project_id, room_id, path)


async def record_shown(
    db: AsyncSession, place: Place, path: str, *, author: str, mime: str | None = None
) -> dict:
    """List a room file among what the room has on show, and tell open clients.

    What `cheese show` does after writing the file; a file a person creates in
    the room (a copy, a new one from a template) is on show the same way."""
    block = await BlockRepository(db).add(
        project_id=place.project_id,
        topic_id=place.room_id,
        author=author,
        author_type=AuthorType.participant,
        content=path,
        kind=BlockKind.artifact,
        mime_type=mime or _ARTIFACT_MIME[artifact_kind_for(path)],
        refs=[path],
    )
    payload = BlockOut.model_validate(block).model_dump(mode="json")
    # Live, like a published message: the reader is usually in the room while
    # 芝士 works, and the card has to appear then, not on the next reload.
    await get_broker().publish(
        str(place.room_id), {"type": "assistant_block", "block": payload}
    )
    return payload
