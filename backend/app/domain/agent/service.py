"""Shared agent events consumed by room persistence and presentation.

Each harness translates its structured protocol into these events. Session
ownership travels with resumable pointers because a delayed event may arrive
after the room has selected another teammate or harness.

``thread_label`` is the contract's answer to "whose work is this" (结论 43): a
string the agent hands its subagent when it spawns one, which the harness then
puts on EVERY event of that sub-thread, unchanged. The platform reads it and
nothing else — the id a harness mints for a worker is its own business, and
carrying the label through is a hard requirement of the contract
(``harness.SubagentRequirement``), so a harness that cannot do it is not one
this deployment runs. Each harness binds the
label to whichever field of its own records rides on a whole sub-thread rather
than on one call; what that field is called there stays inside that harness's
adapter, and the platform never learns the name.

None means the session's own thread: a main thread's records carry no label at
all, so absent IS the answer rather than a gap to reconcile.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass
class AgentMessage:
    """One completed assistant message, at a structural protocol boundary.
    A turn with tool
    calls yields several of these; each becomes its own chat message block
    (Slack-style discrete messages instead of one growing streamed bubble)."""

    text: str
    # The conversation the harness produced it in (FB-56 legacy③): what a
    # session-started turn is stamped with when its row opens.
    session_id: str | None = None
    # Stable per-event id (see AgentToolUse.eid), so a record read twice lands
    # once.
    eid: str | None = None
    # Every id this message was delivered under. Holds eid too when set.
    eids: tuple[str, ...] = ()
    # When 芝士 said this, as the harness recorded it. The timeline is sorted on
    # this, and a record read long after the fact — a reader catching up after
    # a restart — would otherwise file the message at the bottom of a
    # conversation it belongs in the middle of. None where the persist time is
    # already the right one.
    at: datetime | None = None
    # WHICH sub-thread said this, when it was not the session itself. See the
    # module note: the label the agent gave the subagent it spawned, None for
    # the main thread.
    thread_label: str | None = None
    agent_handle: str | None = None


@dataclass
class AgentUserEntry:
    """A native user entry the journal landed: somebody's input, durably.

    The platform's binding point for an input it sent (FB-56): the text may
    carry the input's nonce; ``entry_id`` and ``pos`` are the mirror's own
    identity for the entry, and ``generation`` the mirror's, so the platform
    never has to trust position, page order, or a pre-RPC owner stamp.
    """

    text: str
    entry_id: str
    pos: int
    generation: str
    session_id: str | None = None
    harness: str | None = None
    # The subscription instance that produced this event (FB-56):
    # its identity, not the session's — a replaced subscription's drain
    # is refused at the mutation boundary.
    attachment: str | None = None
    eid: str | None = None
    agent_handle: str | None = None


@dataclass
class AgentToolUse:
    """A platform tool 芝士 invoked (for 施工现场 observability)."""

    name: str
    input: dict[str, Any]
    # Stable per-event id: the harness's own id for the record it came from,
    # so a 现场 event read twice lands once. None where the harness has none.
    eid: str | None = None
    # The HARNESS's own id for this call (Claude Code `tool_use_id`, pi's
    # `toolCall.id`), which is what its later result names. Not the same key as
    # ``eid``: that one identifies the DELIVERY, and a call and its result are
    # two deliveries. None where the harness does not say.
    call_id: str | None = None
    # Which sub-thread did this; None for the session's own (AgentMessage).
    thread_label: str | None = None
    # When the call was made, as the harness recorded it — same contract as
    # AgentMessage.at. Without it a backlog read after a backend handover files
    # every call at its read time, below the words 芝士 wrote after making it.
    at: datetime | None = None
    # Who made the call, as the harness stamped it. Wins over the turn's
    # in-memory bookkeeping, which a backend that took the turn over mid-way
    # does not have.
    agent_handle: str | None = None
    # The conversation the harness ran it in (FB-56 legacy③), same role as
    # AgentMessage.session_id: a session-started turn that opens on tool
    # output is stamped with it, so its row is attributable to that
    # conversation's death evidence — and only that conversation's.
    session_id: str | None = None


#: 一条失败摘要在现场占多少。和分身结论同一个数（``_SUBAGENT_RESULT_MAX``），
#: 理由也一样：房间是给人读的地方。取末尾 —— 命令在最后一行说它为什么不行，
#: 开头往往还是正常的编译日志。
STEP_ERROR_MAX = 500


@dataclass
class AgentStepFailed:
    """A tool call came back an error.

    NOT the tool's return value — only that it failed, and the tail of what it
    said. The room shows one line per step, and a failed step that looks
    exactly like a successful one is the reason a reader has to open the
    transcript to find out whether anything worked.

    Deliberately not an AgentToolResult: that event means "a subagent reported
    its conclusion" and is persisted as its own block. This one has no block of
    its own — it marks the step that is already on the timeline.

    The tail rather than the head: a command that failed says why at the end.
    """

    call_id: str
    text: str = ""
    thread_label: str | None = None


@dataclass
class AgentStepOutput:
    """What a tool call handed back, for the step already on the timeline.

    Like ``AgentStepFailed`` it has no block of its own: it is written onto the
    step it belongs to, where a reader who opens that line finds what the
    command printed. The room persists only a capped, redacted tail of it
    (``step_output``); the harness hands over what it has.
    """

    call_id: str
    text: str = ""
    thread_label: str | None = None


@dataclass
class AgentToolResult:
    """What a tool handed BACK to 芝士 — carried for the subagent tools only.

    Every other tool's return is written onto its own step
    (``AgentStepOutput``), for whoever opens that line. A subagent's is more
    than that: it goes straight into the spawner's context and dies with the
    container's transcript, so the room sees "派了一个分身去查 X" and never what
    the answer was. That is the one return worth an event of its own.

    ``description`` is the spawning call's own one-liner, repeated here so the
    conclusion can be labelled with the question it answers without the UI
    having to pair two blocks up.
    """

    name: str
    text: str
    description: str = ""
    # Stable per-event id, same contract as AgentToolUse.eid.
    eid: str | None = None
    # Which sub-thread SPAWNED this one — not the one it describes. A subagent
    # may spawn its own, and the label on the record is always the thread the
    # tool call was made from.
    thread_label: str | None = None
    # Who made the spawning call; same contract as AgentToolUse.agent_handle.
    agent_handle: str | None = None


@dataclass
class AgentUsage:
    """Token/cost accounting for one turn (spec §9.1/§10.2)."""

    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class AgentSessionInfo:
    """Yielded as soon as the CLI announces the session id — BEFORE the final
    result — so even a turn that dies mid-stream can persist the pointer, and
    '再 @ 一次接着做' truly RESUMES the partial work instead of replaying."""

    session_id: str
    agent_handle: str | None = None
    harness: str | None = None
    # The journal generation the session's mirror lives on (FB-56 epoch):
    # a resume keeps it, a rebuild changes it. None where the harness does
    # not report one.
    generation: str | None = None


@dataclass
class AgentResult:
    """Authoritative final reply plus the session id to resume next time.

    is_error mirrors the SDK ResultMessage's structured flag: the run ended in a
    provider/infra failure (e.g. seat rate-limit) and `text` is that failure's
    detail — NOT something 芝士 said."""

    text: str
    session_id: str | None
    usage: AgentUsage | None = None
    is_error: bool = False
    # Structured failure context (no text sniffing): the failing API call's HTTP
    # status, the CLI's error strings, and — when the seat rate-limit tripped —
    # the RateLimitInfo dict (status / resets_at unix timestamp / type).
    api_error_status: int | None = None
    errors: list[str] | None = None
    rate_limit: dict | None = None
    # Which `platform_failures` classification this is, when the platform raised
    # the failure itself and therefore already knows. Carried rather than
    # re-derived: the alternative is reading back the sentence this same code
    # just wrote, which makes the copy unchangeable.
    failure_code: str | None = None
    # What the failing process printed, when the platform has it (a session
    # that died on its way up). 现场 shows it on the failure notice; the room's
    # line is `text`.
    log: str | None = None
    # Which sub-thread stopped, when the Stop came from one. None for the
    # session's own Stop — the one that ends a turn.
    thread_label: str | None = None
    agent_handle: str | None = None
    harness: str | None = None
    # Successful native main-work completion, not cancellation or synthetic Stop.
    input_work_completed: bool = False
    # A message the session read inside a turn already running has no ending
    # of its own: it ends with that turn, which this names. The room heard
    # that turn end; this ending only settles the message's own work.
    taken_into: uuid.UUID | None = None


@dataclass
class AgentSubagentStart:
    """A subagent the session spawned has begun work.

    A subagent is a second worker inside one session: it has its own context and
    its own tool calls, and everything it does reaches us through the SAME
    stream as the session's own work, told apart by the ``thread_label`` riding
    on each record (see the module note).

    ``agent_id`` is the harness's own name for the worker, and the platform
    keeps it for one job: saying on the card WHICH worker is doing it and
    whether that worker is still alive. It answers no question about which card
    — that is the label's, and only the label's.

    Carried as its own event rather than inferred from the first tool call a
    label makes: a subagent that starts and dies without calling anything is
    invisible under inference, and that is exactly the case a reader needs told.
    """

    agent_id: str
    thread_label: str = ""
    #: The session this subagent belongs to (the spawner's, not its own).
    session_id: str | None = None


@dataclass
class AgentSubagentStop:
    """A subagent finished — but NOT necessarily its work.

    One subagent can report finished more than once: putting a long command in
    its own background and standing by counts as finishing, and resuming it
    produces another Stop later. So this marks "handed something back", never
    "done"; whatever reads it must stay open to a later one for the same id.

    ``text`` is the subagent's closing message verbatim — the answer that
    otherwise reaches only the thread that spawned it and dies with the
    container's transcript.
    """

    agent_id: str
    text: str = ""
    thread_label: str = ""
    #: Path to the subagent's own transcript ON THE MACHINE THAT RAN IT. Present
    #: for a reader that can reach that filesystem; useless to one that cannot,
    #: which is why the closing message is carried in full rather than by
    #: reference to it.
    transcript_path: str | None = None
    session_id: str | None = None


@dataclass
class AgentRetrying:
    """A model request failed and the harness is about to send it again.

    Nothing reaches the room while a harness retries — no message, no tool call
    — so from outside a turn stuck in backoff looks exactly like a turn that is
    thinking hard. This is the harness saying which one it is.

    The counters are the harness's own and any of them may be missing: Claude
    Code reports all of them, Codex says only that it will retry.
    """

    error: str = ""
    attempt: int | None = None
    max_attempts: int | None = None
    delay_ms: int | None = None
    #: The HTTP status of the failed request; None for a connection error.
    status: int | None = None
    #: How long the harness waited without any response before giving up on
    #: this attempt, when it says (Claude Code does, for a request that got none).
    no_response_ms: int | None = None
    thread_label: str | None = None


@dataclass
class AgentCompacting:
    """The session is compacting its context, or has finished doing so.

    A long conversation fills the model's context; the harness then summarises
    it before it can take the next request. That can run for minutes, and the
    session says nothing and answers nothing meanwhile — from outside it looks
    exactly like a session that has hung. ``done`` is False when compaction
    starts and True when it ends; ``error`` is the harness's reason when it
    ended without compacting.
    """

    done: bool = False
    error: str = ""
    thread_label: str | None = None


AgentEvent = (
    AgentMessage
    | AgentToolUse
    | AgentStepFailed
    | AgentStepOutput
    | AgentToolResult
    | AgentSessionInfo
    | AgentResult
    | AgentSubagentStart
    | AgentSubagentStop
    | AgentRetrying
    | AgentCompacting
)


def proves_output(events: list[AgentEvent]) -> bool:
    """Does this batch prove the session is PART-WAY THROUGH a response?

    The rule is "the agent is producing output", and it is deliberately not a
    list of event names: a type added later is covered by it without being
    enumerated. What makes the question worth its own function is what follows
    from a yes — a ``Stop`` is guaranteed to come, so whatever is opened on it
    is guaranteed to be closed again.

    An event that merely HAPPENED is not that. A session coming up, a tool
    returning after the answer was already given, a prompt being typed, a worker
    reporting in — any of those can arrive with no ``Stop`` behind it, and
    whatever was opened on one then stays open forever.

    A batch that carries the ending is not an opening either: nothing is
    in-flight after a ``Stop``.
    """
    if any(isinstance(event, AgentResult) for event in events):
        return False
    return any(
        isinstance(event, AgentMessage | AgentToolUse | AgentToolResult)
        for event in events
    )
