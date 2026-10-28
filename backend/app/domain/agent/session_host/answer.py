"""Asking a session one thing and reading its answer as text, for whoever shows
an answer as it is written (a document's selection box, a person's panel) or
only once it is done (a comment thread).

What is handed on is always a prefix of the answer: the text of every message
the session wrote for this prompt, a blank line between two. What the session
is writing extends it as it streams; a message's record settles what it said,
and whatever the stream did not show is handed on then. The answer ends on the
work's result: the runner's report that the session went idle, or the journal's
result record for a mirrored harness (``Answer``).

Asking is three steps — the session started (``starting``), the prompt said
(``say``), the answer read (``follow``) — and ``ask`` is the three in a row.
They are apart because the reading can be taken up again by another process
(``consumptions``): what was read so far is a ``Progress`` that outlives the
process that read it, and the next reader goes on from it.
"""

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field, replace

from app.domain.agent.nonce import new_nonce
from app.domain.agent.reads import (
    Ended,
    Report,
    SessionExited,
    SessionIdle,
    Writing,
)
from app.domain.agent.service import (
    AgentMessage,
    AgentResult,
    AgentStepFailed,
    AgentToolUse,
    AgentUsage,
)
from app.domain.agent.session_host.contract import (
    Access,
    Prompt,
    SessionRef,
    SessionSpec,
)
from app.domain.agent.session_host.host import SessionHost

#: How long, after the answer is written, the session may go on working
#: (compacting) inside the prompt's window before the reader stops waiting.
SETTLE_S = 60.0
#: How long an answer that was written is read on with nothing more coming
#: before the session is asked whether it stopped.
LOOK_AGAIN_S = 0.2


@dataclass(frozen=True)
class Words:
    """More of the answer: what follows the first ``at`` characters of what
    was handed on so far. ``at`` lets a reader that heard some of it twice —
    a reading taken up again after a restart — put it in its place."""

    text: str
    at: int = 0


@dataclass(frozen=True)
class Tool:
    """The session is calling a tool: ``call`` is the harness's own id for the
    call, when it gives one. ``at`` is how much of the answer's text was
    written before it: a reader shows the call between what was said before
    and after it."""

    name: str
    call: str | None = None
    at: int = 0


@dataclass(frozen=True)
class Thinking:
    """The model started reasoning: what it reasons is never handed on, only
    that it is. ``at`` places it in the answer's text, as ``Tool.at`` does."""

    at: int = 0


@dataclass(frozen=True)
class Subagents:
    """How the branches a person's 芝士 started stand now (``Writing``)."""

    branches: tuple[dict, ...]


@dataclass(frozen=True)
class ToolDone:
    """A call came back without an error: a reader shows it ended now rather
    than when the model next does something. ``shown`` names what it acted
    on (「频道「综合」」), as the tool said it, for a reader to show in place
    of the reference it was given; ``effect`` is what the call does (reads,
    changes, reaches others), which a reader groups its steps by."""

    call: str
    shown: str | None = None
    effect: str | None = None


@dataclass(frozen=True)
class ToolArgs:
    """More of a call's arguments as the model writes them: raw JSON, from its
    ``at``-th character. A reader can show what a call is about to do before
    it is made — a form field filling as the value is written."""

    call: str
    text: str
    at: int = 0


@dataclass(frozen=True)
class ToolFailed:
    """A call came back an error; ``text`` is the tail of what it said."""

    call: str
    text: str


#: What an answer that ran past its ceiling says failed.
PAST_CEILING = "the answer ran past its ceiling and was stopped"

#: What of an answer is handed on while it is written.
type Said = Words | Tool | ToolArgs | ToolFailed | ToolDone | Thinking | Subagents


@dataclass(frozen=True)
class Waiting:
    """The session is waiting for the host to have room for it."""


@dataclass(frozen=True)
class Answer:
    """The whole answer, or what failed to arrive."""

    text: str
    error: str | None = None
    #: What the answer's model calls took, when the session reported it: an
    #: account of the answer, not part of what it is.
    usage: AgentUsage | None = field(default=None, compare=False)


