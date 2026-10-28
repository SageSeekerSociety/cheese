"""What a change to a canvas says, from a person's board or an AI teammate's
tool alike: one list of changes, applied in order, all or none."""

import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

#: The longest a card's Markdown may be.
CARD_CHARS = 4000
#: The longest the words on a line may be.
LABEL_CHARS = 200
#: The most cards one canvas holds.
MAX_CARDS = 300
#: The most changes one request carries.
MAX_CHANGES = 100

_COORD = Field(default=None, ge=-100_000, le=100_000)
_SIZE = Field(default=None, ge=40, le=4000)

#: A card, by its number on the canvas (``3``, ``"3"``, ``"[3]"``) or by the
#: ``ref`` an ``add`` earlier in the same request gave it.
CardKey = Annotated[int | str, Field(union_mode="left_to_right")]


class _Change(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class AddCard(_Change):
    op: Literal["add"]
    #: A name for the new card that later changes in this request use.
    ref: str | None = Field(default=None, max_length=64)
    content: str = Field(default="", max_length=CARD_CHARS)
    #: The task it points at: an id, or the task's number in the project.
    task: str | None = Field(default=None, max_length=64)
    x: float | None = _COORD
    y: float | None = _COORD
    width: float | None = _SIZE
    height: float | None = _SIZE


class UpdateCard(_Change):
    op: Literal["update"]
    card: CardKey
    content: str | None = Field(default=None, max_length=CARD_CHARS)
    #: Given as null: the card no longer points at a task. Left out: unchanged.
    task: str | None = Field(default=None, max_length=64)


class MoveCard(_Change):
    op: Literal["move"]
    card: CardKey
    x: float = Field(ge=-100_000, le=100_000)
    y: float = Field(ge=-100_000, le=100_000)
    width: float | None = _SIZE
    height: float | None = _SIZE


class DeleteCard(_Change):
    op: Literal["delete"]
    card: CardKey


class Connect(_Change):
    """A line from one card to another; between two connected cards it
    replaces the words on the line."""

    op: Literal["connect"]
    source: CardKey = Field(alias="from")
    target: CardKey = Field(alias="to")
    label: str = Field(default="", max_length=LABEL_CHARS)


class Disconnect(_Change):
    op: Literal["disconnect"]
    source: CardKey = Field(alias="from")
    target: CardKey = Field(alias="to")


Change = Annotated[
    AddCard | UpdateCard | MoveCard | DeleteCard | Connect | Disconnect,
    Field(discriminator="op"),
]


class ChangesIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    changes: list[Change] = Field(min_length=1, max_length=MAX_CHANGES)


class CanvasPatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: How tall the board is drawn in its document; null is the default.
    height: int | None = Field(ge=160, le=4000)


class CanvasCopyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: The document the copy belongs to.
    document_id: uuid.UUID


# ---- What the routes answer (documentation: `typed_response`) ---------------


class CanvasTaskOut(BaseModel):
    """The task a card points at, as the reader's board shows it."""

    id: uuid.UUID
    number: int | None
    title: str
    room_id: uuid.UUID
    #: The column and phrase the task list shows (`room_task.presentation`).
    column: str
    phrase: str
    #: The teammate working it, whom the phrase may name (「{agent}在做」):
    #: its name, and ``default`` while that is still the 芝士 it was born
    #: with, which a screen says in its reader's language.
    agent_name: str
    agent_name_source: str


class CanvasCardOut(BaseModel):
    id: uuid.UUID
    number: int
    content: str
    x: float | None
    y: float | None
    width: float | None
    height: float | None
    #: None when it points at no task, or at one the reader may not see.
    task: CanvasTaskOut | None
    #: It points at a task the reader may not see.
    task_hidden: bool
    updated_by: str
    updated_at: str


class CanvasEdgeOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: uuid.UUID
    source: uuid.UUID = Field(alias="from")
    target: uuid.UUID = Field(alias="to")
    label: str


class CanvasOut(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    height: int | None
    #: Whether the reader may change it.
    writable: bool
    cards: list[CanvasCardOut]
    edges: list[CanvasEdgeOut]


class CanvasChangedOut(CanvasOut):
    #: The number each new card got, by the ``ref`` its ``add`` gave it.
    refs: dict[str, int]
    #: The numbers the new cards got, in order.
    added: list[int]


class CanvasTextOut(BaseModel):
    id: uuid.UUID
    text: str


class DocumentCanvasTextOut(CanvasTextOut):
    title: str
