"""Persistent app-server owner, controlled through the device's Unix transport."""

import asyncio
import json
import os
from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path

from app.domain.agent.harness import Opening
from app.domain.agent.harness.codex.app_server import AppServer
from app.domain.agent.harness.codex.journal import Journal
from app.domain.agent.harness.codex.session import Session
from app.domain.agent.harness.driven import runner


class Runner(runner.Runner[Journal]):
    def __init__(self, state: Path, on_tool: Callable[[str, dict], Awaitable[dict]]):
        super().__init__(state, Journal, "events.sqlite")
        self.on_tool = on_tool
        self.session: Session | None = None
        self.submit_lock = asyncio.Lock()

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
        self.journal.append(event)
        if self.session is not None:
            self.session.observe(event)

    async def start(
        self,
        opening: Opening,
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
    ) -> dict:
        content: dict = {"text": text, "images": images or []}
        if work_id is not None:
            content["work_id"] = work_id
        return await self.accept(
            identifier,
            content,
            lambda: self._submit(identifier, text, images, work_id),
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

    async def dispatch(self, method: str, params: dict) -> dict:
        if method == "configure":
            if self.session is None or self.session.turn_id is not None:
                raise RuntimeError("Finish the active turn before changing its model")
            self.session.model = params.get("model")
            return {"configured": True}
        if method == "events":
            return {"events": self.journal.read(int(params.get("after", 0)))}
        if method == "send":
            return await self.send(
                params["input_id"],
                params["text"],
                params.get("images"),
                params.get("work_id"),
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
                "alive": self.process is not None and self.process.returncode is None,
            }
        raise ValueError(f"Unknown session operation: {method}")
