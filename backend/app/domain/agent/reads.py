"""What is read from a session: one item at a time, each with the work it
belongs to (``Read``).

The vocabulary of the session core (`session_host`): what its ``read`` yields,
to a question reading its answer and to a room hearing its seats alike. It sits
below the core and below the harness packages it reads with, so neither has to
know the other.
"""

from dataclasses import dataclass

from app.domain.agent.service import AgentEvent
from app.domain.delivery.input_identity import (
    InputReceipt,
    WorkCompletion,
    WorkTermination,
)


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
class Moved:
    """What one of the session's records says about how its work is going, in
    the liveness vocabulary (``subscription.marks_of``): it said something, a
    tool started or came back. ``took`` is an input the session read inside
    the work already running, rather than in work of its own."""

    marks: frozenset[str]
    took: str | None = None


@dataclass(frozen=True)
class CaughtUp:
    """Everything the session had written when the reading began is read."""


@dataclass(frozen=True)
class ControlsMoved:
    """What the session's controls show changed (Claude Code's)."""


@dataclass(frozen=True)
class Read:
    """One thing read from a session."""

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
        | Moved
        | CaughtUp
        | ControlsMoved
    )
    #: The harness's own id for an ``AgentEvent``: what makes landing it twice
    #: harmless.
    eid: str | None = None
    #: An ``AgentResult`` whose text the session already wrote as a message.
    text_seen: bool = False
    #: Work the session started on its own, with nothing said to it.
    unsolicited: bool = False
