"""AgentRuntime — 在一条通道上跑一个 agent，吐结构化事件。

Two questions were one question until now. *Where does this turn run* — a cloud
machine, the user's own laptop, a container next to the backend — is a
``ComputeProvider``. *What runs there* — Claude Code today, something else next
— is an ``AgentRuntime``. They were the same switch because the only harness we
drive is also the only thing that knows how to reach its own machine.

The contract is deliberately NOT 「跑一轮，返回一个事件迭代器」. Whoever holds
such an iterator owns that turn, and when that process dies the turn is gone —
which is where every piece of salvage machinery came from. Feeding and reading
are separate here:

    ensure     在不在；不在就起
    send       送一条消息进去，回一个「收到了」。不返回事件
    read       从一个游标往后读事件。可重连、可续、可以有多个读者
    interrupt  停手
    close      这条会话不要了

The agent was never the fragile part: claude keeps working inside its machine
while the backend is replaced. What used to die was our BOOKKEEPING, because it
hung off an iterator that died with the process. Reading from a cursor is what
makes recovery a reconnect instead of a salvage operation.

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
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from app.domain.agent.compute import ComputeProvider


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
    harmless. ``payload`` is None for an entry that could not be parsed: a
    corrupt record must not stop the log behind it, so it is reported and
    stepped over rather than skipped silently.

    ``age_s`` is how long ago the harness recorded it. A reader waiting for the
    rest of a half-arrived message needs to know whether the rest is still
    coming or the session died mid-sentence, and only a real clock answers that
    — the key is a sequence number, not a time.
    """

    key: str
    eid: str
    payload: dict | None
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

    def read(
        self, session: SessionRef, *, since: str | None = None
    ) -> Sequence[HarnessEvent]:
        """Events after ``since``, oldest first. None reads from the start.

        A read never consumes: two readers at different cursors both get the
        whole tail. Retention is what eventually removes an event, never having
        been read.
        """
        ...

    def cursor(self, session: SessionRef) -> str | None:
        """How far the platform's own reader has got. None = nothing yet."""
        ...

    def acknowledge(self, session: SessionRef, *, through: str) -> None:
        """Everything up to and including ``through`` has been landed.

        The one cursor the harness keeps, for the platform's own reader. A
        second reader keeps its own ``since`` instead — that is the whole point
        of the log being a log.
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
# it. This is the reader. One entry today, and the registry earns its keep
# anyway: a type that names a harness this deployment does not have must be
# refused when it is WRITTEN rather than quietly running Claude Code — a stored
# value nothing honours is how the column got here in the first place.

CLAUDE_CODE = "claude-code"

# Name → what a person would call it. Not a display concern: the set of keys is
# the set of harnesses that exist, and everything else reads it from here.
HARNESSES: dict[str, str] = {
    CLAUDE_CODE: "Claude Code",
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
