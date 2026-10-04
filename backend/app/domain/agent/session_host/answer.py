"""Asking a session one thing and reading its answer as text, for whoever shows
an answer as it is written (a document's selection box, a person's panel) or
only once it is done (a comment thread).

What is handed on is always a prefix of the answer: the text of every message
the session wrote for this prompt, a blank line between two. What the session
is writing extends it as it streams; a message's record settles what it said,
and whatever the stream did not show is handed on then. The answer ends on the
record the journal keeps of the work's result (``Answer``).

Asking is three steps — the session started (``starting``), the prompt said
(``say``), the answer read (``follow``) — and ``ask`` is the three in a row.
They are apart because the reading can be taken up again by another process
(``consumptions``): what was read so far is a ``Progress`` that outlives the
process that read it, and the next reader goes on from it.
"""

import asyncio
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field, replace

from app.domain.agent.nonce import new_nonce
from app.domain.agent.reads import Ended, Writing
from app.domain.agent.service import AgentMessage, AgentResult, AgentToolUse
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


@dataclass(frozen=True)
class Words:
    """More of the answer: what follows the first ``at`` characters of what
    was handed on so far. ``at`` lets a reader that heard some of it twice —
    a reading taken up again after a restart — put it in its place."""

    text: str
    at: int = 0


@dataclass(frozen=True)
class Tool:
    """The session is calling a tool."""

    name: str


@dataclass(frozen=True)
class Waiting:
    """The session is waiting for the host to have room for it."""


@dataclass(frozen=True)
class Answer:
    """The whole answer, or what failed to arrive."""

    text: str
    error: str | None = None


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
) -> AsyncIterator[Words | Tool | Waiting | Answer]:
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
    #: The records of the messages already folded in, so one read again is
    #: not said twice.
    taken: set[str] = field(default_factory=set)
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
        return [Tool(name)]

    def writing(self, blocks: tuple[dict, ...]) -> list[Words | Tool]:
        said: list[Words | Tool] = []
        for block in blocks:
            if block.get("type") == "tool":
                said += self.tool(block.get("id"), str(block.get("name") or ""))
        written = [str(b.get("text") or "") for b in blocks if b.get("type") == "text"]
        said += self._extend([*self.parts, *written])
        return said

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
            "taken": sorted(self.taken),
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
            taken=set(data.get("taken") or []),
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
    recovered: bool = False,
) -> AsyncIterator[Words | Tool | Answer]:
    """Read the answer to work ``work_id`` on from ``progress``, handing on
    what more of it is written; ends with one ``Answer``. Past ``deadline``
    (on the monotonic clock) the work is stopped and the answer is what failed
    to arrive. ``keep`` is told what was read after each record and before the
    next is asked for, so a reader taking over after this one goes on from no
    earlier than that. ``recovered`` reads a session another process started
    (``host.attach``)."""
    if progress.result is not None:
        yield progress.result
        return
    work = str(work_id)
    reading = host.read(ref, recovered=recovered)

    async def ended(answer: Answer) -> Answer:
        progress.result = answer
        if keep is not None:
            await keep(progress)
        return answer

    try:
        while True:
            left = deadline - time.monotonic()
            try:
                if left <= 0:
                    raise TimeoutError
                # Each read is timed, never the whole loop: a deadline that
                # fell while the caller was handling what was handed on would
                # land in the caller's code.
                read = await asyncio.wait_for(anext(reading), left)
            except StopAsyncIteration:
                yield await ended(Answer("", "the session ended mid-answer"))
                return
            except TimeoutError:
                await host.stop(ref)
                yield await ended(
                    Answer("", "the answer ran past its ceiling and was stopped")
                )
                return
            if read.work_id not in (None, work):
                continue
            event = read.event
            said: list[Words | Tool] = []
            if isinstance(event, Ended):
                yield await ended(Answer("", "the session ended mid-answer"))
                return
            if isinstance(event, Writing):
                said = progress.writing(event.blocks)
            elif read.work_id != work:
                continue
            elif isinstance(event, AgentMessage):
                said = list(progress.message(event.text, read.eid))
            elif isinstance(event, AgentToolUse):
                said = list(progress.tool(event.call_id, event.name))
            elif isinstance(event, AgentResult):
                error = (
                    (event.text or "the model call failed") if event.is_error else None
                )
                await _settle(host, ref, deadline)
                yield await ended(Answer(progress.whole(), error))
                return
            else:
                continue
            for item in said:
                yield item
            if keep is not None:
                await keep(progress)
    finally:
        await reading.aclose()


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
