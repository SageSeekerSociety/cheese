"""A 支线 is a conversation with the teammate who was called into it.

Once a person has called an AI teammate into a 支线, everything a person says
there is said to that teammate, as in a task: while it is working the message
joins that turn, and while it is idle the message starts its next one. A
message that @-s only people is said to them, and the teammate does not pick
it up. A teammate's own message calls nobody, and in the channel's main line a
teammate still answers only when someone @-s it.
"""

import asyncio
import time
import uuid

import pytest

from app.api.deps import get_chat_service
from app.core.sandbox_auth import mint_scoped_token
from app.domain.agent.chat import ChatService
from app.domain.agent.realtime.broker import InProcessBroker
from app.domain.agent.runtime import AgentWorkRunner
from app.domain.topic.services import TopicService
from tests.conftest import (
    StubChannel,
    finish_turn,
    settle_turn,
    stub_compute,
    wait_work_idle,
)
from tests.integration.conftest import (
    in_thread,
    post_message,
    post_project,
    project_service,
    registered,
    room_agent_seat,
    room_socket,
    session_auth_headers,
)
from tests.support.threads import thread_in


class WorkingScreen(StubChannel):
    """A session that, once given work, keeps at it until released.

    The first prompt opens a turn; text written while a turn runs is merged
    into it, as on a real screen, which tells the two apart only by whether
    work is running."""

    def __init__(self) -> None:
        super().__init__()
        self.release = asyncio.Event()
        #: Each turn's opening prompt.
        self.prompts: list[str] = []
        #: What reached a turn already running.
        self.merged: list[str] = []
        self._answering: set[asyncio.Task] = set()

    def arrive(
        self, topic_id: uuid.UUID, message: dict, *, agent: str | None = None
    ) -> None:
        content = message["message"]["content"]
        prompt = content if isinstance(content, str) else content[0]["text"]
        if self._answering:
            self.merged.append(prompt)
            return
        self.prompts.append(prompt)
        self.last_prompt = prompt
        self.starts(topic_id)
        self.acknowledges(topic_id, prompt)
        task = asyncio.get_running_loop().create_task(self._answer(topic_id))
        self._answering.add(task)
        task.add_done_callback(self._answering.discard)

    async def _answer(self, topic_id: uuid.UUID) -> None:
        await self.release.wait()
        self.says(topic_id, "done")
        self.stops(topic_id, "done")


