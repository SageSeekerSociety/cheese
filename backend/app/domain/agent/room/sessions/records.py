"""The shapes this package passes around: a seat's session, its clock, its home.

``Live`` is a seat's session as this process started or found it, ``Clock`` is
what one open turn has done lately, ``_home`` names a state directory the way a
person reads it. They live here rather than in the facade because every moved
body builds a ``Live`` or reads a ``Clock`` (``reading``, ``starting``,
``speaking``, ``lifecycle``, ``books``), and the facade cannot define them before
its own siblings are imported.

``app.domain.agent.room.sessions`` re-exports ``Clock`` and ``Live``: a seat's
session is taken through that module path by tests that hold one
(``tests/integration/test_central_room_sessions.py``), and ``Clock`` is what the
facade's own state (``RoomSessions.clocks``) is keyed on.
"""

import uuid
from dataclasses import dataclass, field

from app.domain.agent.harness import SessionRef
from app.domain.agent.harness.driven.subscription import (
    OUTPUT,
    PROGRESS,
    TOOL_RETURNED,
    TOOL_STARTED,
)
from app.domain.agent.session_host.contract import Access, Ending
from app.domain.agent.session_host.contract import SessionRef as CoreRef


@dataclass
class Clock:
    """What one open turn has done lately, on the monotonic clock."""

    opened: float
    progressed: float
    said: float | None = None
    returned: float | None = None
    running: set[str] = field(default_factory=set)
    seen: bool = False

    def pulse(self, marks: frozenset[str], now: float) -> None:
        self.seen = True
        for mark in marks:
            if mark == PROGRESS:
                self.progressed = now
            elif mark == OUTPUT:
                self.said = now
            elif mark.startswith(TOOL_STARTED):
                self.running.add(mark.removeprefix(TOOL_STARTED))
            elif mark.startswith(TOOL_RETURNED):
                self.running.discard(mark.removeprefix(TOOL_RETURNED))
                self.returned = now


@dataclass(frozen=True)
class Live:
    """A seat's session, as this process started or found it."""

    #: The conversation's key in the room (``SessionRef``): what its row,
    #: its resume token and every ref the room builds are written under.
    session: SessionRef
    #: The session in the core.
    ref: CoreRef
    #: The agent it acts as: the teammate a room addressed by instance acts
    #: under the handle its credential names.
    acting: str
    #: The harness's own id for the conversation.
    conversation: str
    #: Which reading of the seat its events come from (FB-56): a reading
    #: that was replaced is refused where the room's books change.
    attachment: str = field(default="", compare=False)


@dataclass
class Outdated:
    """A seat whose runner another release started, found by recovery: it
    speaks a lifecycle protocol this release does not read, so it is not read
    and is given nothing, and it is replaced by one of this release's once its
    ping says it is idle (``lifecycle``)."""

    live: Live
    #: Where it runs, for asking it again.
    access: Access
    #: Its ping said it was working when it was found.
    working: bool
    #: The room's work it was doing then, which it is left to finish.
    work: uuid.UUID | None
    #: The registration it holds, for the interrupt the cap falls back to.
    epoch: int = 0
    #: Which start of it this is (its ping's ``launch``; its conversation for a
    #: runner that names none): what the drain's start is kept under.
    launch: str = ""
    #: How its last few works ended, as its ping last said
    #: (``SessionStatus.endings``).
    endings: tuple[Ending, ...] = ()
    #: The room was told a message waits for it.
    told: bool = False
    #: Somebody took its work away (a Stop, a closed task, the agent removed
    #: from the room): the work ends there, and is not started again.
    stopped: bool = False
    #: It is being settled: what was held for it is let go.
    settling: bool = False


def _home(state: str) -> str:
    """A state directory as the core names it: under the enrollment's root."""
    # A screen launched before state named the enrollment's root still says
    # `.cheese` on its machine, and its runner keeps answering after a deploy;
    # under the default root both spellings are the same directory.
    return state.removeprefix("$HOME/${CHEESE_PROFILE_ROOT:-.cheese}/").removeprefix(
        "$HOME/.cheese/"
    )
