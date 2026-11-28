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
        return f"$HOME/${{CHEESE_PROFILE_ROOT:-.cheese}}/{self.home}"


#: Carries a teammate's thinking effort through the launch environment to where
#: its session is started, which hands it to the harness as
#: ``ModelSettings.effort`` (``room.sessions.starting``).
EFFORT_ENV = "CHEESE_THINKING_EFFORT"


@dataclass(frozen=True)
class ModelSettings:
    """How the model is called, beyond which model it is. The defaults are the
    provider's own."""

    thinking: bool = True
    #: How hard the model thinks (``gateway.EFFORTS``), one the model honours;
    #: None leaves it to the model. Each harness says it its own way.
    effort: str | None = None
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
    #: With ``tools`` and an execution target: whether the session works on
    #: the target with its harness's own tools, commands included, rather
    #: than only reading it. A person's 芝士 in its own sandbox.
    works: bool = False
    #: With ``tools``: whether it may hand parts of its work to branches of
    #: its own (`Agent`, `SendMessage`, `TaskStop`; a person's 芝士).
    branches: bool = False
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
    #: A conversation it must not take up again, even from what its own state
    #: recorded: given up on, or started under rules its kind has replaced. A
    #: session still running it is started afresh once it is idle. Claude Code
    #: keeps to it; the Codex and pi runners keep one conversation per state
    #: directory and go on with it.
    retired_token: str | None = None
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


#: The context a conversation gone cold has to hold for compacting it first to
#: pay. Cold, its next call re-reads the whole context at full price anyway, so
#: compacting adds only the summary the model writes (some 10–20K tokens) and
#: takes every later call down to the system prompt, tools and that summary
#: (some 30K for a channel's main line). Below about three times that there is
#: little to save, and on a model whose cache lasts minutes nearly every turn
#: starts cold, so a lower bar would compact a conversation over and over. It
#: is also the lowest window Claude Code compacts at on its own
#: (``session_launch._COMPACT_RANGE``): nothing smaller than the harness itself
#: would ever compact is compacted early.
COLD_COMPACT_ABOVE_TOKENS = 100_000


@dataclass(frozen=True)
class ColdCompaction:
    """Compact the conversation before a new turn's input when it has been idle
    ``idle_s`` (its model's cache lifetime) and its context holds at least
    ``above_tokens``. Only the runner knows both of the session's own facts —
    when it last called the model, and how large its context was then — so it
    is the one that decides (Claude Code's ``compaction.when_cold``)."""

    idle_s: float
    above_tokens: int = COLD_COMPACT_ABOVE_TOKENS
    #: The project state the conversation is told again once compacted, as a
    #: conversation that has read its chat holds it: the summary keeps less
    #: than the conversation was told.
    retold: str = ""


@dataclass(frozen=True)
class PrivateText:
    """Words said to a session that only ``owner`` may read in its records: a
    member's `communication` notes, given in a multi-person place
    (`prompts.talk_notes`). The runner puts them in the input as a block of
    their own and stamps every record that carries that block with ``owner``
    (``cheese.private_text``), so `session_record.privacy.for_reader` empties
    it for anyone else. ``held``: the conversation was given this very text
    already, so it is said again only to a conversation started afresh."""

    owner: str
    text: str
    held: bool = False


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
    #: Compact first if the conversation has gone cold, and then say
    #: ``cold_text`` instead of ``text``: what a compacted session is told
    #: again (the whole opening, where ``text`` has only what changed).
    cold: ColdCompaction | None = None
    cold_text: str = ""
    #: Said ahead of ``text``, each as a block only its owner may read in the
    #: session's records (`PrivateText`).
    private: tuple[PrivateText, ...] = ()
    #: What is said ahead of ``cold_text`` instead, when the conversation is
    #: compacted first: every note it held, since a summary keeps none whole.
    cold_private: tuple[PrivateText, ...] = ()


@dataclass(frozen=True)
class Ending:
    """How one work of a runner ended, as its ping says."""

    work: str
    #: It ended in a failure (any typed failure: the model service's, the
    #: runner's own clocks, an interrupt).
    failed: bool
    #: The platform's inputs it read, by input id.
    inputs: tuple[str, ...]


@dataclass(frozen=True)
class SessionStatus:
    """A running session, as its runner says it is now."""

    working: bool
    model: str
    #: The harness's own id for the conversation.
    conversation: str = ""
    #: The work it is doing, while it works.
    work_id: str | None = None
    #: False when the runner answered that its harness process is gone.
    alive: bool = True
    #: The machine answered that this session's runner is not there at all
    #: (``host.attach``). Its process is gone with it.
    runner_gone: bool = False
    #: The runner speaks another lifecycle protocol than this release reads:
    #: one another release started (``host.attach``). It is not read and given
    #: nothing; the room replaces it once it is idle (``room.sessions.lifecycle``),
    #: resuming its conversation.
    outdated: bool = False
    #: The registration the runner holds (its ping's ``worker_epoch``): what a
    #: call this process makes without registering it carries.
    epoch: int = 0
    #: Which start of the runner answered (its ping's ``launch``).
    launch: str | None = None
    #: How the runner's last few works ended (its ping's ``endings``): how a
    #: runner this release does not read says what its work came to.
    endings: tuple[Ending, ...] = ()
    #: ``start`` started the session's process rather than finding it running.
    started: bool = False


class InputUnconfirmed(SessionError):
    """Something said to the session may have reached it: the runner was
    called and no answer came back (``accepted`` False), or it answered and
    what followed failed (``accepted`` True). Saying it again could say it
    twice."""

    def __init__(self, accepted: bool):
        super().__init__("the session may have taken the prompt")
        self.accepted = accepted
