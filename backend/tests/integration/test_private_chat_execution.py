"""私聊和房间走同一条轮次路径，区别只在它不租手（结论 19）。

算力照房间的选择解析，私聊不改写它；「要不要一双手」才是私聊真正不同的地方，
那一问在 ``test_turn_without_files_rents_no_hands`` 里。
"""

import uuid

import pytest

from app.core.sandbox_auth import mint_scoped_token
from app.domain.agent.chat import ChatService
from app.domain.agent.compute import ComputePool
from app.domain.agent.harness.channel import SESSION_TOKEN_TTL_S
from app.domain.block.models import BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.identity.handles import looks_like_agent_handle
from app.domain.project.services import ProjectService
from app.domain.topic.services import TopicService
from tests.conftest import StubChannel, settle_turn
from tests.integration.conftest import registered

pytestmark = pytest.mark.anyio


class PrivateScreen(StubChannel):
    def __init__(self, name):
        self.name = name
        super().__init__()
        self.prompts = []
        self.openings = []

    async def ensure(self, session, opening):
        # What the channel is started with, and the scoped credential it hands
        # the session's `cheese` CLI: the room's place, signed for the agent
        # acting in it (`claude_code/channel.py` mints the same shape).
        self.openings.append(
            {
                "memory_scope": opening.memory_scope,
                "token": mint_scoped_token(
                    project_id=str(session.project_id),
                    topic_id=str(session.topic_id),
                    ttl_s=SESSION_TOKEN_TTL_S,
                    access_scope="project",
                    agent_handle=opening.agent_handle or session.agent_handle,
                ),
            }
        )
        return await super().ensure(session, opening)

    def emit_turn(self, topic_id: uuid.UUID, prompt: str, reply: str) -> None:
        self.prompts.append(prompt)
        self.starts(topic_id, session_id="private-session")
        self.acknowledges(topic_id, prompt)
        self.stops(topic_id, "Draft saved.", session_id="private-session")


@pytest.mark.parametrize("private", [True, False])
async def test_chat_runs_through_a_session(client, tmp_path, private):
    async def run():
        factory = client.test_request_factory
        central, project_machine = PrivateScreen("device"), PrivateScreen("cloud")
        # 房间选了哪条通道，私聊也走哪条——房间的选择是房间的，不因为私聊而改写。
        screen = project_machine
        svc = ChatService(
            session_factory=factory,
            compute=ComputePool([central.runtime, project_machine.runtime], "cloud"),
            base_system_prompt="You are Cheese.",
            workspace_root=str(tmp_path / "ws"),
        )
        async with factory() as session:
            await registered(session, "u")
            project = await ProjectService(session).create(name="P", owner_handle="u")
            if private:
                topic = await TopicService(session).get_or_create_private(
                    project_id=project.id, user_handle="u"
                )
            else:
                topic = await TopicService(session).create(
                    project_id=project.id, title="Work", created_by="u"
                )
            topic_id = topic.id
            topic.compute_profile = "cloud"
            await session.commit()
        async for _ in svc.converse(
            topic_id=topic_id,
            author="u",
            content="Prepare a document draft",
            summon=True,
        ):
            pass
        await settle_turn(svc, topic_id)
        assert len(screen.prompts) == 1
        # 一条发布路径 (结论 19): the private chat is told what a room is told, and
        # its terminal reply lands in activity exactly as a room's does.
        assert "final responses are not published to chat" in screen.prompts[0]
        # 私聊是名册两席的房间（结论 19）: it is told how to publish in its system
        # prompt like any room, and still told what is particular to a private chat.
        assert "chat_send" in screen.last_system_prompt
        assert ("cheese_remember" in screen.last_system_prompt) is private
        assert not central.prompts
        assert screen.openings[0]["memory_scope"] == ("personal" if private else None)
        async with factory() as session:
            blocks = await BlockRepository(session).list_for_topic(topic_id)
        assert any(
            looks_like_agent_handle(b.author)
            and b.kind == BlockKind.event
            and b.content == "Draft saved."
            for b in blocks
        )
        assert not any(
            looks_like_agent_handle(b.author) and b.kind == BlockKind.message
            for b in blocks
        )
        return screen, topic_id, blocks

    screen, topic_id, blocks = client.portal.call(run)
    if private:
        # Exercise the same scoped credential given to Cheese CLI, against the
        # real document API and database rather than the shell HTTP fixture.
        headers = {"X-Cheese-Token": screen.openings[0]["token"]}
        saved = client.put(
            f"/topics/{topic_id}/doc",
            headers=headers,
            json={
                "content": "# Private draft",
                "author": "cheese",
                "expected_version": 0,
            },
        )
        assert saved.status_code == 200, saved.text
        loaded = client.get(f"/topics/{topic_id}/doc", headers=headers)
        assert loaded.status_code == 200, loaded.text
        assert loaded.json()["data"]["content"] == "# Private draft"
