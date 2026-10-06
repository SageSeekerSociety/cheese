"""Topic routes."""

import shutil
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Query, Response
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolver, ActorResolverDep
from app.api.conditional import etag_for_json, if_none_match_hits
from app.api.deps import (
    get_broker,
    get_chat_service,
    get_work_runner,
)
from app.api.place import project_reader
from app.api.response import ok, page
from app.core.config import settings
from app.core.db import get_db
from app.core.errors import ForbiddenError, NotFoundError, ValidationError
from app.core.sentences import say
from app.domain.agent.activity import WORKING
from app.domain.agent.chat import ChatService, project_refs_text
from app.domain.agent.runtime import (
    AgentWorkRunner,
    InProcessBroker,
    addressed_to_agent,
)
from app.domain.block.editing import edit_message
from app.domain.block.models import (
    CHECKLIST_META_KEY,
    AuthorType,
    Block,
    BlockKind,
    checklist_text,
)
from app.domain.block.repositories import BlockRepository
from app.domain.block.schemas import BlockOut
from app.domain.block.waits import REPLY_LOOKBACK, MemberWait, MemberWaits, StuckCard
from app.domain.conversation.services import room_of
from app.domain.idempotency import store as idem
from app.domain.idempotency.keys import action_key
from app.domain.identity.actor import Actor
from app.domain.mentions import canonicalize_refs
from app.domain.project.repositories import ProjectRepository
from app.domain.review import archive
from app.domain.review.models import AcceptCard
from app.domain.review.repositories import AcceptCardRepository
from app.domain.room_task import presentation
from app.domain.room_task.models import LockKind
from app.domain.room_task.place import Place
from app.domain.room_task.services import (
    RoomLockService,
    TaskService,
)
from app.domain.thread import reads as thread_reads
from app.domain.topic.models import Topic, TopicKind
from app.domain.topic.repositories import (
    SortOrder,
    TopicProgressRepository,
    TopicRepository,
    TopicSortField,
)
from app.domain.topic.schemas import (
    CheckResultIn,
    LockIn,
    MemberActivityOut,
    MemberWaitOut,
    TopicCreate,
    TopicOut,
)
from app.domain.topic.services import TopicRelevance, TopicService
from app.domain.topic_membership.services import TopicMemberService
from app.domain.usage.repositories import UsageRepository
from app.domain.usage.services import UsageService
from app.domain.webhook import service as webhook_service

