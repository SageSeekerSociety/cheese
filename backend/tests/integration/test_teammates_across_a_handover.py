"""Two teammates in one room, and the backend changes hands.

Teammates in a room run side by side, each in a conversation of its own. What
the backend taking over does with the turns it finds — which ones are still
being worked on, which messages still need a turn started — is decided for
each teammate, never for the room as a whole: one teammate working says nothing
about another.
"""

import asyncio
import time
import uuid

from sqlalchemy import select

from app.api.deps import get_chat_service, get_work_runner
from app.domain.agent.chat import ChatService
from app.domain.agent.models import AgentTurn
from app.domain.agent_instance.services import AgentInstanceService
from app.domain.identity.handles import agent_instance_handle
from app.domain.topic_membership.services import TopicMemberService
from app.main import app
from tests.conftest import StubChannel, stub_compute
from tests.integration.conftest import chat_ws_url, post_project


class StillWorking(StubChannel):
    """Every teammate takes its prompt and starts a long command."""

    def emit_turn(self, topic_id, prompt, reply, *, agent=None):
        del reply
        self.starts(topic_id, agent=agent)
        self.acknowledges(topic_id, prompt, agent=agent)
        self.uses(topic_id, "Bash", agent=agent, command="sleep 600")


def _service(client, channel: StubChannel) -> ChatService:
    service = ChatService(
        session_factory=client.test_request_factory,
        base_system_prompt="你是芝士。",
        workspace_root="/tmp/teammates-handover-ws",
        compute=stub_compute(channel),
    )
    app.dependency_overrides[get_chat_service] = lambda: service
    return service


def _room_with_a_teammate(client) -> str:
    """A room holding the project's own agent and a second one, "Second"."""
    project = post_project(client, {"name": "Handover", "owner_handle": "alice"})
    data = project.json()["data"]

    async def seat() -> None:
        async with client.test_factory() as session:
            second = await AgentInstanceService(session).create(
                project_id=uuid.UUID(data["id"]),
                handle="second",
                type_name=None,
                display_name="Second",
            )
            await TopicMemberService(session).ensure_agent_seat(
                uuid.UUID(data["root_topic_id"]), agent_instance_handle(second.id)
            )
            await session.commit()

    asyncio.run(seat())
    return data["root_topic_id"]


def _say(client, room: str, content: str) -> None:
    with client.websocket_connect(chat_ws_url(room, "alice")) as ws:
        ws.send_json({"type": "message", "content": content})
        while ws.receive_json()["type"] != "user_block":
            pass


def _prompts(channel: StubChannel, room: str) -> dict[str, list[str]]:
    """What each conversation in the room was handed, by who holds it."""
    return {
        session.actor: [
            str(message["message"]["content"])
            for message in session.written
            if message.get("type") == "user"
        ]
        for (topic, _), session in channel.sessions.items()
        if topic == uuid.UUID(room)
    }


def _heard(channel: StubChannel, room: str, text: str) -> str | None:
    """Who in the room was handed ``text``, if anyone was."""
    return next(
        (
            actor
            for actor, prompts in _prompts(channel, room).items()
            if any(text in prompt for prompt in prompts)
        ),
        None,
    )


def _turns(client, room: str) -> list[AgentTurn]:
    async def read() -> list[AgentTurn]:
        async with client.test_factory() as session:
            return list(
                await session.scalars(
                    select(AgentTurn).where(AgentTurn.topic_id == uuid.UUID(room))
                )
            )

    return asyncio.run(read())


def _until(predicate, what: str) -> None:
    deadline = time.monotonic() + 15
    while not predicate():
        assert time.monotonic() < deadline, what
        time.sleep(0.05)


def test_a_teammate_whose_session_is_gone_has_its_turn_closed(client):
    """Both teammates' sessions were working on their own when the backend
    changed hands, and only the first one's session still answers afterwards.
    The first one's turn goes on; the second one's is over, and is not left
    open because somebody else in the room is still working."""
    room = _room_with_a_teammate(client)
    before = StubChannel()
    _service(client, before)
    _say(client, room, "@芝士 跑一下测试")
    _say(client, room, "@Second 编一下文档")
    _until(
        lambda: (
            _heard(before, room, "跑一下测试") is not None
            and _heard(before, room, "编一下文档") is not None
            and all(turn.stopped_at for turn in _turns(client, room))
        ),
        "the two ordinary turns never finished",
    )
    first = _heard(before, room, "跑一下测试")
    second = _heard(before, room, "编一下文档")
    assert first != second
    fed = {turn.id for turn in _turns(client, room)}

    # Each session wakes up and works without being asked (a worker of its
    # finished): a turn nobody fed, one per teammate.
    topic = uuid.UUID(room)
    before.uses(topic, "Bash", agent=first, command="make test")
    before.uses(topic, "Bash", agent=second, command="make docs")
    _until(
        lambda: len([t for t in _turns(client, room) if t.id not in fed]) == 2,
        "the sessions' own turns were never opened",
    )

    runner = get_work_runner()
    client.portal.call(runner.let_go)
    client.portal.call(before.runtime.stop_listening)

    # The machine kept the first teammate's runner; the second one's is gone.
    after = StubChannel()
    after.root = before.root
    after.sessions = {
        key: session for key, session in before.sessions.items() if key[1] != second
    }
    for session in after.sessions.values():
        session.channel = after
    replaced = _service(client, after)
    assert client.portal.call(replaced.recover_sessions) == 1
    client.portal.call(runner.resume_orphans, replaced)

    still_open = {
        turn.author for turn in _turns(client, room) if turn.stopped_at is None
    }
    assert still_open == {first}, still_open


def test_waiting_messages_for_two_teammates_both_get_their_turn(client):
    """Each teammate was named in a message the previous backend never started
    a turn for. The next backend starts both — one room, two conversations."""
    room = _room_with_a_teammate(client)
    channel = StubChannel()
    service = _service(client, channel)
    runner = get_work_runner()
    runner.hold_turns()
    _say(client, room, "@芝士 修一下登录页")
    _say(client, room, "@Second 看一下注册页")

    client.portal.call(runner.let_go)
    runner.start_turns()
    assert client.portal.call(runner.resume_lost_messages, service) == 2

    _until(
        lambda: (
            _heard(channel, room, "修一下登录页") is not None
            and _heard(channel, room, "看一下注册页") is not None
        ),
        "a waiting message never got its turn",
    )
    assert _heard(channel, room, "修一下登录页") != _heard(
        channel, room, "看一下注册页"
    )


def test_a_waiting_message_is_not_held_back_by_another_teammates_turn(client):
    """The first teammate is mid-turn; a message naming the second one was never
    started. The first one's turn will not read it — it was not addressed to
    them — so the next backend starts the second one's turn beside it."""
    room = _room_with_a_teammate(client)
    channel = StillWorking()
    service = _service(client, channel)
    _say(client, room, "@芝士 跑一下测试")
    _until(
        lambda: any(t.delivered_at for t in _turns(client, room)),
        "the first turn never reached its session",
    )
    runner = get_work_runner()
    runner.hold_turns()
    _say(client, room, "@Second 看一下注册页")

    client.portal.call(runner.let_go)
    runner.start_turns()
    assert client.portal.call(runner.resume_lost_messages, service) == 1

    _until(
        lambda: _heard(channel, room, "看一下注册页") is not None,
        "the second teammate's message never got its turn",
    )
    assert _heard(channel, room, "看一下注册页") != _heard(channel, room, "跑一下测试")
