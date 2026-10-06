"""The part of a session-machine runner that does not depend on what it drives.

A runner owns one agent process and outlives every backend that reads from it.
What it does the same way for any harness: hold the state directory's lock,
answer one JSON line per connection on the socket the connector relays to,
accept each input at most once however many times a reconnecting backend asks,
hold a session to answering a person before it does anything else, and let a
session go once it has sat idle.
"""

import asyncio
import contextlib
import fcntl
import hashlib
import json
import os
import sys
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Generic, TypeVar

from app.domain.agent.harness.driven.journal import Journal

# Not PEP 695 syntax: this module runs on the session machine's interpreter.
J = TypeVar("J", bound=Journal)


@dataclass(frozen=True)
class SessionStart:
    """What a runner starts its harness's session with, read once, at launch:
    a session that is merely reused keeps the one it started with."""

    system_prompt: str
    resume_token: str | None = None
    model: str | None = None
    #: The agent it acts as, when a room addressed a teammate.
    agent_handle: str | None = None


# --- letting an idle session go ------------------------------------------------
#
# A session nobody is talking to holds a process on the session machine all the
# same, and a backend goes on reading every one of them whether anyone talks to
# it or not. So reading is not what keeps a session: what it is doing is. With
# no turn open, nothing of its own still running, no input on its way in and no
# record written either way for ``IDLE_EXIT_S``, and nothing left that a backend
# still reading has yet to take, the runner lets it go. Its conversation stays
# on disk, and the next message starts it again on it (each harness's
# ``ensure`` resumes what the state directory recorded).

IDLE_EXIT_S = 600.0
#: How often the runner looks.
IDLE_CHECK_S = 5.0


# --- a read that waits for news ------------------------------------------------
#
# A backend reads a session by asking for the journal past its cursor. Asked to
# wait, the runner holds the question until there is something to answer it
# with — a record past that cursor, or a change in what the agent is in the
# middle of writing — or the wait is up, so a backend spends one read per wait
# on a quiet session and hears a record the moment it is written. Once there is
# news the answer waits ``READ_BATCH_S`` more, so a stream of tokens arrives a
# few times a second rather than once per token.
#
# What the agent is in the middle of writing is the block it is generating now
# — text, or a tool call with its arguments as far as they have streamed — and
# it lives only here: it is never journaled, and the record of the finished
# block replaces it (each harness says when). Reasoning is not shown.

#: The longest a read may be held, whatever the backend asked.
READ_WAIT_MAX_S = 60.0
#: How long an answer that has news waits for more of it.
READ_BATCH_S = 0.05
#: What a runner of this build can do that one started by an older deployment
#: cannot. The backend reads them from ``ping`` when it greets the runner.
LONG_POLL = "long_poll"
LIVE = "live"


def socket_path(state: Path) -> str:
    """Where the connector will look, given this state directory.

    The name is not ours to choose: ``cli/internal/host/executor.go`` derives it
    from the state directory the backend recorded and relays one JSON line each
    way. Matching it is what lets ``hub.call_executor`` reach this runner with no
    connector change at all.
    """
    digest = hashlib.sha256(str(state.resolve()).encode()).hexdigest()[:24]
    return f"/tmp/cheese-execution-{os.getuid()}-{digest}.sock"