router = APIRouter(prefix="/topics", tags=["topics"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


async def _actor_in_place(resolver: ActorResolver, place: Place) -> Actor:
    """Who is calling here, and whether they may be — identity, then the
    room's roster."""
    actor = await resolver.resolve(
        topic_id=place.conversation_id, project_id=place.project_id
    )
    await resolver.authorize_topic(
        actor, project_id=place.project_id, topic_id=place.room_id
    )
    return actor


@router.post("")
async def create_topic(
    body: TopicCreate, db: DbSession, resolver: ActorResolverDep
) -> dict:
    actor = await resolver.resolve(project_id=body.project_id)
    await resolver.authorize_project(actor, project_id=body.project_id)
    # The creator becomes the topic's roster owner (fusion-design §3), and the
    # creator is whoever the credential names — nobody when there is none.
    topic = await TopicService(db).create(
        project_id=body.project_id,
        title=body.title,
        parent_id=body.parent_id,
        created_by=actor.handle if actor.authenticated else None,
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
        settled_since=datetime.now(UTC) - REPLY_LOOKBACK,
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
    activity: list[dict] | None = None,
    waits: list[MemberWait] | None = None,
) -> dict:
    """TopicOut plus the signals the ORM row cannot carry: 最后活动时间, which is
    derived from the topic's blocks; who is busy in it right now and whom it is
    waiting on (`activity` / `waits` — about members, never about the room); and
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
    out.activity = [MemberActivityOut(**entry) for entry in activity or []]
    # A member working here right now is not one the room is waiting on: it is
    # the one handling it.
    working = {a.member for a in out.activity if a.kind == WORKING}
    out.waits = [
        MemberWaitOut(member=w.member, reason=w.reason, since=w.since, pr=w.pr)
        for w in waits or []
        if w.member is None or w.member not in working
    ]
    mine = (relevance or {}).get(topic.id, TopicRelevance())
    # 芝士停在一道只有我能回答的问题上，同样是「在等我」——而且比一张卡更急：卡是
    # 一轮结束后的状态，提问是一轮**停在半路**。它也蕴含参与，理由同上。
    asking_me = topic.id in (asks_me or set())
    out.i_participate = mine.i_participate or asking_me
    out.awaits_me = mine.awaits_me or asking_me
    data = out.model_dump(mode="json")
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


#: 侧栏每 30 秒轮询一次整份话题清单。它是**登录用户**的私有视图（每一行都带「与我的
#: 相关性」），所以只能是 `private`；`no-cache` 要求每次带 `If-None-Match` 回来问一句，
#: 命中 ETag 就回 304、空 body —— 没有变化的那些轮询不再把一个几百 KB 的清单重传一遍。
#: 和 `admin_members` 那份名单同一个形状。
TOPICS_LIST_CACHE_CONTROL = "private, no-cache"


@router.get("", response_model=None)
async def list_topics(
    project_id: uuid.UUID,
    db: DbSession,
    runner: Annotated[AgentWorkRunner, Depends(get_work_runner)],
    broker: Annotated[InProcessBroker, Depends(get_broker)],
    resolver: ActorResolverDep,
    response: Response,
    sort: TopicSortField | None = None,
    order: SortOrder = "asc",
    active_since: datetime | None = None,
    topic: str = "",
    if_none_match: Annotated[str | None, Header()] = None,
) -> dict | Response:
    """The project's topics.

    `sort=last_activity_at` orders by when something last HAPPENED in each topic
    (its newest block), and `active_since=<ISO instant>` keeps only the topics
    active at or after it — "最近活跃的话题". Neither reads `updated_at`, which
    only moves when the topic's own fields change.

    Every row also carries 与我的相关性 (`i_participate`/`awaits_me`) for the
    caller — this is the endpoint the sidebar groups from.

    条件请求：`ETag` 由整份信封的规范化 JSON 算出（`etag_for_json`），`If-None-Match`
    命中就回 304、空 body。清单里每一行都是「数据库 + 在跑的会话」推出来的：一个房间的
    徽章会因为成员刚被拉进来、一张验收卡刚落地、某个队友刚开始干活而变，而这些都不动
    `updated_at`，所以「有没有变」只能靠整份 body 的指纹来判，不能靠某一列的时间戳。
    没命中那条路仍然走 `ok()` 的信封，前端 `request()` 靠 `{code,message,data}` 解包。
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
    asked = await BlockRepository(db).awaiting_an_answer([t.id for t in topics])
    asks_me = _asks_me(asked, _viewer(actor))
    # 一次，给整页用同一个「现在几点」——见 list_project_tasks 里同一行的理由。
    now = datetime.now(UTC)
    # 每个房间在等哪几位成员，一次查完。
    waits = await MemberWaits(db).for_rooms(
        [t.id for t in topics], now=now, stuck_rooms=_stuck_on_checks(live)
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
            activity=broker.activity.snapshot(str(t.id)),
            waits=waits.get(t.id),
        )
        for t in topics
    ]
    payload = ok(page(items, total))
    etag = etag_for_json(payload)
    cache_headers = {
        "Cache-Control": TOPICS_LIST_CACHE_CONTROL,
        "ETag": f'"{etag}"',
    }
    if if_none_match and if_none_match_hits(if_none_match, etag):
        return Response(status_code=304, headers=cache_headers)
    response.headers.update(cache_headers)
    return payload


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
    broker: Annotated[InProcessBroker, Depends(get_broker)],
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
    place = await service.place_or_404(topic_id)
    if place.task is not None:
        # A task is read as `GET /topics/{task}/task`.
        raise NotFoundError("Topic not found")
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
    asked = await BlockRepository(db).awaiting_an_answer([topic.id])
    waits = await MemberWaits(db).for_rooms(
        [topic.id], now=datetime.now(UTC), stuck_rooms=_stuck_on_checks(live)
    )
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
            activity=broker.activity.snapshot(str(topic.id)),
            waits=waits.get(topic.id),
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
        raise ValidationError(say("cursorOneAnchor"))
    if limit is None and (after is not None or around is not None):
        raise ValidationError(say("cursorNeedsLimit"))
    place = await TopicService(db).place_or_404(topic_id)
    await _actor_in_place(resolver, place)
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
    if limit is None:
        blocks = await repo.list_for_topic(place.conversation_id)
        if older_than is not None:
            blocks = [
                b
                for b in blocks
                if (b.created_at, b.id) < (older_than.created_at, older_than.id)
            ]
        has_more = has_newer = False
    elif centre is not None:
        above = await repo.page_for_topic(
            place.conversation_id, limit=limit // 2, before=centre
        )
        below = await repo.page_for_topic(
            place.conversation_id, limit=limit - limit // 2, after=centre
        )
        blocks = [*above.items, centre, *below.items]
        has_more, has_newer = above.has_more, below.has_more
    elif newer_than is not None:
        page_result = await repo.page_for_topic(
            place.conversation_id, limit=limit, after=newer_than
        )
        blocks = page_result.items
        # Paging down from a block means that block is above this page.
        has_more, has_newer = True, page_result.has_more
    else:
        page_result = await repo.page_for_topic(
            place.conversation_id, limit=limit, before=older_than
        )
        blocks = page_result.items
        # Paging up from a block means that block is below this page.
        has_more, has_newer = page_result.has_more, older_than is not None
    total = await repo.count_for_topic(place.conversation_id)
    # Emoji reactions ride the same payload — ONE batch query, no per-block N+1.
    # Scoped to THIS page's ids, so paging saves the database work too, not just
    # the bytes on the wire.
    reactions = await repo.reactions_for_blocks([b.id for b in blocks])
    # The line under each main-line message that has a 支线: same batch shape.
    threads = (
        await thread_reads.under_messages(db, [b.id for b in blocks])
        if place.inner_id is None
        else {}
    )
    items = []
    for b in blocks:
        item = BlockOut.model_validate(b).model_dump(mode="json")
        if b.id in reactions:
            item["reactions"] = reactions[b.id]
        if b.id in threads:
            item["thread"] = threads[b.id]
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
    repo: BlockRepository, conversation_id: uuid.UUID, block_id: uuid.UUID
) -> Block:
    block = await repo.get(block_id)
    if block is None or block.conversation_id != conversation_id:
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
    place = await TopicService(db).place_or_404(topic_id)
    await _actor_in_place(resolver, place)
    if before is not None and after is not None:
        raise ValidationError("Use before or after, not both")
    repo = BlockRepository(db)
    parent = None
    if reply_to is not None:
        parent = await _history_block(repo, place.conversation_id, reply_to)
    cursor = None
    if cursor_id := before or after:
        cursor = (
            await _channel_block(db, repo, place.room_id, cursor_id)
            if channel
            else await _history_block(repo, place.conversation_id, cursor_id)
        )
    result = await repo.page_for_topic(
        place.conversation_id,
        whole_room=place.room_id if channel else None,
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

    The room's whole bill, its tasks included: a task's sessions spend under
    the room as well as under the task.
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
    cards = [
        card
        for card in await AcceptCardRepository(db).list_for_topic(place.room_id)
        if place.task is None or card.task_id == place.task.id
    ]
    credits = await UsageService(db).project_credits(place.project_id)
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
                "queued_turns": await runner.project_queue_depth(place.project_id),
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
    actor = await resolver.resolve(topic_id=topic_id, project_id=topic.project_id)
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
) -> dict:
    """进度层 (#187): 芝士's checklist for this conversation — a room's, or a
    task's, written by its own session — as of the last turn to touch it. Read
    on open: between turns there is no WS stream to carry it, and "做到哪了" has
    to be visible without summoning anyone."""
    place = await TopicService(db).place_or_404(topic_id)
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


