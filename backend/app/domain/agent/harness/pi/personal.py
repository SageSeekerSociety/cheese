"""A person's 芝士 on the session host: one pi session per conversation, with no hands.

It is the same harness a room runs — the pinned pi, its runner, the launch that
leaves the runner going (`launch.py`, `host.configure`), the read that waits at
the runner for news and carries what pi is writing (`driven.runner`) — started
for a conversation instead of a room's seat. What a room needs and a person's
芝士 does not is absent rather than idle: no project, no machine, no topic, no
lease, no screen, no room log. The session is told to use only the tools it is
given, which the platform runs as the person (`runner._platform_tool`).

One conversation is one pi session, under the conversation's id, in a state
directory of its own under the person's (`state_dir`). A session sits idle for
``IDLE_EXIT_S`` and then exits; its conversation stays on disk, and the next
question starts it again on it. A person keeps at most ``SESSIONS_PER_PERSON``
running: starting one more lets their least recently used one go
(`host._make_room`). Each runs under ``MEMORY_MB`` (`host._capped`).

Reading a question's answer is reading the runner's journal from a cursor, as
a room's poller does, without the room's mirror: what pi writes is handed on as
it is written (``Said``, ``Looking``), and the answer ends on the record the
journal keeps of it (``Answered``). The journal outlives the reader, so a reader
that leaves loses the reading, never the answer.
"""

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

from app.core.config import settings
from app.domain.agent.admission import HostMemory
from app.domain.agent.device_hub import DeviceCallError, DeviceOffline
from app.domain.agent.harness.driven.journal import PAGE
from app.domain.agent.harness.pi.events import Assembler, thread_of
from app.domain.agent.harness.pi.launch import arguments, extension, provider, script
from app.domain.agent.service import AgentMessage, AgentResult, AgentToolUse

logger = logging.getLogger(__name__)

#: How long a person's session may sit idle before it exits.
IDLE_EXIT_S = 180.0
#: How many of one person's sessions may run at once.
SESSIONS_PER_PERSON = 2
#: The memory each may use, pi and runner together.
MEMORY_MB = 512
#: The longest answer, in tokens; the model's other limits follow from it.
MAX_TOKENS = 1500
#: The context the session may grow to, and how much of it compaction keeps
#: free: pi summarises the older part of the conversation once a question's
#: context passes ``CONTEXT_TOKENS - RESERVE_TOKENS``, keeping the latest
#: ``KEEP_TOKENS`` verbatim. What a question costs stays bounded however long
#: the conversation runs.
CONTEXT_TOKENS = 32_000
RESERVE_TOKENS = 16_000
KEEP_TOKENS = 6_000
#: How long a launch may take: the first after a pin bump downloads pi.
LAUNCH_TIMEOUT_S = 900
#: How long one read may be held at the runner.
READ_WAIT_S = 25.0
#: How long, after the answer is written, the session may go on working
#: (compacting) inside the question's window before the reader stops waiting.
SETTLE_S = 60.0
#: How long the runner may be out of reach mid-answer before the answer is
#: given up on.
GONE_S = 15.0


class PersonalSessionError(RuntimeError):
    """The session could not be started or reached; the message says why."""


class HostFull(PersonalSessionError):
    """The session host has no memory for one more session right now."""


@dataclass(frozen=True)
class Launch:
    """What a person's session is started with."""

    user_id: int
    conversation_id: uuid.UUID
    system_prompt: str
    #: The platform path its tools are called under, and what they are.
    tools_path: str
    tools: list[dict]
    #: Its personal credential (`sandbox_auth.mint_personal_credential`).
    token: str
    model: str


@dataclass
class Session:
    """One running session, as this process knows it."""

    device_id: str
    state: str
    #: The newest journal entry already read; None before the first read.
    cursor: str | None = None
    #: Whether anyone has asked this session anything yet.
    asked: bool = False
    #: The model the session was started on.
    model: str = ""


