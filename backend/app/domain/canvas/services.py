"""Reading and changing canvases. Who may is the caller's to decide — a canvas
answers to its document's rules (``app.api.doc_access``) — and the caller owns
the commit."""

import re
import uuid
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, UnprocessableEntityError
from app.core.sentences import say
from app.domain.canvas.block import blocks, renamed
from app.domain.canvas.models import Canvas, CanvasCard, CanvasEdge
from app.domain.canvas.outline import CardText, EdgeText
from app.domain.canvas.schemas import (
    MAX_CARDS,
    AddCard,
    CardKey,
    Change,
    Connect,
    DeleteCard,
    Disconnect,
    MoveCard,
    UpdateCard,
)
from app.domain.living_doc.models import Document

#: Turns what a change names as its task (an id, or a number in the project)
#: into the task's id, refusing one the caller may not point at.
TaskResolver = Callable[[str], Awaitable[uuid.UUID]]

_NUMBER = re.compile(r"^\s*\[?#?(\d+)\]?\s*$")


@dataclass
class Board:
    canvas: Canvas
    cards: list[CanvasCard]
    edges: list[CanvasEdge]

    def card_texts(self) -> list[CardText]:
        return [
            CardText(
                id=card.id,
                number=card.number,
                content=card.content,
                task_id=card.task_id,
                updated_by=card.updated_by,
                updated_at=card.updated_at,
                x=card.x,
                y=card.y,
                width=card.width,
                height=card.height,
            )
            for card in self.cards
        ]

    def edge_texts(self) -> list[EdgeText]:
        return [
            EdgeText(source_id=e.source_id, target_id=e.target_id, label=e.label)
            for e in self.edges
        ]


@dataclass
class Applied:
    """What a list of changes did: the numbers the new cards got, by ``ref``."""

    refs: dict[str, int] = field(default_factory=dict)
    added: list[int] = field(default_factory=list)


class Canvases:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get(self, canvas_id: uuid.UUID) -> Canvas | None:
        return await self._session.get(Canvas, canvas_id)

    async def get_or_404(self, canvas_id: uuid.UUID) -> Canvas:
        canvas = await self.get(canvas_id)
        if canvas is None:
            raise NotFoundError(say("canvasNotFound"))
        return canvas

    async def of_document(self, document_id: uuid.UUID) -> list[Canvas]:
        rows = await self._session.scalars(
            select(Canvas)
            .where(Canvas.document_id == document_id)
            .order_by(Canvas.created_at)
        )
        return list(rows)

    async def project_of(self, canvas: Canvas) -> uuid.UUID | None:
        return await self._session.scalar(
            select(Document.project_id).where(Document.id == canvas.document_id)
        )

    async def create(self, *, document_id: uuid.UUID, actor: str) -> Canvas:
        canvas = Canvas(document_id=document_id, created_by=actor)
        self._session.add(canvas)
        await self._session.flush()
        return canvas

    async def board(self, canvas: Canvas) -> Board:
        cards = list(
            await self._session.scalars(
                select(CanvasCard)
                .where(CanvasCard.canvas_id == canvas.id)
                .order_by(CanvasCard.number)
            )
        )
        edges = list(
            await self._session.scalars(
                select(CanvasEdge)
                .where(CanvasEdge.canvas_id == canvas.id)
                .order_by(CanvasEdge.created_at, CanvasEdge.id)
            )
        )
        return Board(canvas, cards, edges)

    async def copy(
        self, canvas: Canvas, *, document_id: uuid.UUID, actor: str
    ) -> Canvas:
        """A new canvas of ``document_id`` holding what ``canvas`` holds now.
        The two change apart from then on."""
        source = await self.board(canvas)
        copy = Canvas(
            document_id=document_id,
            created_by=actor,
            height=canvas.height,
            next_number=canvas.next_number,
        )
        self._session.add(copy)
        await self._session.flush()
        new_ids: dict[uuid.UUID, uuid.UUID] = {}
        for card in source.cards:
            twin = CanvasCard(
                id=uuid.uuid4(),
                canvas_id=copy.id,
                number=card.number,
                content=card.content,
                x=card.x,
                y=card.y,
                width=card.width,
                height=card.height,
                task_id=card.task_id,
                updated_by=card.updated_by,
                updated_at=card.updated_at,
            )
            new_ids[card.id] = twin.id
            self._session.add(twin)
        await self._session.flush()
        for edge in source.edges:
            self._session.add(
                CanvasEdge(
                    canvas_id=copy.id,
                    source_id=new_ids[edge.source_id],
                    target_id=new_ids[edge.target_id],
                    label=edge.label,
                    created_by=edge.created_by,
                )
            )
        await self._session.flush()
        return copy

    async def set_height(self, canvas: Canvas, height: int | None) -> None:
        canvas.height = height
        await self._session.flush()

    async def apply(
        self,
        canvas_id: uuid.UUID,
        changes: Sequence[Change],
        *,
        actor: str,
        resolve_task: TaskResolver,
    ) -> Applied:
        """Make every change, in order, as ``actor``; a change that cannot be
        made refuses them all, naming it (``data.index``)."""
        canvas = await self._session.scalar(
            select(Canvas).where(Canvas.id == canvas_id).with_for_update()
        )
        if canvas is None:
            raise NotFoundError(say("canvasNotFound"))
        board = await self.board(canvas)
        cards = {card.number: card for card in board.cards}
        edges = {(e.source_id, e.target_id): e for e in board.edges}
        applied = Applied()
        now = datetime.now(UTC)

        def card_of(key: CardKey, index: int) -> CanvasCard:
            if isinstance(key, str) and key in applied.refs:
                number = applied.refs[key]
            else:
                match = _NUMBER.match(str(key))
                number = int(match.group(1)) if match else -1
            card = cards.get(number)
            if card is None:
                raise UnprocessableEntityError(
                    say("canvasCardMissing", card=str(key), index=index + 1),
                    data={"index": index},
                )
            return card

        def touched(card: CanvasCard) -> None:
            card.updated_by = actor
            card.updated_at = now

        for index, change in enumerate(changes):
            if isinstance(change, AddCard):
                if len(cards) >= MAX_CARDS:
                    raise UnprocessableEntityError(
                        say("canvasTooManyCards", limit=MAX_CARDS),
                        data={"index": index},
                    )
                task_id = await resolve_task(change.task) if change.task else None
                card = CanvasCard(
                    id=uuid.uuid4(),
                    canvas_id=canvas.id,
                    number=canvas.next_number,
                    content=change.content,
                    x=change.x,
                    y=change.y,
                    width=change.width,
                    height=change.height,
                    task_id=task_id,
                    updated_by=actor,
                    updated_at=now,
                    created_at=now,
                )
                canvas.next_number += 1
                self._session.add(card)
                cards[card.number] = card
                applied.added.append(card.number)
                if change.ref:
                    applied.refs[change.ref] = card.number
            elif isinstance(change, UpdateCard):
                card = card_of(change.card, index)
                if change.content is not None:
                    card.content = change.content
                if "task" in change.model_fields_set:
                    card.task_id = (
                        await resolve_task(change.task) if change.task else None
                    )
                touched(card)
            elif isinstance(change, MoveCard):
                card = card_of(change.card, index)
                card.x, card.y = change.x, change.y
                if change.width is not None:
                    card.width = change.width
                if change.height is not None:
                    card.height = change.height
                touched(card)
            elif isinstance(change, DeleteCard):
                card = card_of(change.card, index)
                del cards[card.number]
                # Flushed first, so a line drawn earlier in this request is a
                # row that can be deleted like any other.
                await self._session.flush()
                for pair in [p for p in edges if card.id in p]:
                    await self._session.delete(edges.pop(pair))
                await self._session.delete(card)
            elif isinstance(change, Connect):
                source = card_of(change.source, index)
                target = card_of(change.target, index)
                if source.id == target.id:
                    raise UnprocessableEntityError(
                        say("canvasLineToItself", index=index + 1),
                        data={"index": index},
                    )
                edge = edges.get((source.id, target.id))
                if edge is None:
                    edge = CanvasEdge(
                        canvas_id=canvas.id,
                        source_id=source.id,
                        target_id=target.id,
                        label=change.label,
                        created_by=actor,
                    )
                    self._session.add(edge)
                    edges[(source.id, target.id)] = edge
                else:
                    edge.label = change.label
            elif isinstance(change, Disconnect):
                source = card_of(change.source, index)
                target = card_of(change.target, index)
                edge = edges.pop((source.id, target.id), None)
                if edge is None:
                    raise UnprocessableEntityError(
                        say(
                            "canvasNoLine",
                            source=source.number,
                            target=target.number,
                            index=index + 1,
                        ),
                        data={"index": index},
                    )
                await self._session.delete(edge)
        canvas.updated_at = now
        await self._session.flush()
        return applied


