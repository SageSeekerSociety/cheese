"""Topic routes."""

import asyncio
import base64
import binascii
import re
import shutil
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolver, ActorResolverDep
from app.api.deps import (
    get_broker,
    get_chat_service,
    get_work_runner,
    project_device_online,
)
from app.api.response import ok, page
from app.core.config import settings
from app.core.db import get_db
from app.core.errors import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    SystemBusyError,
    ValidationError,
)
from app.domain.agent.announce import notify_question
from app.domain.agent.chat import ChatService
from app.domain.agent.device_hub import device_hub
from app.domain.agent.harness.prompt import thread_relay_prompt, thread_upgraded_prompt
from app.domain.agent.market import (
    COMPUTE_CLOUD,
    COMPUTE_DEVICE,
    MACHINE_VISIBILITY_NOTICE,
    VISIBILITY_HOST,
    compute_default_name,
    compute_listings,
    compute_selectable,
    visibility_listings,
)
from app.domain.agent.preview_hub import preview_hub
from app.domain.agent.repositories import AgentTurnRepository
from app.domain.agent.runtime import AgentWorkRunner
from app.domain.agent_session.services import AgentSessionService
from app.domain.block.models import AuthorType, Block, BlockKind, agent_notice
from app.domain.block.repositories import BlockRepository
from app.domain.block.schemas import BlockOut
from app.domain.device.supply import Visibility
from app.domain.device.wiring import sql_device_service
from app.domain.documents.convert import (
    ConvertFailed,
    ConvertUnavailable,
    convert,
    upgraded_name,
)
from app.domain.documents.revisions import (
    RevisionsFailed,
    RevisionsUnsupported,
    decide,
    revisions_in,
)
from app.domain.documents.spreadsheet import (
    SpreadsheetRecalcFailed,
    SpreadsheetRecalcUnavailable,
    recalculate,
)
from app.domain.idempotency import store as idem
from app.domain.idempotency.keys import action_key
from app.domain.identity.actor import Actor
from app.domain.machine.services import MachineService
from app.domain.mentions import canonicalize_refs
from app.domain.preview.office import (
    OfficeRenderFailed,
    OfficeRenderUnavailable,
    is_renderable,
    render_to_pdf,
)
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
from app.domain.topic.models import Topic, TopicKind
from app.domain.topic.relay import TopicRelayService
from app.domain.topic.repositories import SortOrder, TopicSortField
from app.domain.topic.schemas import (
    BindSubagentIn,
    CheckResultIn,
    ConclusionIn,
    DocEditIn,
    LockIn,
    RelayIn,
    SplitIn,
    TopicCreate,
    TopicOut,
    UpgradeBlockIn,
)
from app.domain.topic.services import TopicRelevance, TopicService
from app.domain.topic_membership.services import TopicMemberService
from app.domain.usage.repositories import ComputeGrantRepository, UsageRepository
from app.domain.webhook import service as webhook_service
from app.domain.workspace import service as ws
from app.domain.workspace.textfile import content_version

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
    response = ok(TopicOut.model_validate(topic).model_dump(mode="json"))
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


async def _live_room_cards(
    db: AsyncSession, room_ids: list[uuid.UUID]
) -> dict[uuid.UUID, AcceptCard]:
    """每个房间**自己**那张还没结算的验收卡，一次查完。

    只要没结算的：一张已经决议的卡对看板没有话说（`presentation` 读到它会让位给
    别的判据），所以拉全量只是白读。哪些状态算「还没结算」不在这里数——那张表是
    `review/archive.py` 维护的，抄第二份就是让它们走散。

    `task_id is None` 才是房间自己的卡：一条活递的卡把房间记在 `topic_id` 上，不
    过滤的话，一条活在等验收会让它上面那个房间也显示成等验收。
    """
    if not room_ids:
        return {}
    cards = await AcceptCardRepository(db).list_live_for_places(
        room_ids, statuses=archive.OPEN_CARD_STATUSES
    )
    # 按 created_at 升序回来，所以同一个房间后写的覆盖先写的 = 留下最新那张。
    return {c.topic_id: c for c in cards if c.task_id is None}