@dataclass(frozen=True)
class Said:
    text: str


@dataclass(frozen=True)
class Looking:
    tool: str


@dataclass(frozen=True)
class Answered:
    text: str
    error: str | None = None


def state_dir(user_id: int, conversation_id: uuid.UUID) -> str:
    """Where a conversation's session lives on the session host, as the
    connector expands it; one directory per person, so their sessions are
    siblings (`host._make_room`)."""
    return f"$HOME/.cheese/personal/{user_id}/{conversation_id}"


def api_base() -> str:
    """The backend as the session host reaches it."""
    return (settings.agent_session_api_base or settings.connector_public_base).rstrip(
        "/"
    )


def models(api: str, model: str) -> str:
    """The provider file: the room's, with thinking off and the answer bounded.

    The gateway's deepseek models think unless told not to, and a model that
    thinks spends its output on it; pi says so to them only for a model it
    knows can reason (``reasoning``) and in their dialect (``thinkingFormat``),
    with ``--thinking off`` on the command line choosing the off.
    """
    shape = json.loads(provider(api, model))
    cheese = shape["providers"]["cheese"]
    cheese["compat"]["thinkingFormat"] = "deepseek"
    cheese["models"] = [
        {
            "id": model,
            "reasoning": True,
            "maxTokens": MAX_TOKENS,
            "contextWindow": CONTEXT_TOKENS,
        }
    ]
    return json.dumps(shape, ensure_ascii=False, indent=2)


def pi_settings() -> str:
    return json.dumps(
        {
            "compaction": {
                "reserveTokens": RESERVE_TOKENS,
                "keepRecentTokens": KEEP_TOKENS,
            }
        }
    )


def configuration(launch: Launch) -> dict:
    return {
        "opening": {
            "system_prompt": launch.system_prompt,
            "resume_token": str(launch.conversation_id),
            "model": launch.model,
        },
        "args": [*arguments(launch.model), "--thinking", "off"],
        "execution_target": None,
        "skills": {},
        "extension": extension(),
        "notice": "",
        "idle_exit_s": IDLE_EXIT_S,
        "tools": {"path": launch.tools_path, "specs": launch.tools},
    }


