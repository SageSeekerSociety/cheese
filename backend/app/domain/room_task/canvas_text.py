"""The tasks a canvas's cards point at, and a document's canvases as the text an
AI teammate reads.

A card that points at a task says which task and whether it has started or
ended, read off the task itself. That is all the text says about it: the finer
column a person's board shows (检查中, 待处理) needs the task's review, which an
AI teammate reaches by opening the task, and a word the text cannot vouch for is
not written.

Who may see which task is the caller's to decide: a card in a document someone
may read can point at a task in a channel they may not, and then its title and
state are not shown to them.
"""

import uuid
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.canvas.block import blocks, canvas_ids
from app.domain.canvas.outline import TaskLine, clipped, outline
from app.domain.canvas.services import Board, Canvases
from app.domain.identity.services import IdentityService
from app.domain.living_doc.models import Document
from app.domain.room_task.models import Task, TaskStatus
from app.domain.room_task.place import Place, living_doc_of
from app.domain.room_task.services import TaskService

#: Which of these channels the reader may read; a task in any other is not
#: named on a card they read.
RoomFilter = Callable[[set[uuid.UUID]], Awaitable[set[uuid.UUID]]]

#: How much of a living document's canvases the system prompt carries.
PROMPT_CANVAS_CHARS = 3000


@dataclass(frozen=True, slots=True)
class LinkedTask:
    id: uuid.UUID
    number: int | None
    title: str
    room_id: uuid.UUID

    #: 进行中 or 已结束: what the task row itself says.
    state: str

    def line(self) -> TaskLine:
        return TaskLine(number=self.number, title=self.title, state=self.state)


def task_state(task: Task) -> str:
    return "进行中" if task.status == TaskStatus.open else "已结束"


async def linked_tasks(
    session: AsyncSession, task_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, LinkedTask]:
    ids = list(dict.fromkeys(task_ids))
    if not ids:
        return {}
    return {
        task.id: LinkedTask(
            id=task.id,
            number=task.number,
            title=task.title,
            room_id=task.room_id,
            state=task_state(task),
        )
        for task in await TaskService(session).list_by_ids(ids)
    }


async def visible_tasks(
    session: AsyncSession, task_ids: Iterable[uuid.UUID], readable: RoomFilter
) -> dict[uuid.UUID, LinkedTask]:
    """The linked tasks whose channel the reader may read."""
    tasks = await linked_tasks(session, task_ids)
    rooms = await readable({task.room_id for task in tasks.values()})
    return {i: task for i, task in tasks.items() if task.room_id in rooms}


async def people_among(session: AsyncSession, handles: Iterable[str]) -> set[str]:
    """Which of these handles are persons, not AI teammates."""
    wanted = sorted(set(handles))
    agents = await IdentityService(session).agents_among(wanted)
    return {handle for handle in wanted if handle not in agents}


async def board_text(
    session: AsyncSession,
    board: Board,
    *,
    title: str,
    readable: RoomFilter,
    positions: bool = False,
) -> str:
    """One canvas as text, naming only the tasks the reader may see."""
    tasks = await visible_tasks(
        session, [c.task_id for c in board.cards if c.task_id is not None], readable
    )
    return outline(
        board.card_texts(),
        board.edge_texts(),
        tasks={task_id: task.line() for task_id, task in tasks.items()},
        people=await people_among(session, [c.updated_by for c in board.cards]),
        title=title,
        canvas_id=board.canvas.id,
        positions=positions,
    )


async def document_canvas_texts(
    session: AsyncSession, document: Document, *, readable: RoomFilter
) -> list[tuple[uuid.UUID, str, str]]:
    """The canvases the document's text names, in order: (id, title, text).
    A block naming a canvas of another document is skipped — the document
    does not own it."""
    canvases = Canvases(session)
    out: list[tuple[uuid.UUID, str, str]] = []
    seen: set[uuid.UUID] = set()
    for block in blocks(document.content or ""):
        if block.canvas_id is None or block.canvas_id in seen:
            continue
        seen.add(block.canvas_id)
        canvas = await canvases.get(block.canvas_id)
        if canvas is None or canvas.document_id != document.id:
            continue
        text = await board_text(
            session, await canvases.board(canvas), title=block.title, readable=readable
        )
        out.append((canvas.id, block.title, text))
    return out


def canvases_section(texts: Sequence[tuple[uuid.UUID, str, str]]) -> str:
    """What a document read by an AI teammate carries after its own text."""
    if not texts:
        return ""
    body = "\n\n".join(text for _, _, text in texts)
    return (
        "\n\n---\n\n文档里的画布（下面是画布的文字版，不在文档原文里：改文档时别引用它；"
        "改画布用 cheese_canvas(action=edit)）\n\n" + body
    )


async def living_doc_canvases(
    session: AsyncSession, place: Place, readable: RoomFilter
) -> str:
    """The canvases of the conversation's living document as text, for the
    system prompt to carry after the document: within `PROMPT_CANVAS_CHARS`
    altogether, each saying where to read the rest."""
    doc = await living_doc_of(session, place)
    if doc is None or not canvas_ids(doc.content or ""):
        return ""
    found = await document_canvas_texts(session, doc, readable=readable)
    if not found:
        return ""
    share = PROMPT_CANVAS_CHARS // len(found)
    return canvases_section(
        [
            (
                canvas_id,
                title,
                clipped(
                    text,
                    share,
                    hint=f"用 cheese_canvas(action=get, canvas={canvas_id}) 读全部",
                ),
            )
            for canvas_id, title, text in found
        ]
    )
