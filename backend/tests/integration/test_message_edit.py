"""Editing a message after it was sent.

Whoever sent a message may edit it — a person with their session, an agent with
its room credential, through the same route and under the same rule — and
nobody else may. Everyone in the room sees the new text live, marked as edited.
"""

import asyncio
import uuid

import pytest

from app.api.deps import get_chat_service
from app.core.sandbox_auth import mint_scoped_token
from app.domain.agent.chat import ChatService
from app.domain.agent.prompt import _pending_platform_notices, _platform_preamble
from app.domain.block.repositories import BlockRepository
from app.main import app
from tests.conftest import stub_compute, wait_work_idle
from tests.integration.conftest import (
    chat_ws_url,
    in_thread,
    join_project_team,
    open_task,
    post_message,
    post_project,
    room_agent_seat,
    session_auth_headers,
)


def _room(client) -> tuple[str, str]:
    """A room of alice's project that bob is also in; returns (room, project)."""
    project = post_project(client, json={"name": "P"}, owner="alice").json()["data"]
    join_project_team(client, project["id"], "bob")
    room = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "话题"},
        headers=session_auth_headers("alice"),
    ).json()["data"]
    return room["id"], project["id"]


def _say(client, room: str, author: str, content: str) -> str:
    """Post a plain message as ``author`` the way the browser does; its id."""
    with client.websocket_connect(chat_ws_url(room, author)) as ws:
        post_message(client, room, author, {"content": content})
        block_id = ""
        while True:
            frame = ws.receive_json()
            if frame["type"] == "user_block":
                block_id = frame["block"]["id"]
            if frame["type"] in ("done", "error"):
                break
    assert block_id
    return block_id


def _edit(client, block_id: str, content: str, headers: dict):
    return client.patch(
        f"/blocks/{block_id}", json={"content": content}, headers=headers
    )


def _shown(client, room: str, block_id: str) -> dict:
    blocks = client.get(f"/topics/{room}/blocks").json()["data"]["data"]
    return next(b for b in blocks if b["id"] == block_id)


def _next_update(ws) -> dict:
    while True:
        frame = ws.receive_json()
        if frame["type"] == "block_updated":
            return frame["block"]


def test_the_author_edits_and_the_room_sees_it_live(client):
    room, _ = _room(client)
    said = _say(client, room, "alice", "周五交初稿")
    with client.websocket_connect(chat_ws_url(room, "bob")) as bob:
        response = _edit(client, said, "周六交初稿", session_auth_headers("alice"))
        assert response.status_code == 200, response.text
        seen = _next_update(bob)
    assert seen["id"] == said
    assert seen["content"] == "周六交初稿"
    assert seen["meta"]["edited_at"]
    stored = _shown(client, room, said)
    assert stored["content"] == "周六交初稿"
    assert stored["meta"]["edited_at"]


def test_an_unedited_message_carries_no_edited_mark(client):
    room, _ = _room(client)
    said = _say(client, room, "alice", "周五交初稿")
    assert not (_shown(client, room, said).get("meta") or {}).get("edited_at")


def test_an_edit_keeps_the_reactions_on_the_message(client):
    room, _ = _room(client)
    said = _say(client, room, "alice", "周五交初稿")
    client.post(
        f"/blocks/{said}/reactions",
        json={"emoji": "👍"},
        headers=session_auth_headers("bob"),
    )
    with client.websocket_connect(chat_ws_url(room, "bob")) as bob:
        _edit(client, said, "周六交初稿", session_auth_headers("alice"))
        seen = _next_update(bob)
    assert [r["emoji"] for r in seen["reactions"]] == ["👍"]


def test_a_name_typed_in_an_edit_becomes_a_mention(client):
    room, _ = _room(client)
    said = _say(client, room, "alice", "谁来看一下")
    _edit(client, said, "@bob 来看一下", session_auth_headers("alice"))
    assert _shown(client, room, said)["content"] == "<@bob> 来看一下"


def test_nobody_else_edits_a_persons_message(client):
    room, project = _room(client)
    said = _say(client, room, "alice", "周五交初稿")
    agent_token = mint_scoped_token(project_id=project, topic_id=room)
    attempts = [
        session_auth_headers("bob"),  # in the room, not the author
        session_auth_headers("mallory"),  # not in the project
        {"X-Cheese-Token": agent_token},  # the room's agent
        {},  # nobody signed in
    ]
    for headers in attempts:
        assert _edit(client, said, "改掉", headers).status_code in (401, 403)
    assert _shown(client, room, said)["content"] == "周五交初稿"


