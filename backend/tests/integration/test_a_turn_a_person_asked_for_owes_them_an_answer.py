"""A turn a person asked for reaches its session owing them an answer; a turn
the platform started for itself does not.

What owing means is the harness's side (`driven/runner.py`, held on every
harness by `tests/unit/test_a_person_is_answered_before_anything_else.py`).
What only the room knows is who spoke, so this checks the room's half: the
session the turn lands in, at the moment it reads the prompt, is holding its
tools back or not.
"""

import uuid

import pytest

from app.api import deps as session_turn_deps
from app.domain.agent.chat import ChatService
from app.domain.agent.compute import ComputePool
from app.domain.agent.harness.driven.runner import reply_owed, reply_owed_path
from app.domain.project.services import ProjectService
from app.domain.topic.services import TopicService
from tests.conftest import StubChannel, settle_turn
from tests.integration.conftest import registered

pytestmark = pytest.mark.anyio


class Screen(StubChannel):
    """A session that notes, as it reads each input, whether it owes a reply."""

    def __init__(self) -> None:
        super().__init__()
        self.owed_when_read: list[bool] = []

    def arrive(self, topic_id, message, *, agent=None) -> None:
        session = self._session_for(topic_id, agent)
        self.owed_when_read.append(
            reply_owed(reply_owed_path(session.state)) is not None
        )
        super().arrive(topic_id, message, agent=agent)


async def _room(factory, tmp_path) -> tuple[ChatService, Screen, uuid.UUID]:
    screen = Screen()
    chat = ChatService(
        work_runner=session_turn_deps.get_work_runner(),
        session_factory=factory,
        compute=ComputePool([screen.runtime], screen.name),
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path / "ws"),
    )
    async with factory() as session:
        await registered(session, "u")
        project = await ProjectService(session).create(name="P", owner_handle="u")
        topic = await TopicService(session).create(
            project_id=project.id, title="Work", created_by="u"
        )
        await session.commit()
    return chat, screen, topic.id


async def test_a_person_who_asks_is_owed_the_first_word(client, tmp_path):
    async def run():
        chat, screen, topic_id = await _room(client.test_request_factory, tmp_path)
        async for _ in chat.converse(
            topic_id=topic_id, author="u", content="修一下构建", summon=True
        ):
            pass
        await settle_turn(chat, topic_id)
        return screen

    screen = client.portal.call(run)
    assert screen.owed_when_read == [True]


async def test_a_turn_the_platform_starts_owes_nobody(client, tmp_path):
    async def run():
        chat, screen, topic_id = await _room(client.test_request_factory, tmp_path)
        async for _ in chat.converse(
            topic_id=topic_id,
            author="system",
            content="巡检一下这间房",
            summon=True,
            nudge_event="平台巡检",
        ):
            pass
        await settle_turn(chat, topic_id)
        return screen

    screen = client.portal.call(run)
    assert screen.owed_when_read == [False]
