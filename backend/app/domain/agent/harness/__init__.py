"""AgentRuntime — 在一条通道上跑一个 agent，吐结构化事件。

Two questions were one question until now. *Where does this turn run* — a cloud
machine, the user's own laptop, a container next to the backend — is a
``ComputeProvider``. *What runs there* — Claude Code today, something else next
— is an ``AgentRuntime``. They were the same switch because the only harness we
drive is also the only thing that knows how to reach its own machine.

The contract's core is deliberately NOT 「跑一轮，返回一个事件迭代器」. Whoever
holds such an iterator OWNS that turn, and when that process dies the turn is
gone — which is where every piece of salvage machinery came from. Feeding and
reading are separate here:

    ensure     在不在；不在就起
    send       送一条消息进去，回一个「收到了」。不返回事件
    backlog    从游标往后读它说过什么。可重连、可续、可以有多个读者
    interrupt  停手
    close      这条会话不要了

The agent was never the fragile part: claude keeps working inside its machine
while the backend is replaced. What used to die was our BOOKKEEPING, because it
hung off an iterator that died with the process. Reading from a cursor is what
makes recovery a reconnect instead of a salvage operation.

``run_turn`` does hand back an iterator, and is safe for the same reason:
what it iterates is a session that outlives it. Dropping it loses the READING,
never the work — the agent keeps going and ``backlog`` picks the tail up again.
That is the whole difference from the shape this contract replaced, where the
process holding the iterator was the process running the turn.

``send`` is also how a person interrupts with words — a message that arrives
mid-work is not a special case here, it is one more send. ``interrupt`` is the
other thing: take the work away without saying anything.

Three verbs of this contract already had implementations under other names, and
the fourth was a hole with consequences. The platform could stop LISTENING to a
session (drop the subscription) and it could DESTROY one (kill the screen), and
between those two there was nothing — so a turn judged wedged had its backend
coroutine cancelled while the claude on the screen kept going, which is the
whole reason the orphan sweep has to go and ask whether that screen is still
alive. ``interrupt`` is that missing middle.
"""

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from app.domain.agent.service import (
    AgentEvent,
    AgentMessage,
)

if TYPE_CHECKING:
    from app.domain.agent.compute import ComputeProvider


# What the platform hands a runtime so the room can hear it. The vocabulary is
# ``AgentEvent`` — a message, a tool call, a result — which every harness has to
# speak anyway; nothing about these three says how the events were sensed. They
# lived in the Claude Code adapter under names starting with "Hook", which is
# how they were sensed and not what they are.
#
# (project, topic, work id, event, event id, final text already seen, unsolicited)
EventConsumer = Callable[
    [
        uuid.UUID,
        uuid.UUID,
        uuid.UUID,
        AgentEvent,
        str | None,
        bool,
        bool,
    ],
    Awaitable[None],
]

# (project, topic, work id, active) — a session started or stopped working.
ActivityConsumer = Callable[[uuid.UUID, uuid.UUID, uuid.UUID, bool], Awaitable[None]]

# (topic, prompt text) — a session CONSUMED an input we injected. Late by
# design: the write is delivery, this is the receipt.
ReceiptConsumer = Callable[[uuid.UUID, str], Awaitable[None]]

# (topic) → the loop-clock reading at which the OLDEST message we injected and
# have not seen consumed was written, or None when nothing is waiting.
#
# The receipt above answers "did this one land"; this answers "is anything still
# unanswered, and since when". A session that has stopped reading its input can
# go on producing output indefinitely, so nothing else in the liveness picture
# notices it: the hooks keep arriving and the screen stays alive. What it cannot
# do is take the next thing somebody typed, and that is a failure with a person
# on the other end of it.
UnreadProbe = Callable[[uuid.UUID], float | None]


