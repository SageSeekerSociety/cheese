"""Asking a session one thing and reading its answer as text, for whoever shows
an answer as it is written (a document's selection box, a person's panel) or
only once it is done (a comment thread).

What is handed on is always a prefix of the answer: the text of every message
the session wrote for this prompt, a blank line between two. What the session
is writing extends it as it streams; a message's record settles what it said,
and whatever the stream did not show is handed on then. The answer ends on the
record the journal keeps of the work's result (``Answer``).
"""

import asyncio
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, replace

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
    """More of the answer: what follows what was handed on so far."""

    text: str


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
    waiting: asyncio.Queue[Waiting] = asyncio.Queue()

    async def wait() -> None:
        waiting.put_nowait(Waiting())

    starting = asyncio.ensure_future(
        host.start(ref, spec, access, on_wait=wait, give_up=stopped)
    )
    try:
        while not starting.done():
            told = asyncio.ensure_future(waiting.get())
            await asyncio.wait({starting, told}, return_when=asyncio.FIRST_COMPLETED)
            if told.done():
                yield told.result()
            else:
                told.cancel()
        starting.result()
    finally:
        starting.cancel()
    # The ceiling is the answer's: a wait for the host is not part of it.
    deadline = time.monotonic() + ceiling_s
    said = prompt if isinstance(prompt, Prompt) else await prompt()
    # The marker is how the runner knows which prompt the entries after it
    # answer (`runner.refresh`).
    said = replace(said, text=f"{said.text}\n{new_nonce()}")
    await host.send(ref, said, work_id=work_id)
    work = str(work_id)
    text = _Text()
    reading = host.read(ref)
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
                yield Answer("", "the session ended mid-answer")
                return
            except TimeoutError:
                await host.stop(ref)
                yield Answer("", "the answer ran past its ceiling and was stopped")
                return
            if read.work_id not in (None, work):
                continue
            event = read.event
            if isinstance(event, Ended):
                yield Answer("", "the session ended mid-answer")
                return
            if isinstance(event, Writing):
                for said in text.writing(event.blocks):
                    yield said
                continue
            if read.work_id != work:
                continue
            if isinstance(event, AgentMessage):
                for said in text.message(event.text):
                    yield said
            elif isinstance(event, AgentToolUse):
                for said in text.tool(event.call_id, event.name):
                    yield said
            elif isinstance(event, AgentResult):
                error = (
                    (event.text or "the model call failed") if event.is_error else None
                )
                await _settle(host, ref, deadline)
                yield Answer(text.whole(), error)
                return
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


class _Text:
    """The answer so far, and what of it has been handed on."""

    def __init__(self) -> None:
        self.parts: list[str] = []
        self.shown = ""
        self.announced: set[str] = set()

    def whole(self) -> str:
        return "\n\n".join(part for part in self.parts if part)

    def _extend(self, parts: list[str]) -> list[Words]:
        whole = "\n\n".join(part for part in parts if part)
        if not whole.startswith(self.shown) or whole == self.shown:
            return []
        more, self.shown = whole[len(self.shown) :], whole
        return [Words(more)]

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

    def message(self, text: str) -> list[Words]:
        self.parts.append(text)
        return self._extend(self.parts)
