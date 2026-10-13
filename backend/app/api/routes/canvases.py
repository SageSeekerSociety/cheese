"""Canvases over HTTP: a person's board and an AI teammate's tools read and
change them here alike.

A canvas answers to its document: whoever may read the document may read the
canvas, whoever may write the document may change it, by the same checks
(`app.api.doc_access`). A change reaches everyone who has the document open as
a frame through the collaboration service, the same way a renamed document
does; the board then reads the canvas again.
"""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolver, ActorResolverDep
from app.api.doc_access import Reached, frozen, reach, require_writable
from app.api.request_deps import DbSession
from app.api.response import ok, typed_response
from app.api.write_access import ROUTE_DECIDES
from app.core.errors import (
    AuthenticationRequiredError,
    NotFoundError,
    UnprocessableEntityError,
)
from app.core.sentences import say
from app.domain.block.queries import awaiting_an_answer
from app.domain.canvas.block import blocks
from app.domain.canvas.schemas import (
    CanvasChangedOut,
    CanvasCopyIn,
    CanvasOut,
    CanvasPatchIn,
    CanvasTextOut,
    ChangesIn,
    DocumentCanvasTextOut,
)
from app.domain.canvas.services import Board, Canvas, Canvases
from app.domain.living_doc import collab
from app.domain.project.address import Numbered, thing_by_id, thing_by_number
from app.domain.review.queries import latest_cards_by_task
from app.domain.room_task import presentation
from app.domain.room_task.canvas_text import (
    RoomFilter,
    board_text,
    document_canvas_texts,
    visible_tasks,
)
from app.domain.room_task.services import TaskService

# Every route asks the document (`app.api.doc_access`) who may read or write.
router = APIRouter(tags=["canvases"], dependencies=[ROUTE_DECIDES])


async def _writer(db: AsyncSession, reached: Reached) -> None:
    if not reached.actor.authenticated:
        raise AuthenticationRequiredError(say("docEditNeedsWriter"))
    await require_writable(db, reached)


async def _canvas(
    db: AsyncSession,
    resolver: ActorResolver,
    canvas_id: uuid.UUID,
    *,
    write: bool = False,
) -> tuple[Canvas, Reached]:
    canvas = await Canvases(db).get_or_404(canvas_id)
    reached = await reach(db, resolver, canvas.document_id, enforce=write)
    if write:
        await _writer(db, reached)
    return canvas, reached


def _readable(resolver: ActorResolver, reached: Reached) -> RoomFilter:
    """The channels whose tasks this reader may see named on a card."""

    async def readable(rooms: set[uuid.UUID]) -> set[uuid.UUID]:
        return {
            room
            for room in rooms
            if await resolver.can_access_topic(
                reached.actor, project_id=reached.doc.project_id, topic_id=room
            )
        }

    return readable


async def _columns(
    db: AsyncSession, task_ids: list[uuid.UUID]
) -> dict[uuid.UUID, dict[str, str]]:
    """Where each task stands on a person's board: the column and the phrase
    the task list shows, from the same facts."""
    if not task_ids:
        return {}
    cards = await latest_cards_by_task(db, task_ids)
    asked = await awaiting_an_answer(db, task_ids)
    now = datetime.now(UTC)
    return {
        task.id: presentation.task_presentation(
            presentation.facts_for_task(
                task, cards.get(task.id), awaiting_answer=task.id in asked
            ),
            now=now,
        ).as_dict()
        for task in await TaskService(db).list_by_ids(task_ids)
    }


async def _state(
    db: AsyncSession, resolver: ActorResolver, canvas: Canvas, reached: Reached
) -> dict:
    return await _board_out(db, resolver, await Canvases(db).board(canvas), reached)


async def _board_out(
    db: AsyncSession, resolver: ActorResolver, board: Board, reached: Reached
) -> dict:
    tasks = await visible_tasks(
        db,
        [c.task_id for c in board.cards if c.task_id is not None],
        _readable(resolver, reached),
    )
    columns = await _columns(db, list(tasks))
    return {
        "id": str(board.canvas.id),
        "document_id": str(board.canvas.document_id),
        "height": board.canvas.height,
        "writable": reached.actor.authenticated and not await frozen(db, reached),
        "cards": [
            {
                "id": str(card.id),
                "number": card.number,
                "content": card.content,
                "x": card.x,
                "y": card.y,
                "width": card.width,
                "height": card.height,
                "task": None
                if card.task_id is None or card.task_id not in tasks
                else {
                    "id": str(card.task_id),
                    "number": tasks[card.task_id].number,
                    "title": tasks[card.task_id].title,
                    "room_id": str(tasks[card.task_id].room_id),
                    **columns.get(card.task_id, {}),
                },
                # A task the reader may not see is still pointed at: they are
                # told there is one, not which.
                "task_hidden": card.task_id is not None and card.task_id not in tasks,
                "updated_by": card.updated_by,
                "updated_at": card.updated_at.isoformat(),
            }
            for card in board.cards
        ],
        "edges": [
            {
                "id": str(edge.id),
                "from": str(edge.source_id),
                "to": str(edge.target_id),
                "label": edge.label,
            }
            for edge in board.edges
        ],
    }