@dataclass(frozen=True, slots=True)
class SessionRef:
    """Which conversation this is, to the harness holding it.

    One per topic today, which is why a topic id names it. When a room can host
    two agents working at once, the pair that identifies a session grows a
    second half and this is the type that grows it — every caller already goes
    through here rather than passing a topic id around.
    """

    project_id: uuid.UUID
    topic_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class HarnessEvent:
    """One thing the agent did, as the harness recorded it.

    ``key`` is this event's place in the log and doubles as the cursor to read
    from next — it orders, and nothing else about it is meaningful. ``eid`` is
    the harness's own id for the event and is what makes landing it twice
    harmless.

    ``age_s`` is how long ago the harness recorded it. A reader waiting for the
    rest of a half-arrived message needs to know whether the rest is still
    coming or the session died mid-sentence, and only a real clock answers that
    — the key is a sequence number, not a time.

    ``record`` is the harness's own note of what happened, and is opaque here on
    purpose: the platform hands it back to ``Backlog.assemble`` rather than
    reading it. None means the entry could not be parsed — a corrupt record must
    not stop the log behind it, so it is reported and stepped over rather than
    skipped silently.
    """

    key: str
    eid: str
    record: object | None
    age_s: float


@dataclass(frozen=True, slots=True)
class Opening:
    """What a session is started with and cannot be told afterwards.

    Carried on every ``send`` rather than set once, because a harness that reads
    its system prompt at launch (Claude Code does) keeps whatever it started
    with — so the opening only matters on the call that turns out to be a cold
    start, and no caller can know in advance which one that is.
    """

    system_prompt: str
    resume_token: str | None = None
    model: str | None = None
    env: dict[str, str] | None = None
    memory_scope: str | None = None
    owner: str | None = None
    agent_handle: str | None = None


