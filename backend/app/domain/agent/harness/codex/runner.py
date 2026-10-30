"""Persistent app-server owner, controlled through the device's Unix transport."""

import asyncio
import json
import os
import sys
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path
from typing import Protocol

from app.domain.agent.harness import CODEX
from app.domain.agent.harness.codex.app_server import AppServer
from app.domain.agent.harness.codex.journal import Journal
from app.domain.agent.harness.codex.session import Session
from app.domain.agent.harness.driven import runner
from app.domain.agent.harness.driven.monitor import Monitors
from app.domain.agent.harness.driven.runner import SessionStart

# How long a person's message waits for an interrupted woken turn to end.
WOKEN_TURN_END_S = 15.0

# How long a new turn waits to see the project's skills as they are now before
# it starts on what the session already has.
CATCH_UP_TIMEOUT_S = 30.0


class Skills(Protocol):
    """Where the project's skills are mirrored for Codex, and keeping them so."""

    def skill_roots(self) -> list[str]: ...

    def sync_skills(self) -> bool: ...


def _turn_of(event: dict) -> str | None:
    """The turn a Codex event names, however it names it."""
    params = event.get("params") or {}
    return (params.get("turn") or {}).get("id") or params.get("turnId")


class Runner(runner.Runner[Journal]):
    async def _open_turn(self, sending: Awaitable[str]) -> str:
        """Send, knowing the turn it opens is the room's before app-server
        answers with its id.

        A `turn/started` can arrive ahead of the answer (`_submit` pins the
        work id for the same reason). While this is in flight the first one is
        taken as the room's; if the answer names a different turn, that one was
        not ours — app-server opened the platform's compaction under us — and
        is handed back to the compaction's own books.
        """
        self.starting_room_turn = True
        self.started_during_send = None
        try:
            turn = await sending
        finally:
            self.starting_room_turn = False
            early, self.started_during_send = self.started_during_send, None
        self.room_turns.add(turn)
        if early is not None and early != turn:
            self.room_turns.discard(early)
            self.compacting_turn = early
        return turn

    def _is_room_turn(self, turn: str) -> bool:
        """Whether a turn is one this runner opened for the room.

        Every turn the room has is opened by a send here; app-server's own
        turns — the compaction `thread/compact/start` runs — are the ones it
        opens without one. A turn whose `turn/started` arrived while a send was
        still in flight was recorded on arrival (``record``), so this is only a
        membership test.
        """
        return bool(turn) and turn in self.room_turns

    def __init__(
        self,
        state: Path,
        on_tool: Callable[[str, dict], Awaitable[dict]],
        skills: Skills | None = None,
        *,
        idle_exit_s: float = runner.IDLE_EXIT_S,
    ):
        super().__init__(state, Journal, "events.sqlite", idle_exit_s=idle_exit_s)
        self.on_tool = on_tool
        self.skills = skills
        self.syncing: asyncio.Future[bool] | None = None
        # A woken turn is being ended for a person's message (`_end_woken_turn`).
        self.ending_woken = False
        #: The text of the thread's last message, until anything else happens
        #: in its turn: what the turn ends on, if it ends now (`insist`).
        self.last_words = ""
        self.cwd = ""
        self.session: Session | None = None
        self.submit_lock = asyncio.Lock()
        self.continuing: asyncio.Future | None = None
        # The session's monitors (`tools.RemoteTools.monitors`): what they
        # print is said to the session through `tell`.
        self.monitors: Monitors | None = None
        # Where the session's own request to compact has got to, if it made
        # one (`cheese_session(action=compact)`): "" for none, "asked" while the turn it asked
        # in is still running, "sent" once app-server has the request and the
        # thread's compaction has not finished. Set from the thread the tool
        # call runs on (`tools.RemoteTools`), read here.
        self.compact_request = ""
        #: The turn app-server opened for the compaction itself, once it has:
        #: a compaction runs as a turn on the thread, and it is the platform's,
        #: not the room's — its ending must not close a room turn.
        self.compacting_turn: str | None = None
        #: A `turn/started` held until the event after it says whose turn it
        #: is: the compaction's item drops it, anything else journals it — one
        #: event late (``record``).
        self.pending_turn: dict | None = None
        #: Turns this runner started on the thread for the room: from a
        #: person's message, `tell`, or a held-back turn's reminder. Only a
        #: turn app-server opens on its own — the platform's compaction — is
        #: absent, which is how `record` tells the two compactions apart.
        #: Turn ids are added when the send answers and dropped when the turn
        #: ends (``record``).
        self.room_turns: set[str] = set()
        #: A room turn is being started right now: set around every send that
        #: opens one. app-server can push its `turn/started` before the send's
        #: answer comes back (`_submit` says so where it pins the work id), and
        #: an id no send has answered for yet would read as a compaction's —
        #: the one thing that must never happen, since its ending is dropped.
        #: So while this is set, the first `turn/started` is taken as the
        #: room's (`record`), and the send's answer corrects it if not.
        self.starting_room_turn = False
        #: The turn a `turn/started` named while a send was in flight. It is
        #: very likely that send's; the answer says so, and corrects it when
        #: it is not (``_submit``, `_hold_to_reply`, `tell`).
        self.started_during_send: str | None = None
        #: The loop `request_compact` hops onto from the tool call's thread.
        #: `start` sets it for sure; a runner built outside a loop (a sync
        #: test) has none yet and gets it there.
        self.loop: asyncio.AbstractEventLoop | None = runner.current_loop()

    async def _catch_up(self) -> bool:
        """The project's skills as they are on the machine now; whether they
        changed. A failure costs the turn nothing: it starts on what the
        session has. Nor does a machine slow to answer: the turn waits
        `CATCH_UP_TIMEOUT_S` and no more, and the synchronization it leaves
        running is the one the next turn waits for."""
        if self.skills is None:
            return False
        if self.syncing is None or self.syncing.done():
            self.syncing = asyncio.ensure_future(
                asyncio.to_thread(self.skills.sync_skills)
            )
        try:
            return await asyncio.wait_for(
                asyncio.shield(self.syncing), CATCH_UP_TIMEOUT_S
            )
        except Exception as error:  # the turn goes on regardless
            print(
                f"project skills not synchronized: {error!r}",
                file=sys.stderr,
                flush=True,
            )
            return False

    async def record(self, event: dict) -> None:
        params = event.get("params", {})
        thread = params.get("thread", {})
        thread_id = params.get("threadId") or thread.get("id")
        if parent := thread.get("parentThreadId"):
            work = self.journal.recall(f"work:{parent}")
            if work:
                self.journal.remember(f"work:{thread_id}", work)
        work = self.journal.recall(f"work:{thread_id}") if thread_id else None
        owner = json.loads(self.journal.recall("owner") or "{}")
        if work:
            owner["work_id"] = work
            if self.journal.recall(f"unsolicited:{work}"):
                # A turn the session started for itself (`tell`): the room
                # opens the books for it when it speaks.
                owner["unsolicited"] = True
        if owner:
            event = {**event, "cheese": owner}
        turn_id = (params.get("turn") or {}).get("id") or params.get("turnId")
        method = event.get("method")
        item = params.get("item") or {}
        same_thread = self.session is not None and thread_id == self.session.thread_id
        # --- the compaction's own turn -------------------------------------
        #
        # `thread/compact/start` runs the compaction as a turn of its own on
        # the thread, announced by a `contextCompaction` item. The room hears
        # the compaction (`AgentCompacting`, from that item) and not a turn:
        # the turn's ending read as a turn would answer, with nothing, work
        # that had already ended, and its start would open books no ending
        # closes.
        #
        # The turn is named by that item, never by arrival order: a person's
        # message can start their turn beside the ask, so the first
        # `turn/started` is not necessarily the compaction's. A start arriving
        # while an ask is outstanding is therefore held, and the first event
        # after it decides: the compaction's item drops it, anything else is a
        # real turn and journals it — one event late.
        #
        # Only a compaction the platform asked for is that turn: one that
        # names a turn the room did not start. Codex also compacts by itself
        # whenever the context nears its limit, before or partway through a
        # turn, and that one keeps the turn it happened in
        # (`run_inline_auto_compact_task` reuses the running turn's context),
        # so its item names a turn the room is waiting on. Reading it as the
        # compaction's would drop that turn's answer — with the tool off as
        # much as on, and in any turn that ran pre-, mid- or post-ask.
        item = params.get("item") or {}
        same_thread = self.session is not None and thread_id == self.session.thread_id
        if method == "turn/started" and same_thread and self.starting_room_turn:
            # A send in flight is opening this one; its answer names it too.
            self.room_turns.add(turn_id or "")
            if self.started_during_send is None:
                self.started_during_send = turn_id
        held, self.pending_turn = self.pending_turn, None
        candidate = str(params.get("turnId") or "") or turn_id
        announced = (
            self.compact_request == "sent"
            and same_thread
            and method in ("item/started", "item/completed")
            and item.get("type") == "contextCompaction"
            and not self._is_room_turn(candidate)
        )
        if announced:
            self.compacting_turn = candidate
            if held is not None and _turn_of(held) != candidate:
                # A start held for a room turn, arriving beside the compaction
                # instead of before its item. Its own ending still comes — it
                # is not the compaction's — so journal it, one event late.
                self.journal.append(held)
            held = None
            if method == "item/completed":
                # The compaction is over, however it went; its turn's ending
                # may still come and is dropped there.
                self.compact_request = ""
        elif held is not None:
            # The next event after a held start. The thread runs one turn at a
            # time, so a start that began while an ask was outstanding is
            # either the room's own turn — journal it, one event late, and the
            # ask the compaction never got is over — or the compaction's turn
            # arriving with no item, whose start and end the room must not see.
            start = _turn_of(held)
            if start in self.room_turns:
                self.journal.append(held)
            else:
                self.compacting_turn = start
            held = None
            self.compact_request = ""
        if method == "turn/completed":
            # Recorded only while the turn runs (`room_turns`).
            self.room_turns.discard(turn_id or "")
        if (
            held is None
            and same_thread
            and method == "turn/completed"
            and self.compacting_turn is not None
        ):
            if turn_id == self.compacting_turn:
                # The compaction's own ending: dropped, the room has the item.
                self.compacting_turn = None
                self.compact_request = ""
                if self.session is not None:
                    self.session.observe(event)
                return
            # A different turn ended while the compaction was still open: it
            # was replaced and will never report. Left alone, the request would
            # sit at `sent` for ever and the session would never go idle.
            self.compacting_turn = None
            self.compact_request = ""
        if method == "turn/started":
            if (
                self.pending_turn is None
                and same_thread
                and self.compact_request == "sent"
                and self.compacting_turn is None
            ):
                # Held until the first event after it says what it is.
                self.pending_turn = event
                if self.session is not None:
                    self.session.observe(event)
                return
        if same_thread and method == "turn/started":
            self.last_words = ""
        elif same_thread and method in ("item/started", "item/completed"):
            # A message the model writes is the turn's last words until it
            # does anything else: a tool call, a command, a message read in.
            if item.get("type") == "agentMessage":
                if method == "item/completed":
                    self.last_words = str(item.get("text") or "")
            elif item.get("type") != "reasoning":
                self.last_words = ""
        ends = (
            self.session is not None
            and event.get("method") == "turn/completed"
            and thread_id == self.session.thread_id
        )
        # A turn that ends with a person unanswered goes on instead: Codex
        # has nothing that keeps a turn open, so its ending is held back and a
        # new turn in the same work carries the reminder. The room sees one
        # piece of work, ending when that turn does.
        # A woken turn ended for a person's message is not held to their
        # reply, and does not settle it: their own turn, next, owes it.
        handed_on = ends and self.ending_woken
        reason = (
            self.insist(self.last_words)
            if ends
            and not handed_on
            and (params.get("turn") or {}).get("status") == "completed"
            else None
        )
        if reason is None:
            self.journal.append(event)
        if self.session is not None:
            self.session.observe(event)
        if reason is not None:
            self.continuing = asyncio.ensure_future(self._hold_to_reply(reason, event))
        elif ends:
            # Codex takes a message said mid-turn into that turn (`turn/steer`
            # needs one in progress), so a finished turn has read every one.
            if not handed_on:
                self.reply_settled()
            self.turn_ended()
            if self.compact_request == "asked":
                # The turn the session asked to be compacted in has ended, so
                # the request can go now (`Session.compact`: asking mid-turn
                # replaces that turn).
                self._ask()

    def turn_work(self) -> str | None:
        """The work the finished thread was in: Codex names it per thread and
        keeps it after the turn ends (``work:<thread id>``)."""
        thread = self.session.thread_id if self.session is not None else None
        return self.journal.recall(f"work:{thread}") if thread else None

    async def _hold_to_reply(self, reason: str, ending: dict) -> None:
        assert self.session is not None
        try:
            async with self.submit_lock:
                await self._open_turn(self.session.send(reason))
        except Exception as error:  # the turn ends as it was going to
            print(f"could not hold the turn to a reply: {error!r}", file=sys.stderr)
            self.journal.append(ending)
            self.reply_settled()

    async def tell(self, text: str) -> None:
        """Say something to the session on the platform's behalf: into the
        turn it is in (`turn/steer`), or as a turn of its own when it is idle —
        the way Claude Code hears what a monitor it started printed."""
        session = self.session
        if session is None or session.thread_id is None:
            return
        async with self.submit_lock:
            if session.turn_id is None:
                work = str(uuid.uuid4())
                self.journal.remember(f"unsolicited:{work}", "1")
                self.journal.remember(f"work:{session.thread_id}", work)
            await self._open_turn(session.send(text))

    # --- the session compacting itself ---------------------------------------

    def request_compact(self) -> str:
        """The session asked to compact its own context (`cheese_session(action=compact)`):
        whether this is the request that will be acted on.

        Asked for once — a request still waiting and one already running are
        the same thing to the agent that asked — and always asked for at the
        end of the turn it was made in: `thread/compact/start` replaces
        whatever the thread is doing, so asking mid-turn would abort the turn
        the asking tool call is in.
        """
        if self.compact_request:
            return "already"
        session = self.session
        if session is None or session.thread_id is None:
            # i18n-exempt: programmer invariant, never shown
            raise RuntimeError("This session has no Codex thread to compact")
        self.compact_request = "asked"
        # From the loop: a platform tool call runs in a worker thread
        # (`tools.RemoteTools.__call__`).
        if self.loop is None:  # nobody to hop onto, none to ask
            self.compact_request = ""
            # i18n-exempt: programmer invariant, never shown
            raise RuntimeError("This session has no loop to compact on")
        self.loop.call_soon_threadsafe(self._compact_when_idle)
        return "requested"

    def _compact_when_idle(self) -> None:
        """On the loop: ask now if no turn is running, or let the turn that is
        ask when it ends (`record`)."""
        if self.compact_request != "asked":
            return
        session = self.session
        if session is None or session.thread_id is None:
            self.compact_request = ""
            return
        if session.turn_id is None:
            self._ask()

    def _ask(self) -> None:
        """Go from `asked` to `sent` and start the ask, on the loop.

        The transition is here, not inside `_compact_now`: two things can see
        an idle thread at once (this call's own hop onto the loop, and the turn
        ending), and only the one that finds `asked` may start it. State set
        inside the task leaves a window where both do."""
        if self.compact_request != "asked":
            return
        self.compact_request = "sent"
        asyncio.ensure_future(self._compact_now())

    async def _compact_now(self) -> None:
        assert self.session is not None
        try:
            await self.session.compact()
        except Exception as error:  # the session goes on without it
            self.compact_request = ""
            print(f"compaction not asked: {error!r}", file=sys.stderr, flush=True)

    async def start(
        self,
        opening: SessionStart,
        *,
        binary: str,
        cwd: str,
        env: dict[str, str],
        tools: list[dict],
        base_instructions: str | None = None,
        overrides: list[str] | None = None,
    ) -> str:
        """``overrides`` are `app-server` configuration overrides (``-c``):
        the project's MCP servers, at the session's gateway."""
        self.claim()
        # This runs on the loop the runner will keep working on, which is the
        # one `request_compact` hops onto from a tool call's own thread.
        self.loop = asyncio.get_running_loop()
        saved = self.journal.recall("thread_id")
        if saved is not None:
            if opening.resume_token not in (None, saved):
                raise ValueError("A session directory cannot resume a different thread")
            opening = replace(opening, resume_token=saved)
        self.journal.remember(
            "owner",
            json.dumps(
                {
                    "harness": CODEX,
                    "agent_handle": opening.agent_handle,
                }
            ),
        )
        self.errors = (self.state / "app-server.log").open("ab")
        self.process = await asyncio.create_subprocess_exec(
            binary,
            "app-server",
            *(overrides or []),
            cwd=cwd,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=self.errors,
            limit=4 * 1024 * 1024,
        )
        assert self.process.stdout is not None and self.process.stdin is not None
        client = AppServer(
            self.process.stdout,
            self.process.stdin,
            on_event=self.record,
            on_request=self.on_tool,
        )
        self.session = Session(client)
        self.listener = asyncio.create_task(client.listen())
        await client.initialize()
        self.cwd = cwd
        if self.skills is not None:
            # Plain Codex finds a repository's skills in the project, through
            # its environment; a room's has none, so it is pointed at the
            # mirror, which it lists and watches as it would the project's.
            await self._catch_up()
            await client.request(
                "skills/extraRoots/set", {"extraRoots": self.skills.skill_roots()}
            )
        thread = await self.session.open(
            opening, cwd=cwd, tools=tools, base_instructions=base_instructions
        )
        if self.session.turn_id is not None:
            # A resumed thread's running turn started before this runner, and
            # is the room's all the same.
            self.room_turns.add(self.session.turn_id)
        self.journal.remember("thread_id", thread)
        await self.listen(4 * 1024 * 1024)
        return thread

    async def send(
        self,
        identifier: str,
        text: str,
        images: list[str] | None = None,
        work_id: str | None = None,
        *,
        owes_reply: bool = False,
    ) -> dict:
        content: dict = {"text": text, "images": images or []}
        if work_id is not None:
            content["work_id"] = work_id
        return await self.accept(
            identifier,
            content,
            lambda: self._submit(identifier, text, images, work_id),
            owes_reply=owes_reply,
        )

    async def _submit(
        self,
        identifier: str,
        text: str,
        images: list[str] | None,
        work_id: str | None,
    ) -> dict:
        assert self.session is not None
        async with self.submit_lock:
            if (
                self.skills is not None
                and self.session.turn_id is None
                and await self._catch_up()
            ):
                # Plain Codex notices a changed skill through its watcher, a
                # turn or so later; this makes the next turn see it.
                await self.session.client.request(
                    "skills/list", {"cwds": [self.cwd], "forceReload": True}
                )
            if work_id is not None:
                thread_id = self.session.thread_id
                previous = self.journal.recall(f"work:{thread_id}")
                # A turn the session started for itself (`tell`) that the room
                # has not steered into: the room sends a person's message then
                # as work of its own, and closes each work by the records that
                # name it. So that turn is stopped and ends as its own work,
                # and the message starts the room's turn.
                woken = (
                    previous is not None
                    and self.journal.recall(f"unsolicited:{previous}") is not None
                )
                if (
                    self.session.turn_id is not None
                    and previous != work_id
                    and not (woken and await self._end_woken_turn())
                ):
                    raise RuntimeError("A different room work item is still active")
                # Persist attribution before the call: notifications can
                # arrive before app-server acknowledges turn/start.
                self.journal.remember(f"work:{thread_id}", work_id)
            turn = await self._open_turn(
                self.session.send(text, images=images)
                if images
                else self.session.send(text)
            )
        return {"turn_id": turn, "input_id": identifier}

    async def _end_woken_turn(self) -> bool:
        """Interrupt the turn a monitor woke and wait for its ending, which
        `record` journals under that turn's own work; whether it ended."""
        session = self.session
        assert session is not None
        self.ending_woken = True
        try:
            await session.interrupt()
            deadline = asyncio.get_running_loop().time() + WOKEN_TURN_END_S
            while session.turn_id is not None:
                left = deadline - asyncio.get_running_loop().time()
                if left <= 0:
                    return False
                news = self.news
                try:
                    await asyncio.wait_for(news.wait(), min(left, 0.2))
                except TimeoutError:
                    pass
            return True
        finally:
            self.ending_woken = False

    async def yield_foreground(self) -> None:
        """Nothing to send: the session's Bash waits in this process
        (`tools.RemoteTools`), which watches the file the debt was just written
        to and has the executor let go of the command as soon as it changes."""

    def busy(self) -> bool:
        return bool(
            (self.session is not None and self.session.turn_id is not None)
            or (self.continuing is not None and not self.continuing.done())
            # A watch still running is the session's own work: letting the
            # session go would end it unheard.
            or (self.monitors is not None and self.monitors.count() > 0)
            # So is a compaction asked for and not finished: between the turn
            # ending and the compaction being taken there is no turn to say so.
            or bool(self.compact_request)
        )

    async def close(self) -> None:
        if self.monitors is not None:
            await self.monitors.close()
        await super().close()

    async def dispatch(self, method: str, params: dict) -> dict:
        if method == "configure":
            if self.session is None or self.session.turn_id is not None:
                raise RuntimeError("Finish the active turn before changing its model")
            self.session.model = params.get("model")
            return {"configured": True}
        if method == "events":
            after = int(params.get("after", 0))
            news = await self.news_for(after, params)
            return {"events": self.records(after), **news}
        if method == "send":
            return await self.send(
                params["input_id"],
                params["text"],
                params.get("images"),
                params.get("work_id"),
                owes_reply=bool(params.get("owes_reply")),
            )
        if method == "interrupt":
            return {
                "interrupted": await self.session.interrupt() if self.session else False
            }
        if method == "ping":
            return {
                "pid": os.getpid(),
                "thread_id": self.journal.recall("thread_id"),
                "turn_id": self.session.turn_id if self.session else None,
                "work_id": self.journal.recall(
                    f"work:{self.journal.recall('thread_id')}"
                ),
                "alive": self.alive(),
                "capabilities": list(self.capabilities),
            }
        raise ValueError(f"Unknown session operation: {method}")
