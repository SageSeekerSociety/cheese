"""What is read from a session: one item at a time, each with the cursor to read
on from and the work it belongs to (``Read``).

The vocabulary every reader shares, whoever drives the session: the session
core (`session_host`) reading a question's answer, and a room hearing its
sessions through its runtime (``harness.RoomReader``). It sits below both, so
neither has to know the other.
"""

from dataclasses import dataclass

from app.domain.agent.service import AgentEvent
from app.domain.delivery.input_identity import (
    InputReceipt,
    WorkCompletion,
    WorkTermination,
)

#: A position in a session's journal, as the runner names its entries.
Cursor = str


@dataclass(frozen=True)
class Writing:
    """What the session is in the middle of writing, as it stands now."""

    blocks: tuple[dict, ...]
    #: Who is writing it, when that is not the session's own name: a room's
    #: seat may act under the handle of the teammate it was addressed as.
    author: str | None = None


@dataclass(frozen=True)
class Ended:
    """The session is gone: its runner exited, or stayed out of reach for
    longer than ``SessionSpec.gone_after_s``."""

    reason: str


@dataclass(frozen=True)
class Working:
    """The session started or stopped working on the work this is read for."""

    active: bool


@dataclass(frozen=True)
class Received:
    """The session took in something said to it: accepted by its runner, or
    echoed back by the harness itself."""

    receipt: InputReceipt


@dataclass(frozen=True)
class Completed:
    """The harness's own record that the work finished, and which inputs it
    answered."""

    completion: WorkCompletion


@dataclass(frozen=True)
class Terminated:
    """The work ended without completing: taken away, or failed."""

    termination: WorkTermination


@dataclass(frozen=True)
class Reachable:
    """The machine the work runs on went out of reach (with what was seen), or
    came back."""

    yes: bool
    reason: str = ""


@dataclass(frozen=True)
class Read:
    """One thing read from a session, and the cursor to read on from."""

    cursor: Cursor | None
    #: The work it belongs to, when the session says.
    work_id: str | None
    event: (
        AgentEvent
        | Writing
        | Ended
        | Working
        | Received
        | Completed
        | Terminated
        | Reachable
    )
    #: The harness's own id for an ``AgentEvent``: what makes landing it twice
    #: harmless.
    eid: str | None = None
    #: An ``AgentResult`` whose text the session already wrote as a message.
    text_seen: bool = False
    #: Work the session started on its own, with nothing said to it.
    unsolicited: bool = False
