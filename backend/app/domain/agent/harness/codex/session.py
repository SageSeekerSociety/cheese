"""Translate the shared opening and input operations into app-server requests."""

import asyncio

from app.domain.agent.harness import Opening
from app.domain.agent.harness.codex.app_server import AppServer


class Session:
    def __init__(self, client: AppServer):
        self.client = client
        self.thread_id: str | None = None
        self.turn_id: str | None = None
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
        elif method == "turn/completed" and params["turn"]["id"] == self.turn_id:
            self.turn_id = None

    async def send(self, text: str) -> str:
        async with self.input_lock:
            if self.thread_id is None:
                raise RuntimeError("Open the Codex thread before sending input")
            params: dict = {
                "threadId": self.thread_id,
                "input": [{"type": "text", "text": text}],
            }
            if self.turn_id is not None:
                params["expectedTurnId"] = self.turn_id
                result = await self.client.request("turn/steer", params)
                return result["turnId"]
            result = await self.client.request("turn/start", params)
            # Notifications own active state: completion can precede this reply.
            return result["turn"]["id"]

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
