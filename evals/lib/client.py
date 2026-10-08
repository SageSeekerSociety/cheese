"""API driver for the isolated eval backend.

Scenarios drive the REAL backend the same way the frontend does: REST for
structure (projects/topics/blocks/docs/memory) and for messages, and the chat
WebSocket to watch what lands — a real agent turn streams real frames back.
"""

import asyncio
import json
import time
import uuid
from typing import Any

import httpx
import jwt
import websockets


class ApiError(RuntimeError):
    pass


class EvalApi:
    """Thin typed wrapper over the backend's REST + WS surface."""

    def __init__(
        self,
        base_url: str,
        ws_base_url: str,
        *,
        sandbox_token: str,
        jwt_secret: str,
        platform_admin_handle: str,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._ws_base = ws_base_url.rstrip("/")
        self._sandbox_token = sandbox_token
        self._jwt_secret = jwt_secret
        # Who reads /debug/turns: a platform-admin surface, so the backend must
        # name this handle in PLATFORM_ADMIN_HANDLES for the call to answer.
        self._platform_admin_handle = platform_admin_handle
        self._http = httpx.AsyncClient(base_url=self._base, timeout=30.0)

    def session_token(self, handle: str) -> str:
        """A session token naming ``handle``, signed with the eval backend's own
        secret: scenarios speak as several people with no login behind them."""
        now = int(time.time())
        claims = {
            "sub": handle,
            "handle": handle,
            "type": "access",
            "iat": now,
            "exp": now + 3600,
        }
        return jwt.encode(claims, self._jwt_secret, algorithm="HS256")

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

    async def get(self, path: str, *, cheese: bool = False, **params: Any) -> Any:
        headers = {"X-Cheese-Token": self._sandbox_token} if cheese else None
        return self._unwrap(
            await self._http.get(path, params=params or None, headers=headers)
        )

    async def post(
        self,
        path: str,
        body: dict | None = None,
        *,
        cheese: bool = False,
        as_handle: str | None = None,
    ) -> Any:
        """POST body; as_handle signs the request in as that person."""
        headers: dict[str, str] = {}
        if cheese:
            headers["X-Cheese-Token"] = self._sandbox_token
        if as_handle is not None:
            headers["Authorization"] = f"Bearer {self.session_token(as_handle)}"
        return self._unwrap(
            await self._http.post(path, json=body or {}, headers=headers or None)
        )

    # ---- structure ---------------------------------------------------------
    # The owner of a project and the creator of a topic are whoever the request
    # is signed in as: the backend takes neither from the body.

    async def create_project(self, name: str, owner: str) -> dict:
        return await self.post("/api/projects", {"name": name}, as_handle=owner)

    async def create_topic(self, project_id: str, title: str, creator: str) -> dict:
        return await self.post(
            "/api/topics",
            {"project_id": project_id, "title": title},
            as_handle=creator,
        )

    async def get_topic(self, topic_id: str) -> dict:
        return await self.get(f"/api/topics/{topic_id}")

    async def list_blocks(self, topic_id: str) -> list[dict]:
        return (await self.get(f"/api/topics/{topic_id}/blocks"))["data"]

    async def get_doc(self, topic_id: str) -> dict | None:
        document = (await self.get(f"/api/topics/{topic_id}/document"))["id"]
        return await self.get(f"/api/documents/{document}")

    async def upgrade_block(self, block_id: str) -> dict:
        return await self.post(f"/api/blocks/{block_id}/upgrade", {})

    # ---- memory (cheese-gated write; the runner owns this backend's token) --

    async def seed_memory(self, project_id: str, content: str) -> None:
        await self.post(
            f"/api/projects/{project_id}/memory", {"content": content}, cheese=True
        )

    async def list_memory(self, project_id: str) -> list[dict]:
        return (await self.get("/api/memory", cheese=True, project_id=project_id))[
            "data"
        ]

    # ---- turns / debug ------------------------------------------------------

    async def recent_turns(self) -> list[dict]:
        # Signed in as the backend's platform admin: the summaries name rooms,
        # people and failure reasons, so the route is gated now.
        headers = {
            "Authorization": f"Bearer {self.session_token(self._platform_admin_handle)}"
        }
        resp = await self._http.get("/debug/turns", headers=headers)
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

    # ---- chat: POST a message, watch the room's socket ------------------------

    async def send_chat(
        self,
        topic_id: str,
        *,
        content: str,
        author: str,
        turn_timeout_s: float = 360.0,
    ) -> list[dict]:
        """Send one chat message as ``author`` and collect the room's frames until
        the turn's `done` frame (a message that names no teammate produces
        user_block + done at once). Returns every frame received, in order."""
        token = self.session_token(author)
        frames: list[dict] = []
        url = f"{self._ws_base}/api/rooms/live?token={token}"
        async with websockets.connect(url, max_size=8 * 1024 * 1024) as ws:
            # The room is watched once the server says so; a message posted
            # before that would have its echo published to nobody.
            await ws.send(
                json.dumps({"type": "subscribe", "topic": topic_id, "token": token})
            )
            while json.loads(await ws.recv()).get("type") != "subscribed":
                pass
            resp = await self._http.post(
                f"/api/topics/{topic_id}/messages",
                json={"content": content, "request_id": str(uuid.uuid4())},
                headers={"Authorization": f"Bearer {token}"},
            )
            self._unwrap(resp)
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


def by_an_agent(block: dict) -> bool:
    """这条是不是芝士写的 —— 问的是署名，不是事件行的档位。

    档位只分「参与者」和「平台」两档：人和 agent 都是参与者，一个房间里坐着谁由
    署名（``author``，一条 handle）说了算。拿档位问这一句的旧写法不会报错，只会
    安静地一条都匹配不上 —— 而这里每个调用点都拿它当断言依据，数字算成 0 照样
    "通过"。
    """
    author = block.get("author") or ""
    return author == "cheese" or author.startswith("cheese-")


def agent_blocks(blocks: list[dict]) -> list[dict]:
    """芝士写下的块，不论哪一种 kind。"""
    return [b for b in blocks if by_an_agent(b)]


def ai_messages(blocks: list[dict]) -> list[dict]:
    """Chat messages authored by 芝士 (structural fields, no text sniffing)."""
    return [b for b in blocks if by_an_agent(b) and b["kind"] == "message"]


def human_messages(blocks: list[dict]) -> list[dict]:
    return [b for b in blocks if not by_an_agent(b) and b["kind"] == "message"]


def new_id() -> str:
    return uuid.uuid4().hex[:8]
