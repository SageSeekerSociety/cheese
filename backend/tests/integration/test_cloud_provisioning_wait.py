"""A Cloud boot is durable room state, not a failed/retried turn."""

import asyncio
import uuid
from unittest.mock import AsyncMock

from app.api.deps import get_chat_service
from app.domain.agent.chat import ChatService
from app.domain.agent.cloud_provider import CloudChannel
from app.domain.agent.compute import ComputePool
from app.domain.agent.harness.claude_code import ClaudeCodeRuntime
from app.domain.block.models import BlockKind, consumed_turn, prompt_attempts
from app.domain.block.repositories import BlockRepository
from app.domain.topic.repositories import TopicRepository
from app.main import app
from tests.integration.conftest import chat_ws_url


def test_cloud_boot_preserves_pending_input_and_prompt_accounting(client, tmp_path):
    project_id = client.post("/projects", json={"name": "Cloud wait"}).json()["data"][
        "id"
    ]
    topic_id = client.post(
        "/topics",
        json={"project_id": project_id, "title": "Boot", "created_by": "user-1"},
    ).json()["data"]["id"]

    async def _select_cloud() -> None:
        async with client.test_factory() as session:
            topic = await TopicRepository(session).get(uuid.UUID(topic_id))
            topic.compute_profile = "cloud"
            await session.commit()

    asyncio.run(_select_cloud())
    cloud = CloudChannel(
        configured=True,
        ensure_topic_cloud=AsyncMock(),
        read_topic_cloud=AsyncMock(),
    )
    cloud.prepare_topic = AsyncMock(  # type: ignore[method-assign]
        return_value=(False, "⏳ Cloud 机器正在创建")
    )

    def override() -> ChatService:
        return ChatService(
            session_factory=client.test_factory,
            base_system_prompt="你是芝士。",
            workspace_root=str(tmp_path / "workspace"),
            compute=ComputePool([ClaudeCodeRuntime(cloud)], "cloud"),
        )

    # Restored in a finally: this override outlives the test otherwise, and every
    # later test on the same worker then builds on a ChatService bound to a loop
    # that has already closed. The one that pays is whichever test next reaches
    # for a chat service — never this one — so it surfaces as `RuntimeError:
    # Event loop is closed` in an unrelated file that passes in isolation.
    app.dependency_overrides[get_chat_service] = override
    seen: list[str] = []
    try:
        with client.websocket_connect(chat_ws_url(topic_id, "user-1")) as ws:
            ws.send_json({"type": "message", "content": "不要丢掉我", "summon": True})
            while True:
                frame = ws.receive_json()
                seen.append(frame["type"])
                if frame["type"] == "done":
                    break
    finally:
        app.dependency_overrides.pop(get_chat_service, None)

    assert "waiting" in seen
    assert "error" not in seen

    async def _pending_accounting() -> tuple[str, int, str | None]:
        async with client.test_factory() as session:
            blocks = await BlockRepository(session).list_for_topic(uuid.UUID(topic_id))
            message = next(block for block in blocks if block.kind == BlockKind.message)
            return message.content, prompt_attempts(message), consumed_turn(message)

    assert asyncio.run(_pending_accounting()) == ("不要丢掉我", 0, None)