#: Whether the caller may read this document: a canvas of another document
#: is copied only for someone who may read it.
ReadableDocument = Callable[[uuid.UUID], Awaitable[bool]]


@dataclass
class Settled:
    """Markdown written into a document, its canvas blocks each naming a canvas
    of that document; and the canvases made for ``new`` blocks, by title."""

    text: str
    made: list[tuple[uuid.UUID, str]] = field(default_factory=list)


async def settle(
    session: AsyncSession,
    text: str,
    *,
    document_id: uuid.UUID,
    project_id: uuid.UUID,
    actor: str,
    keep: Counter[uuid.UUID],
    readable: ReadableDocument,
) -> Settled:
    """``text``, written into document ``document_id`` from outside an editor,
    with every canvas block naming a canvas of that document, the way an editor
    leaves it: a ``new`` block gets a new, empty canvas; a block naming one of
    the document's canvases keeps it while ``keep`` still counts it (what the
    write replaces held that block — once for a whole document, the edited
    passage's for an edit); any other block, a copy of the canvas it names, as
    pasting a block does. A canvas of another project, or of a document the
    caller may not read, is refused rather than copied."""
    canvases = Canvases(session)
    left = Counter(keep)
    refs: list[uuid.UUID | None] = []
    made: list[tuple[uuid.UUID, str]] = []
    for block in blocks(text):
        if block.canvas_id is None:
            canvas = await canvases.create(document_id=document_id, actor=actor)
            made.append((canvas.id, block.title))
            refs.append(canvas.id)
            continue
        canvas = await canvases.get(block.canvas_id)
        if canvas is not None and canvas.document_id == document_id and left[canvas.id]:
            left[canvas.id] -= 1
            refs.append(canvas.id)
            continue
        if (
            canvas is None
            or await canvases.project_of(canvas) != project_id
            or not await readable(canvas.document_id)
        ):
            raise UnprocessableEntityError(
                say("canvasNotInDocument", canvas=str(block.canvas_id))
            )
        refs.append(
            (await canvases.copy(canvas, document_id=document_id, actor=actor)).id
        )
    if not refs:
        return Settled(text)
    return Settled(renamed(text, refs), made)