class PersonalSessions:
    """Every person's sessions this backend process talks to."""

    def __init__(self, hub=None, memory: HostMemory | None = None):
        self._hub = hub
        self._memory = memory
        self._sessions: dict[uuid.UUID, Session] = {}
        self._locks: dict[uuid.UUID, asyncio.Lock] = {}

    @property
    def hub(self):
        if self._hub is None:
            from app.domain.agent.device_hub import device_hub

            self._hub = device_hub
        return self._hub

    def available(self) -> bool:
        host = settings.agent_session_device_id
        return bool(host and self.hub.is_online(host))

    # --- the session ---------------------------------------------------------

    async def ensure(self, launch: Launch) -> Session:
        """The conversation's session, running: the one this process already
        reads, one another process started, or one started now."""
        conversation = launch.conversation_id
        lock = self._locks.setdefault(conversation, asyncio.Lock())
        async with lock:
            known = self._sessions.get(conversation)
            if known is not None and known.model == launch.model:
                return known
            host = settings.agent_session_device_id
            if not host or not self.hub.is_online(host):
                raise PersonalSessionError("The session host is not connected")
            session = Session(
                host, state_dir(launch.user_id, conversation), model=launch.model
            )
            running = await self._ping(session)
            if running is None or running.get("model") != launch.model:
                await self._start(session, launch)
            await self._catch_up(session)
            self._sessions[conversation] = session
            return session

    def forget(self, conversation_id: uuid.UUID) -> None:
        self._sessions.pop(conversation_id, None)

    async def _ping(self, session: Session) -> dict | None:
        try:
            status = await self.call(session, "ping", {}, timeout=15)
        except (DeviceOffline, DeviceCallError, TimeoutError):
            return None
        return status if status.get("alive") else None

    async def _start(self, session: Session, launch: Launch) -> None:
        memory = self._memory or HostMemory(self.hub)
        if not await memory.can_start(MEMORY_MB):
            raise HostFull("The session host has no memory for another session")
        api = api_base()
        program = script(
            state=session.state,
            config=configuration(launch),
            api_base=api,
            model=launch.model,
            env={"CHEESE_API": api, "CHEESE_TOKEN": launch.token},
            models=models(api, launch.model),
            host={
                "group_limit": SESSIONS_PER_PERSON,
                "memory_max": f"{MEMORY_MB}M",
                "settings": pi_settings(),
            },
        )
        started = time.monotonic()
        try:
            result = await self.hub.exec(
                session.device_id,
                ["python3", "-"],
                stdin=program,
                timeout=LAUNCH_TIMEOUT_S,
            )
        except (DeviceOffline, DeviceCallError, TimeoutError) as exc:
            raise PersonalSessionError(
                f"The session could not be started: {exc}"
            ) from exc
        if result.get("exit") != 0 or result.get("truncated"):
            raise PersonalSessionError(
                (result.get("stderr") or "pi did not start").strip()[-600:]
            )
        logger.info(
            "personal session started conversation=%s in %.2fs",
            launch.conversation_id,
            time.monotonic() - started,
        )

    async def call(
        self, session: Session, method: str, params: dict, *, timeout: float = 660
    ) -> dict:
        return await self.hub.call_executor(
            session.device_id, session.state, method, params, timeout=timeout
        )

    async def _entries(self, session: Session, **params) -> dict:
        return await self.call(session, "entries", {"since": session.cursor, **params})

    async def _catch_up(self, session: Session) -> None:
        """Read the journal to its end: where the next answer starts, and
        whether the session was ever asked anything (a session started for a
        conversation that has history somewhere else has not)."""
        while True:
            page = (await self._entries(session))["entries"]
            for record in page:
                session.cursor = record.get("id") or session.cursor
                message = record.get("message") or {}
                if not thread_of(record) and message.get("role") == "user":
                    session.asked = True
            if len(page) < PAGE:
                return

    # --- a question ----------------------------------------------------------

    async def _send(
        self, launch: Launch, work: str, text: str, earlier: str
    ) -> Session:
        """Put the question into the conversation's session, starting it again
        when the one this process knew has gone (it exits once idle).

        ``earlier`` is what was said in this conversation before its session
        existed; a session nobody has asked anything is given it once, ahead
        of the question."""
        for attempt in range(2):
            session = await self.ensure(launch)
            prompt = f"{earlier}\n\n{text}" if earlier and not session.asked else text
            try:
                await self.call(
                    session,
                    "send",
                    {"input_id": work, "work_id": work, "text": prompt},
                )
            except (DeviceOffline, DeviceCallError) as exc:
                self.forget(launch.conversation_id)
                if attempt:
                    raise PersonalSessionError(
                        f"The session did not take the question: {exc}"
                    ) from exc
                continue
            session.asked = True
            return session
        raise AssertionError("unreachable")

    async def ask(
        self,
        launch: Launch,
        work_id: uuid.UUID,
        text: str,
        *,
        earlier: str = "",
        ceiling_s: float,
    ) -> AsyncIterator[Said | Looking | Answered]:
        """Put one question in, and hand on its answer as it is written.

        Ends with one ``Answered``. Past ``ceiling_s`` the work is taken away
        and the answer is what failed to arrive."""
        work = str(work_id)
        deadline = time.monotonic() + ceiling_s
        session = await self._send(launch, work, text, earlier)
        reading = _Reading(work)
        mark: str | None = None
        failing_since: float | None = None
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                await self._abort(session)
                yield Answered("", "the answer ran past its ceiling and was stopped")
                return
            try:
                answer = await self._entries(
                    session, wait=min(READ_WAIT_S, left), live=mark
                )
            except (DeviceOffline, DeviceCallError, TimeoutError) as exc:
                # The connection to the host blinks; a runner that is gone
                # stays gone, and the answer with it.
                now = time.monotonic()
                failing_since = failing_since or now
                if now - failing_since > GONE_S:
                    self.forget(launch.conversation_id)
                    logger.warning("personal session out of reach: %s", exc)
                    yield Answered("", "the session ended mid-answer")
                    return
                await asyncio.sleep(1)
                continue
            failing_since = None
            live = answer.get("live")
            if isinstance(live, dict):
                mark = live.get("mark")
                if live.get("work_id") in (None, work):
                    for event in reading.live(list(live.get("blocks") or [])):
                        yield event
            ended = None
            for record in answer.get("entries") or []:
                session.cursor = record.get("id") or session.cursor
                for event in reading.record(record):
                    if isinstance(event, Answered):
                        ended = event
                    else:
                        yield event
            if ended is not None:
                await self._settle(session, deadline)
                yield ended
                return
            if not answer.get("alive", True):
                self.forget(launch.conversation_id)
                yield Answered("", "the session ended mid-answer")
                return

    async def _settle(self, session: Session, deadline: float) -> None:
        """Wait, briefly, for whatever the session does after answering — pi
        compacts a conversation that has grown past its budget right then — so
        that it happens inside the question that grew it."""
        until = min(deadline, time.monotonic() + SETTLE_S)
        while time.monotonic() < until:
            status = await self._ping(session)
            if status is None or not status.get("working"):
                return
            await asyncio.sleep(0.2)

    async def _abort(self, session: Session) -> None:
        try:
            await self.call(session, "abort", {}, timeout=15)
        except (DeviceOffline, DeviceCallError, TimeoutError):
            logger.warning("aborting a personal session failed", exc_info=True)


