"""Shared agent events consumed by room persistence and presentation.

Each harness translates its structured protocol into these events. Session
ownership travels with resumable pointers because a delayed event may arrive
after the room has selected another teammate or harness.
"""

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
    # Stable per-event id on the hooks path (see AgentToolUse.eid) so the spool
    # reconcile can dedup a backfilled message against its live delivery.
    eid: str | None = None
    # On the hooks path a message arrives as several MessageDisplay flushes,
    # each with its own event id; the assembled message carries every one so
    # dedup (live and reconcile) recognizes any constituent flush. Holds eid
    # too when set. Empty off the hooks path (sdk backend).
    eids: tuple[str, ...] = ()
    # When 芝士 STARTED saying this — the arrival of its first flush, not the
    # moment assembly finished. The two differ by however long the message took
    # to stream, and the timeline is sorted on this: a message is only complete
    # once the event AFTER it arrives, so stamping completion time files every
    # message that precedes a tool call *behind* that tool call. Measured: the
    # room showed 「先看代码链路。」 between the two greps it introduced.
    # None off the hooks path, where the persist time is already the right one.
    at: datetime | None = None
    # WHO produced this, when it was not the session itself: a subagent the
    # session spawned. See the module note on AgentSubagentStart. None means the
    # main thread — the harness leaves the key off entirely there, so absent and
    # "the session" are the same answer.
    agent_id: str | None = None
    agent_type: str | None = None
    agent_handle: str | None = None
    # Every representation of this message shares its ID. Legacy hooks can
    # echo a Stop under another ID and still need text-based recovery dedup.
    complete_identity: bool = False


@dataclass
class AgentToolUse:
    """A platform tool 芝士 invoked (for 施工现场 observability)."""

    name: str
    input: dict[str, Any]
    # Stable per-event id (the hook forwarder's X-Cheese-Event-Id / spool filename).
    # Lets the durable-spool reconcile dedup a backfilled 现场 event against the one
    # the live hook path already persisted. None off the hooks path (sdk backend).
    eid: str | None = None
    # The HARNESS's own id for this call (Claude Code `tool_use_id`, pi's
    # `toolCall.id`), which is what its later result names. Not the same key as
    # ``eid``: that one identifies the DELIVERY, and a call and its result are
    # two deliveries. None where the harness does not say.
    call_id: str | None = None
    # Which subagent did this; None for the session's own thread (AgentMessage).
    agent_id: str | None = None
    agent_type: str | None = None


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
    agent_id: str | None = None


@dataclass
class AgentToolResult:
    """What a tool handed BACK to 芝士 — carried for the subagent tools only.

    Every other tool's return value is already visible in the room through its
    effect (a file changed, a command's output scrolled past). A subagent's is
    not: it goes straight into the spawner's context and dies with the
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
    # Which subagent SPAWNED this one — not the one it describes. A subagent may
    # spawn its own, and the id on the hook is always the thread the tool call
    # was made from.
    agent_id: str | None = None
    agent_type: str | None = None


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
    # Which subagent stopped, when the Stop came from one. None for the
    # session's own Stop — the one that ends a turn.
    agent_id: str | None = None
    agent_type: str | None = None
    agent_handle: str | None = None
    harness: str | None = None


@dataclass
class AgentSubagentStart:
    """A subagent the session spawned has begun work.

    A subagent is a second worker inside one session: it has its own context and
    its own tool calls, and everything it does reaches us through the SAME hook
    stream as the session's own work, distinguished only by ``agent_id`` riding
    on each payload. The main thread's hooks carry no such key at all, so
    "absent" is the session itself rather than an unknown subagent — which is
    what makes the id usable as the sole discriminator.

    Carried as its own event rather than inferred from the first tool call an
    unseen id makes: a subagent that starts and dies without calling anything is
    invisible under inference, and that is exactly the case a reader needs told.
    """

    agent_id: str
    agent_type: str = ""
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
    agent_type: str = ""
    #: Path to the subagent's own transcript ON THE MACHINE THAT RAN IT. Present
    #: for a reader that can reach that filesystem; useless to one that cannot,
    #: which is why the closing message is carried in full rather than by
    #: reference to it.
    transcript_path: str | None = None
    session_id: str | None = None


AgentEvent = (
    AgentMessage
    | AgentToolUse
    | AgentStepFailed
    | AgentToolResult
    | AgentSessionInfo
    | AgentResult
    | AgentSubagentStart
    | AgentSubagentStop
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