def test_an_agent_edits_its_own_message_through_the_same_route(client):
    room, project = _room(client)
    headers = {"X-Cheese-Token": mint_scoped_token(project_id=project, topic_id=room)}
    posted = client.post(
        f"/topics/{room}/messages",
        json={
            "content": "先看 issue",
            "request_id": "00000000-0000-4000-8000-000000000001",
        },
        headers=headers,
    )
    assert posted.status_code == 200, posted.text
    message = posted.json()["data"]
    assert message["author"] == room_agent_seat(client, room)
    with client.websocket_connect(chat_ws_url(room, "alice")) as alice:
        response = _edit(client, message["id"], "先看 issue，再写测试", headers)
        assert response.status_code == 200, response.text
        seen = _next_update(alice)
    assert seen["content"] == "先看 issue，再写测试"
    assert seen["meta"]["edited_at"]
    # …and its message is its own: alice cannot rewrite what the agent said.
    assert (
        _edit(client, message["id"], "改掉", session_auth_headers("alice")).status_code
        == 403
    )


def test_only_messages_are_edited(client):
    room, _ = _room(client)
    assert (
        _edit(
            client,
            "00000000-0000-4000-8000-00000000abcd",
            "x",
            session_auth_headers("alice"),
        ).status_code
        == 404
    )
    said = _say(client, room, "alice", "周五交初稿")
    assert _edit(client, said, "   ", session_auth_headers("alice")).status_code == 422


# An edit stores what sending the same text in that room stores: a person's
# message and an agent's, in a shared room and in a private one.
SAID = "@bob 和 @芝士 看一下，@alice 也看"


def _private_room(client) -> tuple[str, str, str]:
    """A private chat between a member and the project's agent."""
    project = post_project(client, json={"name": "P"}, owner="user-1").json()["data"]
    room = client.get(
        f"/projects/{project['id']}/private-chat", params={"user_handle": "user-1"}
    ).json()["data"]
    return room["id"], project["id"], "user-1"


def _shared_room(client) -> tuple[str, str, str]:
    """A 支线 of a shared channel: where a message calling 芝士 is answered."""
    room, project = _room(client)
    return in_thread(client, room, "alice"), project, "alice"


ROOMS = pytest.mark.parametrize(
    "make_room", [_shared_room, _private_room], ids=["shared", "private"]
)


def _stored(client, room: str, block_id: str) -> str:
    return _shown(client, room, block_id)["content"]


@ROOMS
def test_a_persons_edit_stores_what_sending_it_stores(client, make_room):
    room, _, person = make_room(client)
    sent = _say(client, room, person, SAID)
    edited = _say(client, room, person, "先占个位")
    response = _edit(client, edited, SAID, session_auth_headers(person))
    assert response.status_code == 200, response.text
    assert _stored(client, room, edited) == _stored(client, room, sent)