@router.put("/{topic_id}/progress")
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
    place = await TopicService(db).place_or_404(topic_id)
    actor = await resolver.resolve(
        topic_id=place.conversation_id, project_id=place.project_id
    )
    await resolver.authorize_topic(
        actor, project_id=place.project_id, topic_id=place.room_id, enforce=True
    )
    if not actor.authenticated:
        # The global development token opens the room but is nobody, and a
        # message needs an author.
        raise ForbiddenError("Sign in to write a checklist")
    # A task's or a 支线's checklist is written by its own session, and shown
    # as its progress; a room's by an agent seated in it, as a message.
    inner_id = place.inner_id
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
            place.room_id, actor.handle, message=body.message
        )
        if current is None and body.message is not None:
            raise NotFoundError("Checklist not found in this room")
        if current is not None and current.author != actor.handle:
            raise ForbiddenError("Only the author can edit this message")
    items = [
        {"id": str(number), "subject": todo.content, "status": todo.status}
        for number, todo in enumerate(body.todos, start=1)
    ]
    runner = get_work_runner()
    turn_id = None
    if plans_the_turn:
        work = runner.live_work_for_topic(place.conversation_id)
        turn_id = uuid.UUID(work["turn_id"]) if work is not None else None
        await TopicProgressRepository(db).save(
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
        # An agent's list is its own output; a person's is said to the room and
        # waits to be read like anything else they post.
        own_output=plans_the_turn,
        extra_meta={CHECKLIST_META_KEY: checklist},
    )
    assert message is not None  # a publication with no eid never deduplicates
    await get_broker().publish(
        str(place.room_id), {"type": "assistant_block", "block": message}
    )
    if turn_id is not None:
        runner.note_session_output(turn_id, tool=False)
    return ok({"items": items, "message_id": message["id"], "posted": True})


