"""Translate the shared opening and input operations into app-server requests."""

import asyncio

from app.domain.agent.harness import Opening
from app.domain.agent.harness.codex.app_server import AppServer


class Session:
    def __init__(self, client: AppServer):
        self.client = client
        self.thread_id: str | None = None
        self.turn_id: str | None = None
        self.model: str | None = None
        self.completed_turns: set[str] = set()
        self.input_lock = asyncio.Lock()

    async def open(
        self,
        opening: Opening,
        *,
        cwd: str,
        tools: list[dict],
        base_instructions: str | None = None,
    ) -> str:
        async with self.input_lock:
            if self.thread_id is not None:
                raise RuntimeError("This adapter already holds a Codex thread")
            params: dict = {
                "cwd": cwd,
                "developerInstructions": opening.system_prompt,
                "approvalPolicy": "never",
                # Native tools must not write into the central session host.
                "sandbox": "read-only",
                # No native host environment: all filesystem tools are dynamic
                # calls into the selected room executor, including image reads.
                "environments": [],
                "config": {
                    # File/shell operations belong to the room executor. The
                    # platform question tool is `cheese ask`, as on Claude Code.
                    "features.shell_tool": False,
                    "features.view_image": False,
                    "tools.experimental_request_user_input.enabled": False,
                },
            }
            if opening.model:
                params["model"] = opening.model
            if base_instructions is not None:
                params["baseInstructions"] = base_instructions
            if opening.resume_token:
                params["threadId"] = opening.resume_token
                method = "thread/resume"
            else:
                params["dynamicTools"] = tools
                method = "thread/start"
            result = await self.client.request(method, params)
            thread = result["thread"]
            thread_id: str = thread["id"]
            self.thread_id = thread_id
            self.model = opening.model
            running = [
                turn
                for turn in thread.get("turns", [])
                if turn["status"] == "inProgress"
            ]
            self.turn_id = running[-1]["id"] if running else None
            return thread_id

    def observe(self, notification: dict) -> None:
        params = notification.get("params", {})
        if params.get("threadId") != self.thread_id:
            return
        method = notification["method"]
        if method == "turn/started":
            self.turn_id = params["turn"]["id"]
        elif method == "turn/completed":
            turn_id = params["turn"]["id"]
            self.completed_turns.add(turn_id)
            if turn_id == self.turn_id:
                self.turn_id = None

    async def send(self, text: str, *, images: list[str] | None = None) -> str:
        async with self.input_lock:
            if self.thread_id is None:
                raise RuntimeError("Open the Codex thread before sending input")
            params: dict = {
                "threadId": self.thread_id,
                "input": [
                    {"type": "text", "text": text},
                    *({"type": "image", "url": url} for url in images or []),
                ],
            }
            if self.turn_id is not None:
                params["expectedTurnId"] = self.turn_id
                result = await self.client.request("turn/steer", params)
                return result["turnId"]
            if self.model is not None:
                params["model"] = self.model
            result = await self.client.request("turn/start", params)
            turn_id = result["turn"]["id"]
            # The acknowledgement can precede turn/started, or follow completion.
            # Only an unfinished acknowledged turn can own the next steer.
            if turn_id not in self.completed_turns:
                self.turn_id = turn_id
            return turn_id

    async def interrupt(self) -> bool:
        async with self.input_lock:
            if self.thread_id is None or self.turn_id is None:
                return False
            await self.client.request(
                "turn/interrupt",
                {
                    "threadId": self.thread_id,
                    "turnId": self.turn_id,
                },
            )
            return True
