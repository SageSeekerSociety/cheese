"""Private chats use sessions with tools, independently of the project's machine."""

import uuid

import pytest

from app.domain.agent.chat import ChatService
from app.domain.agent.compute import ComputePool
from app.domain.block.models import AuthorType, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.project.services import ProjectService
from app.domain.topic.services import TopicService
from tests.conftest import StubChannel, settle_turn

pytestmark = pytest.mark.anyio


class PrivateScreen(StubChannel):
    def __init__(self, name):
        self.name = name
        super().__init__()
        self.prompts = []
        self.openings = []

    async def ensure_ready(self, **kwargs):
        self.openings.append(kwargs)
        return await super().ensure_ready(**kwargs)

    async def send_prompt(self, screen: uuid.UUID, prompt: str) -> bool:
        self.prompts.append(prompt)
        self.starts(screen, session_id="private-session")
        self.stops(screen, "Draft saved.", session_id="private-session")
        return True


@pytest.mark.parametrize("private", [True, False])
async def test_chat_runs_through_a_session(client, tmp_path, private):
    factory = client.test_factory
    central, project_machine = PrivateScreen("device"), PrivateScreen("cloud")
    screen = central if private else project_machine
    svc = ChatService(
        session_factory=factory,
        compute=ComputePool([central.runtime, project_machine.runtime], "cloud"),
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path / "ws"),
    )
    async with factory() as session:
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
        topic_id=topic_id, author="u", content="Prepare a document draft", summon=True
    ):
        pass
    await settle_turn(svc, topic_id)
    assert len(screen.prompts) == 1
    assert not (project_machine if private else central).prompts
    assert screen.openings[0]["memory_scope"] == ("personal" if private else None)
    async with factory() as session:
        blocks = await BlockRepository(session).list_for_topic(topic_id)
    assert any(
        b.author_type == AuthorType.ai
        and b.kind == (BlockKind.message if private else BlockKind.event)
        and b.content == "Draft saved."
        for b in blocks
    )
    if not private:
        assert not any(
            b.author_type == AuthorType.ai and b.kind == BlockKind.message
            for b in blocks
        )
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