async def _told(canvas: Canvas) -> None:
    await collab.tell(
        canvas.document_id,
        {"type": "state", "resource": "canvas", "canvas": str(canvas.id)},
    )


@router.post("/documents/{document_id}/canvases", **typed_response(CanvasOut))
async def create_canvas(
    document_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """A new, empty canvas of this document, for a canvas block an editor just
    put in it."""
    reached = await reach(db, resolver, document_id, enforce=True)
    await _writer(db, reached)
    canvas = await Canvases(db).create(
        document_id=document_id, actor=reached.actor.handle
    )
    await db.commit()
    return ok(await _state(db, resolver, canvas, reached))


@router.get(
    "/documents/{document_id}/canvases",
    **typed_response(list[DocumentCanvasTextOut]),
)
async def document_canvases(
    document_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """The canvases the document's text names, each as text, in order."""
    reached = await reach(db, resolver, document_id)
    texts = await document_canvas_texts(
        db, reached.doc, readable=_readable(resolver, reached)
    )
    return ok(
        [
            {"id": str(canvas_id), "title": title, "text": text}
            for canvas_id, title, text in texts
        ]
    )


@router.get("/canvases/{canvas_id}", **typed_response(CanvasOut))
async def get_canvas(
    canvas_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    canvas, reached = await _canvas(db, resolver, canvas_id)
    return ok(await _state(db, resolver, canvas, reached))


@router.get("/canvases/{canvas_id}/text", **typed_response(CanvasTextOut))
async def canvas_text(
    canvas_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    positions: bool = Query(default=False),
) -> dict:
    """The canvas as an outline an AI teammate reads; ``positions`` adds where
    each card sits."""
    canvas, reached = await _canvas(db, resolver, canvas_id)
    title = next(
        (
            b.title
            for b in blocks(reached.doc.content or "")
            if b.canvas_id == canvas.id
        ),
        "",
    )
    text = await board_text(
        db,
        await Canvases(db).board(canvas),
        title=title,
        readable=_readable(resolver, reached),
        positions=positions,
    )
    return ok({"id": str(canvas.id), "text": text})


@router.post("/canvases/{canvas_id}/changes", **typed_response(CanvasChangedOut))
async def change_canvas(
    canvas_id: uuid.UUID, body: ChangesIn, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """Make the changes in order, all or none; a refusal names the change
    (``data.index``). Answers the canvas as it now is, and the numbers new
    cards got by their ``ref`` (``refs``)."""
    canvas, reached = await _canvas(db, resolver, canvas_id, write=True)
    project_id = reached.doc.project_id
    readable = _readable(resolver, reached)

    async def task_of(ref: str) -> uuid.UUID:
        flat = ref.strip().lstrip("#")
        found = None
        if flat.isdigit():
            found = await thing_by_number(db, project_id, Numbered.task, int(flat))
        else:
            try:
                found = await thing_by_id(db, Numbered.task, uuid.UUID(flat))
            except ValueError:
                found = None
        if (
            found is None
            or found.project_id != project_id
            or found.room_id is None
            or found.room_id not in await readable({found.room_id})
        ):
            raise UnprocessableEntityError(say("canvasTaskNotFound", task=ref))
        return found.id

    applied = await Canvases(db).apply(
        canvas.id, body.changes, actor=reached.actor.handle, resolve_task=task_of
    )
    await db.commit()
    await _told(canvas)
    state = await _state(db, resolver, canvas, reached)
    return ok({**state, "refs": applied.refs, "added": applied.added})


@router.patch("/canvases/{canvas_id}", **typed_response(CanvasOut))
async def patch_canvas(
    canvas_id: uuid.UUID,
    body: CanvasPatchIn,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    canvas, reached = await _canvas(db, resolver, canvas_id, write=True)
    await Canvases(db).set_height(canvas, body.height)
    await db.commit()
    await _told(canvas)
    return ok(await _state(db, resolver, canvas, reached))


@router.post("/canvases/{canvas_id}/copy", **typed_response(CanvasOut))
async def copy_canvas(
    canvas_id: uuid.UUID, body: CanvasCopyIn, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """A copy of this canvas as it is now, belonging to ``document_id``: a
    canvas block pasted into a document, or into the same one again. Whoever
    may read the canvas and write that document may make one."""
    source, reached_source = await _canvas(db, resolver, canvas_id)
    target = await reach(db, resolver, body.document_id, enforce=True)
    await _writer(db, target)
    if reached_source.doc.project_id != target.doc.project_id:
        raise NotFoundError(say("canvasNotFound"))
    copy = await Canvases(db).copy(
        source, document_id=target.doc.id, actor=target.actor.handle
    )
    await db.commit()
    return ok(await _state(db, resolver, copy, target))