@router.get("/{topic_id}/overview")
async def get_topic_overview(
    topic_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """总览房间的自动区（#1889）：②③，结构化，给文档面板正文下方那一栏。

    总览文档是三块：① 写在文档正文里，②③ 由平台现拼。注入 agent 提示词的
    那一份是同一批数据的 markdown 排版（`topic/overview.py`），这里给的是能
    逐个点击的结构化条目。

    授权和读文档那一份完全一样：先认出「谁在这儿」，再看他在不在这个房间的
    名册上。只有根话题有总览，别处 404（见 `TopicService.overview_auto`）。
    """
    topics = TopicService(db)
    place = await topics.place_or_404(topic_id)
    await _actor_in_place(resolver, place)
    blocks = await topics.overview_auto(place.room_id)
    return ok({"root_topic_id": str(place.room_id), "blocks": blocks})


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
        topic_id=place.conversation_id, project_id=place.project_id
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
    # 房间），才回落到默认席位。
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
            say("pendingHandedTo", actor=f"<@{actor.handle}>", seat=f"<@{seat}>")
            if seat
            else say("pendingHandedOver", actor=f"<@{actor.handle}>")
        ),
        provision_actor=actor,
    )
    return ok({"started": True})


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
        project_id=place.project_id, topic_id=place.conversation_id
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


