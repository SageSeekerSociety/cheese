"""The 现场 timeline is a read projection of immutable harness records."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from app.api.auth import ActorResolverDep
from app.api.request_deps import DbSession
from app.api.response import ok, page
from app.api.session_record_rows import (
    hidden_private_memory,
    no_platform_reminders,
    text_of,
)
from app.api.topics_deps import _actor_in_place
from app.core.errors import NotFoundError
from app.core.sentences import say
from app.domain.agent.harness.notices import without_reminders
from app.domain.agent.run_records import for_site
from app.domain.agent.turn_times import turn_starts
from app.domain.agent_instance.services import AgentInstanceService
from app.domain.identity.handles import agent_instance_handle
from app.domain.room_task.place import place_or_404
from app.domain.session_record import service
from app.domain.session_record.history import earlier_turns
from app.domain.session_record.views import as_block

router = APIRouter(prefix="/topics", tags=["topics"])

#: How long the newest page waits on the private bucket before answering with
#: the rows the database already holds. The bucket leg is what makes opening
#: the panel slow: it is a round trip that gets throttled and is retried by the
#: SDK, and a page that answers without it is up in the time the database takes
#: (tens of milliseconds). A budget small enough to feel instant, large enough
#: that a healthy bucket still fills the page in one request.
SITE_FIRST_PAGE_BUDGET_S = 0.5


def hide_platform_reminders(items: list[dict]) -> list[dict]:
    """现场 keeps every record, but does not repeat the platform's own reminders.

    The 现场 reads the harness journal, and every input in it carries the
    platform's words — the chat contract on every turn, a nudge when the room or
    the checklist went quiet. They are instructions to the model, not
    conversation, so showing them makes a reader scroll past the same paragraph
    over and over. `without_reminders` takes them out of the display only: the
    journal keeps every line (`meta.record` still has it) and the model already
    read them.

    A text part left with nothing is dropped, so a notice-only input leaves no
    empty row behind. The stripping itself lives in `session_record_rows`, which
    a pulled transcript goes through too — one set of words, one place.
    """
    for item in items:
        meta = item.get("meta")
        if not isinstance(meta, dict):
            continue
        parts = meta.get("parts")
        if not isinstance(parts, list) or not parts:
            if isinstance(item.get("content"), str):
                item["content"] = without_reminders(item["content"])
            continue
        kept, changed = no_platform_reminders(parts)
        if changed:
            meta["parts"] = kept
            item["content"] = text_of(kept)
    return items


def _read_cursor(raw: str | None) -> service.Key | None:
    """A page cursor off the query string. Anything malformed gets the same
    refusal a stale event id does."""
    if raw is None:
        return None
    try:
        return service.decode_cursor(raw)
    except ValueError:
        raise NotFoundError(say("cursorEventNotFound")) from None


def _order(item: dict) -> service.Key:
    return service.decode_cursor(item["meta"]["cursor"])


async def _newer_page(
    db,
    place,
    authors: dict[str, str],
    native_author: str | None,
    author: str | None,
    forward: service.Key,
    limit: int,
    reader: str,
) -> JSONResponse:
    """The rows written after `forward`, oldest first.

    `has_newer` says another page follows: a reader catching up on a busy turn
    keeps asking until it is false, instead of being handed a page it already
    has. `has_more` stays false and `oldest_id` stays null — this direction
    says nothing about what is above the window, and the reader's own upward
    cursor must not move because of it.
    """
    raw = await service.read(
        db,
        place.conversation_id,
        after=forward,
        author=native_author,
        limit=limit + 1,
    )
    items = [as_block(item, place.conversation_id) for item in raw.items]
    for item in items:
        item["author"] = authors.get(item["author"], item["author"])
    old, _, boundary = await earlier_turns(
        db,
        place.conversation_id,
        before=None,
        after=forward,
        author=author,
        limit=limit + 1,
    )
    records = await for_site(
        db,
        place.conversation_id,
        since=max(boundary, forward[0]),
        until=None,
        author=author,
        limit=limit + 1,
        after=forward,
    )
    for record in records:
        key = (
            datetime.fromisoformat(record["created_at"]),
            "platform",
            record["author"],
            uuid.UUID(record["id"]).int,
        )
        record["meta"]["cursor"] = service.encode_cursor(key)
    candidates = sorted([*items, *old, *records], key=_order)
    taken = candidates[:limit]
    # 游标取**交出去的那几条**里的最后一条，不是藏掉平台提醒之后剩下的最后一条：
    # 否则一页全是提醒时读者手上的游标不动，下一次又把同一段读回来。
    #
    # 一条都没有（读者已经追上了）时把进来的那个游标原样还回去：不还的话读者手上
    # 的游标就被清空了，下一次只能整页重读——正是这一路要消掉的那件事。
    newest = taken[-1]["meta"]["cursor"] if taken else service.encode_cursor(forward)
    shown = hidden_private_memory(hide_platform_reminders(taken), reader)
    starts = await turn_starts(
        db,
        place.conversation_id,
        (uuid.UUID(i["turn_id"]) for i in shown if i["turn_id"]),
    )
    return JSONResponse(
        ok(
            {
                **page(shown, len(shown)),
                "has_more": False,
                "oldest_id": None,
                "has_newer": len(candidates) > limit,
                "newest_id": newest,
                "turn_starts": {
                    str(turn): at.isoformat() for turn, at in starts.items()
                },
            }
        )
    )


@router.get("/{topic_id}/transcript")
async def topic_transcript(
    topic_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    limit: int = Query(120, ge=1, le=200),
    before: str | None = Query(None, max_length=512),
    after: str | None = Query(None, max_length=512),
    author: str | None = Query(None, min_length=1, max_length=120),
    resident: bool = Query(
        False,
        description=(
            "Answer the newest page from the rows the database already holds, "
            "bounded by a short wait on the private bucket. The panel that opens "
            "现场 asks for this first, so it paints without waiting on the one "
            "leg that is slow, throttled and retried; it re-reads without the "
            "flag to fill in what the budget skipped."
        ),
    ),
) -> JSONResponse:
    """One page of a conversation's 现场.

    With neither cursor, the newest page. `before` walks up through the
    history. `after` walks forward from a page the reader already holds: that
    is the refresh a reader with this panel open runs, and asking for it by
    cursor is what makes it transfer the lines written since rather than the
    page they already have.
    """
    place = await place_or_404(db, topic_id)
    if place.room.deleted_at is not None:
        raise NotFoundError(say("topicNotFound"))
    actor = await _actor_in_place(resolver, place)
    instances = await AgentInstanceService(db).list_for_project(place.project_id)
    authors = {
        instance.handle: agent_instance_handle(instance.id) for instance in instances
    }
    native_author = (
        next((name for name, seat in authors.items() if seat == author), "")
        if author is not None
        else None
    )
    cursor = _read_cursor(before)
    forward = _read_cursor(after)
    if forward is not None:
        return await _newer_page(
            db, place, authors, native_author, author, forward, limit, actor.handle
        )
    raw = await service.read(
        db,
        place.conversation_id,
        before=cursor,
        newest=True,
        author=native_author,
        limit=limit,
        # `resident`: the newest page is what a reader stares at while 现场
        # loads, so it answers from the rows already in the database instead of
        # waiting on the bucket — the one leg that is slow, throttled and
        # retried. `deferred` tells the reader the page skipped the archive so
        # it re-reads (without `resident`) to fill it in.
        resident_budget_s=SITE_FIRST_PAGE_BUDGET_S if resident else None,
    )
    items = [as_block(item, place.conversation_id) for item in raw.items]
    for item in items:
        item["author"] = authors.get(item["author"], item["author"])
    old, more_old, boundary = await earlier_turns(
        db, place.conversation_id, before=cursor, author=author, limit=limit
    )
    records = await for_site(
        db,
        place.conversation_id,
        since=boundary,
        until=cursor[0] if cursor else None,
        author=author,
        limit=limit + 1,
        before=cursor,
    )
    for record in records:
        key = (
            datetime.fromisoformat(record["created_at"]),
            "platform",
            record["author"],
            uuid.UUID(record["id"]).int,
        )
        record["meta"]["cursor"] = service.encode_cursor(key)
    candidates = sorted(
        (
            item
            for item in [*items, *old, *records]
            if cursor is None or _order(item) < cursor
        ),
        key=_order,
    )
    # `raw.deferred`: the read hit its bucket budget and left sealed runs it
    # would otherwise have opened. They sit above this page, so the page is not
    # complete and the reader must be able to walk up into them — the same
    # `has_more` / `oldest_id` contract a page the database alone could not fill
    # already uses.
    has_more = (
        raw.next is not None or more_old or raw.deferred or len(candidates) > limit
    )
    taken = candidates[-limit:]
    # 交出去的那几条里最后一条的游标：读者接着问「从这儿往后有什么」用的就是它。
    # 藏在平台提醒后面取，理由同 `_newer_page`：一页全是提醒时游标就不动了。
    newest = taken[-1]["meta"]["cursor"] if taken else None
    items = hidden_private_memory(hide_platform_reminders(taken), actor.handle)
    starts = await turn_starts(
        db,
        place.conversation_id,
        (uuid.UUID(i["turn_id"]) for i in items if i["turn_id"]),
    )
    return JSONResponse(
        ok(
            {
                **page(items, len(items)),
                "has_more": has_more,
                # The reader is told this page skipped the bucket, so it re-reads
                # the newest page once instead of trusting a short one: what was
                # skipped is at the newest end, where paging up cannot reach it.
                "deferred": raw.deferred,
                "oldest_id": items[0]["meta"]["cursor"] if items and has_more else None,
                "newest_id": newest,
                "turn_starts": {
                    str(turn): at.isoformat() for turn, at in starts.items()
                },
            }
        )
    )