@runtime_checkable
class AgentRuntime(Protocol):
    """One harness, driven over whatever channel the compute side opened."""

    # WHICH harness this is — a key in ``HARNESSES``, and what an agent type's
    # ``harness`` field names. Deliberately not ``name``: the objects that
    # implement this today also carry a ``name`` that answers a different
    # question ("which machine pool"), and one attribute cannot mean both.
    harness: str

    async def ensure(
        self, session: SessionRef, opening: Opening, *, work_id: uuid.UUID | None = None
    ) -> object:
        """Is this session live? Start it if not."""
        ...

    async def send(
        self,
        session: SessionRef,
        message: str,
        opening: Opening,
        *,
        work_id: uuid.UUID,
        on_mark: Callable[[uuid.UUID], None],
        images: list[dict] | None = None,
    ) -> bool | None:
        """Put a message into the session. True = the transport took it.

        An ack, not an answer. What the agent does about it arrives through
        ``read`` — possibly minutes later, possibly to a different process than
        the one that sent this.

        ``work_id`` and ``on_mark`` do not belong to this contract and are
        declared anyway, because the only caller passes them and a signature
        that pretended otherwise would be a promise no second harness could
        keep. They are the platform's turn bookkeeping — a turn is still what
        the room shows and what gets billed — and they leave when a turn stops
        being how work is tracked. Same for ``ensure``'s ``work_id``.
        """
        ...

    def backlog(self, session: SessionRef) -> "Backlog":
        """What this session has said that the platform has not landed yet.

        A fresh reader each call, starting from the one cursor the harness keeps
        for the platform. Reading never consumes, so a second reader that keeps
        its own position sees the same tail — that is the whole point of the log
        being a log.
        """
        ...

    async def deliver(
        self, topic_id: uuid.UUID, text: str, images: list[dict] | None = None
    ) -> bool:
        """Put text into a session that is already working, with no turn opened
        for it. True = it landed; False = there is no live session here.

        The bare form of ``send``: no opening, no bookkeeping, nothing to start.
        It is how a person's mid-turn message reaches 芝士, and how the platform
        tells a working session that the world changed under it. The two will be
        one call once a turn stops being how work is tracked; today ``send``
        opens a turn and this does not, so they are still two.

        Keyed by topic rather than by ``SessionRef`` because the caller is on the
        hot path with a person waiting and has no project id in hand — the same
        reason ``close`` is topic-keyed underneath.
        """
        ...

    async def interrupt(self, session: SessionRef) -> bool:
        """Take the work away. True = the stop signal reached the session.

        Not a message — ``send`` is how you interrupt with words. This is for
        when the platform has decided the work should not continue and has
        nothing to say about it: a turn judged wedged, a ceiling reached, a
        person pressing stop.

        Weaker than ``close``, deliberately: the session stays, its conversation
        stays, and the next ``send`` continues it. Killing the screen would take
        the conversation with it.
        """
        ...

    async def close(self, session: SessionRef) -> None:
        """Let this session go: stop listening, forget the channel."""
        ...

    @property
    def hard_ceiling_s(self) -> float:
        """How long a turn on this harness may run before it is called dead.

        A harness fact, not a machine one: how long "no output" may last before
        it means something is wrong depends on what is producing the output. The
        turn path reschedules its own outer wall clock to this rather than to a
        deployment-wide default, so a harness that thinks longer is not killed
        for it.
        """
        ...

    def run_turn(
        self,
        *,
        project_id: uuid.UUID,
        topic_id: uuid.UUID | None,
        prompt: str,
        system_prompt: str,
        resume_session_id: str | None,
        model: str | None = None,
        env: dict[str, str] | None = None,
        memory_scope: str | None = None,
        owner: str | None = None,
        turn_id: uuid.UUID | None = None,
        images: list[dict] | None = None,
        agent_handle: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """One turn, start to finish, as the events it produced.

        ``ensure`` + ``send`` + read, for a caller that has nothing to recover
        to: the platform's OWN errands — the activity digest, the heartbeat
        patrol, the project summary — have no room waiting on them and no
        timeline to backfill, so the turn is worth exactly as much as the
        iterator that reads it.

        A room's turn does NOT go through here. It sends, and reads what comes
        back through the subscription and the backlog, because there the work
        has to survive the reader.
        """
        ...

    def bind_events(self, consumer: EventConsumer) -> None:
        """Where the room's persistence and broadcast live."""
        ...

    def bind_activity(self, consumer: ActivityConsumer) -> None:
        """Where 「这个会话在干活 / 停了」 goes."""
        ...

    def bind_receipts(self, consumer: ReceiptConsumer) -> None:
        """Where 「会话真的读到了那条消息」 goes."""
        ...

    def bind_unread_probe(self, probe: UnreadProbe) -> None:
        """Where 「还有没有消息在等着被读」 is asked."""
        ...

    def holds(self, topic_id: uuid.UUID) -> bool:
        """Is there a session here this runtime can still reach?

        This is what "the work survived" means after a backend restart: the
        coroutine waiting on the turn died with the process, the agent in the
        execution environment did not, and ``recover`` found it again.
        """
        ...

    async def recover(self, device_id: str | None = None) -> list[SessionRef]:
        """Sessions of ours that outlived this process, listening again.

        Called on the way up, and again whenever a machine reconnects. What
        they SAID while nobody was listening is ``replay``'s job — this only
        establishes that we are listening.
        """
        ...

    async def replay(self, session: SessionRef, *, known_texts: set[str]) -> None:
        """Land the tail a recovered session produced while nobody listened.

        ``known_texts`` is what the room already shows, so a message the live
        path did persist before the process died is not landed twice. The
        platform supplies it because the room is the platform's; which of the
        harness's own records are still unlanded is the harness's.
        """
        ...


@runtime_checkable
class Backlog(Protocol):
    """The unread tail of one session, as the platform needs to consume it.

    Six calls, and the split between them is the point. ``unread`` and
    ``assemble`` are the harness's — what did this agent say, and what does one
    log entry mean. Deciding what to DO about it (persist a block, broadcast a
    frame, skip a duplicate) is the platform's, and happens between the two.

    ``assemble`` returns a LIST because one entry is not one thing: a harness
    that reports partial output emits several records per message, and whether
    they add up to something whole is knowledge only it has. That is also why
    ``unfinished`` exists — the platform must not move the cursor past an entry
    whose message is still missing a piece, or the pass that completes it will
    never see what it is made of.
    """

    def unread(self) -> Sequence[HarnessEvent]:
        """Everything after the cursor, oldest first. A snapshot: landing
        things during the pass does not change what this returned."""
        ...

    def assemble(self, entry: HarnessEvent) -> Sequence[AgentEvent]:
        """What this entry means, once anything it completes is folded in.
        Empty = nothing whole yet, or nothing that could be read."""
        ...

    def unfinished(self) -> set[str]:
        """Ids of entries assembled so far whose thing is still incomplete."""
        ...

    def give_up(self) -> Sequence[AgentMessage]:
        """Hand over the incomplete pieces anyway — the session died
        mid-sentence and what arrived is better landed than lost.

        Messages, not events: a tool call is whole the moment it is reported,
        so the only thing that can be caught half-arrived is something the
        agent was still saying."""
        ...

    def landed(self, *, through: str) -> None:
        """Everything up to and including this key reached the timeline."""
        ...

    def forget(self, *, older_than_s: float) -> None:
        """Drop records older than this. Retention removes an event, never
        having been read."""
        ...


def runtime_for(provider: "ComputeProvider") -> AgentRuntime:
    """The harness behind this provider.

    Every provider has one — ``ComputePool`` refuses one that does not, so this
    is where that guarantee is stated rather than a question each caller has to
    handle a None for. It reads structurally instead of by class so the rest of
    the code can ask 「这台机器上跑的是什么」 without naming Claude Code to find
    out.

    There used to be backends with no runtime at all: a subprocess that starts a
    model, streams its output and exits has no session to ensure, nothing to
    send into afterwards, and no log to read from a cursor. That shape is gone.
    """
    if not isinstance(provider, AgentRuntime):
        raise TypeError(f"{type(provider).__name__} runs no harness")
    return provider


# --- which harness ----------------------------------------------------------
#
# ``AgentType.harness`` has existed as a column for a while with nobody reading
# it. This is the reader. A type that names a harness this deployment does not
# have must be refused when it is WRITTEN rather than quietly running Claude
# Code — a stored value nothing honours is how the column got here in the first
# place.

CLAUDE_CODE = "claude-code"
CODEX = "codex"
PI = "pi"


@dataclass(frozen=True, slots=True)
class Harness:
    """One harness, and what it can be pointed at.

    These two facts used to be recorded the other way round — every MODEL
    carried a list of the harnesses allowed to drive it — and the direction was
    backwards in a way that cost real things. It is the harness that can or
    cannot speak to something: Codex supports the models it has adapters for,
    an Anthropic subscription credential is minted for the one harness that can
    present it. A model has no opinion about any of that.

    Written the wrong way round, adding a harness meant editing the model
    catalogue, an editor had to infer the harness from the model the person
    picked, and refusing a combination produced an error about the model — the
    half the person had actually chosen on purpose.
    """

    name: str
    # What a person would call it. Not a display concern: this is the only
    # place the name a human sees is written down.
    label: str
    # Does it speak the platform gateway's own shape? Then every model the
    # project can use is one it can drive, and no deployment has to list them.
    # False means it supports only what it has its own adapter for, and an
    # operator names those in ``agent_harness_models``.
    speaks_gateway: bool = True
    # Can it present an Anthropic subscription credential? That credential is
    # minted for ONE harness; no other can carry it, whatever it can otherwise
    # drive.
    carries_subscription: bool = False
    # Does the agent DRAW on the screen it was started in? Claude Code is a TUI,
    # so its pane is the 现场 — a person watching it sees the work happen. A
    # harness whose screen runs a runner and talks to the agent over RPC has a
    # pane with nothing in it, for ever.
    #
    # Read by the terminal endpoint, which must answer "will the drawer
    # actually show a pane?" and until now answered "is a screen open" — the
    # same thing for a TUI, and not the same thing at all for a runner. The
    # drawer replaces the 施工记录 timeline with the embed on a true, so
    # answering it wrongly is what leaves someone in front of a black frame
    # with no way back to the timeline.
    draws_on_its_screen: bool = True


HARNESSES: dict[str, Harness] = {
    CLAUDE_CODE: Harness(CLAUDE_CODE, "Claude Code", carries_subscription=True),
    # Both of these run a runner as the screen's program and drive the agent
    # over RPC, so neither has a pane worth attaching to.
    CODEX: Harness(CODEX, "Codex", speaks_gateway=False, draws_on_its_screen=False),
    PI: Harness(PI, "pi", draws_on_its_screen=False),
}

# What a type that declines to choose runs on. A type is 出厂设置, not a
# deployment decision — most of them have no opinion about the harness, and the
# null they store means "whatever this platform runs", not "none".
DEFAULT_HARNESS = CLAUDE_CODE


def harness_name(declared: str | None) -> str:
    """The harness a type declared, or the default when it declared none."""
    return declared or DEFAULT_HARNESS


def known_harness(declared: str | None) -> bool:
    """Is this a harness we can actually run? None (undeclared) always is."""
    return declared is None or declared in HARNESSES
