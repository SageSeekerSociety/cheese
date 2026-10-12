"""Reading and writing one task's card — the only code that touches the table.

Who may read or write is the route's question (`api/routes/task_map.py`); here
the card is checked for shape only. The limits keep a card a card: a sentence
per field, a handful of people waited on, two terms. They are not a judgement
of what the teammate wrote.
"""

import uuid
from datetime import UTC, datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import UnprocessableEntityError
from app.core.live_frames import show_once_committed
from app.core.sentences import say
from app.domain.membership.roster import roster
from app.domain.task_map.models import TaskMapCard

#: The frame a task's page hears when its card changes; the page reads it again.
CARD_FRAME_RESOURCE = "task_map_card"

_Line = Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)]


class WaitingOn(BaseModel):
    """One person the task waits on, and what is wanted of them."""

    model_config = ConfigDict(extra="forbid")

    # A project member's handle; a leading @ is dropped.
    handle: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)
    ]
    ask: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)
    ]


class Term(BaseModel):
    """A word a reader may not know, and what it means here."""

    model_config = ConfigDict(extra="forbid")

    term: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)
    ]
    meaning: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)
    ]


class MapCardIn(BaseModel):
    """A change to the card. A field left out stays as it is; an empty string
    or list empties it. The lists are replaced whole."""

    model_config = ConfigDict(extra="forbid")

    what: _Line | None = None
    progress: _Line | None = None
    waiting_on: Annotated[list[WaitingOn], Field(max_length=5)] | None = None
    terms: Annotated[list[Term], Field(max_length=2)] | None = None


def _out(row: TaskMapCard) -> dict:
    return {
        "what": row.what,
        "progress": row.progress,
        "waiting_on": list(row.waiting_on or []),
        "terms": list(row.terms or []),
        "updated_by": row.updated_by,
        "updated_at": row.updated_at.isoformat(),
    }


async def read(session: AsyncSession, task_id: uuid.UUID) -> dict | None:
    """The task's card, or None when nobody has written one yet."""
    row = await session.get(TaskMapCard, task_id)
    return _out(row) if row is not None else None


async def write(
    session: AsyncSession,
    *,
    task_id: uuid.UUID,
    project_id: uuid.UUID,
    change: MapCardIn,
    by: str,
) -> dict:
    """Apply ``change`` to the task's card as ``by`` and return the card.

    A change that leaves every field as it was writes nothing: the card keeps
    its last writer and time, and no page is told."""
    given = change.model_dump(include=change.model_fields_set)
    if given.get("waiting_on"):
        for item in given["waiting_on"]:
            item["handle"] = item["handle"].lstrip("@")
        members = {m.handle for m in await roster(session, project_id)}
        strangers = [
            i["handle"] for i in given["waiting_on"] if i["handle"] not in members
        ]
        if strangers:
            raise UnprocessableEntityError(
                say("taskCardNotMember", handle=strangers[0])
            )
    for key in ("what", "progress"):
        if key in given and given[key] is None:
            given[key] = ""
    for key in ("waiting_on", "terms"):
        if key in given and given[key] is None:
            given[key] = []

    row = await session.get(TaskMapCard, task_id)
    if row is None:
        row = TaskMapCard(
            task_id=task_id, what="", progress="", waiting_on=[], terms=[]
        )
        session.add(row)
    elif all(getattr(row, key) == value for key, value in given.items()):
        return _out(row)
    for key, value in given.items():
        setattr(row, key, value)
    row.updated_by = by
    row.updated_at = datetime.now(UTC)
    await session.flush()
    show_once_committed(
        session,
        task_id,
        {"type": "state", "resource": CARD_FRAME_RESOURCE, "id": str(task_id)},
    )
    return _out(row)


class MapCardOut(BaseModel):
    """The card as written: every field, possibly empty."""

    what: str
    progress: str
    waiting_on: list[WaitingOn]
    terms: list[Term]
    updated_by: str
    updated_at: str


class MapCardView(BaseModel):
    """What the task page reads: the card (None until someone writes one) and
    whether the reader may change it."""

    card: MapCardOut | None
    can_edit: bool