async def ask(
    host: SessionHost,
    ref: SessionRef,
    spec: SessionSpec,
    access: Access,
    prompt: Prompt | Callable[[], Awaitable[Prompt]],
    *,
    work_id: uuid.UUID,
    ceiling_s: float,
    stopped: Callable[[], Awaitable[bool]] | None = None,
) -> AsyncIterator[Said | Waiting | Answer]:
    """Say ``prompt`` and hand on the answer as it is written; ends with one
    ``Answer``. ``prompt`` may be what makes it once the session has started,
    for a prompt whose credential should count from then rather than from
    before a wait for the host. ``Waiting`` comes first when the session has
    to wait for the host to have room for it, a wait ``stopped`` ends
    (``StartAbandoned``).
    Past ``ceiling_s`` from when it is said, the work is stopped and the
    answer is what failed to arrive."""
    async for waiting in starting(host, ref, spec, access, stopped=stopped):
        yield waiting
    # The ceiling is the answer's: a wait for the host is not part of it.
    deadline = time.monotonic() + ceiling_s
    await say(host, ref, prompt, work_id=work_id)
    async for event in follow(
        host, ref, work_id=work_id, deadline=deadline, progress=Progress()
    ):
        yield event


async def starting(
    host: SessionHost,
    ref: SessionRef,
    spec: SessionSpec,
    access: Access,
    *,
    stopped: Callable[[], Awaitable[bool]] | None = None,
) -> AsyncIterator[Waiting]:
    """Have the session running; ``Waiting`` when it has to wait for the host
    first. Raises what the start raised (``HostFull``, ``StartAbandoned``)."""
    waiting: asyncio.Queue[Waiting] = asyncio.Queue()

    async def wait() -> None:
        waiting.put_nowait(Waiting())

    started = asyncio.ensure_future(
        host.start(ref, spec, access, on_wait=wait, give_up=stopped)
    )
    try:
        while not started.done():
            told = asyncio.ensure_future(waiting.get())
            await asyncio.wait({started, told}, return_when=asyncio.FIRST_COMPLETED)
            if told.done():
                yield told.result()
            else:
                told.cancel()
        started.result()
    finally:
        started.cancel()


async def say(
    host: SessionHost,
    ref: SessionRef,
    prompt: Prompt | Callable[[], Awaitable[Prompt]],
    *,
    work_id: uuid.UUID,
) -> None:
    """Say ``prompt`` to the started session as work ``work_id``."""
    said = prompt if isinstance(prompt, Prompt) else await prompt()
    # The marker is how the runner knows which prompt the entries after it
    # answer (`runner.refresh`).
    said = replace(said, text=f"{said.text}\n{new_nonce()}")
    await host.send(ref, said, work_id=work_id)


