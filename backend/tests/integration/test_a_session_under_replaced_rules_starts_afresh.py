"""A session started under rules its kind has since replaced is not continued.

A channel's main line that ran for days under the old role (do the work
yourself, acknowledge first) copied its own earlier turns after its role
changed to coordinating tasks: its history taught it the old rules louder than
the new system prompt did. So a session remembers which generation of its
kind's rules it was started under (`Profile.generation`), and a turn that finds
another one starts a new conversation for that place and names the old one so
nothing takes it up again. A kind whose rules did not change goes on.
"""

import uuid

import pytest

from app.api import deps as session_turn_deps
from app.domain.agent.chat import ChatService
from app.domain.agent.compute import ComputePool
from app.domain.agent.turn.store.session_inputs import abandon_opening
from app.domain.agent_instance.adapters import room_teammate
from app.domain.agent_session.services import AgentSessionService
from app.domain.prompts import PROFILES, Kind
from app.domain.topic.services import TopicService
from tests.conftest import StubChannel, settle_turn
from tests.integration.conftest import project_service, registered

pytestmark = pytest.mark.anyio

#: What the main line was running before generations were recorded.
_OLD = "native-before-the-main-line-coordinated"


async def _channel(factory, tmp_path) -> tuple[ChatService, StubChannel, uuid.UUID]:
    screen = StubChannel()
    chat = ChatService(
        work_runner=session_turn_deps.get_work_runner(),
        session_factory=factory,
        compute=ComputePool([screen.runtime]),
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path / "ws"),
    )
    async with factory() as session:
        await registered(session, "u")
        project = await project_service(session).create(name="P", owner_handle="u")
        topic = await TopicService(session).create(
            project_id=project.id, title="Work", created_by="u"
        )
        await session.commit()
    return chat, screen, topic.id


async def _seed(factory, topic_id: uuid.UUID, generation: int) -> str:
    """The channel's teammate has a conversation here, recorded under
    ``generation`` (0: before generations were counted)."""
    async with factory() as session:
        _, handle = await room_teammate(session, topic_id)
        service = AgentSessionService(session)
        await service.ensure(topic_id, handle, harness="claude-code")
        await service.remember(
            conversation_id=topic_id,
            agent_handle=handle,
            resume_token=_OLD,
            harness="claude-code",
            generation=generation,
        )
        await session.commit()
    return handle


async def _turn(chat: ChatService, topic_id: uuid.UUID, content: str) -> None:
    async for _ in chat.converse(
        topic_id=topic_id, author="u", content=content, summon=True
    ):
        pass
    await settle_turn(chat, topic_id)


async def test_a_main_line_from_before_its_role_changed_starts_afresh_once(
    client, tmp_path
):
    async def run():
        factory = client.test_request_factory
        chat, screen, topic_id = await _channel(factory, tmp_path)
        await _seed(factory, topic_id, generation=0)

        await _turn(chat, topic_id, "把 README 的标题改一下")
        first = (screen.last_resume_session_id, screen.last_retired_session_id)
        await _turn(chat, topic_id, "改好了吗")
        second = (screen.last_resume_session_id, screen.last_retired_session_id)
        return first, second, screen.new_session_id

    first, second, started = client.portal.call(run)

    assert first == (None, _OLD), "the old conversation is neither resumed nor reused"
    assert second == (started, None), "the new conversation is the one that goes on"


async def test_a_main_line_started_under_the_current_rules_goes_on(client, tmp_path):
    async def run():
        factory = client.test_request_factory
        chat, screen, topic_id = await _channel(factory, tmp_path)
        await _seed(factory, topic_id, generation=PROFILES[Kind.main_line].generation)
        await _turn(chat, topic_id, "改好了吗")
        return screen.last_resume_session_id, screen.last_retired_session_id

    assert client.portal.call(run) == (_OLD, None)


async def test_a_kind_whose_rules_did_not_change_goes_on(client):
    """Sessions recorded before generations were counted are generation 0, the
    generation every kind but the main line is still at: none of them restart."""

    async def run():
        factory = client.test_request_factory
        async with factory() as session:
            await registered(session, "u")
            project = await project_service(session).create(name="P", owner_handle="u")
            await session.commit()
        conversation = project.root_topic_id
        async with factory() as session:
            service = AgentSessionService(session)
            await service.ensure(conversation, "cheese", harness="claude-code")
            await service.remember(
                conversation_id=conversation,
                agent_handle="cheese",
                resume_token=_OLD,
                harness="claude-code",
                generation=0,
            )
            await session.commit()
        async with factory() as session:
            return await AgentSessionService(session).continuation(
                conversation,
                "cheese",
                harness="claude-code",
                generation=PROFILES[Kind.task].generation,
            )

    found = client.portal.call(run)
    assert (found.resume, found.retired) == (_OLD, None)


async def test_a_conversation_given_up_on_is_named_so_it_is_not_taken_up(
    client, tmp_path
):
    """A conversation the platform gave up on (it could not be compacted) is
    retired the same way: the next start is told which one not to take up."""

    async def run():
        factory = client.test_request_factory
        chat, screen, topic_id = await _channel(factory, tmp_path)
        handle = await _seed(
            factory, topic_id, generation=PROFILES[Kind.main_line].generation
        )
        await abandon_opening(
            factory, conversation_id=topic_id, seat=handle, harness="claude-code"
        )
        await _turn(chat, topic_id, "接着做")
        return screen.last_resume_session_id, screen.last_retired_session_id

    assert client.portal.call(run) == (None, _OLD)
