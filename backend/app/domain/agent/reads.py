"""What is read from a session: one item at a time, each with the work it
belongs to (``Read``).

The vocabulary of the session core (`session_host`): what its ``read`` yields,
to a question reading its answer and to a room hearing its seats alike. It sits
below the core and below the harness packages it reads with, so neither has to
know the other.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from app.domain.agent.service import AgentEvent, AgentResult
from app.domain.delivery.input_identity import InputIdentity, InputReceipt


@dataclass(frozen=True)
class Writing:
    """What the session is in the middle of writing, as it stands now."""

    blocks: tuple[dict, ...]
    #: Who is writing it, when that is not the session's own name: a room's
    #: seat may act under the handle of the teammate it was addressed as.
    author: str | None = None
    #: The calls of the work under way that have come back so far. It only
    #: grows while the work goes on, so a reader that missed a moment still
    #: learns every call that returned.
    returned: tuple[str, ...] = ()
    #: A person's 芝士's branches the work under way started or moved, as
    #: they stand: each one's id, the call that started it, what it does, how
    #: it is going and its latest steps.
    branches: tuple[dict, ...] = ()
    #: The calls of the work under way as they were made: each one's
    #: ``call``, its whole ``args`` once it is made, and what it read
    #: (``shown``) once it came back, when a harness says so.
    made: tuple[dict, ...] = ()


@dataclass(frozen=True)
class Ended:
    """The session is gone: its runner exited, or stayed out of reach for
    longer than ``SessionSpec.gone_after_s``."""

    reason: str
    #: What its runner recorded of a process that ended before it ever
    #: started, when it recorded that (`Driver.last_words`).
    said: str = ""


@dataclass(frozen=True)
class Abandoned:
    """A work whose session is gone with its inputs still unfinished, and
    nobody left to report on them: the runner that would have reported them
    read or dropped is the process that died. Read with the work it
    belongs to, so the room can end those inputs itself."""


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
class Reachable:
    """The machine the work runs on went out of reach (with what was seen), or
    came back."""

    yes: bool
    reason: str = ""


@dataclass(frozen=True)
class Moved:
    """What one of the session's records says about how its work is going, in
    the liveness vocabulary (``subscription.marks_of``): it said something, a
    tool started or came back."""

    marks: frozenset[str]


@dataclass(frozen=True)
class CaughtUp:
    """Everything the session had written when the reading began is read."""


@dataclass(frozen=True)
class ControlsMoved:
    """What the session's controls show changed: its background tasks."""


# --- what a runner reports in the lifecycle protocol --------------------------
#
# A runner that speaks the protocol (`harness/driven/lifecycle.py`) reports
# facts, and each one is read as a ``Report`` naming the event it came from. Its
# reader applies it and its effects in one transaction (``session_state.take``)
# and only then lets the reading go on; the reading acknowledges it to the
# runner after that.


@dataclass(frozen=True)
class InputWritten:
    """The input is on the agent's input: written, not read yet."""

    identity: InputIdentity


@dataclass(frozen=True)
class InputRead:
    """The agent took the input in: it is delivered."""

    receipt: InputReceipt


@dataclass(frozen=True)
class InputDropped:
    """The agent will never read the input (``reason``, the runner's)."""

    identity: InputIdentity
    reason: str


@dataclass(frozen=True)
class SessionRunning:
    """The session started working, on ``work_id`` (the room's work it is
    attributed to; one the session started on its own when ``unsolicited``)."""

    work_id: uuid.UUID | None
    unsolicited: bool = False


@dataclass(frozen=True)
class SessionIdle:
    """The session went idle. ``result`` is how the work it was doing ended,
    in the room's vocabulary; ``read_inputs`` the platform inputs it read since
    it last went idle, which a failure (``result.is_error``) is attached to."""

    work_id: uuid.UUID | None
    result: AgentResult
    read_inputs: tuple[uuid.UUID, ...] = ()
    #: The failure's kind (`lifecycle`), None for a clean ending.
    failure: str | None = None
    unsolicited: bool = False


@dataclass(frozen=True)
class SessionExited:
    """The agent process ``launch`` is gone. The event carries the epoch that
    process was registered under (a runner also reports the ending of the
    process before it, when that one died with its runner, under that one's
    epoch): only an exit of the seat's current registration ends its session
    (``Report.of_this_registration``)."""

    launch: str
    exit_code: int | None = None
    #: What it said, when it ended before it ever came up.
    said: str = ""


@dataclass(frozen=True)
class Report:
    """One event a runner reported, and where it came from: the seat, the
    epoch of the runner that delivered it, and its own ``(epoch, seq)``."""

    conversation_id: uuid.UUID
    agent_handle: str
    harness: str
    worker_epoch: int
    epoch: int
    seq: int
    at: datetime
    fact: (
        InputWritten
        | InputRead
        | InputDropped
        | SessionRunning
        | SessionIdle
        | SessionExited
    )

    @property
    def of_this_registration(self) -> bool:
        """Whether the event describes the process the delivering registration
        holds: it was written under that registration's epoch. Applying it
        checks the delivering epoch is the seat's current one
        (``session_state.take``), so for an applied report this is whether it
        is about the seat's current process."""
        return self.epoch == self.worker_epoch


@dataclass(frozen=True)
class Replaced:
    """The process holding native session ``conversation`` was replaced by one
    of this release's (a runner another release started). The platform's
    inputs its runner reported its works read (``read``: each work's id and the
    input ids it read) were read, into that work; whatever else it was written
    and has no record of reading, it never will read."""

    conversation: str
    read: tuple[tuple[str, tuple[str, ...]], ...] = ()


@dataclass(frozen=True)
class Compacted:
    """The session compacted its context, and the compaction took: the history
    it was told the session opening in is a summary now. Said with the seat's
    own session, which the event itself does not name."""


@dataclass(frozen=True)
class ResultLanded:
    """The room landed the closing result ``report`` carries: delivered again,
    it is not handed to the room a second time (``runner_reports.landed``)."""

    report: Report


@dataclass(frozen=True)
class Followed:
    """Everything that follows from ``report`` was done, the room's books
    included: delivered again, it asks nothing more (``runner_reports``)."""

    report: Report


@dataclass(frozen=True)
class Read:
    """One thing read from a session."""

    #: The work it belongs to, when the session says.
    work_id: str | None
    event: (
        AgentEvent
        | Writing
        | Ended
        | Abandoned
        | Working
        | Received
        | Report
        | Followed
        | ResultLanded
        | Replaced
        | Compacted
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