async def _until(cond, timeout: float = 5.0) -> None:
    """Give the delivery time without failing here: the assert that follows
    says what is on the screen."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not cond() and loop.time() < deadline:
        await asyncio.sleep(0.01)


class _Channel:
    """A channel with one AI teammate, a 支线 under one of its messages, and
    ``u`` who talks there."""

    def __init__(self, factory, tmp_path) -> None:
        self.factory = factory
        self.screen = WorkingScreen()
        self.runner = AgentWorkRunner(InProcessBroker(), turn_timeout_s=10.0)
        self.service = ChatService(
            work_runner=self.runner,
            session_factory=factory,
            compute=stub_compute(self.screen),
            base_system_prompt="你是芝士。",
            workspace_root=str(tmp_path / "ws"),
        )
        self.room: uuid.UUID
        self.thread: uuid.UUID

    async def open(self) -> "_Channel":
        async with self.factory() as session:
            await registered(session, "u")
            await registered(session, "bob")
            project = await project_service(session).create(name="P", owner_handle="u")
            room = await TopicService(session).create(
                project_id=project.id, title="讨论", created_by="u"
            )
            self.room = room.id
            self.thread = await thread_in(session, room, "u")
            await session.commit()
        return self

    async def say(
        self, text: str, *, where=None, author="u", reply_to=None
    ) -> uuid.UUID:
        return await self.runner.receive_message(
            self.service,
            where or self.thread,
            author=author,
            content=text,
            reply_to=reply_to,
        )

    async def called_and_answered(self) -> None:
        """``u`` calls the teammate in the 支线 and it finishes that turn."""
        await self.say("@芝士 先看一下这段说明")
        await _until(lambda: len(self.screen.prompts) == 1)
        assert len(self.screen.prompts) == 1
        self.screen.release.set()
        await finish_turn(self.service, self.thread)

    async def nothing_more_started(self) -> None:
        await self.runner.drain()
        assert len(self.screen.prompts) == 1, self.screen.prompts
        assert self.screen.merged == []


@pytest.mark.anyio
async def test_a_follow_up_without_an_at_wakes_the_idle_teammate(
    business_db_factory, tmp_path
):
    channel = await _Channel(business_db_factory, tmp_path).open()
    await channel.called_and_answered()

    await channel.say("再查一下出处")

    await _until(lambda: len(channel.screen.prompts) == 2)
    assert len(channel.screen.prompts) == 2, channel.screen.prompts
    assert "再查一下出处" in channel.screen.prompts[1]
    await finish_turn(channel.service, channel.thread)
    await channel.runner.drain()


@pytest.mark.anyio
async def test_someone_else_in_the_channel_wakes_the_teammate_too(
    business_db_factory, tmp_path
):
    """A 支线 has no owner: whoever speaks there speaks to its teammate."""
    channel = await _Channel(business_db_factory, tmp_path).open()
    await channel.called_and_answered()

    await channel.say("我补充一个出处", author="bob")

    await _until(lambda: len(channel.screen.prompts) == 2)
    assert len(channel.screen.prompts) == 2, channel.screen.prompts
    assert "我补充一个出处" in channel.screen.prompts[1]
    await finish_turn(channel.service, channel.thread)
    await channel.runner.drain()


@pytest.mark.anyio
async def test_a_thread_nobody_called_a_teammate_into_calls_nobody(
    business_db_factory, tmp_path
):
    channel = await _Channel(business_db_factory, tmp_path).open()

    await channel.say("这个周五前能定吗")

    await channel.runner.drain()
    assert channel.screen.prompts == []


@pytest.mark.anyio
async def test_a_follow_up_joins_the_turn_running(business_db_factory, tmp_path):
    channel = await _Channel(business_db_factory, tmp_path).open()
    await channel.say("@芝士 去查一下这条链路")
    await _until(lambda: len(channel.screen.prompts) == 1)

    await channel.say("直接说你准备怎么改")

    await _until(lambda: len(channel.screen.merged) == 1)
    assert len(channel.screen.prompts) == 1
    assert [p.split("\n\n", 1)[0] for p in channel.screen.merged] == [
        "[u]: 直接说你准备怎么改"
    ]
    channel.screen.release.set()
    await finish_turn(channel.service, channel.thread)
    await channel.runner.drain()


@pytest.mark.anyio
async def test_the_main_line_without_an_at_calls_nobody(business_db_factory, tmp_path):
    channel = await _Channel(business_db_factory, tmp_path).open()
    await channel.called_and_answered()

    await channel.say("大家下午好", where=channel.room)

    await channel.nothing_more_started()


@pytest.mark.anyio
async def test_a_message_at_someone_else_does_not_call_the_teammate(
    business_db_factory, tmp_path
):
    channel = await _Channel(business_db_factory, tmp_path).open()
    await channel.called_and_answered()

    await channel.say("<@bob> 你也看一下")

    await channel.nothing_more_started()


@pytest.mark.anyio
async def test_an_at_of_a_person_said_by_anyone_does_not_call_the_teammate(
    business_db_factory, tmp_path
):
    channel = await _Channel(business_db_factory, tmp_path).open()
    await channel.called_and_answered()

    await channel.say("<@u> 我觉得是第二种", author="bob")

    await channel.nothing_more_started()


# --- through the routes: two teammates in one channel ----------------------


def _room_with_two_teammates(client) -> tuple[str, str, str]:
    """A channel seating the project's default teammate and one more; returns
    the channel, the second teammate's seat and the default's."""
    p = post_project(client, json={"name": "P"}, owner="alice").json()["data"]
    room = client.post(
        "/topics",
        json={"project_id": p["id"], "title": "T"},
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]
    default = room_agent_seat(client, room)
    made = client.post(f"/projects/{p['id']}/agents", json={"handle": "opus"})
    assert made.status_code == 200, made.text
    teammate = made.json()["data"]["seat_handle"]
    joined = client.post(
        f"/topics/{room}/members",
        json={"handle": teammate, "role": "member", "actor": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert joined.status_code == 200, joined.text
    assert teammate != default
    return room, teammate, default


def _say(client, conversation: str, text: str) -> None:
    """alice says one thing there and waits for whatever it started to end."""
    with room_socket(client, conversation, "alice") as ws:
        post_message(client, conversation, "alice", {"content": text})
        while ws.receive_json()["type"] not in ("done", "error"):
            pass
    _settle(client, conversation)


def _settle(client, conversation: str) -> None:
    service = client.app.dependency_overrides[get_chat_service]()
    client.portal.call(settle_turn, service, uuid.UUID(conversation))
    wait_work_idle()


def _wait_for_prompt(stub_hooks, needle: str) -> None:
    for _ in range(500):
        if needle in (stub_hooks.last_prompt or ""):
            return
        time.sleep(0.01)
    raise AssertionError(f"the teammate never got it: {stub_hooks.last_prompt!r}")


def test_a_follow_up_reaches_the_teammate_called_there(client, stub_hooks):
    """Not the channel's default: the teammate called into this 支线."""
    room, teammate, _ = _room_with_two_teammates(client)
    thread = in_thread(client, room, "alice")
    _say(client, thread, f"<@{teammate}> 这段说明读一下")

    _say(client, thread, "再查一下出处")

    _wait_for_prompt(stub_hooks, "再查一下出处")
    assert client.get(f"/topics/{thread}/thread").json()["data"]["teammate"] == (
        teammate
    )


def test_a_follow_up_reaches_the_teammate_called_in_the_main_line(client, stub_hooks):
    """Called from the channel's main line, the teammate answers in the 支线
    under that message: the 支线 is a conversation with it from the start."""
    room, teammate, _ = _room_with_two_teammates(client)
    with room_socket(client, room, "alice"):
        called = post_message(
            client, room, "alice", {"content": f"<@{teammate}> 这段说明读一下"}
        )
    _wait_for_prompt(stub_hooks, "这段说明读一下")
    opened = client.post(
        f"/blocks/{called['id']}/thread", headers=session_auth_headers("alice")
    )
    assert opened.status_code == 200, opened.text
    thread = opened.json()["data"]["id"]
    _settle(client, thread)

    _say(client, thread, "再查一下出处")

    _wait_for_prompt(stub_hooks, "再查一下出处")


def test_a_teammates_own_message_calls_nobody(client, stub_hooks):
    """Two teammates in a 支线 would otherwise wake each other without end."""
    room, teammate, other = _room_with_two_teammates(client)
    thread = in_thread(client, room, "alice")
    _say(client, thread, f"<@{teammate}> 这段说明读一下")
    _wait_for_prompt(stub_hooks, "这段说明读一下")

    project = client.get(f"/topics/{room}").json()["data"]["project_id"]
    token = mint_scoped_token(project_id=project, topic_id=thread, agent_handle=other)
    said = client.post(
        f"/topics/{thread}/messages",
        json={"request_id": str(uuid.uuid4()), "content": "我这边查完了"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert said.status_code == 200, said.text
    _settle(client, thread)

    assert "我这边查完了" not in (stub_hooks.last_prompt or "")
