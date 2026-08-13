"""GET /api/topics/{id}/status → `turn`: 芝士到底还在不在跑 (#349).

The runner-level property lives in tests/unit/test_turn_status_masking.py; this
is the same question asked the way a human and a监听 actually ask it — over
HTTP, against the endpoint whose answer was wrong.

The failure being pinned: a turn is running with 81 tool calls, somebody posts a
message that does NOT summon 芝士, and `/status` starts reporting
`done / tools=0 / duration≈0.1s` — indistinguishable from a turn that died at
birth, which this repo has really had. `/health` said `active_turns=1` the whole
time; the two answers must agree.
"""

import asyncio
import uuid

import pytest

from app.api.deps import get_turn_runner
from app.domain.agent.runtime import InProcessBroker, TurnRunner
from app.main import app


class Chat:
    """Stand-in ChatService for the runner: the summoned turn parks until
    released, a plain post lands its block and ends."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.posted: list[str] = []

    async def turn_policy(self, topic_id: uuid.UUID) -> dict | None:
        return None  # unmetered: admission is not what this test is about

    async def post_system_event(
        self,
        topic_id: uuid.UUID,
        content: str,
        turn_id: uuid.UUID | None = None,
        meta: dict | None = None,
    ) -> dict:
        return {"content": content}

    def tmux_activity_status(self, topic_id: uuid.UUID) -> dict | None:
        return None

    async def converse(self, **kwargs):
        if not kwargs.get("summon", True):
            self.posted.append(kwargs["content"])
            yield {"type": "user_block", "block": {"content": kwargs["content"]}}
            yield {"type": "done"}
            return
        yield {"type": "tool", "name": "Bash", "input": {}}
        self.started.set()
        await self.release.wait()
        yield {"type": "done"}


async def _until(cond, timeout: float = 5.0) -> None:
    async with asyncio.timeout(timeout):
        while not cond():
            await asyncio.sleep(0.01)


async def _topic(python_client) -> str:
    project = await python_client.post("/api/projects", json={"name": "P"})
    pid = project.json()["data"]["id"]
    topic = await python_client.post(
        "/api/topics", json={"project_id": pid, "title": "干活"}
    )
    return topic.json()["data"]["id"]


@pytest.mark.anyio
async def test_status_still_reports_the_running_turn_after_someone_chats(
    python_client,
):
    topic_id = await _topic(python_client)
    runner = TurnRunner(InProcessBroker(), turn_timeout_s=10.0)
    chat = Chat()
    app.dependency_overrides[get_turn_runner] = lambda: runner
    try:
        runner.submit(chat, uuid.UUID(topic_id), author="u1", content="开工", summon=True)
        await asyncio.wait_for(chat.started.wait(), 5)

        before = (await python_client.get(f"/api/topics/{topic_id}/status")).json()
        assert before["data"]["turn"]["status"] == "running"

        # 有人在房间里说了句不 @ 芝士的话 —— 最普通的操作。
        runner.submit(
            chat, uuid.UUID(topic_id), author="u2", content="我插一句", summon=False
        )
        await _until(lambda: chat.posted == ["我插一句"])
        await _until(lambda: runner.active_turns() == 1)

        data = (await python_client.get(f"/api/topics/{topic_id}/status")).json()["data"]
        turn = data["turn"]
        assert turn["status"] == "running", "说句话就把干活中的话题报成停了"
        assert turn["turn_id"] == before["data"]["turn"]["turn_id"]
        assert turn["summon"] is True
        assert turn["tools"] == 1, "报的是那条空转记录（tools=0），不是干活的那一轮"
        # `turn` 和同一份快照里的 `active_turns` 说的是同一件事 —— 原帖就是靠
        # 这两个数（那边是 /health 的 active_turns）对不上才发现自己被骗了。
        assert data["platform"]["active_turns"] == 1
    finally:
        chat.release.set()
        await _until(lambda: runner.active_turns() == 0)
        app.dependency_overrides.pop(get_turn_runner, None)


@pytest.mark.anyio
async def test_status_reports_a_finished_turn_once_nothing_is_running(python_client):
    """兜底方向：没有轮次在跑时，`turn` 还是「最后发生的那件事」，没变。"""
    topic_id = await _topic(python_client)
    runner = TurnRunner(InProcessBroker(), turn_timeout_s=10.0)
    chat = Chat()
    app.dependency_overrides[get_turn_runner] = lambda: runner
    try:
        runner.submit(chat, uuid.UUID(topic_id), author="u1", content="开工", summon=True)
        await asyncio.wait_for(chat.started.wait(), 5)
        chat.release.set()
        await _until(lambda: runner.active_turns() == 0)

        turn = (await python_client.get(f"/api/topics/{topic_id}/status")).json()[
            "data"
        ]["turn"]
        assert turn["status"] == "done"
        assert turn["tools"] == 1
    finally:
        app.dependency_overrides.pop(get_turn_runner, None)