@dataclass
class Progress:
    """What of one answer was read: the messages the session wrote, what of
    them was handed on, the tools announced, and the result once it was read.
    It is all a reader needs to go on from where another one stopped."""

    parts: list[str] = field(default_factory=list)
    shown: str = ""
    announced: set[str] = field(default_factory=set)
    #: How much of each call's arguments was handed on.
    argued: dict[str, int] = field(default_factory=dict)
    #: The reasoning blocks already told, by message and position.
    thought: set[str] = field(default_factory=set)
    #: The records of the messages already folded in, so one read again is
    #: not said twice.
    taken: set[str] = field(default_factory=set)
    #: The calls told as come back.
    returned: set[str] = field(default_factory=set)
    #: The branches as last told.
    branched: str = ""
    #: The calls whose arguments were told whole, as they were made.
    completed: set[str] = field(default_factory=set)
    result: Answer | None = None

    def whole(self) -> str:
        return "\n\n".join(part for part in self.parts if part)

    def _extend(self, parts: list[str]) -> list[Words]:
        whole = "\n\n".join(part for part in parts if part)
        if not whole.startswith(self.shown) or whole == self.shown:
            return []
        at = len(self.shown)
        more, self.shown = whole[at:], whole
        return [Words(more, at)]

    def tool(self, call: str | None, name: str) -> list[Tool]:
        if not name or (call and call in self.announced):
            return []
        if call:
            self.announced.add(call)
        return [Tool(name, call, len(self.shown))]

    def arguments(self, call: str | None, raw: str) -> list[ToolArgs]:
        """What more of a call's arguments was written since last handed on."""
        if not call or call in self.completed:
            return []
        seen = self.argued.get(call, 0)
        if len(raw) <= seen:
            return []
        self.argued[call] = len(raw)
        return [ToolArgs(call, raw[seen:], seen)]

    def writing(
        self,
        blocks: tuple[dict, ...],
        returned: tuple[str, ...] = (),
        branches: tuple[dict, ...] = (),
        made: tuple[dict, ...] = (),
    ) -> list[Said]:
        """What the message being written says more, block by block in the
        order it is written: words said before a call are handed on before
        it, so the call is placed after them. ``made`` is each call of the
        work as it was made: its arguments whole, which the message may have
        stopped showing before they were all written, and what it read."""
        said: list[Said] = []
        calls = {str(m.get("call")): m for m in made if m.get("call")}
        # A call that came back was made before anything this message says.
        for call in returned:
            if call in self.announced and call not in self.returned:
                self.returned.add(call)
                said += self.complete(calls.get(call))
                made_call = calls.get(call) or {}
                shown, effect = made_call.get("shown"), made_call.get("effect")
                said.append(
                    ToolDone(
                        call,
                        str(shown) if shown else None,
                        str(effect) if effect else None,
                    )
                )
        now = json.dumps(list(branches), ensure_ascii=False, sort_keys=True)
        if branches and now != self.branched:
            self.branched = now
            said.append(Subagents(tuple(branches)))
        written: list[str] = []
        for index, block in enumerate(blocks):
            kind = block.get("type")
            if kind == "text":
                written.append(str(block.get("text") or ""))
                # The message that just landed, still shown a moment after:
                # nothing new.
                if self.parts and self.parts[-1].startswith("\n\n".join(written)):
                    continue
                said += self._extend([*self.parts, *written])
            elif kind == "thinking":
                key = f"{len(self.parts)}:{index}"
                if key not in self.thought:
                    self.thought.add(key)
                    said.append(Thinking(len(self.shown)))
            elif kind == "tool":
                call = block.get("id")
                said += self.tool(call, str(block.get("name") or ""))
                said += self.arguments(call, str(block.get("arguments") or ""))
        for call in calls:
            if call in self.announced:
                said += self.complete(calls[call])
        return said

    def complete(self, made: dict | None) -> list[ToolArgs]:
        """A made call's arguments, whole, once: they replace what was told
        of them as they were written."""
        if not made or made.get("args") is None:
            return []
        call = str(made["call"])
        if call in self.completed:
            return []
        self.completed.add(call)
        args = str(made["args"])
        self.argued[call] = len(args)
        return [ToolArgs(call, args, 0)]

    def message(self, text: str, record: str | None = None) -> list[Words]:
        if record is not None:
            if record in self.taken:
                return []
            self.taken.add(record)
        self.parts.append(text)
        return self._extend(self.parts)

    def to_json(self) -> dict:
        return {
            "parts": self.parts,
            "shown": self.shown,
            "announced": sorted(self.announced),
            "argued": self.argued,
            "thought": sorted(self.thought),
            "taken": sorted(self.taken),
            "returned": sorted(self.returned),
            "branched": self.branched,
            "completed": sorted(self.completed),
            "result": (
                None
                if self.result is None
                else {"text": self.result.text, "error": self.result.error}
            ),
        }

    @classmethod
    def from_json(cls, data: dict) -> "Progress":
        result = data.get("result")
        return cls(
            parts=list(data.get("parts") or []),
            shown=str(data.get("shown") or ""),
            announced=set(data.get("announced") or []),
            argued={str(k): int(v) for k, v in (data.get("argued") or {}).items()},
            thought=set(data.get("thought") or []),
            taken=set(data.get("taken") or []),
            returned=set(data.get("returned") or []),
            branched=str(data.get("branched") or ""),
            completed=set(data.get("completed") or []),
            result=None if result is None else Answer(result["text"], result["error"]),
        )


