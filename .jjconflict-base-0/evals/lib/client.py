"""API driver for the isolated eval backend.

Scenarios drive the REAL backend the same way the frontend does: REST for
structure (projects/topics/blocks/docs/memory) and the chat WebSocket for
messages — a real agent turn streams real frames back.
"""

import asyncio
import json
import time
import uuid
from typing import Any

import httpx
import websockets


class ApiError(RuntimeError):
    pass


class EvalApi:
    """Thin typed wrapper over the backend's REST + WS surface."""

    def __init__(
        self, base_url: str, ws_base_url: str, *, sandbox_token: str
    ) -> None:
        self._base = base_url.rstrip("/")
        self._ws_base = ws_base_url.rstrip("/")
        self._sandbox_token = sandbox_token
        self._http = httpx.AsyncClient(base_url=self._base, timeout=30.0)

    async def close(self) -> None:
        await self._http.aclose()

    # ---- envelope ----------------------------------------------------------

    @staticmethod
    def _unwrap(resp: httpx.Response) -> Any:
        resp.raise_for_status()
        body = resp.json()
        if body.get("code") != 200:
            raise ApiError(f"API error: {body.get('code')} {body.get('message')}")
        return body.get("data")

    async def get(self, path: str, **params: Any) -> Any:
        return self._unwrap(await self._http.get(path, params=params or None))

    async def post(
        self, path: str, body: dict | None = None, *, cheese: bool = False
    ) -> Any:
        headers = {"X-Cheese-Token": self._sandbox_token} if cheese else None
        return self._unwrap(
            await self._http.post(path, json=body or {}, headers=headers)
        )

    # ---- structure ---------------------------------------------------------

    async def create_project(self, name: str, owner_handle: str) -> dict:
        return await self.post(
            "/api/projects", {"name": name, "owner_handle": owner_handle}
        )

    async def create_topic(
        self, project_id: str, title: str, created_by: str
    ) -> dict:
        return await self.post(
            "/api/topics",
            {"project_id": project_id, "title": title, "created_by": created_by},
        )

    async def get_topic(self, topic_id: str) -> dict:
        return await self.get(f"/api/topics/{topic_id}")

    async def list_blocks(self, topic_id: str) -> list[dict]:
        return (await self.get(f"/api/topics/{topic_id}/blocks"))["data"]

    async def get_doc(self, topic_id: str) -> dict | None:
        return await self.get(f"/api/topics/{topic_id}/doc")

    async def upgrade_block(self, block_id: str, created_by: str) -> dict:
        return await self.post(
            f"/api/blocks/{block_id}/upgrade", {"created_by": created_by}
        )

    # ---- memory (cheese-gated write; the runner owns this backend's token) --

    async def seed_memory(self, project_id: str, content: str) -> None:
        await self.post(
            f"/api/projects/{project_id}/memory", {"content": content}, cheese=True
        )

    async def list_memory(self, project_id: str) -> list[dict]:
        return (await self.get("/api/memory", project_id=project_id))["data"]

    # ---- turns / debug ------------------------------------------------------

    async def recent_turns(self) -> list[dict]:
        resp = await self._http.get("/debug/turns")
        resp.raise_for_status()
        return resp.json()["data"]

    async def active_turns(self) -> int:
        resp = await self._http.get("/health")
        resp.raise_for_status()
        return int(resp.json()["data"]["active_turns"])

    async def wait_turn_done(
        self, topic_id: str, *, timeout_s: float = 360.0, poll_s: float = 3.0
    ) -> dict:
        """Wait for the newest turn on `topic_id` to leave `running` (structural
        turn lifecycle from /debug/turns — no output parsing). Returns the turn
        record; raises TimeoutError if none finishes in time."""
        deadline = time.monotonic() + timeout_s
        last: dict | None = None
        while time.monotonic() < deadline:
            for rec in await self.recent_turns():  # newest first
                if rec["topic_id"] == topic_id:
                    last = rec
                    break
            if last is not None and last["status"] != "running":
                return last
            await asyncio.sleep(poll_s)
        raise TimeoutError(
            f"turn on topic {topic_id} still {last['status'] if last else 'absent'} "
            f"after {timeout_s}s"
        )

    # ---- chat over WebSocket -------------------------------------------------

    async def send_chat(
        self,
        topic_id: str,
        *,
        content: str,
        author: str,
        summon: bool,
        turn_timeout_s: float = 360.0,
    ) -> list[dict]:
        """Send one chat message and collect the WS frames until the turn's
        `done` frame (summon=False turns produce user_block + done immediately).
        Returns every frame received, in order."""
        frames: list[dict] = []
        url = f"{self._ws_base}/api/topics/{topic_id}/chat"
        async with websockets.connect(url, max_size=8 * 1024 * 1024) as ws:
            await ws.send(
                json.dumps(
                    {
                        "type": "message",
                        "content": content,
                        "author": author,
                        "summon": summon,
                    }
                )
            )
            deadline = time.monotonic() + turn_timeout_s
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        f"no `done` frame within {turn_timeout_s}s "
                        f"(got {len(frames)} frames)"
                    )
                raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
                frame = json.loads(raw)
                frames.append(frame)
                if frame.get("type") == "done":
                    return frames


def ai_messages(blocks: list[dict]) -> list[dict]:
    """Chat messages authored by 芝士 (structural fields, no text sniffing)."""
    return [
        b
        for b in blocks
        if b["author_type"] == "ai" and b["kind"] == "message"
    ]


def human_messages(blocks: list[dict]) -> list[dict]:
    return [
        b
        for b in blocks
        if b["author_type"] == "human" and b["kind"] == "message"
    ]


def new_id() -> str:
    return uuid.uuid4().hex[:8]