def _topic_out(
    topic: Topic,
    running_ids: set[uuid.UUID],
    last_activity: dict[uuid.UUID, datetime],
    relevance: dict[uuid.UUID, TopicRelevance] | None = None,
    cards: dict[uuid.UUID, AcceptCard] | None = None,
    now: datetime | None = None,
    managed_ids: set[uuid.UUID] | None = None,
    asked: set[uuid.UUID] | None = None,
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
    mine = (relevance or {}).get(topic.id, TopicRelevance())
    out.i_participate = mine.i_participate
    out.awaits_me = mine.awaits_me
    data = out.model_dump(mode="json")
    data["running"] = topic.id in running_ids
    facts = presentation.facts_for_room(
        topic,
        running_ids,
        (cards or {}).get(topic.id),
        awaiting_answer=topic.id in (asked or set()),
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
    resolver: ActorResolverDep,
    sort: TopicSortField | None = None,
    order: SortOrder = "asc",
    active_since: datetime | None = None,
) -> dict:
    """The project's topics.

    `sort=last_activity_at` orders by when something last HAPPENED in each topic
    (its newest block), and `active_since=<ISO instant>` keeps only the topics
    active at or after it — "最近活跃的话题". Neither reads `updated_at`, which
    only moves when the topic's own fields change.

    Every row also carries 与我的相关性 (`i_participate`/`awaits_me`) for the
    caller — this is the endpoint the sidebar groups from.
    """
    actor = await resolver.resolve(fallback_handle=None, project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    service = TopicService(db)
    topics, last_activity, total = await service.list_for_project(
        project_id, sort=sort, order=order, active_since=active_since
    )
    running_ids = runner.running_topic_ids()
    relevance = await service.relevance_for_topics(topics, _viewer(actor))
    cards = await _live_room_cards(db, [t.id for t in topics])
    managed = (
        await TopicMemberService(db).managed_topic_ids(
            [t.id for t in topics], actor.handle
        )
        if actor.authenticated
        else set()
    )
    # 哪几个房间停在一个未回答的提问上（房间自己那条线）——一次查完。
    asked = await BlockRepository(db).rooms_awaiting_an_answer([t.id for t in topics])
    # 一次，给整页用同一个「现在几点」——见 list_project_tasks 里同一行的理由。
    now = datetime.now(UTC)
    items = [
        _topic_out(t, running_ids, last_activity, relevance, cards, now, managed, asked)
        for t in topics
    ]
    return ok(page(items, total))


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
    cards = await _live_room_cards(db, [topic.id])
    managed = (
        await TopicMemberService(db).managed_topic_ids([topic.id], actor.handle)
        if actor.authenticated
        else set()
    )
    return ok(
        _topic_out(
            topic,
            runner.running_topic_ids(),
            last_activity,
            relevance,
            cards,
            managed_ids=managed,
            asked=await BlockRepository(db).rooms_awaiting_an_answer([topic.id]),
        )
    )


@router.get("/{topic_id}/blocks")
async def list_topic_blocks(
    topic_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    limit: Annotated[int | None, Query(ge=1, le=500)] = None,
    before: uuid.UUID | None = None,
) -> dict:
    """The topic's conversation timeline, oldest-first.

    Authorized, like `/comments` and `/doc` beside it. This one carries the
    conversation ITSELF, and it was the only unguarded route of the three that
    did: measured on a test server, a logged-in caller belonging to no part of
    the project read another team's messages verbatim by holding a topic id.

    Paging is OPT-IN: with no `limit` this returns the whole timeline, exactly
    as it always has. That default is deliberate — agents read this endpoint to
    review history (`cheese api GET /topics/{id}/blocks`), and a default window
    would silently truncate them with no way to notice. Callers that DO page get
    `has_more` + `oldest_id` and can walk backwards.

    - `?limit=N`                  → the newest N blocks (chat is bottom-anchored)
    - `?limit=N&before=<block_id>` → the N blocks immediately older than that one
    """
    place = await TopicService(db).place_or_404(topic_id)
    await _actor_in_place(resolver, place)
    repo = BlockRepository(db)
    cursor: Block | None = None
    if before is not None:
        cursor = await repo.get(before)
        # An unknown cursor must not silently degrade into "newest N" — that
        # would hand the caller a duplicate page it can't distinguish. A cursor
        # from one of this room's CARDS is just as wrong as one from another
        # room: the card's timeline is read through the card.
        if (
            cursor is None
            or cursor.topic_id != place.room_id
            or cursor.task_id is not None
        ):
            raise NotFoundError("游标消息不存在")
    if limit is None:
        blocks = await repo.list_for_topic(place.room_id)
        if cursor is not None:
            blocks = [
                b
                for b in blocks
                if (b.created_at, b.id) < (cursor.created_at, cursor.id)
            ]
        has_more = False
    else:
        page_result = await repo.page_for_topic(
            place.room_id, limit=limit, before=cursor
        )
        blocks, has_more = page_result.items, page_result.has_more
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
    actor = await resolver.resolve(
        fallback_handle=None, topic_id=topic_id, project_id=topic.project_id
    )
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
    # One answer for the whole room: every thread's worker lives in this room's
    # one session, so the screen is alive for all of them or for none.
    screen_live = chat.has_live_screen(topic_id)
    now = datetime.now(UTC)
    items = []
    for task, blocks in threads:
        card = cards.get(task.id)
        items.append(
            {
                **TaskOut.model_validate(task).model_dump(mode="json"),
                # 同一个函数算的那一格，和项目级列表、和这条活自己的头一模一样。
                "presentation": presentation.task_presentation(
                    presentation.facts_for_task(
                        task,
                        card,
                        beats.get(task.id),
                        room_screen_live=screen_live,
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
) -> dict:
    """One card, with its conversation — the same shape `/tasks` lists.

    Through the room, because a card is not a place: `GET /topics/{card}` is a
    404 by construction, and the person reading a card is standing in the room
    it belongs to anyway.

    `limit` caps the timeline at its newest N blocks; with none it comes back
    whole. Same default as `/blocks` and for the same reason — an invented
    window truncates an agent reading history with no way to notice.
    """
    place = await TopicService(db).place_or_404(topic_id)
    await _actor_in_place(resolver, place)
    tasks = TaskService(db)
    task = await tasks.get(task_id)
    if task is None or task.room_id != place.room_id:
        raise NotFoundError("这个房间里没有这条活")
    blocks = await tasks.blocks_for_thread(task_id, limit=limit)
    cards = await AcceptCardRepository(db).latest_by_task([task.id])
    beats = await TaskRepository(db).last_block_at_for_tasks([task.id])
    out = TaskOut.model_validate(task).model_dump(mode="json")
    # 看板那一格，和它在列表里显示的是同一句话——同一个函数算的，所以深链接进来
    # 和从看板点进来不可能给出两种说法。
    out["presentation"] = presentation.task_presentation(
        presentation.facts_for_task(
            task,
            cards.get(task.id),
            beats.get(task.id),
            # 做这条活的分身住在房间的会话里 —— 屏幕没了它就没了，而它不会来说
            # 一声。这一位是内存里的当下事实，不是库里的一列。
            room_screen_live=chat.has_live_screen(place.room_id),
            awaiting_answer=bool(
                await BlockRepository(db).tasks_awaiting_an_answer([task.id])
            ),
        ),
        now=datetime.now(UTC),
    ).as_dict()
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


@router.post("/{topic_id}/tasks/{task_id}/messages")
async def say_on_task(
    topic_id: uuid.UUID,
    task_id: uuid.UUID,
    body: dict,
    db: DbSession,
    resolver: ActorResolverDep,
    chat: Annotated[ChatService, Depends(get_chat_service)],
    runner: Annotated[AgentWorkRunner, Depends(get_work_runner)],
) -> dict:
    """在一张卡下面说话 —— 落在这条活的时间线上，房间被叫来转达。

    A person watching a card cannot reach the 分身 doing it: that worker lives
    inside the room's session and only the room's 芝士 can pass it a message.
    So this lands what was said WHERE THE WORK IS, and wakes the ROOM to act on
    it. Nothing is woken on the card — there is no session there to wake.

    Through the room's id for the same reason `/bind` and `/title` are: a card
    is not a place, so it has no address of its own and no token scoped to it.
    """
    place = await TopicService(db).place_or_404(topic_id)
    task = await TaskService(db).get(task_id)
    if task is None or task.room_id != place.room_id:
        raise NotFoundError("这个房间里没有这条活")
    content = (body.get("content") or "").strip()
    if not content:
        raise ValidationError("消息内容不能为空")
    actor = await resolver.resolve(
        fallback_handle=body.get("author"),
        topic_id=place.room_id,
        project_id=place.project_id,
    )
    await resolver.authorize_topic(
        actor, project_id=place.project_id, topic_id=place.room_id
    )
    content = await canonicalize_refs(
        db, place.project_id, content, exclude_topic_id=place.room_id
    )
    block = await BlockRepository(db).add(
        project_id=place.project_id,
        topic_id=place.room_id,
        task_id=task.id,
        author=actor.handle,
        author_type=AuthorType.ai if actor.is_agent else AuthorType.human,
        content=content,
        kind=BlockKind.message,
    )
    payload = BlockOut.model_validate(block).model_dump(mode="json")
    # Visible before the turn that reads it — same ordering as the doc comment.
    await db.commit()
    # 卡下的实时帧走这条活自己的频道，因为块落在这条活上：发给房间的话，看着房间
    # 的人会看见一条刷新之后就搬走了的消息。
    await get_broker().publish(
        str(task.id), {"type": "assistant_block", "block": payload}
    )
    if not actor.is_agent:
        runner.submit(
            chat,
            place.room_id,
            author="system",
            content=thread_relay_prompt(
                task_id=task.id,
                task_title=task.title,
                author=actor.handle,
                message=f"说：{content}",
            ),
            summon=True,
            nudge_event=f"{actor.handle} 在一条活上说话了，芝士来转达",
            provision_actor=actor,
        )
    return ok(payload)


@router.post("/{topic_id}/tasks/{task_id}/bind")
async def bind_task_subagent(
    topic_id: uuid.UUID,
    task_id: uuid.UUID,
    body: BindSubagentIn,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """认领: the room says which worker in its session is doing this thread.

    The one new thing a room has to tell the platform under 任务=分身. A worker
    id is minted inside the container when the worker starts, so nothing handed
    out at dispatch could name it — the room spawns one and reports back, and
    only then can the platform tell that worker's events from its own.

    ONLY the room may call it, and only the room can: a card is not a place,
    so `{topic_id}` naming one is a 404 before the body is read.
    """
    place = await TopicService(db).place_or_404(topic_id)
    await _actor_in_place(resolver, place)
    task = await TaskService(db).bind_subagent(
        room_id=place.room_id, task_id=task_id, subagent_id=body.agent_id
    )
    out = TaskOut.model_validate(task).model_dump(mode="json")
    await db.commit()
    return ok(out)


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
        raise NotFoundError("这个房间里没有这条活")
    title = (body.get("title") or "").strip()
    if not title:
        raise ValidationError("title 不能为空")
    task.title = title[:80]
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
    on every `SubagentStop` from a bound worker. This is the other half — the
    room saying the work is over — and it is deliberately a separate act, done
    by hand.

    It has to be. A worker reports finished more than once (parking a long
    command in its own background counts as finishing), and stops arrive from
    workers the platform never bound — measured on 2.1.224: after the session's
    own Stop, with an unknown id, an empty type and a fragment of a prompt as
    their closing message. Closing on either of those would collapse work that
    is still going. The room decides when work has ended; code acceptance merges
    the task's branch and closes it through the separate acceptance flow.

    `conclusion` is optional: given, it overwrites the worker's last word (which
    is sometimes the fragment above); omitted, that last word stands.
    """
    place = await TopicService(db).place_or_404(topic_id)
    await _actor_in_place(resolver, place)
    tasks = TaskService(db)
    task = await tasks.get(task_id)
    if task is None or task.room_id != place.room_id:
        raise NotFoundError("这个房间里没有这条活")
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


@router.get("/{topic_id}/transcript")
async def topic_transcript(
    topic_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    limit: int | None = Query(None, ge=1, le=200),
    before: uuid.UUID | None = None,
) -> dict:
    """施工现场 (spec §7.1): the topic's AI session record — tool/event actions,
    read-only, newest window first.

    Paged for the same reason the conversation is: events are the MOST numerous
    kind of block (one per tool call), so a topic that has run for a while makes
    this the largest response the app can ask for, and it only ever grows.
    `limit=None` keeps the whole-history behaviour for callers that still want
    it.

    The room's own line. What one of its 分身 did is on that card, and is read
    through it (`GET /topics/{room}/tasks/{card}`) — interleaving every card's
    actions here would bury what the room itself did."""
    place = await TopicService(db).place_or_404(topic_id)
    await _actor_in_place(resolver, place)
    repo = BlockRepository(db)
    # 现场 = what 芝士 DID (tool/system events), full stop. Its messages belong
    # to the conversation pane — mirroring them here just duplicates the chat.
    kinds = {BlockKind.event}
    cursor: Block | None = None
    if before is not None:
        cursor = await repo.get(before)
        # Same rule as the conversation's pager: an unknown cursor must not
        # degrade into "newest N", which the caller cannot tell from a real page.
        # A cursor from one of this room's CARDS is as wrong as one from
        # another room.
        if (
            cursor is None
            or cursor.topic_id != place.room_id
            or cursor.task_id is not None
        ):
            raise NotFoundError("游标事件不存在")
    if limit is None:
        site = [b for b in await repo.list_for_topic(place.room_id) if b.kind in kinds]
        has_more = False
    else:
        result = await repo.page_for_topic(
            place.room_id,
            limit=limit,
            before=cursor,
            kinds=kinds,
        )
        site, has_more = result.items, result.has_more
    items = [BlockOut.model_validate(b).model_dump(mode="json") for b in site]
    return ok(
        {
            **page(items, len(items)),
            "has_more": has_more,
            "oldest_id": str(site[0].id) if site else None,
        }
    )


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
        # (`cheese gh-token` + gh api) — the snapshot carries the pointer.
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


@router.get("/{topic_id}/docs")
async def list_topic_docs(
    topic_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """Document-tree view of a topic: the living doc's structured node tree
    (B1, spec §5) in document order."""
    place = await TopicService(db).place_or_404(topic_id)
    await _actor_in_place(resolver, place)
    nodes = await BlockRepository(db).list_doc_nodes(place.room_id)
    items = [BlockOut.model_validate(b).model_dump(mode="json") for b in nodes]
    return ok(page(items, len(items)))


@router.get("/{topic_id}/comments")
async def list_comments(
    topic_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """段落评论 (eval B4): inline comments, each anchored to a doc node via
    reply_to."""
    place = await TopicService(db).place_or_404(topic_id)
    await _actor_in_place(resolver, place)
    comments = await BlockRepository(db).list_comments_for_topic(place.room_id)
    items = [BlockOut.model_validate(c).model_dump(mode="json") for c in comments]
    return ok(page(items, len(items)))


@router.post("/{topic_id}/comments")
async def add_comment(
    topic_id: uuid.UUID,
    body: dict,
    db: DbSession,
    resolver: ActorResolverDep,
    chat: Annotated[ChatService, Depends(get_chat_service)],
    runner: Annotated[AgentWorkRunner, Depends(get_work_runner)],
) -> dict:
    """Add an inline comment anchored to a doc node (eval B4). Dual-use like the
    doc panel — a human selects text and comments; not cheese-gated."""
    place = await TopicService(db).place_or_404(topic_id)
    anchor = (body.get("anchor") or "").strip()
    content = (body.get("content") or "").strip()
    if not content:
        raise ValidationError("评论内容不能为空")
    repo = BlockRepository(db)
    reply_to: uuid.UUID | None = None
    if anchor:
        node = await repo.get(uuid.UUID(anchor))
        # `task_id` too: a card's blocks sit under the same `topic_id`, so
        # checking only the room would let a comment anchor onto one of them.
        if node is None or node.topic_id != place.room_id or node.task_id is not None:
            raise ValidationError("锚点不是本话题的文档块")
        reply_to = node.id
    # B4 Feishu-style: the exact selected span, kept for display next to the
    # comment. Bounded so a runaway selection can't bloat the row.
    quote = (body.get("quote") or "").strip() or None
    if quote and len(quote) > 500:
        quote = quote[:500]
    actor = await resolver.resolve(
        fallback_handle=body.get("author"),
        topic_id=place.room_id,
        project_id=place.project_id,
    )
    await resolver.authorize_topic(
        actor, project_id=place.project_id, topic_id=place.room_id
    )
    author = actor.handle
    comment = await repo.add(
        project_id=place.project_id,
        # The place id, not the room's: `add` splits it, and handing it the room
        # would post a thread's comment onto the room for everyone to read.
        topic_id=topic_id,
        author=author,
        author_type=AuthorType.ai if actor.is_agent else AuthorType.human,
        content=content,
        kind=BlockKind.comment,
        reply_to=reply_to,
        anchor_quote=quote,
    )
    payload = BlockOut.model_validate(comment).model_dump(mode="json")
    await db.commit()  # the comment must be visible before the turn reads it
    # 评论即反馈：文档是芝士维护的界面，人评论了就叫它来处理（回应/改文档）。

    if not actor.is_agent:
        where = f"「{quote[:80]}」" if quote else "整篇"
        said = f"在实况文档 {where} 处评论：{content}"
        runner.submit(
            chat,
            place.room_id,
            author="system",
            content=(
                f"{author} {said}\n"
                "请处理这条评论：需要改文档就直接改；有分歧就在对话里简短回应。"
            ),
            summon=True,
            nudge_event=f"{author} 在文档上留了评论，芝士来处理",
            provision_actor=actor,
        )
    return ok(payload)


@router.get("/{topic_id}/progress")
async def get_topic_progress(
    topic_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """进度层 (#187): 芝士's checklist for this topic, as of the last turn to
    touch it. Read on topic open — between turns there is no WS stream to carry
    it, and "做到哪了" has to be visible without summoning anyone."""
    place = await TopicService(db).place_or_404(topic_id)
    await _actor_in_place(resolver, place)
    items, updated_at = await TopicService(db).get_progress(topic_id)
    return ok(
        {
            "items": items,
            "updated_at": updated_at.isoformat() if updated_at else None,
        }
    )


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
    never the text: the doc is one `cheese doc get` away, and a document
    injected mid-turn displaces the work instead of informing it."""
    # A thread has a doc of its own — its brief, and then how the work is
    # going — and `edit_doc` has always written by place. Only this handler
    # still refused to name one, so `cheese doc set` 404ed for every 分身 doing
    # the work while `cheese doc get` right above answered fine.
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
        author_type=AuthorType.ai if actor.is_agent else AuthorType.human,
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
    await broker.publish(str(place.room_id), {"type": "state", "resource": "doc"})
    if notice is not None and (line := agent_notice(notice)):
        # The notice tells 芝士 to go re-read the doc, so the doc has to BE the
        # new one by the time it does — same ordering as the comment route.
        # What it says was written where the document moved (`edit_doc`), so the
        # running turn and the next one are told the same thing; naming `notice`
        # is what lets the receipt stamp it consumed instead of it being said
        # twice.
        await chat.notify_running_turn(topic_id, line, blocks=[notice.id])
    return ok(BlockOut.model_validate(doc).model_dump(mode="json"))


@router.get("/{topic_id}/compute-profile")
async def get_topic_compute_profile(
    topic_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """Room choice, project favorites and the matching execution lock."""
    topic = await TopicService(db).get_or_404(topic_id)
    actor = await resolver.resolve(
        fallback_handle=None, topic_id=topic_id, project_id=topic.project_id
    )
    await resolver.authorize_topic(
        actor, project_id=topic.project_id, topic_id=topic_id
    )
    project = await ProjectRepository(db).get(topic.project_id)
    from app.domain.agent.compute_configs import project_configs, room_choice
    from app.domain.machine.repositories import ProjectMachineRepository

    configs = project_configs(project.settings if project else None)
    choice = room_choice(topic, project.settings if project else None)
    current = choice.profile
    device_online = await project_device_online(db, topic.project_id)
    device_service = sql_device_service(db)
    devices = await device_service.list_devices_for_project(topic.project_id)
    # #282 §四 / #358 · whether THIS topic's agent can see the whole machine. The
    # effective answer is the visibility on the topic↔machine binding (device
    # affinity freezes a topic to one machine on its first turn); a topic
    # on platform compute or not yet pinned has none. Surfaced so the room can show
    # a visible safety badge for a Hosted Machine turn instead of the platform
    # granting whole-machine access silently (原则八).
    binding = await device_service.topic_binding(topic_id)
    if current == COMPUTE_DEVICE and binding is not None:
        choice.device_id = binding.device_id
        if topic.compute_config is None:
            named = next((d for d in devices if d.device_id == binding.device_id), None)
            choice.name = named.name if named else "自有设备"
    effective_visibility: str | None = None
    if binding is not None:
        effective_visibility = binding.visibility.value
    return ok(
        {
            "current": current,
            "choice": choice.model_dump(),
            "project_default": configs.default.model_dump(),
            "favorites": [v.model_dump() for v in configs.favorites],
            # A machine id only has selection meaning under the self-hosted pool.
            # Cloud also records its connector in device_topic, but that endpoint is
            # an implementation detail of the freshly provisioned topic machine, not
            # a machine the person chose from a list.
            "device_id": (
                binding.device_id
                if current == COMPUTE_DEVICE and binding is not None
                else choice.device_id
            ),
            "devices": [
                {
                    "device_id": device.device_id,
                    "name": device.name,
                    "online": device_hub.is_online(device.device_id),
                }
                for device in devices
            ],
            "locked": bool(
                await AgentSessionService(db).has_run(topic_id)
                or await ProjectMachineRepository(db).get_active_for_topic(topic_id)
            ),
            "inherited": topic.compute_profile is None,
            "profiles": [
                asdict(v)
                for v in compute_listings(settings, device_online=device_online)
            ],
            "visibility": {
                "options": [asdict(v) for v in visibility_listings()],
                # "host" | "isolated" | null (platform compute / not yet pinned).
                "effective": effective_visibility,
                # The one boolean the room's badge keys on: this turn can see and
                # operate the whole machine.
                "machine_access": effective_visibility == VISIBILITY_HOST,
                # The honest #282 line, for the badge text / tooltip.
                "notice": MACHINE_VISIBILITY_NOTICE,
            },
        }
    )


@router.put("/{topic_id}/compute-profile")
async def set_topic_compute_profile(
    topic_id: uuid.UUID, body: dict, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """Change only this room before its first resource allocation or session."""
    from pydantic import ValidationError as SchemaError

    from app.domain.agent.compute_configs import (
        ComputeChoice,
        standard_choice,
        validate_choice,
    )
    from app.domain.machine.repositories import ProjectMachineRepository

    topic = await TopicService(db).get_or_404(topic_id)
    actor = await resolver.resolve(
        fallback_handle=None, topic_id=topic_id, project_id=topic.project_id
    )
    await resolver.authorize_topic(
        actor, project_id=topic.project_id, topic_id=topic_id
    )
    await ProjectMachineRepository(db).lock_topic(topic_id)
    if await AgentSessionService(db).has_run(
        topic_id
    ) or await ProjectMachineRepository(db).get_active_for_topic(topic_id):
        raise ValidationError("话题已开始，算力已锁定；新建话题可另选算力")
    name = (body.get("profile") or "").strip() or compute_default_name()
    try:
        choice = ComputeChoice.model_validate(
            body.get("choice")
            or {
                **standard_choice(name).model_dump(),
                "profile": name,
                "device_id": body.get("device_id"),
            }
        )
    except SchemaError as exc:
        raise ValidationError("算力配置无效，请检查名称、设备和资源规格") from exc
    name = choice.profile
    body = {**body, "device_id": choice.device_id}
    raw_device_id = body.get("device_id")
    if raw_device_id is not None and not isinstance(raw_device_id, str):
        raise ValidationError("device_id 必须是字符串")
    device_id = (raw_device_id or "").strip() or None
    if name != COMPUTE_DEVICE and device_id is not None:
        raise ValidationError("只有自托管设备可以指定 device_id")

    device_online = await project_device_online(db, topic.project_id)
    allowed = {v.id for v in compute_selectable(settings, device_online=device_online)}
    # A named self-hosted machine may deliberately be offline: the topic is pinned
    # now and waits for that exact box. The automatic option keeps the old rule and
    # is selectable only when at least one project-scoped device is online.
    if name not in allowed and not (name == COMPUTE_DEVICE and device_id is not None):
        raise ValidationError(f"算力池 {name!r} 尚未接入，暂不可选")
    if name == COMPUTE_CLOUD:
        await MachineService(db).require_use_authority(topic.project_id, actor)
    if body.get("choice"):
        await validate_choice(db, topic.project_id, choice)

    device_service = sql_device_service(db)
    if device_id is not None:
        scoped_devices = await device_service.list_devices_for_project(topic.project_id)
        if device_id not in {device.device_id for device in scoped_devices}:
            raise ValidationError("设备不属于当前项目")

    # A pre-turn choice has no worktree/session state yet, so it remains editable.
    # Release then bind preserves bind_topic_device's write-once contract: the bind
    # itself never overwrites, while an explicit user change before the lock removes
    # the obsolete choice first. Selecting Cloud or 「系统挑一台」 leaves no pin;
    # the latter is frozen by resolve_pinned_device on the first turn as before.
    binding = await device_service.topic_binding(topic_id)
    if binding is not None and (
        name != COMPUTE_DEVICE or binding.device_id != device_id
    ):
        await device_service.release_topic_device(
            topic_id, reason="compute choice changed before the first turn"
        )
        binding = None
    if name == COMPUTE_DEVICE and device_id is not None and binding is None:
        await device_service.bind_topic_device(
            topic_id, device_id, visibility=Visibility.host
        )

    topic.compute_profile = name
    topic.compute_config = choice.model_dump()
    await db.flush()
    return ok(
        {
            "current": name,
            "choice": choice.model_dump(),
            "device_id": device_id if name == COMPUTE_DEVICE else None,
            "locked": False,
            "inherited": False,
        }
    )


class ChatPublishIn(BaseModel):
    content: str = Field(min_length=1, max_length=100000)
    request_id: uuid.UUID
    reply_to: uuid.UUID | None = None


@router.post("/{topic_id}/messages", operation_id="chat-publish")
async def publish_chat_message(
    topic_id: uuid.UUID,
    body: ChatPublishIn,
    db: DbSession,
    resolver: ActorResolverDep,
    chat: Annotated[ChatService, Depends(get_chat_service)],
) -> dict:
    """Publish an agent-authored message without starting a model turn."""
    place = await TopicService(db).place_or_404(topic_id)
    actor = await resolver.resolve(
        fallback_handle=None, topic_id=place.room_id, project_id=place.project_id
    )
    if not actor.authenticated or not actor.is_agent:
        raise ForbiddenError("An authenticated agent must publish this message")
    await resolver.authorize_topic(
        actor, project_id=place.project_id, topic_id=place.room_id, enforce=True
    )
    content = body.content.strip()
    if not content:
        raise ValidationError("content must not be blank")
    if body.reply_to is not None:
        parent = await BlockRepository(db).get(body.reply_to)
        if (
            parent is None
            or parent.topic_id != place.room_id
            or parent.task_id is not None
        ):
            raise ValidationError("reply_to must belong to this conversation")
    content = await canonicalize_refs(
        db, place.project_id, content, exclude_topic_id=place.room_id
    )
    # The input request can finish while its terminal session is still working.
    runner = get_work_runner()
    work = runner.live_work_for_topic(place.room_id)
    turn_id = uuid.UUID(work["turn_id"]) if work is not None else None
    payload = await chat._persist_assistant_message(
        project_id=place.project_id,
        topic_id=place.room_id,
        text=content,
        turn_id=turn_id,
        reply_to=body.reply_to,
        roster=None,
        topic_refs=[],
        publish=True,
        author=actor.handle,
        publication_id=str(body.request_id),
    )
    await get_broker().publish(
        str(topic_id), {"type": "assistant_block", "block": payload}
    )
    if turn_id is not None:
        runner.note_session_output(turn_id, tool=False)
    return ok(payload)


@router.post("/{topic_id}/ask")
async def ask_options(
    topic_id: uuid.UUID, body: dict, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """芝士 asks an option question IN the chat (cheese ask): a message block
    whose meta.options renders as one-click buttons. Structured interaction —
    the answer comes back as data, never parsed from prose (spec §14.5).

    本轮停在这里等回答，所以它同时通知发起这一轮的人（#1084）：其余每一种「下一步
    在人手上」都是一轮结束之后的状态，唯独这一种**中断**运行，而房间安静下来这件事
    本身没有人会注意到。
    """
    place = await TopicService(db).place_or_404(topic_id)
    actor = await _actor_in_place(resolver, place)
    question = (body.get("question") or "").strip()
    options = [str(o).strip() for o in (body.get("options") or []) if str(o).strip()]
    if not question:
        raise ValidationError("question is required")
    if not 2 <= len(options) <= 4:
        raise ValidationError("需要 2-4 个选项")
    blk = await BlockRepository(db).add(
        project_id=place.project_id,
        # The place id: `add` splits it, so a thread's question is asked in the
        # thread rather than shouted into the room around it.
        topic_id=topic_id,
        author=(
            actor.handle
            if actor.authenticated
            else await TopicMemberService(db).resolve_agent_handle(
                topic_id, room_id=place.room_id
            )
        ),
        author_type=(
            AuthorType.human
            if actor.authenticated and not actor.is_agent
            else AuthorType.ai
        ),
        content=question,
        kind=BlockKind.message,
        meta={"options": options},
    )
    # 发起这一轮的人 —— 芝士是代他执行这件事的，这个问题也只有他能回答。平台发起
    # 的轮次（resume、各类提醒）作者是 system，那种提问指不到具体的人。
    waiting_for = await AgentTurnRepository(db).open_turn_author_for_topic(
        place.room_id
    )
    await notify_question(
        db,
        place=place,
        block_id=blk.id,
        question=question,
        asker=blk.author,
        recipients=() if waiting_for in (None, "system") else (waiting_for,),
    )
    await db.commit()
    payload = BlockOut.model_validate(blk).model_dump(mode="json")
    await get_broker().publish(
        str(topic_id), {"type": "assistant_block", "block": payload}
    )
    return ok(payload)


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
    if not await chat.has_unread_human_input(place.room_id):
        return ok({"started": False, "reason": "nothing_pending"})
    # content 在有待读消息时会被待读窗口取代（_converse_impl 的 backlog 分支），
    # 这里正是要那个结果：芝士收到的东西和「当时就 @ 了它」一模一样。这句只在
    # 待读窗口刚好被别人清空的缝隙里当兜底。
    runner.submit(
        chat,
        place.room_id,
        author="system",
        content="有人请你看一下房间里还没读到的消息，照常处理。",
        summon=True,
        nudge_event=f"<@{actor.handle}> 叫芝士来看前面的消息",
        provision_actor=actor,
    )
    return ok({"started": True})


@router.post("/blocks/{block_id}/answer")
async def answer_options(
    block_id: uuid.UUID,
    body: dict,
    db: DbSession,
    resolver: ActorResolverDep,
    chat: Annotated[ChatService, Depends(get_chat_service)],
    runner: Annotated[AgentWorkRunner, Depends(get_work_runner)],
) -> dict:
    """One-click answer to an option question: validates the choice against the
    ask block's own options, records it on the block (meta.answered), and posts
    the choice as the answerer's message with summon — 芝士 continues."""
    option = (body.get("option") or "").strip()
    repo = BlockRepository(db)
    blk = await repo.get(block_id)
    if blk is None:
        raise NotFoundError("问题不存在")
    actor = await resolver.resolve(
        fallback_handle=body.get("author"),
        topic_id=blk.topic_id,
        project_id=blk.project_id,
    )
    await resolver.authorize_topic(
        actor, project_id=blk.project_id, topic_id=blk.topic_id
    )
    author = actor.handle
    if author == "anonymous" or not option:
        raise ValidationError("author 和 option 都要有")
    meta = dict(blk.meta or {})
    options = meta.get("options") or []
    if option not in options:
        raise ValidationError("不在选项里")
    if meta.get("answered"):
        raise ValidationError(
            f"已由 {meta.get('answered_by')} 选过：{meta.get('answered')}"
        )
    meta["answered"] = option
    meta["answered_by"] = author
    blk.meta = meta
    await db.flush()
    updated = BlockOut.model_validate(blk).model_dump(mode="json")
    await db.commit()
    await get_broker().publish(
        str(blk.topic_id), {"type": "block_updated", "block": updated}
    )
    # The choice lands as the answerer's own message + summons 芝士 to continue.
    await get_broker().receive_message(
        chat,
        blk.topic_id,
        author=author,
        content=option,
        summon=True,
        provision_actor=actor,
    )
    return ok(updated)


@router.post("/{topic_id}/webhook-token")
async def mint_webhook_token(topic_id: uuid.UUID, db: DbSession) -> dict:
    """Mint (or rotate) this place's webhook credential — used by the `cheese`
    CLI to hand a caller a token for POST /webhooks/{topic_id}. Rotating
    invalidates every previously-minted token for this place; the raw value is
    returned once and never recoverable afterwards."""
    place = await TopicService(db).place_or_404(topic_id)
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
    """记录关键决策到决策记录 (spec §7.1) — used by the `cheese decision` CLI."""
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
        author_type=(
            AuthorType.human
            if actor.authenticated and not actor.is_agent
            else AuthorType.ai
        ),
        content=decision,
        kind=BlockKind.decision,
        refs=[str(topic_id)],
    )
    out = BlockOut.model_validate(block).model_dump(mode="json")
    if key is not None:
        await idem.record_result(db, key, out)
    return ok(out)


@router.post("/{topic_id}/title")
async def set_title(
    topic_id: uuid.UUID, body: dict, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """给这个地方起/改标题 — used by both `cheese title` (AI-generated, naming an
    untitled place) and the frontend sidebar rename UI (dual-use, like doc/split).

    Names the THREAD when the id is a thread's. Resolving only rooms did not
    fail here, which is what made it dangerous: a 分身 naming the piece of work
    it had just been handed would have renamed the whole room around it.
    """
    place = await TopicService(db).place_or_404(topic_id)
    actor = await resolver.resolve(
        fallback_handle=body.get("by"),
        topic_id=place.room_id,
        project_id=place.project_id,
    )
    await resolver.authorize_topic(
        actor, project_id=place.project_id, topic_id=place.room_id
    )
    title = (body.get("title") or "").strip()
    if not title:
        raise ValidationError("title 不能为空")
    place.room.title = title[:80]
    await db.flush()
    return ok(TopicOut.model_validate(place.room).model_dump(mode="json"))


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
    spawns one inside its own session and reports the id back with
    `/tasks/{id}/bind`. The platform used to raise a whole second container per
    piece of work — its own screen, its own home, its own clone of the
    repository — to run something that is a second worker in a session the room
    already has.

    So a card returned from here has no worker yet, and that is a normal state
    rather than a half-finished dispatch: 认领 is a separate call because the id
    it carries does not exist until the worker does."""
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
        # answer exists. None whenever no person is identifiable (an autonomous
        # 分身, a platform-initiated turn), and the ladder in the service then
        # behaves exactly as it did before.
        triggered_by=runner.turn_author_for(topic_id),
    )
    out = TaskOut.model_validate(task).model_dump(mode="json")
    if key is not None:
        await idem.record_result(db, key, out)
    # The thread and its idempotency key commit together, so a crash here cannot
    # produce a second thread on resume — and the caller must see the row and
    # its brief doc before it can bind a worker to them.
    await db.commit()
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
    (`cheese tell`). See `app.domain.topic.relay` for why the comments endpoint
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


# How long ``cheese serve`` may wait for the helper it just started to finish its
# upgrade. It declares the preview in the same breath as starting the tunnel, so
# without this the platform would refuse a preview that is one round trip away.
_PREVIEW_ATTACH_WAIT_S = 8.0


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
    TaskService._bind_workspace(work)


def _source_bytes(
    project_id: uuid.UUID, room_id: uuid.UUID, path: str, task: uuid.UUID | None
) -> bytes:
    """One of this room's files, from whichever store holds it.

    A room keeps what it delivered outside git; a card keeps what it is still
    writing, on its own branch. Both are 「这个房间的文件」 to a reader, so the
    viewers take the source as a parameter instead of each being wired to one
    store — that wiring is why a document on a branch had no view but a raw
    binary diff.
    """
    if task is not None:
        return ws.read_file_bytes(project_id, path, topic_id=task)
    return ws.read_room_file(project_id, room_id, path)


async def _reject_unreachable_app(topic_id: uuid.UUID) -> None:
    """Refuse an app artifact the platform provably cannot render (``cheese serve``).

    Setting it used to always succeed, so 芝士 announced 「预览已就绪」 while the
    panel showed 「应用暂时不在线」. Two separate things can be missing and they
    read differently to whoever has to fix them: the tunnel (nothing on that
    machine is carrying a preview out) and the app behind it (the tunnel is up and
    the declared port answers nothing).
    """
    if not await preview_hub.wait_online(topic_id, _PREVIEW_ATTACH_WAIT_S):
        raise ValidationError(
            "这台机器还没有把预览通道拨出来，预览到不了运行中的应用。"
            "用 cheese serve <端口> 登记（它会把通道带起来）；"
            "要给人看结果也可以用 cheese_artifact 点名一个文件——网页、图片，"
            "或报告、表格这类文档。"
        )
    if not await preview_hub.probe(topic_id):
        raise ValidationError(
            "登记的端口上没有服务在应答，预览会是一个白框。"
            "先把应用起在 127.0.0.1 上、确认能访问，再登记这个端口。"
        )


@router.post("/{topic_id}/artifact")
async def set_artifact(
    topic_id: uuid.UUID,
    body: dict,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """芝士 marks a worktree file as a renderable artifact (spec §9.1) — used by
    `cheese artifact`. With no anchor it becomes this place's current preview."""
    place = await TopicService(db).place_or_404(topic_id)
    await _actor_in_place(resolver, place)
    declared = (body.get("as") or "").strip().lower()
    if declared == "app":
        # An app artifact points at the running server, not a file — the stored
        # content is a human note ("Vue dev server"), not a path.
        path = (body.get("path") or "app").strip()[:120]
        await _reject_unreachable_app(topic_id)
    else:
        path = _clean_artifact_path(body.get("path") or "")
    as_ = declared or artifact_kind_for(path)
    mime = _ARTIFACT_MIME.get(as_)
    if mime is None:
        allowed = "、".join(_ARTIFACT_MIME)
        raise ValidationError(f"暂不支持的类型 {as_!r}（可选：{allowed}）")
    if as_ != "app" and ("content" in body or "content_b64" in body):
        # A remote machine's file is not in the backend worktree until published.
        # Office files and PDFs are not text, so they travel base64-encoded; a
        # caller that sends them as `content` would either fail to read them or
        # corrupt them on the way, which is why the two fields are separate
        # rather than one field that guesses.
        if "content_b64" in body:
            encoded = body["content_b64"]
            if not isinstance(encoded, str):
                raise ValidationError("content_b64 必须是文本")
            try:
                raw = base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error) as exc:
                raise ValidationError("content_b64 不是合法的 base64") from exc
        else:
            content = body["content"]
            if not isinstance(content, str):
                raise ValidationError("content 必须是文本")
            raw = content.encode()
        if len(raw) > MAX_ARTIFACT_BYTES:
            raise ValidationError(
                f"产物太大（上限 {MAX_ARTIFACT_BYTES // (1024 * 1024)}MB）"
            )
        ws.write_room_file(place.project_id, topic_id, path, raw)
    block = await BlockRepository(db).add(
        project_id=place.project_id,
        topic_id=topic_id,  # the place; `add` splits it
        author=await TopicMemberService(db).resolve_agent_handle(
            topic_id, room_id=place.room_id
        ),
        author_type=AuthorType.ai,
        content=path,
        kind=BlockKind.artifact,
        mime_type=mime,
        refs=[path],
    )
    return ok(BlockOut.model_validate(block).model_dump(mode="json"))


@router.post("/{topic_id}/documents/recalc")
async def recalc_spreadsheet(
    topic_id: uuid.UUID,
    body: dict,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """Recompute a workbook's formulas — used by `cheese recalc`.

    The room cannot do this itself: recomputing means loading the workbook in
    something that evaluates formulas, and the sandbox image carries no
    LibreOffice and has no root to install one. The platform already runs one
    for previews, so this is the path to it.

    The workbook travels in the body rather than being read from the worktree,
    because a room on a remote machine has no file here — the same reason
    `artifact` takes `content_b64`.
    """
    place = await TopicService(db).place_or_404(topic_id)
    await _actor_in_place(resolver, place)
    path = _clean_artifact_path(body.get("path") or "")
    raw = _document_bytes(body, place.project_id, topic_id, path)
    try:
        book, bad = await recalculate(raw, path, settings.office_render_endpoint)
    except SpreadsheetRecalcUnavailable as exc:
        raise SystemBusyError(str(exc)) from exc
    except SpreadsheetRecalcFailed as exc:
        raise ValidationError(str(exc)) from exc
    return ok(
        {
            "path": path,
            "content_b64": base64.b64encode(book).decode(),
            "errors": [cell.as_dict() for cell in bad],
        }
    )


@router.post("/{topic_id}/documents/convert")
async def convert_document(
    topic_id: uuid.UUID,
    body: dict,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """Convert one document to another format — used by `cheese convert`.

    The pre-2007 binary formats are the reason this exists: a room cannot read
    or write them at all, so the alternative is asking the user to open Office
    himself. It also gets a room a PDF of a Word file, which is how it looks at
    its own layout before delivering it.
    """
    place = await TopicService(db).place_or_404(topic_id)
    await _actor_in_place(resolver, place)
    path = _clean_artifact_path(body.get("path") or "")
    target = str(body.get("to") or "").strip()
    raw = _document_bytes(body, place.project_id, topic_id, path)
    try:
        made = await convert(raw, path, target, settings.office_render_endpoint)
    except ConvertUnavailable as exc:
        raise SystemBusyError(str(exc)) from exc
    except ConvertFailed as exc:
        raise ValidationError(str(exc)) from exc
    return ok(
        {
            "path": upgraded_name(path, target.lower().lstrip(".")),
            "content_b64": base64.b64encode(made).decode(),
        }
    )


@router.get("/{topic_id}/documents/revisions")
async def list_document_revisions(
    topic_id: uuid.UUID,
    path: str,
    db: DbSession,
    resolver: ActorResolverDep,
    task: uuid.UUID | None = None,
) -> dict:
    """The tracked changes in a `.docx`, one row per decision a reader makes.

    The preview beside this list already draws the changes — LibreOffice renders
    insertions and deletions, measured — so the list is not there to show them.
    It is there to act on them: a reader can accept or reject one without
    opening Word.
    """
    topic = await TopicService(db).get_or_404(topic_id)
    actor = await resolver.resolve(
        fallback_handle=None, topic_id=topic_id, project_id=topic.project_id
    )
    await resolver.authorize_topic(
        actor, project_id=topic.project_id, topic_id=topic_id
    )
    clean = _clean_artifact_path(path)
    if task is not None:
        await _bind_source_task(db, topic_id, task)
    raw = _source_bytes(topic.project_id, topic_id, clean, task)
    try:
        found = revisions_in(raw, clean)
    except RevisionsUnsupported as exc:
        raise ValidationError(str(exc)) from exc
    except RevisionsFailed as exc:
        raise ValidationError(str(exc)) from exc
    return ok(
        {
            "path": clean,
            "version": content_version(raw),
            "revisions": [r.as_dict() for r in found],
        }
    )


@router.post("/{topic_id}/documents/revisions")
async def decide_document_revisions(
    topic_id: uuid.UUID,
    body: dict,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """Accept or reject tracked changes, and write the document back.

    Accepting an insertion removes its wrapper and keeps the text; accepting a
    deletion removes the text with it; rejecting does the opposite. All of it is
    a determinate transformation of the XML, so the file the reader downloads
    afterwards is the file Word would have produced.

    ``version`` is the one the list was read at. A room re-publishing the
    artifact between that read and this write would otherwise lose its newer
    copy to a decision taken against the older one, so a moved file is a
    conflict here rather than an overwrite.
    """
    topic = await TopicService(db).get_or_404(topic_id)
    actor = await resolver.resolve(
        fallback_handle=None, topic_id=topic_id, project_id=topic.project_id
    )
    await resolver.authorize_topic(
        actor, project_id=topic.project_id, topic_id=topic_id
    )
    clean = _clean_artifact_path(body.get("path") or "")
    accept = _row_numbers(body.get("accept"), "accept")
    reject = _row_numbers(body.get("reject"), "reject")
    expected = str(body.get("version") or "")
    if not expected:
        raise ValidationError("缺少 version：要处理的是哪一版清单")
    source = body.get("task")
    task = uuid.UUID(str(source)) if source else None
    if task is not None:
        await _bind_source_task(db, topic_id, task)
    raw = _source_bytes(topic.project_id, topic_id, clean, task)
    actual = content_version(raw)
    if actual != expected:
        raise ConflictError(
            "文件已被改动（芝士或其他人写过），这份清单是基于旧内容的",
            data={"path": clean, "version": actual},
        )
    try:
        made, left = decide(raw, clean, accept=accept, reject=reject)
    except RevisionsUnsupported as exc:
        raise ValidationError(str(exc)) from exc
    except RevisionsFailed as exc:
        raise ValidationError(str(exc)) from exc
    if task is not None:
        ws.write_file_bytes(topic.project_id, clean, made, topic_id=task)
    else:
        ws.write_room_file(topic.project_id, topic_id, clean, made)
    return ok(
        {
            "path": clean,
            "version": content_version(made),
            "revisions": [r.as_dict() for r in left],
        }
    )


def _row_numbers(raw, field: str) -> list[int]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValidationError(f"{field} 要是一个序号数组")
    out: list[int] = []
    for item in raw:
        if not isinstance(item, int) or isinstance(item, bool) or item < 1:
            raise ValidationError(f"{field} 里的序号要是从 1 起的整数")
        out.append(item)
    return out


def _document_bytes(
    body: dict, project_id: uuid.UUID, topic_id: uuid.UUID, path: str
) -> bytes:
    """The document a request is about, from the body or from the workspace.

    A room on a remote machine has no file here, so it sends the bytes; the
    panel is reading a file the platform already holds. Same reason
    `artifact` takes `content_b64`.
    """
    encoded = body.get("content_b64")
    if isinstance(encoded, str):
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValidationError("content_b64 不是合法的 base64") from exc
    else:
        raw = ws.read_room_file(project_id, topic_id, path)
    if len(raw) > MAX_ARTIFACT_BYTES:
        raise ValidationError(
            f"文件超过 {MAX_ARTIFACT_BYTES // (1024 * 1024)}MB，处理不了"
        )
    return raw


@router.get("/{topic_id}/preview")
async def get_preview(
    topic_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """This room's current preview (spec §7.1): the artifact 芝士 last pointed at,
    as {path, mime}. Null when none is set — the client may fall back to scanning
    the worktree. Content is fetched separately via the guarded file reader."""
    place = await TopicService(db).place_or_404(topic_id)
    await _actor_in_place(resolver, place)
    art = await BlockRepository(db).latest_artifact(place.room_id)
    if art is None:
        return ok(None)
    from app.api.preview_host import preview_origin

    if art.mime_type == _ARTIFACT_MIME["app"]:
        # Knocked on LIVE, through the tunnel, every time the panel asks. A
        # declared preview is not a running one: the agent's dev server exits,
        # the machine goes offline, the helper's token ages out — and each of
        # those renders as a white iframe unless the two states are reported
        # apart. `tunnel_up` without a `url` is 「通道在，应用没在跑」.
        tunnel_up = preview_hub.is_online(topic_id)
        alive = tunnel_up and await preview_hub.probe(topic_id)
        return ok(
            {
                "kind": "app",
                "path": art.content,
                "mime": art.mime_type,
                # Every executable preview stays outside the platform origin.
                "url": (preview_origin(topic_id) + "/" if alive else None),
                "tunnel_up": tunnel_up,
                "artifact_id": str(art.id),
            }
        )
    return ok(
        {
            "kind": "file",
            "url": preview_origin(topic_id) + "/",
            "version": await asyncio.to_thread(
                ws.preview_file_version, place.project_id, topic_id, art.content
            ),
            "path": art.content,
            "mime": art.mime_type,
            # Which artifact this is, so a client can tell "芝士 pointed at
            # something new" from "the same preview, re-fetched" — re-pointing at
            # the same path is a new preview too, so the path cannot carry this.
            "artifact_id": str(art.id),
        }
    )


@router.get("/{topic_id}/preview/file")
async def preview_file(
    topic_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    path: str | None = None,
) -> dict:
    """The file the preview is showing: the room's current artifact, or `path`.

    A `<&path>` chip in a message names a file without saying which store holds
    it, and a room's own files are here rather than on a branch. Reading one by
    path is how a reader gets from that chip to the file, instead of to a
    listing that does not contain it.
    """
    place = await TopicService(db).place_or_404(topic_id)
    await _actor_in_place(resolver, place)
    if path:
        return ok(
            ws.read_room_text_file(
                place.project_id, topic_id, _clean_artifact_path(path)
            )
        )
    art = await BlockRepository(db).latest_artifact(place.room_id)
    if art is None or art.mime_type == _ARTIFACT_MIME["app"]:
        raise NotFoundError("No file preview")
    return ok(ws.read_room_text_file(place.project_id, topic_id, art.content))


# ---- Chat attachments -----------------------------------------------------
# An attachment is a REAL file in the topic's worktree (所有产出都是 git): the
# upload writes bytes under uploads/, the message references it as an
# attachment block, and 芝士 sees it by Read-ing the file in its sandbox.

# Only these image types may render inline; other files require download.
_IMAGE_MIME_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
}
_EXT_IMAGE_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024


@router.post("/{topic_id}/attachments")
async def upload_attachment(
    topic_id: uuid.UUID, file: UploadFile, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """Upload a file into the topic's worktree (uploads/…). Returns the
    {path, mime} the client then references when sending the message."""
    topic = await TopicService(db).get_or_404(topic_id)
    await resolver.require_verified_caller(
        project_id=topic.project_id, topic_id=topic_id
    )
    actor = await resolver.resolve(
        fallback_handle=None, topic_id=topic_id, project_id=topic.project_id
    )
    await resolver.authorize_topic(
        actor, project_id=topic.project_id, topic_id=topic_id
    )
    mime = (
        (file.content_type or "application/octet-stream").split(";")[0].strip().lower()
    )
    ext = _IMAGE_MIME_EXT.get(mime)
    if mime.startswith("image/") and ext is None:
        mime = "application/octet-stream"
    data = await file.read(MAX_ATTACHMENT_BYTES + 1)
    if not data:
        raise ValidationError("空文件")
    if len(data) > MAX_ATTACHMENT_BYTES:
        raise ValidationError("文件太大（上限 10MB）")
    # Preserve the basename; a unique directory prevents overwrites.
    name = (file.filename or "file").replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r"[\x00-\x1f\x7f]", "_", name).strip().strip(".") or "file"
    name = name.encode("utf-8")[:180].decode("utf-8", errors="ignore")
    if ext and not name.lower().endswith(ext):
        name += ext
    path = f"uploads/{uuid.uuid4().hex}/{name}"
    ws.write_room_file(topic.project_id, topic_id, path, data)
    return ok({"path": path, "mime": mime, "bytes": len(data)})


@router.get("/{topic_id}/attachments/raw")
async def attachment_raw(
    topic_id: uuid.UUID,
    path: str,
    db: DbSession,
    resolver: ActorResolverDep,
    download: bool = False,
    task: uuid.UUID | None = None,
) -> Response:
    """Raw bytes of an image attachment, for <img src=…>. Extension-whitelisted
    to images so this can never serve executable HTML from the worktree."""
    topic = await TopicService(db).get_or_404(topic_id)
    if download:
        await resolver.require_verified_caller(
            project_id=topic.project_id, topic_id=topic_id
        )
    actor = await resolver.resolve(
        fallback_handle=None, topic_id=topic_id, project_id=topic.project_id
    )
    await resolver.authorize_topic(
        actor, project_id=topic.project_id, topic_id=topic_id
    )
    clean = _clean_artifact_path(path)
    suffix = "." + clean.rsplit(".", 1)[-1].lower() if "." in clean else ""
    mime = _EXT_IMAGE_MIME.get(suffix)
    if mime is None and not download:
        raise ValidationError("只能读取图片附件")
    if task is not None:
        await _bind_source_task(db, topic_id, task)
    data = _source_bytes(topic.project_id, topic_id, clean, task)
    filename = quote(clean.rsplit("/", 1)[-1], safe="")
    return Response(
        content=data,
        media_type="application/octet-stream" if download else mime,
        headers={
            "Content-Disposition": (
                f"attachment; filename*=UTF-8''{filename}" if download else "inline"
            ),
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "Cache-Control": "private, max-age=3600",
        },
    )


@router.get("/{topic_id}/attachments/pdf")
async def attachment_as_pdf(
    topic_id: uuid.UUID,
    path: str,
    db: DbSession,
    resolver: ActorResolverDep,
    task: uuid.UUID | None = None,
) -> Response:
    """A Word or PowerPoint deliverable, converted so a browser can show it.

    Browsers draw PDF and nothing else in this family, so this is what stands
    between "看得见的成果" and a download button on a tab labelled 预览.

    Spreadsheets are not here on purpose: paginating a sheet breaks the columns
    apart and throws away the cell addresses, which are the only thing anyone can
    point at afterwards. Those are drawn from the original bytes instead.
    """
    topic = await TopicService(db).get_or_404(topic_id)
    actor = await resolver.resolve(
        fallback_handle=None, topic_id=topic_id, project_id=topic.project_id
    )
    await resolver.authorize_topic(
        actor, project_id=topic.project_id, topic_id=topic_id
    )
    clean = _clean_artifact_path(path)
    if not is_renderable(clean):
        raise ValidationError("这个格式不能转换为预览")
    if task is not None:
        await _bind_source_task(db, topic_id, task)
    data = _source_bytes(topic.project_id, topic_id, clean, task)
    if len(data) > MAX_ARTIFACT_BYTES:
        raise ValidationError(
            f"文件超过 {MAX_ARTIFACT_BYTES // (1024 * 1024)}MB，无法生成预览"
        )
    try:
        pdf = await render_to_pdf(data, clean, settings.office_render_endpoint)
    except OfficeRenderUnavailable as exc:
        # 503 (SystemBusyError is this codebase's 503), not 500: the renderer is
        # absent or unreachable, which the panel reports as its own state and
        # pairs with the download — a different sentence from "这个文件转换不了",
        # which is about the file and will not improve on a retry.
        raise SystemBusyError(str(exc)) from exc
    except OfficeRenderFailed as exc:
        raise ValidationError(str(exc)) from exc
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": "inline",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "Cache-Control": "private, max-age=3600",
        },
    )


# Per-project unread map lives under /api/projects (a "/unread" path under
# /api/topics would be shadowed by the /{topic_id} route). Separate router.
project_router = APIRouter(prefix="/projects", tags=["topics"])


@project_router.get("/{project_id}/topic-unread")
async def project_topic_unread(
    project_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    handle: str | None = None,
) -> dict:
    """话题级未读数 (Feishu-style badges): {topic_id: unread_count} for the
    calling user, one query. Topics with zero unread are omitted.

    Read-state is per-person, so the recipient comes from the verified
    credential (``handle`` is only checked against it) — a caller without one
    used to read anybody's badge map by naming them here."""
    recipient = await resolver.resolve_recipient(
        requested=handle, project_id=project_id, allow_anonymous=False
    )
    counts = await TopicService(db).unread_counts(project_id, recipient)
    return ok({str(topic_id): count for topic_id, count in counts.items()})


@project_router.get("/{project_id}/private-unread")
async def project_private_unread(
    project_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    handle: str | None = None,
) -> dict:
    """私聊未读数: {peer: unread_count} for the calling user, one query.

    Keyed by the other party rather than by topic id — private chats are not in
    the topic tree, so the roster page renders their rows from the member list
    and has no topic id to look one up with. A person is their handle; an AI
    teammate is `agent:<handle>`, which is also the word the DM's URL uses.
    Peers with zero unread are omitted.

    Same rule as ``topic-unread``: the recipient comes from the verified
    credential, never from the query string. It matters more here — this map
    names who a person is talking to privately, so honouring a caller-supplied
    handle would leak the shape of everyone's DMs."""
    recipient = await resolver.resolve_recipient(
        requested=handle, project_id=project_id, allow_anonymous=False
    )
    counts = await TopicService(db).private_unread_counts(project_id, recipient)
    return ok(counts)


# Block upgrade lives here (it produces a topic). Separate router prefix.
block_router = APIRouter(prefix="/blocks", tags=["topics"])


@block_router.post("/{block_id}/upgrade")
async def upgrade_block(
    block_id: uuid.UUID,
    body: UpgradeBlockIn,
    db: DbSession,
    chat: Annotated[ChatService, Depends(get_chat_service)],
) -> dict:
    """讨论升级：upgrade a block into a place of its own (eval A1).

    Same mechanics as /split: the upgraded block is preset as the new place's
    task-brief doc, and it starts untitled.

    A message in a room becomes a CARD of work in that room; a message in a
    private chat becomes a room, because private chats are not in the topic tree
    and a card there would be one nobody else could open. The response says
    which by carrying either a task or a topic.

    Who gets woken follows from that split. A room has a session of its own, so
    it kicks itself off. A card does NOT — the 分身 doing it lives in the room's
    own session. So the ROOM is woken, and it is told to name the card, raise
    the worker and bind it.
    """
    room, thread, created = await TopicService(db).upgrade_block_to_place(
        block_id=block_id,
        created_by=body.created_by,
        reviewer_handle=body.reviewer_handle,
    )
    out = (
        TaskOut.model_validate(thread).model_dump(mode="json")
        if thread is not None
        else TopicOut.model_validate(room).model_dump(mode="json")
    )
    # 升级的那段话 IS the brief — read it before the commit expires the instance,
    # the same way the returned card reads its id before waking the room.
    source_message = (await BlockRepository(db).get(block_id)) if created else None
    upgraded_text = "" if source_message is None else source_message.content
    # Commit BEFORE waking (the woken turn runs on its own session); an
    # idempotent re-upgrade (created=False) must not wake anyone again.
    await db.commit()
    if created:
        if thread is not None:
            get_work_runner().submit_kickoff(
                chat,
                room.id,
                prompt=thread_upgraded_prompt(
                    task_id=thread.id, source_message=upgraded_text
                ),
            )
        else:
            get_work_runner().submit_kickoff(chat, room.id)
    return ok(out)
