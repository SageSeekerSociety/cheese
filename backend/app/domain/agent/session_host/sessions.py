"""What this process holds of a session, and the facts about one it goes by.

``_Running`` is what the core records of a session it started or found on a
host: the launch it was left at, whether its runner has answered since, where
it is read (``_seat``, ``key``) and, for a harness that keeps one, where this
backend mirrors its journal (``_mirror``).
Beside it the two facts about a session that are read from a name rather than
from a field — whether its harness keeps memory files the platform reconciles
(``keeps_memory``), and whether its model reads pictures (``PICTURES``,
``with_pictures``) — because more than one part of the core decides by them.

``SessionHost`` holds the records; ``host`` re-exports these names.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path

from app.domain.agent import gateway_catalog
from app.domain.agent.session_state import SeatKey
from app.domain.agent.harness import HARNESSES
from app.domain.agent.harness import SessionRef as Seat
from app.domain.agent.harness.driven.reporting import Reading
from app.domain.agent.harness.driven.subscription import Subscription
from app.domain.agent.session_host.contract import (
    Access,
    SessionRef,
    SessionSpec,
)
from app.domain.agent.session_host.driver import Driver, Launched, Mirrors


@dataclass
class _Running:
    """A session this process started or found running."""

    host: str
    launched: Launched
    #: Who reads it: the place it is in, and the agent acting there.
    seat: Seat
    acting: str
    mirror: Path | None
    spec: SessionSpec | None = None
    access: Access | None = None
    #: Whether its runner has answered alive on every read since it was
    #: started or found: what lets a start from the same launch skip the host.
    answering: bool = True
    #: Something said to it is unanswered: its runner going out of reach then
    #: is a reader left waiting (a harness that keeps a mirror; one whose runner
    #: reports in the lifecycle protocol is only ever unreachable).
    owed: bool = False
    #: Whether anything was said to it before this process found it; None
    #: until asked.
    spoken_to: bool | None = None
    #: Cut short a reading's wait when something is said to the session.
    woken: asyncio.Event = field(default_factory=asyncio.Event)
    #: The reading under way, when there is one.
    subscription: Subscription | Reading | None = None
    reading: asyncio.Task | None = None
    stopping: bool = False
    #: The epoch this process's own registration of the runner was issued and
    #: is still being handed to the runner: a refusal naming it is ours, not a
    #: newer registration's (``starting.register``).
    registering: int | None = None


def keeps_memory(harness: str) -> bool:
    """Whether a harness's sessions keep memory as files the platform
    reconciles (``SessionHost.memory``): the one place the registry's bit is
    read, so the system prompt's memory section and the reconciliation cannot
    disagree about it."""
    entry = HARNESSES.get(harness)
    return bool(entry and entry.keeps_memory)


#: Set to "1" for a session whose model reads pictures, so a tool that has
#: some to hand back asks for them (`sandbox/cheese`'s `_app_save`); a
#: session without it is told nothing of pictures.
PICTURES = "CHEESE_PICTURES"


def with_pictures(spec: SessionSpec) -> SessionSpec:
    """``spec`` with its tools told whether its model reads pictures, as the
    gateway says (``gateway_catalog.sees_pictures``)."""
    if not gateway_catalog.sees_pictures(spec.model):
        return spec
    return replace(spec, env={**spec.env, PICTURES: "1"})


def key(ref: SessionRef, seat: Seat, acting: str) -> SeatKey:
    """What the session's runner is registered under (`session_state`): its
    seat, or, for a session no place owns, its home."""
    if seat.conversation_id.int == 0:
        return SeatKey.unowned(ref.home, ref.harness)
    return SeatKey(seat.conversation_id, acting, ref.harness)


def _seat(ref: SessionRef, access: Access) -> tuple[Seat, str]:
    """The session as its subscription names what it reads: the place and the
    session's name in it, and the agent acting there. A session with no owner
    reads into no place."""
    owner = access.owner
    if owner is None:
        nobody = uuid.UUID(int=0)
        return Seat(nobody, nobody, "", harness=ref.harness), ""
    return (
        Seat(owner.project_id, owner.place_id, owner.name, harness=ref.harness),
        owner.handle,
    )


def _mirror(root: Path, ref: SessionRef, access: Access, driver: Driver) -> Path | None:
    """Where this backend keeps its copy of the session's journal, under
    ``root``; None for a harness that keeps none. A session with an owner keeps
    it under the owner's place, by the machine and the agent it acts as there,
    so a reopened place starts a fresh one."""
    if not isinstance(driver, Mirrors):
        return None
    owner = access.owner
    if owner is None:
        return root / ref.home / driver.mirror
    return (
        root
        / str(owner.project_id)
        / str(owner.place_id)
        / ref.harness
        / hashlib.sha256(f"{owner.resource_id}{owner.handle}".encode()).hexdigest()
        / driver.mirror
    )
