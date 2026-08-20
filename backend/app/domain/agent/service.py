"""Claude Agent SDK integration — 芝士 (spec §8, §9).

Wraps `claude-agent-sdk` to run a resumable, streaming conversation per topic.
We do NOT parse the model's natural-language output (spec §9.1); the platform
observes the agent through structured SDK messages only.

Key SDK facts (verified against installed claude-agent-sdk 0.2.x):
- `StreamEvent.event` carries Anthropic-style streaming deltas when
  `include_partial_messages=True` — we surface `text_delta`s for live UI.
- `AssistantMessage` text blocks are the authoritative final text we persist.
- `ResultMessage.session_id` is the token used to resume the conversation.
"""

from dataclasses import dataclass
from typing import Any


# The cheese CLI rules, injected into every sandbox turn's system prompt (the
# cheese Agent Skill is lazy-loaded and weak models don't self-load it). Read
@dataclass
class AgentMessage:
    """One COMPLETED top-level assistant message (the SDK's AssistantMessage
    boundary — a STRUCTURAL event, never parsed out of prose). A turn with tool
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


@dataclass
class AgentToolUse:
    """A platform tool 芝士 invoked (for 施工现场 observability)."""

    name: str
    input: dict[str, Any]
    # Stable per-event id (the hook forwarder's X-Cheese-Event-Id / spool filename).
    # Lets the durable-spool reconcile dedup a backfilled 现场 event against the one
    # the live hook path already persisted. None off the hooks path (sdk backend).
    eid: str | None = None


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
class AgentDeliveryFailure:
    """The prompt driver on a device screen gave up delivering the turn's
    prompt (#445): the cheeselet reports it over its server-call channel the
    moment it abandons, instead of the failure dying in the connector's local
    journal while the room stares at silence until the 300s bound. The provider
    loop intercepts this event — re-sends the prompt immediately and surfaces a
    visible message — and it is never forwarded to the chat layer."""

    phase: str = ""
    ticks: int = 0


@dataclass
class AgentSessionInfo:
    """Yielded as soon as the CLI announces the session id — BEFORE the final
    result — so even a turn that dies mid-stream can persist the pointer, and
    '再 @ 一次接着做' truly RESUMES the partial work instead of replaying."""

    session_id: str


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


AgentEvent = (
    AgentMessage | AgentToolUse | AgentToolResult | AgentSessionInfo | AgentResult
)
