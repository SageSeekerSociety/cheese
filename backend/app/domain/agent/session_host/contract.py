"""What the session core is asked with and what it reads back.

A session is named by ``SessionRef``: which harness, and where on the session
host it keeps its conversation. It is started by ``SessionSpec`` (what the
process is) and ``Access`` (what it acts with and on); a ``Prompt`` is one thing
said to it. Reading it yields ``Read`` items (`agent/reads.py`), in the order
the session wrote them.

Nothing here knows a room, a document or a person: those are the assemblies'
(``agent.room``, ``agent.document``, ``agent.personal``).
"""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field


class SessionError(RuntimeError):
    """The session could not be started or reached; the message says why."""


class StartRefused(SessionError):
    """The session did not start, in one sentence for whoever asked, chosen
    from what the host recorded (``failure_code``, `platform_failures`), and
    that record itself (``log``)."""

    def __init__(
        self,
        message: str,
        *,
        failure_code: str | None = None,
        log: str | None = None,
    ) -> None:
        super().__init__(message)
        self.failure_code = failure_code
        self.log = log


class HostFull(SessionError):
    """The session host has no memory for one more session right now."""


class StartAbandoned(SessionError):
    """Whoever asked for the session stopped waiting for the host to have room
    for it."""


class InputProtocolUnavailable(SessionError):
    """The session's runner predates the receipt protocol, so nothing can be
    said to it whose reading would be known. Nothing was said; the session,
    its conversation and its pending inputs stay as they are."""

    def __init__(self):
        super().__init__(
            "The original runner needs a receipt protocol upgrade; its "
            "conversation and pending inputs are retained"
        )


@dataclass(frozen=True)
class SessionRef:
    """One session: its harness, and where it lives on the session host.

    ``home`` is a path under the host's ``~/.cheese``. Sessions whose homes
    share a parent directory are counted together (``Footprint.group_limit``);
    the core does not otherwise read it."""

    harness: str
    home: str

    @property
    def state(self) -> str:
        """Its state directory, as the connector expands it."""
        return f"$HOME/.cheese/{self.home}"


@dataclass(frozen=True)
class ModelSettings:
    """How the model is called, beyond which model it is. The defaults are the
    provider's own."""

    thinking: bool = True
    #: The longest answer, in tokens.
    max_tokens: int | None = None
    #: The context the conversation may grow to.
    context_tokens: int | None = None
    #: Compaction: how much of the context is kept free, and how much of the
    #: latest conversation is kept verbatim when the older part is summarised.
    reserve_tokens: int | None = None
    keep_tokens: int | None = None
    #: How long the harness waits before retrying a failed model call, in
    #: milliseconds; each further retry waits twice as long.
    retry_base_delay_ms: int | None = None


@dataclass(frozen=True)
class Footprint:
    """What one session may take of the session host."""

    #: Memory, the harness and its runner together.
    memory_mb: int
    #: How many sessions under the same parent directory may run at once;
    #: starting one more lets the least recently used idle one go.
    group_limit: int


@dataclass(frozen=True)
class SessionSpec:
    """What a session's process is started as.

    A session running from a launch that differs from this one in what the
    process is (its build, arguments, model, tools, skills or execution target)
    is started again from this one once it is idle; while it works, it goes on
    as it is. The system prompt and the resume token take effect on a cold
    start only: a room rebuilds its prompt every turn, and that alone is no
    reason to start anything again."""

    system_prompt: str
    model: str
    #: The tool table's tools it has (`sandbox/cheese`), by name; None is all
    #: of them.
    tools: tuple[str, ...] | None = None
    #: None is the session host's own limits.
    footprint: Footprint | None = None
    #: How long it may sit idle before it exits; None is the runner's own.
    idle_exit_s: float | None = None
    #: How long its runner may be out of reach while something said to it is
    #: unanswered before the reading gives it up (``Ended``).
    gone_after_s: float = 120.0
    #: How long a start waits for the host to have memory for it before it
    #: gives up (``HostFull``); 0 gives up at once.
    host_wait_s: float = 0.0
    model_settings: ModelSettings = ModelSettings()
    #: The conversation it resumes.
    resume_token: str | None = None
    #: What its tools read of where they act (the project, the room).
    env: Mapping[str, str] = field(default_factory=dict)
    #: Who it acts as, when that is a teammate's handle.
    acting: str | None = None
    #: The platform's skills, as files: path to content.
    skills: Mapping[str, str] = field(default_factory=dict)
    #: The marker the platform's instructions to it carry.
    notice: str = ""


@dataclass(frozen=True)
class Owner:
    """Whose session it is, as a harness that keeps one process per owner names
    it: the project, the place in it, and the agent acting there."""

    project_id: uuid.UUID
    place_id: uuid.UUID
    #: What the place's machine and its mirror are kept under (a room's
    #: resource, which a reopened room renews), as the place records it.
    resource_id: str
    #: The session's name in the place (a teammate), and the agent it acts as
    #: there: the two differ when a place addresses a teammate by instance.
    name: str
    handle: str
    #: The agent's account, to open its process as; not needed to reach one
    #: already running.
    user_id: int | None = None
    #: Which seat of the place the session takes (`place.seat_key`), when the
    #: agent may hold more than one there: empty is the agent's own.
    seat: str = ""


@dataclass(frozen=True)
class Image:
    """A picture said along with a prompt."""

    media_type: str
    data: bytes


@dataclass(frozen=True)
class Access:
    """What a session acts with and on.

    ``credential`` is what it calls the model with, and its tools when a prompt
    brings none of its own. ``target`` is the machine its own file tools work
    on, when it has one (`remote_execution`). ``host`` is the session host it
    runs on, when that is not the deployment's own: a session stays on the
    host it was started on.

    ``owner`` is whose session it is, for a harness that keeps one process per
    owner on the host and names it so (Claude Code's screens)."""

    credential: str
    target: dict | None = None
    host: str | None = None
    owner: Owner | None = None


@dataclass(frozen=True)
class Prompt:
    """One thing said to a session."""

    #: Recorded by the caller before it is sent; the session's answer to it is
    #: filed under the work it is sent with.
    id: uuid.UUID
    text: str
    #: What the session's tools act with while answering it, when that is not
    #: the session's own credential: one minted for this prompt.
    acting: str | None = None
    #: Said ahead of ``text`` to a session nobody has spoken to yet: what was
    #: said before the session existed.
    preface: str = ""
    images: tuple[Image, ...] = ()
    #: A person said it, and the session answers them before doing anything
    #: else (`driven/runner.py`).
    owes_reply: bool = False


@dataclass(frozen=True)
class SessionStatus:
    """A running session, as its runner says it is now."""

    working: bool
    model: str
    #: The harness's own id for the conversation.
    conversation: str = ""
    #: The work it is doing, while it works.
    work_id: str | None = None
    #: Whether its runner can take an input whose receipt will be read.
    takes_inputs: bool = True
    #: False when the runner answered that its harness process is gone.
    alive: bool = True
    #: The machine answered that this session's runner is not there at all
    #: (``host.attach``). Its process is gone with it.
    runner_gone: bool = False


class InputUnconfirmed(SessionError):
    """Something said to the session may have reached it: the runner was
    called and no answer came back (``accepted`` False), or it answered and
    what followed failed (``accepted`` True). Saying it again could say it
    twice."""

    def __init__(self, accepted: bool):
        super().__init__("the session may have taken the prompt")
        self.accepted = accepted
