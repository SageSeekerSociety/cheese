"""Private chats use sessions with tools, independently of the project's machine."""

import uuid

import pytest

from app.domain.agent.chat import ChatService
from app.domain.block.models import AuthorType, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.project.services import ProjectService
from app.domain.topic.services import TopicService
from tests.conftest import StubChannel, settle_turn, stub_compute

pytestmark = pytest.mark.anyio


class PrivateScreen(StubChannel):
    def __init__(self):
        super().__init__()
        self.prompts = []

    async def send_prompt(self, screen: uuid.UUID, prompt: str) -> bool:
        self.prompts.append(prompt)
        self.starts(screen, session_id="private-session")
        self.stops(screen, "Draft saved.", session_id="private-session")
        return True


@pytest.mark.parametrize("private", [True, False])
async def test_chat_runs_through_a_session(client, tmp_path, private):
    factory = client.test_factory
    screen = PrivateScreen()
    svc = ChatService(
        session_factory=factory,
        compute=stub_compute(screen),
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
        await session.commit()
    async for _ in svc.converse(
        topic_id=topic_id, author="u", content="Prepare a document draft", summon=True
    ):
        pass
    await settle_turn(svc, topic_id)
    assert len(screen.prompts) == 1
    async with factory() as session:
        blocks = await BlockRepository(session).list_for_topic(topic_id)
    assert any(
        b.author_type == AuthorType.ai
        and b.kind == BlockKind.message
        and b.content == "Draft saved."
        for b in blocks
    )