@ROOMS
def test_an_agents_edit_stores_what_publishing_it_stores(client, make_room):
    room, project, _ = make_room(client)
    headers = {"X-Cheese-Token": mint_scoped_token(project_id=project, topic_id=room)}

    def publish(content: str) -> str:
        response = client.post(
            f"/topics/{room}/messages",
            json={"content": content, "request_id": str(uuid.uuid4())},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        return response.json()["data"]["id"]

    sent = publish(SAID)
    edited = publish("先占个位")
    response = _edit(client, edited, SAID, headers)
    assert response.status_code == 200, response.text
    assert _stored(client, room, edited) == _stored(client, room, sent)


# ---- What an edit does beyond the text ----


def _mentions(client, project: str, handle: str) -> list[dict]:
    """The @ alerts ``handle`` has in this project."""
    response = client.get(
        f"/projects/{project}/alerts", headers=session_auth_headers(handle)
    )
    assert response.status_code == 200, response.text
    return [a for a in response.json()["data"]["data"] if a["kind"] == "MENTION"]


def test_an_edit_notifies_only_whom_it_newly_mentions(client):
    room, project = _room(client)
    join_project_team(client, project, "carol")
    said = _say(client, room, "alice", "@bob 看一下")
    assert len(_mentions(client, project, "bob")) == 1
    assert _mentions(client, project, "carol") == []

    _edit(client, said, "@bob @carol 看一下", session_auth_headers("alice"))
    assert len(_mentions(client, project, "carol")) == 1, "carol is newly mentioned"
    assert len(_mentions(client, project, "bob")) == 1, "bob was told when it was sent"

    _edit(client, said, "@bob @carol 再看一下", session_auth_headers("alice"))
    assert len(_mentions(client, project, "carol")) == 1
    assert len(_mentions(client, project, "bob")) == 1


def _read_by_a_turn(client, block_id: str) -> None:
    """Stamp a message as read, the way a turn's clean stop does."""

    async def stamp() -> None:
        async with client.test_factory() as session:
            await BlockRepository(session).mark_consumed(
                [uuid.UUID(block_id)], uuid.uuid4()
            )
            await session.commit()

    asyncio.run(stamp())


async def _waiting_notices(client, room: str) -> list:
    async with client.test_factory() as session:
        history = await BlockRepository(session).turn_history(uuid.UUID(room))
    return _pending_platform_notices(history)


class _Screen:
    """The session a running turn is on: records what the platform pushes."""

    def __init__(self) -> None:
        self.pushed: list[str] = []

    async def steer(
        self,
        topic_id,
        text,
        images=None,
        *,
        register_input=None,
        expected_work_id=None,
        agent_handle=None,
        owes_reply=False,
    ):
        self.pushed.append(text)
        return True


@pytest.fixture
def running_turn(client):
    """A turn running in the room ``room``, and the screen it reads from."""

    def start(room: str) -> _Screen:
        screen = _Screen()
        service = ChatService(
            session_factory=client.test_request_factory,
            base_system_prompt="你是芝士。",
            workspace_root="/tmp/message-edit-ws",
            compute=stub_compute(),
        )
        service.live.active_turn_ids[uuid.UUID(room)] = {uuid.uuid4()}
        service._compute.steer = screen.steer  # type: ignore[method-assign]
        app.dependency_overrides[get_chat_service] = lambda: service
        return screen

    yield start
    app.dependency_overrides.pop(get_chat_service, None)


def test_a_running_turn_is_told_a_message_it_read_was_edited(client, running_turn):
    room, _ = _room(client)
    said = _say(client, room, "alice", "周五交初稿")
    _read_by_a_turn(client, said)
    screen = running_turn(room)

    _edit(client, said, "周六交初稿", session_auth_headers("alice"))

    assert len(screen.pushed) == 1
    assert "改了之前发的一条消息" in screen.pushed[0]
    assert "周六交初稿" in screen.pushed[0]


def test_the_next_turn_reads_the_edit_when_no_turn_is_running(client):
    room, _ = _room(client)
    said = _say(client, room, "alice", "周五交初稿")
    _read_by_a_turn(client, said)

    _edit(client, said, "周六交初稿", session_auth_headers("alice"))

    [notice] = asyncio.run(_waiting_notices(client, room))
    assert "周六交初稿" in _platform_preamble([notice])
    # The room already shows the edit on the message; the notice is the agent's.
    assert notice.id not in {m["id"] for m in _visible(client, room)}


def test_an_edit_to_an_unread_message_needs_no_notice(client, running_turn):
    room, _ = _room(client)
    said = _say(client, room, "alice", "周五交初稿")
    screen = running_turn(room)

    _edit(client, said, "周六交初稿", session_auth_headers("alice"))

    # The next turn reads the message itself, as it now reads.
    assert screen.pushed == []
    assert asyncio.run(_waiting_notices(client, room)) == []


def _visible(client, room: str) -> list[dict]:
    blocks = client.get(f"/topics/{room}/blocks").json()["data"]["data"]
    return [b for b in blocks if (b.get("meta") or {}).get("in_room") is not False]


# ---- In a task ----


def _task(client, room: str) -> str:
    """A task of alice's in ``room``."""
    return open_task(client, room, "子活", owner="alice", start=False)["id"]


def _say_in_task(client, room: str, task: str, author: str, content: str) -> str:
    response = client.post(
        f"/topics/{task}/messages",
        json={"request_id": str(uuid.uuid4()), "content": content},
        headers=session_auth_headers(author),
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["id"]


def test_a_task_message_is_edited_by_its_author_under_the_same_rules(client):
    room, _ = _room(client)
    task = _task(client, room)
    said = _say_in_task(client, room, task, "alice", "接口先别动")
    assert _edit(client, said, "改掉", session_auth_headers("bob")).status_code == 403
    with client.websocket_connect(chat_ws_url(task, "bob")) as bob:
        response = _edit(client, said, "接口可以动了", session_auth_headers("alice"))
        assert response.status_code == 200, response.text
        seen = _next_update(bob)
    assert seen["id"] == said
    assert seen["content"] == "接口可以动了"
    assert seen["meta"]["edited_at"]
    task_blocks = client.get(f"/topics/{task}/blocks").json()["data"]["data"]
    assert [b["content"] for b in task_blocks if b["id"] == said] == ["接口可以动了"]


def test_a_task_edit_reaches_the_tasks_agent_the_way_the_message_did(
    client, stub_hooks
):
    """A message in a task is said to the task's own session, and so is an edit
    to it."""
    room, _ = _room(client)
    task = _task(client, room)
    said = _say_in_task(client, room, task, "alice", "接口先别动")
    wait_work_idle()
    assert "接口先别动" in stub_hooks.told

    _edit(client, said, "接口可以动了", session_auth_headers("alice"))
    wait_work_idle()

    assert "接口可以动了" in stub_hooks.told