# --- a person waiting for an answer -------------------------------------------
#
# A message a person sends into a room is answered in the room before the session
# does anything else. Telling the model so was not enough: it would read the
# message at a tool boundary and go straight on with the tool it had planned,
# and the person saw nothing until that work was done. So the harness refuses
# every other tool until the session has published.
#
# The rule is decided here, once for every harness: which input owes an answer
# (the platform says so when it delivers a person's message; a platform notice
# owes nothing), from when (the moment the harness accepts it), what answers it
# (`REPLY_TOOLS`), what may still be read on the way to answering
# (`REPLY_READS`), what the refusal says (`REPLY_OWED`), and when it lapses (the
# turn ends with nothing unread). What differs is only where each harness lets a
# tool call be refused, so each harness's tool path reads the file below and
# refuses on its word: Claude Code's function hook (`remote_execution/
# proxy.js`), Codex's dynamic-tool handler (`codex/tools.py`) and pi's extension
# (`pi/platform.ts`). A subagent is never held to it: it reports to the agent
# that started it, not to the room.
#
# Reading the room is part of answering it, so the room's own record stays
# open. The refusal can reach the model before the person's words do, and a
# person's message is often only a mention pointing at what they wrote above
# it: with the room closed too, the session could only say that it had not
# read them yet. Every other read stays refused — a file, a command, the
# project's status: that is the work the person was waiting behind.
#
# Nor may the turn simply end with the person unanswered — a model that calls no
# tool at all is refused nothing. The tool path that lets the reply through
# writes down that it did (the file named in the debt, `answered`), and when the
# turn is about to end the runner asks `insist`: still owed, and the session is
# held to it once more (`REPLY_INSIST`). How a turn is kept going is each
# harness's own — Claude Code's Stop hook, a new turn inside the same work for
# Codex, a turn of the session's own for pi — and the rule is here. Once is the
# limit: a session that still does not answer is let go, and the runner's log
# says so.

#: The variable that names the file to the agent process and whatever it spawns.
REPLY_OWED_ENV = "CHEESE_REPLY_OWED"
#: The platform tools that answer a person, by their name in the tool table.
REPLY_TOOLS = ("chat_send", "cheese_ask")
#: The platform tools that read the room's own messages: let through, and no answer.
REPLY_READS = (
    "cheese_chat_list",
    "cheese_chat_get",
    "cheese_chat_search",
    "cheese_chat_replies",
)
REPLY_OWED = (
    "A person in this conversation has sent a message you have not answered yet. "
    "Reply to it in the conversation with chat_send first (or ask them with "
    "cheese_ask): answer it if you can; otherwise say what you understood and "
    "what you will do next — and if they asked you to stop, stop. If you have "
    "not seen what they wrote, read the conversation with cheese_chat_list (or "
    "cheese_chat_get / cheese_chat_search / cheese_chat_replies); every other "
    "tool is refused until you have replied."
)


REPLY_INSIST = (
    "You are about to stop, but the person who wrote to you in this room has "
    "had no reply there. Reply to them now with chat_send (or ask them with "
    "cheese_ask) before you finish; anything you only write here, they never "
    "see."
)


def reply_owed_path(state: Path) -> Path:
    return state / "reply-owed.json"


def reply_answered_path(state: Path) -> Path:
    return state / "reply-answered"


def reply_owed(path: str | Path | None) -> dict | None:
    """What the file says is owed, or None when nothing is.

    ``{"id": the input that owes it, "answers": REPLY_TOOLS, "reads":
    REPLY_READS, "reason": REPLY_OWED}`` — everything a tool path needs to
    refuse, so none of them
    keeps a copy of the rule. Unreadable is nothing owed: a refusal nobody can
    explain would stop the session for good.
    """
    if not path:
        return None
    try:
        owed = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None
    return owed if isinstance(owed, dict) and owed.get("id") else None