async def follow(
    host: SessionHost,
    ref: SessionRef,
    *,
    work_id: uuid.UUID,
    deadline: float,
    progress: Progress,
    keep: Callable[[Progress], Awaitable[None]] | None = None,
) -> AsyncIterator[Said | Answer]:
    """Read the answer to work ``work_id`` on from ``progress``, handing on
    what more of it is written; ends with one ``Answer``. Past ``deadline``
    (on the monotonic clock) the work is stopped and the answer is what failed
    to arrive. ``keep`` is told what was read after each record and before the
    next is asked for, so a reader taking over after this one goes on from no
    earlier than that."""
    if progress.result is not None:
        yield progress.result
        return
    work = str(work_id)
    reading = host.read(ref)

    async def ended(answer: Answer) -> Answer:
        progress.result = answer
        if keep is not None:
            await keep(progress)
        return answer

    # An answer written while the session goes on (a person's 芝士 waiting for
    # its branches, then going on with what they found): it ends with what is
    # written by the time the session stops.
    pending: Answer | None = None
    reported = False

    def last() -> Answer:
        assert pending is not None
        return Answer(progress.whole(), pending.error, usage=pending.usage)

    # The next item being read. A look that times out leaves it reading:
    # cancelling it would close the reading itself, and the next look would
    # find it ended — an answer cut off while 芝士 waits for its branches.
    reading_next: asyncio.Future | None = None
    try:
        while True:
            left = deadline - time.monotonic()
            try:
                if left <= 0:
                    raise TimeoutError
                # Each read is timed, never the whole loop: a deadline that
                # fell while the caller was handling what was handed on would
                # land in the caller's code.
                if reading_next is None:
                    reading_next = asyncio.ensure_future(anext(reading))
                done, _ = await asyncio.wait(
                    {reading_next},
                    timeout=left if pending is None else min(left, LOOK_AGAIN_S),
                )
                if not done:
                    raise TimeoutError
                read, reading_next = reading_next.result(), None
            except StopAsyncIteration:
                yield await ended(
                    last() if pending else Answer("", "the session ended mid-answer")
                )
                return
            except TimeoutError:
                if pending is not None and time.monotonic() < deadline:
                    if not await _working(host, ref):
                        yield await ended(last())
                        return
                    continue
                await host.stop(ref)
                yield await ended(Answer("", PAST_CEILING))
                return
            if read.work_id not in (None, work):
                continue
            event = read.event
            said: list[Said] = []
            if isinstance(event, Report):
                # What a runner reported: of all of it, only the ending of the
                # work, or of the session, ends an answer.
                fact = event.fact
                if isinstance(fact, SessionExited) and event.of_this_registration:
                    yield await ended(Answer("", "the session ended mid-answer"))
                    return
                if not isinstance(fact, SessionIdle) or read.work_id != work:
                    continue
                # The runner says the work ended: what it ended with is final.
                reported = True
                event = fact.result
            if isinstance(event, Ended):
                yield await ended(Answer("", "the session ended mid-answer"))
                return
            if isinstance(event, Writing):
                said = [
                    *progress.writing(
                        event.blocks, event.returned, event.branches, event.made
                    )
                ]
            elif read.work_id != work:
                continue
            elif isinstance(event, AgentMessage):
                said = list(progress.message(event.text, read.eid))
            elif isinstance(event, AgentToolUse):
                said = [*progress.tool(event.call_id, event.name)]
                if event.call_id:
                    # The call's whole arguments, once, as it was made: a
                    # reader may have seen none of them, or only their start.
                    said += progress.complete(
                        {
                            "call": event.call_id,
                            "args": json.dumps(event.input, ensure_ascii=False),
                        }
                    )
            elif isinstance(event, AgentStepFailed):
                said = [ToolFailed(event.call_id, event.text)]
            elif isinstance(event, AgentResult):
                error = (
                    (event.text or "the model call failed") if event.is_error else None
                )
                answer = Answer(progress.whole(), error, usage=event.usage)
                if error is not None or reported:
                    await _settle(host, ref, deadline)
                    yield await ended(answer)
                    return
                # Written, and the session may go on (a person's 芝士 waiting
                # for its branches, then going on with what they found): read
                # on until the runner says the work ended, or the session is
                # quiet and stopped.
                pending = answer
                continue
            else:
                continue
            for item in said:
                yield item
            if keep is not None:
                await keep(progress)
    finally:
        if reading_next is not None and not reading_next.done():
            reading_next.cancel()
            await asyncio.gather(reading_next, return_exceptions=True)
        await reading.aclose()


async def _working(host: SessionHost, ref: SessionRef) -> bool:
    status = await host.status(ref)
    return status is not None and status.working


async def _settle(host: SessionHost, ref: SessionRef, deadline: float) -> None:
    """Wait, briefly, for whatever the session does after answering — pi
    compacts a conversation that has grown past its budget right then — so
    that it happens inside the prompt that grew it."""
    until = min(deadline, time.monotonic() + SETTLE_S)
    while time.monotonic() < until:
        status = await host.status(ref)
        if status is None or not status.working:
            return
        await asyncio.sleep(0.2)
