"""Persistent app-server owner, controlled through the device's Unix transport."""

import asyncio
import contextlib
import fcntl
import hashlib
import json
import os
from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path

from app.domain.agent.harness import Opening
from app.domain.agent.harness.codex.app_server import AppServer
from app.domain.agent.harness.codex.journal import Journal
from app.domain.agent.harness.codex.session import Session


def socket_path(state: Path) -> str:
    digest = hashlib.sha256(str(state.resolve()).encode()).hexdigest()[:24]
    return f"/tmp/cheese-execution-{os.getuid()}-{digest}.sock"


class Runner:
    def __init__(self, state: Path, on_tool: Callable[[str, dict], Awaitable[dict]]):
        state.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.state = state
        self.journal = Journal(state / "events.sqlite")
        self.on_tool = on_tool
        self.process: asyncio.subprocess.Process | None = None
        self.session: Session | None = None
        self.listener: asyncio.Task | None = None
        self.server: asyncio.Server | None = None
        self.inputs: dict[str, asyncio.Task] = {}
        self.submit_lock = asyncio.Lock()
        self.errors = None
        self.lock = None

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
        self.lock = (self.state / "runner.lock").open("a")
        fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
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
        # Only the lock owner may remove a socket left by a crashed runner.
        Path(socket_path(self.state)).unlink(missing_ok=True)
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
        self.server = await asyncio.start_unix_server(
            self.handle, path=socket_path(self.state), limit=4 * 1024 * 1024
        )
        os.chmod(socket_path(self.state), 0o600)
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
            task = asyncio.create_task(self._submit(identifier, text, images, work_id))
            self.inputs[identifier] = task

            def finished(task):
                self.inputs.pop(identifier, None)
                if not task.cancelled():
                    task.exception()  # Failure is retained in the input journal.

            task.add_done_callback(finished)
        return await asyncio.shield(self.inputs[identifier])

    async def _submit(
        self,
        identifier: str,
        text: str,
        images: list[str] | None,
        work_id: str | None,
    ) -> dict:
        assert self.session is not None
        try:
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
            result = {"turn_id": turn, "input_id": identifier}
            self.journal.finish_input(identifier, "accepted", result)
            return result
        except Exception as error:
            self.journal.finish_input(identifier, "failed", {"error": str(error)})
            raise

    async def dispatch(self, method: str, params: dict) -> dict:
        if method == "stage_file":
            return await self.on_tool("cheese/stage_file", params)
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
            # A disconnected backend does not cancel the accepted model input.
            pass
        finally:
            writer.close()
            with contextlib.suppress(ConnectionError, BrokenPipeError):
                await writer.wait_closed()

    async def close(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
        if self.process is not None and self.process.returncode is None:
            self.process.terminate()
            await self.process.wait()
        try:
            if self.listener is not None:
                await self.listener
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