class Runner(Generic[J]):  # noqa: UP046
    def __init__(
        self,
        state: Path,
        journal: type[J],
        name: str,
        *,
        idle_exit_s: float = IDLE_EXIT_S,
    ):
        state.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.state = state
        self.journal = journal(state / name)
        self.process: asyncio.subprocess.Process | None = None
        self.listener: asyncio.Task | None = None
        self.server: asyncio.Server | None = None
        self.inputs: dict[str, asyncio.Task] = {}
        self.errors = None
        self.lock = None
        # The input a person is waiting on an answer to, while one is.
        self.owed: str | None = None
        # Debts the session has already been held to once at a turn's end.
        self.insisted: set[str] = set()
        # 0 keeps the session however long it sits idle.
        self.idle_exit_s = idle_exit_s
        self.active_at = time.monotonic()
        # How far a backend has read the journal, and when it last did.
        self.read_through = 0
        self.read_at = time.monotonic()
        self.idler: asyncio.Task | None = None
        self.ender: asyncio.Task | None = None
        # Set, and replaced, whenever there is news for a waiting read.
        self.news = asyncio.Event()
        self.journal.on_grow = self._grew
        self.closing = False
        # What the agent is writing right now, and the work it is for; the
        # mark changes with every change of it, and from one runner to the next.
        self.live_blocks: list[dict] = []
        self.live_work: str | None = None
        self.live_epoch = uuid.uuid4().hex[:12]
        self.live_count = 0

    def claim(self) -> None:
        """Take the state directory, or fail if another runner holds it."""
        self.lock = (self.state / "runner.lock").open("a")
        fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Only the lock owner may remove a socket a crashed runner left behind.
        Path(socket_path(self.state)).unlink(missing_ok=True)
        # Nor may a debt outlive the runner that recorded it: the next one
        # starts a session that has not been told about it.
        reply_owed_path(self.state).unlink(missing_ok=True)
        reply_answered_path(self.state).unlink(missing_ok=True)

    # --- a person waiting for an answer --------------------------------------

    def agent_env(self, env: dict[str, str]) -> dict[str, str]:
        """The agent's environment, with the file its tool path reads named."""
        return {**env, REPLY_OWED_ENV: str(reply_owed_path(self.state))}

    def owe_reply(self, identifier: str) -> None:
        """The session has a person's message in hand: hold it to answering."""
        path = reply_owed_path(self.state)
        written = path.with_name(path.name + ".next")
        written.write_text(
            json.dumps(
                {
                    "id": identifier,
                    "answers": list(REPLY_TOOLS),
                    "reads": list(REPLY_READS),
                    "reason": REPLY_OWED,
                    # Where the tool path writes the id down once it lets a
                    # reply through (`insist`).
                    "answered": str(reply_answered_path(self.state)),
                }
            )
        )
        written.replace(path)
        self.owed = identifier

    def reply_settled(self) -> None:
        """The turn is over: whatever it owed, it no longer holds a tool back."""
        self.owed = None
        reply_owed_path(self.state).unlink(missing_ok=True)
        reply_answered_path(self.state).unlink(missing_ok=True)

    def insist(self) -> str | None:
        """Asked as a turn is about to end: what to hold the session to, if
        anything — `REPLY_INSIST` while a person is still unanswered, once per
        message. A session that ends a second time without answering is let
        go, and said so here, rather than kept going round."""
        if self.owed is None:
            return None
        try:
            answered = reply_answered_path(self.state).read_text().strip()
        except OSError:
            answered = ""
        if answered == self.owed:
            return None
        if self.owed in self.insisted:
            print(
                f"turn ended without a reply to {self.owed} after being held to it",
                file=sys.stderr,
                flush=True,
            )
            return None
        self.insisted.add(self.owed)
        return REPLY_INSIST

    async def yield_foreground(self) -> None:
        """Give the model its turn back: what it is waiting on moves to the
        background and keeps running there.

        A message reaches the model only between tool calls, so a person who
        writes while a long command runs would otherwise wait for it to end.
        What can be moved and how is each harness's own (Ctrl+B's control for
        Claude Code, the executor's for Codex's Bash, pi's shell tool as the
        platform extension runs it); a file edit or an MCP call cannot be, and
        is short. A tool the model starts after this is refused until it has
        answered, so nothing new takes the message's place.
        """
        raise NotImplementedError

    async def listen(self, limit: int) -> None:
        """Open the socket; last, so a backend that reaches it finds a session."""
        self.server = await asyncio.start_unix_server(
            self.handle, path=socket_path(self.state), limit=limit
        )
        os.chmod(socket_path(self.state), 0o600)
        if self.idle_exit_s:
            self.idler = asyncio.create_task(self._idle())
        if self.process is not None:
            self.ender = asyncio.create_task(self._announce_exit(self.process))

    async def _announce_exit(self, process: asyncio.subprocess.Process) -> None:
        """The agent process ended: a read held now is answered at once, saying
        so (``alive``), whether or not the process wrote anything first."""
        await process.wait()
        self.announce()

    # --- a read that waits for news ------------------------------------------

    #: What ``ping`` tells the backend this runner can do; a harness that
    #: reports what its agent is writing adds ``LIVE``.
    capabilities: tuple[str, ...] = (LONG_POLL,)

    def _grew(self) -> None:
        """Records were written: the session is active now, not whenever
        ``_idle`` next looks, which can be ``IDLE_CHECK_S`` later. Which of a
        person's sessions was used least recently is read from this (a pi
        runner's ``idle_s``), and a session that looked used seconds after it
        went quiet would be kept in place of one used since."""
        self.active_at = time.monotonic()
        self.announce()

    def announce(self) -> None:
        """Wake every read waiting for news."""
        news, self.news = self.news, asyncio.Event()
        news.set()

    def shown(self) -> None:
        """What the agent is writing changed (the harness changed
        ``live_blocks`` in place, or replaced them)."""
        self.live_count += 1
        self.announce()

    def show(self, blocks: list[dict], work: str | None) -> None:
        """Replace what the agent is shown writing; [] when it writes nothing."""
        changed = bool(blocks or self.live_blocks)
        self.live_blocks, self.live_work = blocks, work
        if changed:
            self.shown()

    def live_mark(self) -> str:
        return f"{self.live_epoch}.{self.live_count}"

    def alive(self) -> bool:
        return self.process is not None and self.process.returncode is None

    def ended(self) -> bool:
        """The agent process was started and has exited since."""
        return self.process is not None and self.process.returncode is not None

    async def news_for(self, after: int, params: dict) -> dict:
        """Hold a read that asked to wait until there is news past ``after``,
        then add what else the reader keeps track of: what the agent is
        writing, when it changed since the mark the reader last saw, and
        whether the agent process is still there. A read that did not ask to
        wait is answered at once, with records only.

        Waiting is not activity (``_idle``): an idle session is let go with a
        read still waiting on it, and that read is answered as the runner
        closes. A closing runner answers that its agent is gone, though the
        process may not have ended yet: the backend reuses a launch only while
        its runner says it is alive, and one that is going will refuse the next
        send.
        """
        if "wait" not in params:
            return {}
        seen = params.get("live")
        wait = min(max(float(params["wait"] or 0), 0.0), READ_WAIT_MAX_S)
        deadline = time.monotonic() + wait
        waited = False
        while True:
            news = self.news
            if (
                self.closing
                or self.ended()
                or self.journal.last() > after
                or self.live_mark() != seen
            ):
                break
            left = deadline - time.monotonic()
            if left <= 0:
                break
            waited = True
            # ``asyncio.wait`` rather than ``wait_for``: it raises nothing on
            # timeout, on whichever Python the session host runs.
            waiting = asyncio.ensure_future(news.wait())
            try:
                done, _ = await asyncio.wait({waiting}, timeout=left)
            finally:
                waiting.cancel()
            if not done:
                break
        if waited and not self.closing:
            await asyncio.sleep(READ_BATCH_S)
        answer: dict = {"alive": self.alive() and not self.closing}
        if self.live_mark() != seen:
            answer["live"] = {
                "mark": self.live_mark(),
                "work_id": self.live_work,
                # As they stand now: the harness goes on changing them in place.
                "blocks": [dict(block) for block in self.live_blocks],
            }
        return answer

    # --- letting an idle session go ------------------------------------------

    def records(self, after: int) -> list[dict]:
        """The journal past ``after``, as a backend reads it.

        What it has taken is what an idle session waits for before it goes: a
        backend still reading would otherwise find the rest only when the room
        next speaks.
        """
        rows = self.journal.read(after)
        self.read_through = max(
            self.read_through, rows[-1]["sequence"] if rows else after
        )
        self.read_at = time.monotonic()
        return rows

    def busy(self) -> bool:
        """Whether the session has something going besides an input on its way
        in: a turn, or work of its own still running. Each harness knows its
        own."""
        raise NotImplementedError

    async def release(self) -> None:
        """Let the session go. Its process exiting is what ends the runner."""
        if self.process is not None and self.process.returncode is None:
            self.process.terminate()

    async def _idle(self) -> None:
        while True:
            await asyncio.sleep(min(IDLE_CHECK_S, self.idle_exit_s))
            now = time.monotonic()
            # A record counts when it is written (``_grew``); work still going
            # on counts until now.
            if self.inputs or self.busy():
                self.active_at = now
                continue
            if now - self.active_at < self.idle_exit_s:
                continue
            # A backend that has not asked for an idle window is gone; what it
            # left unread is read from this journal when the session is next
            # started.
            last = self.journal.last()
            if last > self.read_through and now - self.read_at < self.idle_exit_s:
                continue
            await self.release()
            return

    async def accept(
        self,
        identifier: str,
        content: dict,
        submit: Callable[[], Awaitable[dict]],
        *,
        owes_reply: bool = False,
    ) -> dict:
        """Put one input in, at most once, however many times we are asked.

        The identifier is the platform's, and it is what makes a reconnection
        safe: a backend that never saw our answer resends the same id and gets
        the same outcome rather than a second turn. The same id with different
        content is refused, since answering it with the first input's outcome
        would report something that was never sent.

        ``owes_reply`` is the platform saying a person wrote this. The session
        owes the answer from the moment the input goes in, not from when the
        model reads it: a tool it starts in between would otherwise run with
        the message still unread behind it, and refusing that tool is also what
        puts the message in front of the model at once.
        """
        self.active_at = time.monotonic()
        payload = json.dumps(content, sort_keys=True)
        previous = self.journal.input(identifier)
        if previous is not None:
            if previous[0] != payload:
                raise ValueError("An input ID cannot be reused for different text")
            if previous[1] == "accepted":
                return previous[2] or {}
            if previous[1] == "failed":
                raise RuntimeError((previous[2] or {})["error"])
            if identifier not in self.inputs:
                raise RuntimeError(
                    "Previous input outcome is unresolved; it was not resubmitted"
                )
        else:
            self.journal.begin_input(identifier, payload)
            task = asyncio.create_task(
                self._settle(identifier, submit, owes_reply=owes_reply)
            )
            self.inputs[identifier] = task

            def finished(task):
                self.inputs.pop(identifier, None)
                if not task.cancelled():
                    task.exception()  # Failure is retained in the input journal.

            task.add_done_callback(finished)
        return await asyncio.shield(self.inputs[identifier])

    async def _settle(
        self,
        identifier: str,
        submit: Callable[[], Awaitable[dict]],
        *,
        owes_reply: bool = False,
    ) -> dict:
        before = self.owed
        if owes_reply:
            self.owe_reply(identifier)
        try:
            result = await submit()
            self.journal.finish_input(identifier, "accepted", result)
        except Exception as error:
            self.journal.finish_input(identifier, "failed", {"error": str(error)})
            # The message never reached the session, so nothing is owed on it;
            # whatever was owed before it still is.
            if owes_reply and self.owed == identifier:
                if before is None:
                    self.reply_settled()
                else:
                    self.owe_reply(before)
            raise
        if owes_reply:
            try:
                await self.yield_foreground()
            except Exception as error:  # noqa: BLE001 — the message is in regardless
                print(
                    f"could not move foreground work aside for {identifier}: {error}",
                    file=sys.stderr,
                    flush=True,
                )
        return result

    async def dispatch(self, method: str, params: dict) -> dict:
        raise NotImplementedError

    async def handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            request = json.loads(await reader.readline())
            response = {
                "result": await self.dispatch(
                    request["method"], request.get("params", {})
                )
            }
        except Exception as error:
            response = {"error": str(error)}
        try:
            writer.write(json.dumps(response, ensure_ascii=False).encode() + b"\n")
            await writer.drain()
        except (ConnectionError, BrokenPipeError):
            # A disconnected backend does not cancel the accepted input.
            pass
        finally:
            writer.close()
            with contextlib.suppress(ConnectionError, BrokenPipeError):
                await writer.wait_closed()

    async def stopped(self) -> None:
        """Wait out the protocol reader once its process is gone."""
        if self.listener is not None:
            await self.listener

    async def close(self) -> None:
        # A read still waiting is answered first: the server does not finish
        # closing while a connection is being handled.
        self.closing = True
        self.announce()
        for task in (self.idler, self.ender):
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
        if self.process is not None and self.process.returncode is None:
            self.process.terminate()
            await self.process.wait()
        try:
            await self.stopped()
        finally:
            # Protocol failure must release the lock and database too, so a
            # supervised replacement can resume the recorded session.
            await asyncio.gather(*self.inputs.values(), return_exceptions=True)
            if self.errors is not None:
                self.errors.close()
            self.journal.close()
            if self.server is not None:
                Path(socket_path(self.state)).unlink(missing_ok=True)
            if self.lock is not None:
                self.lock.close()