def _parse_moment(raw: object) -> datetime | None:
    """一个可选的 ISO-8601 时刻；空串和缺席是一回事。"""
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        moment = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        raise ValidationError(say("sinceUntilIso")) from None
    # 不带时区的按 UTC 读：否则它和 now() 相减时 naive/aware 直接抛，而调用方
    # 只写了个「2026-09-06」也得能用。
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _weekly_window(body: dict) -> tuple[datetime, datetime]:
    """这份周报讲的是哪一段。略过就是「截止到现在的一周」。"""
    until = _parse_moment(body.get("until")) or datetime.now(UTC)
    since = _parse_moment(body.get("since")) or until - timedelta(days=7)
    if since > until:
        raise ValidationError(say("sinceAfterUntil"))
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

    `refs` 指向它写在哪：周报集里那一行的「来自话题」靠它跳回去。
    """
    place = await TopicService(db).place_or_404(topic_id)
    actor = await _actor_in_place(resolver, place)
    report = (body.get("body") or "").strip()
    if not report:
        raise ValidationError(say("bodyRequired"))
    since, until = _weekly_window(body)
    report = await canonicalize_refs(
        db, place.project_id, report, exclude_topic_id=place.room_id
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


@router.post("/{topic_id}/read")
async def mark_topic_read(
    topic_id: uuid.UUID, body: dict, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """已读位: bump the caller's read cursor on a room or on one of its tasks
    (opening either clears its unread badge, Feishu-style). ``topic_id`` is
    the conversation's id; the door is its room's.

    The cursor is per person, so whose it is comes from the verified
    credential — ``handle`` in the body is only an assertion checked against
    it (it used to BE the identity, letting anyone move anyone's cursor)."""
    room_id = await room_of(db, topic_id)
    topic = await TopicService(db).get_or_404(room_id)
    actor = await resolver.resolve(topic_id=room_id, project_id=topic.project_id)
    await resolver.authorize_topic(actor, project_id=topic.project_id, topic_id=room_id)
    handle = await resolver.resolve_recipient(
        requested=(body.get("handle") or "").strip() or None,
        project_id=topic.project_id,
        allow_anonymous=False,
    )
    await TopicService(db).mark_read(topic_id, handle)
    return ok({"topic_id": str(topic_id), "handle": handle})


@router.put("/{topic_id}/notify-level")
async def set_topic_notify_level(
    topic_id: uuid.UUID, body: dict, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """这间房对我的通知级别：`all` 或 `mute`。和已读位一样按人记，人是谁取自
    已验证的凭据。"""
    topic = await TopicService(db).get_or_404(topic_id)
    actor = await resolver.resolve(topic_id=topic_id, project_id=topic.project_id)
    await resolver.authorize_topic(
        actor, project_id=topic.project_id, topic_id=topic_id
    )
    handle = await resolver.resolve_recipient(
        requested=None, project_id=topic.project_id, allow_anonymous=False
    )
    level = str(body.get("level") or "")
    await TopicService(db).set_notify_level(topic_id, handle, level)
    return ok({"topic_id": str(topic_id), "level": level})


@router.post("/{topic_id}/archive")
async def archive_topic(
    topic_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """手动归档 (归档去向): explicit archive, independent of 采纳."""
    topic = await TopicService(db).get_or_404(topic_id)
    actor = await resolver.resolve(topic_id=topic_id, project_id=topic.project_id)
    await resolver.authorize_topic(
        actor, project_id=topic.project_id, topic_id=topic_id
    )
    if not actor.authenticated:
        raise ForbiddenError(say("archiveSignIn"))
    await TopicMemberService(db).require_archive_manager(topic_id, actor.handle)
    topic = await TopicService(db).archive(topic_id, by=actor.handle)
    return ok(TopicOut.model_validate(topic).model_dump(mode="json"))


@router.get("/{topic_id}/cleanup")
async def cleanup_status(
    topic_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    from app.domain.topic.models import RoomCleanup

    topic = await TopicService(db).get_or_404(topic_id)
    actor = await resolver.resolve(topic_id=topic_id, project_id=topic.project_id)
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
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """取消归档: bring an archived topic back to active."""
    topic = await TopicService(db).get_or_404(topic_id)
    actor = await resolver.resolve(topic_id=topic_id, project_id=topic.project_id)
    await resolver.authorize_topic(
        actor, project_id=topic.project_id, topic_id=topic_id
    )
    if not actor.authenticated:
        raise ForbiddenError(say("unarchiveSignIn"))
    await TopicMemberService(db).require_archive_manager(topic_id, actor.handle)
    topic = await TopicService(db).unarchive(topic_id, by=actor.handle)
    return ok(TopicOut.model_validate(topic).model_dump(mode="json"))


@router.post("/{topic_id}/check-result")
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
    place = await TopicService(db).place_or_404(topic_id)
    await _actor_in_place(resolver, place)
    if place.task is None:
        raise NotFoundError(say("taskNotFound"))
    task = place.task
    await TaskService(db).record_check(task, ok=body.ok, detail=body.detail)
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
    holder = await _lock_holder(db, place, body)
    acquired, reason = await RoomLockService(db).acquire(
        room_id=place.room_id,
        kind=LockKind(body.kind),
        resource=body.resource or "",
        holder_task_id=holder,
    )
    await db.commit()
    return ok({"acquired": acquired, "reason": reason})


async def _lock_holder(db, place, body: LockIn) -> uuid.UUID:
    """The task a room lock is taken or given back for: the task asking, or
    for a room the task it names."""
    if place.task is not None:
        if body.task_id not in (None, place.task.id):
            raise ValidationError(say("taskNotFound"))
        return place.task.id
    if body.task_id is None:
        raise ValidationError(say("taskNotFound"))
    await TaskService(db).require_in_room(place.room_id, body.task_id)
    return body.task_id


@router.post("/{topic_id}/unlock")
async def release_room_lock(
    topic_id: uuid.UUID,
    body: LockIn,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    place = await TopicService(db).place_or_404(topic_id)
    await _actor_in_place(resolver, place)
    holder = await _lock_holder(db, place, body)
    released = await RoomLockService(db).release(
        room_id=place.room_id,
        kind=LockKind(body.kind),
        resource=body.resource or "",
        holder_task_id=holder,
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
        target_topic_id=topic_id, source_topic_id=source_id
    )
    await db.commit()
    return ok(TopicOut.model_validate(topic).model_dump(mode="json"))
