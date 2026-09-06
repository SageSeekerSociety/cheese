"""Topic routes."""

import shutil
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import proxy
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
from app.core.errors import NotFoundError, ValidationError
from app.core.sandbox_auth import mint_scoped_token
from app.domain.agent import awaited_tasks
from app.domain.agent.chat import (
    ChatService,
    conclusion_digest_prompt,
    thread_upgraded_prompt,
)
from app.domain.agent.device_hub import device_hub
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
from app.domain.agent.runtime import AgentWorkRunner
from app.domain.agent_instance.schemas import TopicAgentIn
from app.domain.agent_instance.services import ResolvedAgent
from app.domain.agent_session.services import AgentSessionService
from app.domain.block.models import AuthorType, Block, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.block.schemas import BlockOut
from app.domain.conclusion.repositories import ConclusionCardRepository
from app.domain.device.supply import Visibility
from app.domain.device.wiring import sql_device_service
from app.domain.idempotency import store as idem
from app.domain.idempotency.keys import action_key
from app.domain.identity.actor import Actor
from app.domain.machine.services import MachineService
from app.domain.mentions import canonicalize_refs
from app.domain.project.repositories import ProjectRepository
from app.domain.review import archive
from app.domain.review.models import AcceptCard
from app.domain.review.repositories import AcceptCardRepository
from app.domain.room_task import presentation
from app.domain.room_task.models import LockKind, Task
from app.domain.room_task.place import Place
from app.domain.room_task.repositories import TaskRepository
from app.domain.room_task.schemas import TaskOut
from app.domain.room_task.services import (
    ClaimService,
    RoomLockService,
    TaskService,
    WorkTreeService,
)
from app.domain.team.repositories import TeamRepository
from app.domain.topic.doc_change import summarize_doc_change
from app.domain.topic.models import Topic, TopicStatus
from app.domain.topic.relay import TopicRelayService, deliver_or_wake
from app.domain.topic.repositories import SortOrder, TopicSortField
from app.domain.topic.schemas import (
    BackgroundTaskDoneIn,
    BackgroundTaskIn,
    BindSubagentIn,
    CheckResultIn,
    ClaimIn,
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

router = APIRouter(prefix="/topics", tags=["topics"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


async def _actor_in_place(resolver: ActorResolver, place: Place) -> Actor:
    """Who is calling here, and whether they may be.

    Two questions, two DIFFERENT ids, and for a thread they are not the same id:

    - identity/scope is checked against the PLACE a per-turn token was minted
      for — a thread's agent holds a token naming the thread;
    - access is the ROOM's roster, which is the only roster there is.

    They are both uuids, so passing one where the other belongs raises nothing
    and reads fine. It is only wrong for a thread, and then it is wrong in the
    one direction nobody tests: the thread's own 分身 gets 403 from its own
    living doc, its own timeline, and `cheese conclude` — the token names the
    thread, the check compared it to the room. Measured, not inferred. Pairing
    them here is the only way the two cannot drift apart again.
    """
    actor = await resolver.resolve(
        fallback_handle=None, topic_id=place.id, project_id=place.project_id
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
        agent_instance_id=body.agent_instance_id,
    )
    return ok(TopicOut.model_validate(topic).model_dump(mode="json"))


@router.get("/{topic_id}/agent")
async def get_topic_agent(
    topic_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """Which agent works in this topic, and whether that was chosen here.

    ``inherited`` is what a settings screen needs to render honestly: a topic
    that never picked one is not "using 芝士", it is *following the project*, and
    changing the project's default will move it.

    Answers for a thread as readily as for a room — asking who is doing a piece
    of work is the same question, and the work is what has an id to ask about.
    """
    service = TopicService(db)
    place = await service.place_or_404(topic_id)
    await _actor_in_place(resolver, place)
    # The thread's own row when there is one: it was handed the room's agent
    # when the work went out, so reading the room here would be right by
    # accident and wrong the moment the two differ.
    owner = place.task if place.task is not None else place.room
    agent = await service.resolve_agent(owner)
    return ok(_topic_agent_payload(owner, agent))


@router.put("/{topic_id}/agent")
async def set_topic_agent(
    topic_id: uuid.UUID, body: TopicAgentIn, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """Hand this topic to a different agent (``instance_id: null`` = back to the
    project's default).

    Costs nothing and warns about nothing: each agent's conversation here is its
    own row, so the incoming one starts fresh and the outgoing one's thread is
    still there if the topic is handed back.
    """
    service = TopicService(db)
    topic = await service.get_or_404(topic_id)
    actor = await resolver.resolve(
        fallback_handle=None, topic_id=topic_id, project_id=topic.project_id
    )
    await resolver.authorize_topic(
        actor, project_id=topic.project_id, topic_id=topic_id
    )
    topic, agent = await service.set_agent(topic_id, body.instance_id)
    await db.commit()
    return ok(_topic_agent_payload(topic, agent))


def _topic_agent_payload(topic: Topic | Task, agent: ResolvedAgent) -> dict:
    return {
        "topic_id": str(topic.id),
        "instance_id": str(agent.instance_id) if agent.instance_id else None,
        "handle": agent.handle,
        "type_name": agent.type_name,
        "display_name": agent.display_name,
        "inherited": topic.agent_instance_id is None,
    }


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
    # Assign before dumping so the instant is serialized by the same schema as
    # created_at/updated_at — a hand-rolled isoformat() here rendered "+00:00"
    # where every other timestamp in the payload says "Z".
    out.last_activity_at = last_activity.get(topic.id)
    mine = (relevance or {}).get(topic.id, TopicRelevance())
    out.i_participate = mine.i_participate
    out.awaits_me = mine.awaits_me
    data = out.model_dump(mode="json")
    data["running"] = topic.id in running_ids
    facts = presentation.facts_for_room(topic, running_ids, (cards or {}).get(topic.id))
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
    topics, total = await service.list_for_project(
        project_id, sort=sort, order=order, active_since=active_since
    )
    running_ids = runner.running_topic_ids()
    last_activity = await service.last_activity_for_topics([t.id for t in topics])
    relevance = await service.relevance_for_topics(topics, _viewer(actor))
    cards = await _live_room_cards(db, [t.id for t in topics])
    # 一次，给整页用同一个「现在几点」——见 list_project_tasks 里同一行的理由。
    now = datetime.now(UTC)
    items = [
        _topic_out(t, running_ids, last_activity, relevance, cards, now) for t in topics
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
    """One place's header — a room's, or one thread's.

    Answers for either, because one id is how the whole platform addresses a
    place and a caller holding one has no way to know which kind it got. A
    thread comes back as a task (`room_id`, `owner_handle`, `status`), a room as
    a topic; the shapes differ because the things differ, and pretending a
    thread has a roster or an archive state would be worse than saying so.

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
    if place.task is not None:
        # 一条活的头也要带上看板那一格，和它在列表里显示的是同一句话——同一个函数
        # 算的，所以深链接进来和从侧栏点进来不可能给出两种说法。
        cards = await AcceptCardRepository(db).latest_by_task([place.task.id])
        beats = await TaskRepository(db).last_block_at_for_tasks([place.task.id])
        pending = await ConclusionCardRepository(db).live_task_ids([place.task.id])
        out = TaskOut.model_validate(place.task).model_dump(mode="json")
        out["presentation"] = presentation.task_presentation(
            presentation.facts_for_task(
                place.task,
                cards.get(place.task.id),
                beats.get(place.task.id),
                # 做这条活的分身住在房间的会话里 —— 屏幕没了它就没了，而它不会来
                # 说一声。这一位是内存里的当下事实，不是库里的一列。
                room_screen_live=chat.has_live_screen(place.room_id),
                conclusion_pending=place.task.id in pending,
            ),
            now=datetime.now(UTC),
        ).as_dict()
        return ok(out)
    last_activity = await service.last_activity_for_topics([topic.id])
    relevance = await service.relevance_for_topics([topic], _viewer(actor))
    cards = await _live_room_cards(db, [topic.id])
    return ok(
        _topic_out(topic, runner.running_topic_ids(), last_activity, relevance, cards)
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
    # The PLACE: this id may name a thread, and a thread's timeline is its own.
    # Authorization is on the ROOM either way — a thread has no roster of its
    # own, which is the whole difference between it and a room.
    place = await TopicService(db).place_or_404(topic_id)
    await _actor_in_place(resolver, place)
    repo = BlockRepository(db)
    cursor: Block | None = None
    if before is not None:
        cursor = await repo.get(before)
        # An unknown cursor must not silently degrade into "newest N" — that
        # would hand the caller a duplicate page it can't distinguish. A cursor
        # from a different THREAD is just as wrong as one from another room.
        if (
            cursor is None
            or cursor.topic_id != place.room_id
            or cursor.task_id != place.task_id
        ):
            raise NotFoundError("游标消息不存在")
    if limit is None:
        blocks = await repo.list_for_topic(place.room_id, task_id=place.task_id)
        if cursor is not None:
            blocks = [
                b
                for b in blocks
                if (b.created_at, b.id) < (cursor.created_at, cursor.id)
            ]
        has_more = False
    else:
        page_result = await repo.page_for_topic(
            place.room_id, task_id=place.task_id, limit=limit, before=cursor
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
    pending = await ConclusionCardRepository(db).live_task_ids(thread_ids)
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
                        conclusion_pending=task.id in pending,
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

    ONLY the room may call it. A thread's token names the thread, so the scope
    check below refuses one anyway; the explicit refusal is here because "the
    caller is the room" is a rule worth failing loudly on rather than by a
    coincidence of ids.
    """
    place = await TopicService(db).place_or_404(topic_id)
    if place.is_thread:
        raise ValidationError("认领分身是房间的事，一条活自己认领不了")
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

    `/{topic_id}/title` already names a thread when the id IS the thread's, and
    that is the path a person takes from the sidebar. 芝士 cannot take it: its
    per-turn token names the room, so addressing a thread's id with it is a
    cross-place call and gets a 403 — correctly, since the token was minted for
    one place. So the room names its thread through the room, like 认领 and
    结论回流.

    Naming is the room's now because nothing else is left to do it: a thread
    dispatched by /split is named by whoever dispatched it, but one upgraded
    out of a message starts untitled, and the session that used to name itself
    on its first turn is exactly what 任务=分身 removed.
    """
    place = await TopicService(db).place_or_404(topic_id)
    if place.is_thread:
        raise ValidationError("这是房间给它的活起名字，一条活自己起不了")
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


@router.post("/{topic_id}/tasks/{task_id}/conclude")
async def conclude_task(
    topic_id: uuid.UUID,
    task_id: uuid.UUID,
    body: ConclusionIn,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """结论回流, said by the ROOM about one of its threads.

    A worker inside the room's session has no token and no place of its own to
    call `/return-conclusion` from, so the room files the conclusion for it —
    after it has read what came back and satisfied itself the work is done.

    Deliberately NOT automatic on the worker's Stop. A worker reports finished
    more than once (parking a long command counts as finishing), and Stops
    arrive from workers the platform never bound — measured on 2.1.224: after
    the session's own Stop, with an unknown id, an empty type and a fragment of
    a prompt as their closing message. Filing a conclusion off either of those
    would open a card for work that is not done, or for work nobody dispatched.

    No wake, unlike `/return-conclusion`: the room is the caller and is already
    running the turn that would be woken. The card still has its own deadline,
    so nothing waits on a turn that never comes.
    """
    service = TopicService(db)
    place = await service.place_or_404(topic_id)
    if place.is_thread:
        raise ValidationError("这是房间替它的活回流结论，一条活自己回流不了")
    await _actor_in_place(resolver, place)
    task = await TaskService(db).get(task_id)
    if task is None or task.room_id != place.room_id:
        raise NotFoundError("这个房间里没有这条活")
    # Friendly "@名字/@话题名" → structured tokens BEFORE it lands in the room,
    # same as the thread's own path: chips render and notifications fire there.
    conclusion = await canonicalize_refs(
        db, place.project_id, body.conclusion, exclude_topic_id=place.room_id
    )
    block, _ = await service.return_conclusion(
        subtopic_id=task_id, conclusion=conclusion
    )
    out = BlockOut.model_validate(block).model_dump(mode="json")
    await db.commit()
    await get_broker().publish(
        str(place.room_id), {"type": "assistant_block", "block": out}
    )
    return ok(out)


@router.get("/{topic_id}/trees")
async def list_room_trees(
    topic_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """This room's batches — newest first, each with the PR it rides on.

    一棵树 = 一个分支 = 一个 PR = 一批活. A room seals one and opens the next, so
    "is what I write right now going into the batch that is currently under CI,
    or into the next one" has an answer — and until this endpoint existed, no
    caller outside the backend could get it. A room whose batch is sealed reads
    on screen exactly like one that is not, which is how somebody keeps working
    and wonders why their changes are not on the PR.

    `last_check_*` is the agent's own quick check on this tree's content. It
    gates nothing (#296 settled that the PR's real CI decides) — it is here so a
    red check is visible to whoever is about to accept.
    """
    topic = await TopicService(db).get_or_404(topic_id)
    actor = await resolver.resolve(
        fallback_handle=None, topic_id=topic_id, project_id=topic.project_id
    )
    await resolver.authorize_topic(
        actor, project_id=topic.project_id, topic_id=topic_id
    )
    trees = await WorkTreeService(db).history(topic_id)
    cards = await AcceptCardRepository(db).latest_by_tree([t.id for t in trees])
    items = []
    for tree in reversed(trees):
        card = cards.get(tree.id)
        items.append(
            {
                "id": str(tree.id),
                "status": str(tree.status),
                "created_at": tree.created_at,
                "sealed_at": tree.sealed_at,
                "merged_at": tree.merged_at,
                "last_check_at": tree.last_check_at,
                "last_check_ok": tree.last_check_ok,
                "last_check_detail": tree.last_check_detail,
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

    A thread's 现场 is its own, exactly as its conversation is: the work is what
    ran the tools, so asking the room would hand back every other thread's
    actions interleaved with the room's — and asking the thread used to 404,
    because this route resolved only rooms while `/blocks` beside it already
    resolved places."""
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
        # A cursor from a different THREAD is as wrong as one from another room.
        if (
            cursor is None
            or cursor.topic_id != place.room_id
            or cursor.task_id != place.task_id
        ):
            raise NotFoundError("游标事件不存在")
    if limit is None:
        site = [
            b
            for b in await repo.list_for_topic(place.room_id, task_id=place.task_id)
            if b.kind in kinds
        ]
        has_more = False
    else:
        result = await repo.page_for_topic(
            place.room_id,
            task_id=place.task_id,
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
    """资源用量 (spec §9.1): token/cost for this place.

    A room answers with the TOTAL — its own main line plus every thread it has
    dispatched — and a thread answers for itself. Not two aggregations of the
    same number: a spend row carries both halves of the place it happened in
    (`topic_id` = room, `task_id` = thread), so summing by room already includes
    the threads, while "what did this piece of work cost" needs the thread key
    and is the question a room-level total cannot answer.
    """
    place = await TopicService(db).place_or_404(topic_id)
    await _actor_in_place(resolver, place)
    repo = UsageRepository(db)
    if place.task_id is not None:
        return ok(await repo.for_task(place.task_id))
    return ok(await repo.for_topic(place.room_id))


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
    # 盲飞防护 is asked BY whoever is flying, and that is usually a thread —
    # so the snapshot has to describe the place asked about, not the room it
    # happens to sit in. Turn state, background tasks and cards are all keyed
    # by place already; only this handler could not name one.
    cards = await AcceptCardRepository(db).list_for_topic(topic_id)
    credits = await ComputeGrantRepository(db).summary(place.project_id)
    turn = runner.topic_work(topic_id)
    background = awaited_tasks.status_snapshot(topic_id)
    stall = await topics.stall_signal(
        topic_id,
        live_turn=runner.live_work_for_topic(topic_id),
        background_tasks=len(background["tasks"]),
    )
    return ok(
        {
            "topic": {
                "id": str(place.id),
                "title": place.title,
                # A room is archived/active; a thread is open/closed. Reporting
                # the room's word for a thread would say "active" about work
                # that finished.
                "status": str(
                    place.task.status if place.task is not None else place.room.status
                ),
                "branch": place.branch_name,
            },
            "turn": turn,
            "stall": stall,
            "cards": [_card_snapshot(c) for c in cards],
            "background": background,
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
    nodes = await BlockRepository(db).list_doc_nodes(
        place.room_id, task_id=place.task_id
    )
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
    comments = await BlockRepository(db).list_comments_for_topic(
        place.room_id, task_id=place.task_id
    )
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
        # Both halves: a room and a thread inside it share `topic_id`, so
        # checking only that would let a comment anchor onto the other one's doc.
        if (
            node is None
            or node.topic_id != place.room_id
            or node.task_id != place.task_id
        ):
            raise ValidationError("锚点不是本话题的文档块")
        reply_to = node.id
    # B4 Feishu-style: the exact selected span, kept for display next to the
    # comment. Bounded so a runaway selection can't bloat the row.
    quote = (body.get("quote") or "").strip() or None
    if quote and len(quote) > 500:
        quote = quote[:500]
    actor = await resolver.resolve(
        fallback_handle=body.get("author"),
        topic_id=place.id,
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
        runner.submit(
            chat,
            topic_id,
            author="system",
            content=(
                f"{author} 在实况文档 {where} 处评论：{content}\n"
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
        topic_id=place.id,
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
    # Read before the write, for the summary. Not a race: a doc that moved in
    # between is exactly what `expected_version` refuses, so the version this
    # read saw is the version the accepted write replaced.
    previous = await BlockRepository(db).doc_root(place.room_id, task_id=place.task_id)
    was = previous.content if previous else ""
    doc = await TopicService(db).edit_doc(
        topic_id=topic_id,
        content=content,
        author=actor.handle,
        expected_version=body.expected_version,
    )
    if not actor.is_agent:
        # The notice tells 芝士 to go re-read the doc, so the doc has to BE the
        # new one by the time it does — same ordering as the comment route.
        await db.commit()
        # 芝士's own `cheese doc set` is not news to 芝士.
        await chat.notify_running_turn(
            topic_id,
            f"{actor.handle} 刚改了实况文档，现在是第 {doc.doc_version} 版"
            f"（{summarize_doc_change(was, content)}）。你手上那份可能已经旧了："
            "要接着改文档，先 cheese doc get 重新读一遍，否则写回去会被拒。",
        )
    return ok(BlockOut.model_validate(doc).model_dump(mode="json"))


@router.get("/{topic_id}/compute-profile")
async def get_topic_compute_profile(
    topic_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """The compute this topic runs on (execution-architecture v4 会话级选择).

    `current` is the effective pool
    (topic choice → project sticky → team default → platform default).
    `locked` is true once the topic has run (some agent has a session here) — the
    picker freezes then, matching the device-affinity boundary. `sticky` is the
    effective starting
    choice for a new topic (project memory, then team default); `profiles` include
    unavailable targets so a locked offline device still has a readable label."""
    topic = await TopicService(db).get_or_404(topic_id)
    actor = await resolver.resolve(
        fallback_handle=None, topic_id=topic_id, project_id=topic.project_id
    )
    await resolver.authorize_topic(
        actor, project_id=topic.project_id, topic_id=topic_id
    )
    project = await ProjectRepository(db).get(topic.project_id)
    sticky = (project.settings or {}).get("compute_profile") if project else None
    team_default = None
    if project is not None and project.team_id is not None:
        team = await TeamRepository(db).get_by_id(project.team_id)
        team_default = team.compute_profile if team is not None else None
    current = topic.compute_profile or sticky or team_default or compute_default_name()
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
    effective_visibility: str | None = None
    if binding is not None:
        effective_visibility = binding.visibility.value
    return ok(
        {
            "current": current,
            # A machine id only has selection meaning under the self-hosted pool.
            # Cloud also records its connector in device_topic, but that endpoint is
            # an implementation detail of the freshly provisioned topic machine, not
            # a machine the person chose from a list.
            "device_id": (
                binding.device_id
                if current == COMPUTE_DEVICE and binding is not None
                else None
            ),
            "devices": [
                {
                    "device_id": device.device_id,
                    "name": device.name,
                    "online": device_hub.is_online(device.device_id),
                }
                for device in devices
            ],
            "locked": await AgentSessionService(db).has_run(topic_id),
            "inherited": topic.compute_profile is None,
            "sticky": sticky or team_default or compute_default_name(),
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
    """Pick the topic's compute pool. Allowed only before the first turn (no agent
    has a session here yet); once the topic has run the pin is frozen so its work
    tree / session never move. The choice also updates the project's sticky default, so
    the next new topic inherits it (spec v4: 选了之后持久化，除非新 session 又改)."""
    topic = await TopicService(db).get_or_404(topic_id)
    actor = await resolver.resolve(
        fallback_handle=None, topic_id=topic_id, project_id=topic.project_id
    )
    await resolver.authorize_topic(
        actor, project_id=topic.project_id, topic_id=topic_id
    )
    if await AgentSessionService(db).has_run(topic_id):
        raise ValidationError("话题已开始，算力已锁定；新建话题可另选算力")
    name = (body.get("profile") or "").strip() or compute_default_name()
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
        await MachineService(db).require_create_authority(topic.project_id, actor)

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
    project = await ProjectRepository(db).get(topic.project_id)
    if project is not None:
        project.settings = {**(project.settings or {}), "compute_profile": name}
    await db.flush()
    return ok(
        {
            "current": name,
            "device_id": device_id if name == COMPUTE_DEVICE else None,
            "locked": False,
            "inherited": False,
        }
    )


@router.post("/{topic_id}/ask")
async def ask_options(topic_id: uuid.UUID, body: dict, db: DbSession) -> dict:
    """芝士 asks an option question IN the chat (cheese ask): a message block
    whose meta.options renders as one-click buttons. Structured interaction —
    the answer comes back as data, never parsed from prose (spec §14.5)."""
    place = await TopicService(db).place_or_404(topic_id)
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
        author=await TopicMemberService(db).resolve_agent_handle(
            topic_id, room_id=place.room_id
        ),
        author_type=AuthorType.ai,
        content=question,
        kind=BlockKind.message,
        meta={"options": options},
    )
    await db.commit()
    payload = BlockOut.model_validate(blk).model_dump(mode="json")
    await get_broker().publish(
        str(topic_id), {"type": "assistant_block", "block": payload}
    )
    return ok(payload)


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
    await runner.submit_message(
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


@router.post("/{topic_id}/background-task")
async def register_background_task(
    topic_id: uuid.UUID, body: BackgroundTaskIn, db: DbSession
) -> dict:
    """`cheese await` announces a command it is about to run in its own sandbox.

    Returns the task id plus a wake token that outlives the container's own
    CHEESE_TOKEN (1h) — these tasks routinely run longer than that, and a result
    that 401s at the finish line is exactly the frozen topic this path exists to
    prevent."""
    place = await TopicService(db).place_or_404(topic_id)
    # 归档后工作面定格: freezing the room freezes the threads in it, so a
    # thread's long command is refused with the room it belongs to.
    if place.room.status == TopicStatus.archived:
        raise ValidationError("话题已归档，不再受理后台任务")
    task = awaited_tasks.register(
        project_id=place.project_id,
        # The place, so the result wakes whoever is waiting on it.
        topic_id=topic_id,
        command=body.command,
        label=body.label,
        timeout_s=body.timeout_s,
        log_path=body.log_path,
    )
    return ok(
        {
            "task_id": str(task.id),
            "wake_token": mint_scoped_token(
                project_id=str(place.project_id),
                topic_id=str(topic_id),
                # Cover the whole run plus an hour of slack for a slow report.
                ttl_s=task.timeout_s + 3600,
            ),
            "label": task.label,
        }
    )


@router.post("/{topic_id}/background-task/{task_id}/done")
async def finish_background_task(
    topic_id: uuid.UUID,
    task_id: uuid.UUID,
    body: BackgroundTaskDoneIn,
    chat: Annotated[ChatService, Depends(get_chat_service)],
    runner: Annotated[AgentWorkRunner, Depends(get_work_runner)],
) -> dict:
    """The backgrounded command exited — land its result and (guards permitting)
    wake the topic. Reached by the detached child `cheese await` forked, carrying
    the wake token from registration."""
    task = awaited_tasks.get(task_id)
    if task is None or task.topic_id != topic_id:
        raise NotFoundError("这个后台任务不存在或已经回报过了")
    return ok(
        await awaited_tasks.report(
            chat.session_factory,
            chat,
            runner,
            task=task,
            exit_code=body.exit_code,
            tail=body.tail,
            duration_s=body.duration_s,
        )
    )


@router.post("/{topic_id}/decision")
async def record_decision(
    topic_id: uuid.UUID,
    body: dict,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """记录关键决策到决策记录 (spec §7.1) — used by the `cheese decision` CLI."""
    place = await TopicService(db).place_or_404(topic_id)
    await _actor_in_place(resolver, place)
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
        author=await TopicMemberService(db).resolve_agent_handle(
            topic_id, room_id=place.room_id
        ),
        author_type=AuthorType.ai,
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
        fallback_handle=body.get("by"), topic_id=place.id, project_id=place.project_id
    )
    await resolver.authorize_topic(
        actor, project_id=place.project_id, topic_id=place.room_id
    )
    title = (body.get("title") or "").strip()
    if not title:
        raise ValidationError("title 不能为空")
    if place.task is not None:
        place.task.title = title[:80]
        await db.flush()
        return ok(TaskOut.model_validate(place.task).model_dump(mode="json"))
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
    by = (body.get("by") or "anonymous").strip() or "anonymous"
    topic = await TopicService(db).archive(topic_id, by=by)
    return ok(TopicOut.model_validate(topic).model_dump(mode="json"))


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
    by = (body.get("by") or "anonymous").strip() or "anonymous"
    topic = await TopicService(db).unarchive(topic_id, by=by)
    return ok(TopicOut.model_validate(topic).model_dump(mode="json"))


@router.post("/{topic_id}/split")
async def split_topic(
    topic_id: uuid.UUID,
    body: SplitIn,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """从上往下拆解：dispatch a todo as a thread of work in this room (eval A2).

    Writes the thread and seeds its task-brief living doc, and stops there. The
    WORKER is the caller's to start: it spawns one inside its own session and
    reports the id back with `/tasks/{id}/bind`. The platform used to raise a
    whole second container per thread — its own screen, its own home, its own
    clone of the repository — to run something that is a second worker in a
    session the room already has.

    So a thread returned from here has no worker yet, and that is a normal state
    rather than a half-finished dispatch: 认领 is a separate call because the id
    it carries does not exist until the worker does.

    `topic_id` may be a thread's — 芝士 working on one piece of work often finds
    a second. Work does not nest, so the new thread hangs in the same ROOM
    either way."""
    service = TopicService(db)
    parent_place = await service.place_or_404(topic_id)
    # actor 在信任边界注入 (同 edit_topic_doc): prefer the verified token, fall
    # back to body.created_by, and require the caller actually have access to
    # the ROOM — a body-trusted `created_by` let anyone dispatch work in anyone
    # else's room and name an arbitrary owner.
    actor = await resolver.resolve(
        fallback_handle=body.created_by,
        topic_id=parent_place.id,
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
        paths=body.paths,
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
    trees = WorkTreeService(db)
    tree = await trees.ensure_open(project_id=place.project_id, room_id=place.room_id)
    await trees.record_check(tree, ok=body.ok, detail=body.detail)
    await db.commit()
    return ok({"recorded": True, "tree_id": str(tree.id)})


@router.post("/{topic_id}/claim")
async def claim_paths(
    topic_id: uuid.UUID,
    body: ClaimIn,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """声明这条活要碰哪些路径 —— and find out who else is already there.

    Additive, because a claim always grows: work reaches files nobody predicted
    when the brief was written. That is also why this is a call rather than a
    field in the brief — a brief is written once and cannot be changed.

    Refusals and warnings come back together. Claiming the same FILE as another
    open piece of work on the same tree is refused (the second write wins in
    silence, and nothing else would report it); overlapping DIRECTORIES is a
    warning, because two threads under one package is ordinary and refusing it
    would make the rule something people route around.
    """
    place = await TopicService(db).place_or_404(topic_id)
    await _actor_in_place(resolver, place)
    svc = ClaimService(db)
    if place.task is None:
        # A room writes to the same tree as its threads and can overwrite their
        # files exactly as they can overwrite each other's, so it claims too —
        # it just has no row of its own to hold the claim, so this reports the
        # conflicts without recording anything.
        refusals, warnings = await svc.check(
            tree_id=place.tree_id or place.room_id,
            paths=svc.normalise(body.paths),
            exclude_task_id=None,
        )
        return ok({"claimed": [], "refusals": refusals, "warnings": warnings})
    refusals, warnings = await svc.claim(place.task, body.paths)
    await db.commit()
    return ok(
        {
            "claimed": [] if refusals else list(place.task.claimed_paths or []),
            "refusals": refusals,
            "warnings": warnings,
        }
    )


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
    acquired, reason = await RoomLockService(db).acquire(
        room_id=place.room_id,
        kind=LockKind(body.kind),
        resource=body.resource or "",
        holder_task_id=place.task_id,
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
    released = await RoomLockService(db).release(
        room_id=place.room_id,
        kind=LockKind(body.kind),
        resource=body.resource or "",
        holder_task_id=place.task_id,
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
    chat: Annotated[ChatService, Depends(get_chat_service)],
    runner: Annotated[AgentWorkRunner, Depends(get_work_runner)],
    resolver: ActorResolverDep,
) -> dict:
    """母子传话: send one message across the parent/child edge AND wake the other
    side (`cheese tell`). See `app.domain.topic.relay` for why the comments
    endpoint could not be this channel and why only this one edge is open.

    `topic_id` is the SENDER — the place whose turn is speaking, which is what
    the per-turn token in `_CHEESE_WRITE_PATHS` is scoped to. The receiver rides
    in the body and is resolved against what the sender can reach (its room, or
    the threads it dispatched): an id in the URL says "who is talking", never
    "which resource is this".
    """
    sender = await TopicService(db).place_or_404(topic_id)
    await _actor_in_place(resolver, sender)
    service = TopicRelayService(db)
    target = await service.resolve_target(sender=sender, target=body.target)
    # Friendly "@名字 / @话题名" → structured tokens BEFORE the message lands in
    # the other room, so chips render and @mentions notify over there.
    content = await canonicalize_refs(
        db, sender.project_id, body.content, exclude_topic_id=target.id
    )
    block, direction = await service.relay(
        sender=sender, target=target, content=content
    )
    out = BlockOut.model_validate(block).model_dump(mode="json")
    # Commit BEFORE waking: the woken turn runs on its own session and has to be
    # able to read the message it is being woken about.
    await db.commit()
    # The room is where a person is watching; a thread's message shows up there
    # too, under its thread.
    await get_broker().publish(
        str(target.room_id), {"type": "assistant_block", "block": out}
    )
    delivery = await deliver_or_wake(
        chat=chat,
        runner=runner,
        target=target,
        direction=direction,
        sender_title=sender.title,
        sender_id=sender.id,
        block_id=block.id,
        message=content,
    )
    return ok(
        {
            "block": out,
            "target_topic_id": str(target.id),
            "target_title": target.title,
            "direction": direction,
            # injected / woke / merged / archived — see relay.RelayDelivery. The
            # sender is told which, because "芝士 has it now" and "nobody will
            # ever read it" must not look the same.
            "delivery": delivery,
        }
    )


@router.post("/{topic_id}/return-conclusion")
async def return_conclusion(
    topic_id: uuid.UUID,
    body: ConclusionIn,
    db: DbSession,
    chat: Annotated[ChatService, Depends(get_chat_service)],
    resolver: ActorResolverDep,
) -> dict:
    """结论回流：write a sub-topic's conclusion back to its parent — then WAKE
    the parent to digest it (the return leg of the subagent loop: in Claude
    Code the parent resumes when the Task result arrives; here the parent 芝士
    runs a turn to weave the conclusion in and decide what's next)."""
    service = TopicService(db)
    place = await service.place_or_404(topic_id)
    await _actor_in_place(resolver, place)
    # Friendly "@名字/@话题名" in the conclusion → structured tokens BEFORE it
    # lands in the room (chips render + notifications fire there).
    conclusion = await canonicalize_refs(
        db, place.project_id, body.conclusion, exclude_topic_id=place.room_id
    )
    block, card = await service.return_conclusion(
        subtopic_id=topic_id, conclusion=conclusion
    )
    parent = place.room
    out = BlockOut.model_validate(block).model_dump(mode="json")
    wake = parent.status != TopicStatus.archived
    # 结论卡·阶段一: the card id has to reach the digest turn, otherwise the
    # parent has a card it cannot address — read it BEFORE the commit expires
    # the instance.
    card_id = str(card.id) if card is not None else None
    card_deadline = card.digest_deadline_at if card is not None else None
    # Commit BEFORE waking: the parent's turn runs on its own session.
    await db.commit()
    await get_broker().publish(
        str(parent.id), {"type": "assistant_block", "block": out}
    )
    if wake:
        get_work_runner().submit_kickoff(
            chat,
            parent.id,
            prompt=conclusion_digest_prompt(
                block.content, card_id=card_id, deadline=card_deadline
            ),
        )
    return ok(out)


# 芝士 → UI rendering (spec §9.1): an artifact is a file the AI explicitly points
# at + how to render it. The type comes from the tool call, never from parsing
# prose. MVP renders html/svg in the preview window; more types are additive.
_ARTIFACT_MIME = {
    "html": "text/html",
    "svg": "image/svg+xml",
    # 运行环境预览: the artifact is a RUNNING app on the machine this place's turn
    # lives on, reached over the preview tunnel that machine dialled out. HOW to
    # run it — and on which port — is the agent's judgment; the platform only
    # carries what answers there.
    "app": "application/x-cheesex-app",
}

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
            "要给人看结果也可以用 cheese artifact 点名一个网页或 SVG 文件。"
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
    as_ = (body.get("as") or "html").strip().lower()
    if as_ == "app":
        # An app artifact points at the running server, not a file — the stored
        # content is a human note ("Vue dev server"), not a path.
        path = (body.get("path") or "app").strip()[:120]
        await _reject_unreachable_app(topic_id)
    else:
        path = _clean_artifact_path(body.get("path") or "")
    mime = _ARTIFACT_MIME.get(as_)
    if mime is None:
        allowed = "、".join(_ARTIFACT_MIME)
        raise ValidationError(f"暂不支持的类型 {as_!r}（可选：{allowed}）")
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


@router.get("/{topic_id}/preview")
async def get_preview(
    topic_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """This place's current preview (spec §7.1): the artifact 芝士 last pointed at,
    as {path, mime}. Null when none is set — the client may fall back to scanning
    the worktree. Content is fetched separately via the guarded file reader.

    Per place, matching `cheese artifact`: each thread previews its own result,
    and the room's stays the room's rather than being overwritten by whichever
    thread pointed at something most recently."""
    place = await TopicService(db).place_or_404(topic_id)
    await _actor_in_place(resolver, place)
    art = await BlockRepository(db).latest_artifact(
        place.room_id, task_id=place.task_id
    )
    if art is None:
        return ok(None)
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
                # Root-relative: the backend's reverse proxy, reachable from any
                # browser. There is no machine-local address to hand out — that
                # is the whole reason the tunnel exists.
                "url": (
                    proxy.browser_path(f"/topics/{topic_id}/app/") if alive else None
                ),
                "tunnel_up": tunnel_up,
                "artifact_id": str(art.id),
            }
        )
    return ok(
        {
            "kind": "file",
            "path": art.content,
            "mime": art.mime_type,
            # Which artifact this is, so a client can tell "芝士 pointed at
            # something new" from "the same preview, re-fetched" — re-pointing at
            # the same path is a new preview too, so the path cannot carry this.
            "artifact_id": str(art.id),
        }
    )


@router.get("/{topic_id}/preview/raw")
async def get_preview_raw(
    topic_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> Response:
    """The current file artifact served as a real page — 在新窗口打开 (Claude
    Artifacts style). CSP `sandbox allow-scripts` keeps it an opaque origin so
    artifact JS can't call our API as the user."""
    topic = await TopicService(db).get_or_404(topic_id)
    actor = await resolver.resolve(
        fallback_handle=None, topic_id=topic_id, project_id=topic.project_id
    )
    await resolver.authorize_topic(
        actor, project_id=topic.project_id, topic_id=topic_id
    )
    art = await BlockRepository(db).latest_artifact(topic_id)
    if art is None or art.mime_type == _ARTIFACT_MIME["app"]:
        raise NotFoundError("没有可打开的文件 artifact")
    data = ws.read_file_bytes(topic.project_id, art.content, topic_id=topic_id)
    return Response(
        content=data,
        media_type=art.mime_type or "text/html",
        headers={"Content-Security-Policy": "sandbox allow-scripts"},
    )


# ---- 聊天图片附件 (图片输入) -------------------------------------------------
# An attachment is a REAL file in the topic's worktree (所有产出都是 git): the
# upload writes bytes under uploads/, the message references it as an
# attachment block, and 芝士 sees it by Read-ing the file in its sandbox.

# Images only for now; the mime comes from the upload's content-type and the
# raw reader re-derives it from the extension (never from file sniffing).
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
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024  # 10MB per image


@router.post("/{topic_id}/attachments")
async def upload_attachment(
    topic_id: uuid.UUID, file: UploadFile, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """Upload a chat image into the topic's worktree (uploads/…). Returns the
    {path, mime} the client then references when sending the message."""
    topic = await TopicService(db).get_or_404(topic_id)
    actor = await resolver.resolve(
        fallback_handle=None, topic_id=topic_id, project_id=topic.project_id
    )
    await resolver.authorize_topic(
        actor, project_id=topic.project_id, topic_id=topic_id
    )
    mime = (file.content_type or "").split(";")[0].strip().lower()
    ext = _IMAGE_MIME_EXT.get(mime)
    if ext is None:
        allowed = "、".join(sorted(_IMAGE_MIME_EXT))
        raise ValidationError(f"只支持图片（{allowed}）")
    data = await file.read(MAX_ATTACHMENT_BYTES + 1)
    if not data:
        raise ValidationError("空文件")
    if len(data) > MAX_ATTACHMENT_BYTES:
        raise ValidationError("图片太大（上限 10MB）")
    # Structural name only (uuid + extension) — nothing derived from content.
    path = f"uploads/img-{uuid.uuid4().hex[:12]}{ext}"
    ws.write_file_bytes(topic.project_id, path, data, topic_id=topic_id)
    return ok({"path": path, "mime": mime, "bytes": len(data)})


@router.get("/{topic_id}/attachments/raw")
async def attachment_raw(
    topic_id: uuid.UUID, path: str, db: DbSession, resolver: ActorResolverDep
) -> Response:
    """Raw bytes of an image attachment, for <img src=…>. Extension-whitelisted
    to images so this can never serve executable HTML from the worktree."""
    topic = await TopicService(db).get_or_404(topic_id)
    actor = await resolver.resolve(
        fallback_handle=None, topic_id=topic_id, project_id=topic.project_id
    )
    await resolver.authorize_topic(
        actor, project_id=topic.project_id, topic_id=topic_id
    )
    clean = _clean_artifact_path(path)
    suffix = "." + clean.rsplit(".", 1)[-1].lower() if "." in clean else ""
    mime = _EXT_IMAGE_MIME.get(suffix)
    if mime is None:
        raise ValidationError("只能读取图片附件")
    data = ws.read_file_bytes(topic.project_id, clean, topic_id=topic_id)
    return Response(
        content=data,
        media_type=mime,
        headers={
            "Content-Disposition": "inline",
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
    """私聊未读数: {peer_handle: unread_count} for the calling user, one query.

    Keyed by the other party's handle rather than by topic id — private chats
    are not in the topic tree, so the sidebar renders their rows from the member
    roster and has no topic id to look one up with. `cheese` is the 芝士 DM.
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

    A message in a room becomes a THREAD of work in that room; a message in a
    private chat becomes a room, because private chats are not in the topic tree
    and a thread there would be one nobody else could open. The response says
    which by carrying either a task or a topic.

    Who gets woken follows from that split. A room has a session of its own, so
    it kicks itself off. A thread does NOT — it is a 分身 inside the room's own
    session, and addressing a thread's id here would raise a whole container for
    a shape threads stopped having. So the ROOM is woken, and it is told to name
    the thread, raise the worker and bind it.
    """
    place, created = await TopicService(db).upgrade_block_to_place(
        block_id=block_id, created_by=body.created_by
    )
    thread = place.task
    out = (
        TaskOut.model_validate(thread).model_dump(mode="json")
        if thread is not None
        else TopicOut.model_validate(place.room).model_dump(mode="json")
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
                place.room_id,
                prompt=thread_upgraded_prompt(
                    task_id=thread.id, source_message=upgraded_text
                ),
            )
        else:
            get_work_runner().submit_kickoff(chat, place.id)
    return ok(out)
