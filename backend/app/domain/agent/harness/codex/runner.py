"""Persistent app-server owner, controlled through the device's Unix transport."""

import asyncio
import json
import os
import sys
from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path
from typing import Protocol

from app.domain.agent.harness.codex.app_server import AppServer
from app.domain.agent.harness.codex.journal import Journal
from app.domain.agent.harness.codex.session import Session
from app.domain.agent.harness.driven import runner
from app.domain.agent.harness.driven.runner import SessionStart

# How long a new turn waits to see the project's skills as they are now before
# it starts on what the session already has.
CATCH_UP_TIMEOUT_S = 30.0


class Skills(Protocol):
    """Where the project's skills are mirrored for Codex, and keeping them so."""

    def skill_roots(self) -> list[str]: ...

    def sync_skills(self) -> bool: ...


class Runner(runner.Runner[Journal]):
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
        self.cwd = ""
        self.session: Session | None = None
        self.submit_lock = asyncio.Lock()
        self.continuing: asyncio.Future | None = None

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
        except Exception as error:  # noqa: BLE001 — the turn goes on regardless
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
        if owner:
            event = {**event, "cheese": owner}
        ends = (
            self.session is not None
            and event.get("method") == "turn/completed"
            and thread_id == self.session.thread_id
        )
        # A turn that ends with a person unanswered goes on instead: Codex
        # has nothing that keeps a turn open, so its ending is held back and a
        # new turn in the same work carries the reminder. The room sees one
        # piece of work, ending when that turn does.
        reason = (
            self.insist()
            if ends and (params.get("turn") or {}).get("status") == "completed"
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
            self.reply_settled()
            self.turn_ended()

    async def _hold_to_reply(self, reason: str, ending: dict) -> None:
        assert self.session is not None
        try:
            async with self.submit_lock:
                await self.session.send(reason)
        except Exception as error:  # noqa: BLE001 — the turn ends as it was going to
            print(f"could not hold the turn to a reply: {error!r}", file=sys.stderr)
            self.journal.append(ending)
            self.reply_settled()

    async def start(
        self,
        opening: SessionStart,
        *,
        binary: str,
        cwd: str,
        env: dict[str, str],
        tools: list[dict],
        base_instructions: str | None = None,
    ) -> str:
        self.claim()
        saved = self.journal.recall("thread_id")
        if saved is not None:
            if opening.resume_token not in (None, saved):
                raise ValueError("A session directory cannot resume a different thread")
            opening = replace(opening, resume_token=saved)
        self.journal.remember(
            "owner",
            json.dumps(
                {
                    "harness": "codex",
                    "agent_handle": opening.agent_handle,
                }
            ),
        )
        self.errors = (self.state / "app-server.log").open("ab")
        self.process = await asyncio.create_subprocess_exec(
            binary,
            "app-server",
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
                if self.session.turn_id is not None and previous != work_id:
                    raise RuntimeError("A different room work item is still active")
                # Persist attribution before the call: notifications can
                # arrive before app-server acknowledges turn/start.
                self.journal.remember(f"work:{thread_id}", work_id)
            turn = (
                await self.session.send(text, images=images)
                if images
                else await self.session.send(text)
            )
        return {"turn_id": turn, "input_id": identifier}

    async def yield_foreground(self) -> None:
        """Nothing to send: the session's Bash waits in this process
        (`tools.RemoteTools`), which watches the file the debt was just written
        to and has the executor let go of the command as soon as it changes."""

    def busy(self) -> bool:
        return bool(
            (self.session is not None and self.session.turn_id is not None)
            or (self.continuing is not None and not self.continuing.done())
        )

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