class _Reading:
    """One answer, as it is written and as the journal records it.

    What has been handed on is always a prefix of the answer: the text of
    every message the session wrote for this question, a blank line between
    two. What pi is writing extends it as it streams (``live``); a message's
    record settles what it said (``record``), and whatever the stream did not
    show is handed on then.
    """

    def __init__(self, work: str):
        self.work = work
        self.assembler = Assembler()
        self.parts: list[str] = []
        self.shown = ""
        self.announced: set[str] = set()

    def _extend(self, parts: list[str]) -> list[Said]:
        whole = "\n\n".join(part for part in parts if part)
        if not whole.startswith(self.shown) or whole == self.shown:
            return []
        more, self.shown = whole[len(self.shown) :], whole
        return [Said(more)]

    def _announce(self, call: str | None, name: str) -> list[Looking]:
        if not name or (call and call in self.announced):
            return []
        if call:
            self.announced.add(call)
        return [Looking(name)]

    def live(self, blocks: list[dict]) -> list[Said | Looking]:
        events: list[Said | Looking] = []
        for block in blocks:
            if block.get("type") == "tool":
                events += self._announce(block.get("id"), str(block.get("name") or ""))
        writing = [str(b.get("text") or "") for b in blocks if b.get("type") == "text"]
        events += self._extend([*self.parts, *writing])
        return events

    def record(self, record: dict) -> list[Said | Looking | Answered]:
        if (record.get("cheese") or {}).get("work_id") != self.work:
            return []
        events: list[Said | Looking | Answered] = []
        for event in self.assembler.accept(record):
            if isinstance(event, AgentMessage):
                self.parts.append(event.text)
                events += self._extend(self.parts)
            elif isinstance(event, AgentToolUse):
                events += self._announce(event.call_id, event.name)
            elif isinstance(event, AgentResult):
                answer = "\n\n".join(part for part in self.parts if part)
                events.append(
                    Answered(
                        answer,
                        (event.text or "the model call failed")
                        if event.is_error
                        else None,
                    )
                )
        return events
