"""Editing a message after it was sent.

Whoever sent a message may edit it — a person with their session, an agent with
its room credential, through the same route and under the same rule — and
nobody else may. Everyone in the room sees the new text live, marked as edited.
"""

import asyncio
import time
import uuid

import pytest
from sqlalchemy import select

from app.api import deps as session_turn_deps
from app.api.deps import get_chat_service
from app.core.sandbox_auth import mint_scoped_token
from app.domain.agent.chat import ChatService
from app.domain.agent.prompt import _pending_platform_notices, _platform_preamble
from app.domain.block.repositories import BlockRepository
from app.domain.delivery import mention
from app.domain.thread.models import Thread
from app.main import app
from tests.conftest import seed_user, stub_compute, wait_work_idle
from tests.integration.conftest import (
    in_thread,
    inbox,
    join_project_team,
    open_task,
    post_message,
    post_project,
    room_agent_seat,
    room_socket,
    session_auth_headers,
)
from tests.integration.test_agent_mentions import (
    _as,
    _deliveries,
    _publish,
    _record_arrivals,
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
    with room_socket(client, room, author) as ws:
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
    with room_socket(client, room, "bob") as bob:
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
    with room_socket(client, room, "bob") as bob:
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
        {"Authorization": f"Bearer {agent_token}"},  # the room's agent
        {},  # nobody signed in
    ]
    for headers in attempts:
        assert _edit(client, said, "改掉", headers).status_code in (401, 403)
    assert _shown(client, room, said)["content"] == "周五交初稿"


def test_an_agent_edits_its_own_message_through_the_same_route(client):
    room, project = _room(client)
    headers = {
        "Authorization": f"Bearer {mint_scoped_token(project_id=project, topic_id=room)}"
    }
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
    with room_socket(client, room, "alice") as alice:
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
    headers = {
        "Authorization": f"Bearer {mint_scoped_token(project_id=project, topic_id=room)}"
    }

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


def _mentions(client, handle: str) -> list[dict]:
    """The mentions in ``handle``'s inbox."""
    return inbox(client, handle, "MENTION")


def test_an_edit_notifies_only_whom_it_newly_mentions(client):
    for who in ("bob", "carol"):
        seed_user(client, who)
    room, project = _room(client)
    join_project_team(client, project, "carol")
    said = _say(client, room, "alice", "@bob 看一下")
    assert len(_mentions(client, "bob")) == 1
    assert _mentions(client, "carol") == []

    _edit(client, said, "@bob @carol 看一下", session_auth_headers("alice"))
    assert len(_mentions(client, "carol")) == 1, "carol is newly mentioned"
    assert len(_mentions(client, "bob")) == 1, "bob was told when it was sent"

    _edit(client, said, "@bob @carol 再看一下", session_auth_headers("alice"))
    assert len(_mentions(client, "carol")) == 1
    assert len(_mentions(client, "bob")) == 1


def test_an_edit_in_a_task_notifies_the_person_it_newly_names(client):
    """A task's conversation is a conversation: its edit tells the person it
    newly names, and stores the name the way sending the same text does."""
    seed_user(client, "bob")
    room, _ = _room(client)
    task = open_task(client, room)
    said = _say(client, task["id"], "alice", "谁看一下")
    wait_work_idle()
    assert _mentions(client, "bob") == []

    _edit(client, said, "@bob 看一下", session_auth_headers("alice"))

    assert len(_mentions(client, "bob")) == 1
    assert _shown(client, task["id"], said)["content"] == "<@bob> 看一下"


def test_an_edit_in_a_task_wakes_the_teammate_it_newly_names(client, stub_hooks):
    """The seat an edit in a task newly names is delivered to and woken, the
    way the same text sent to the task would have woken it."""
    room = _two_teammates(client)
    channel, other = room["channel"], room["other"]
    task = open_task(client, channel)
    said = _say(client, task["id"], "alice", "接口先别动")
    wait_work_idle()
    arrivals = _record_arrivals(stub_hooks)

    _edit(client, said, f"<@{other}> 接口先别动", session_auth_headers("alice"))
    wait_work_idle()

    [row] = [
        d
        for d in _deliveries(client, task["id"])
        if d.payload.get("eventType") == mention.BY_PERSON
    ]
    assert row.recipient_handle == other
    assert row.payload["blockId"] == said
    assert row.state == "received", row.last_error
    assert any(agent == other for agent, _ in arrivals), arrivals


def _two_teammates(client) -> dict:
    """A channel of alice's with two AI seats on it, and a 支线 of that channel.

    `place` is the 支线 — where a person talks when they call a teammate;
    `channel` is the main line, where a message is answered in its own 支线.
    `own` is the seat the channel falls back to, `other` the one it does not.
    """
    project = post_project(client, json={"name": "编辑点名"}, owner="alice").json()[
        "data"
    ]
    channel = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "话题"},
        headers=session_auth_headers("alice"),
    ).json()["data"]
    other = client.post(
        f"/projects/{project['id']}/agents",
        json={"handle": "reviewer", "display_name": "评审"},
    ).json()["data"]
    added = client.post(
        f"/topics/{channel['id']}/members",
        json={"handle": other["seat_handle"], "role": "member", "actor": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert added.status_code == 200, added.text
    members = client.get(f"/topics/{channel['id']}/members").json()["data"]["data"]
    seats = [m["member_handle"] for m in members if m["agent"]]
    own = next(s for s in seats if s != other["seat_handle"])
    return {
        "project": project["id"],
        "channel": channel["id"],
        "place": in_thread(client, channel["id"], "alice"),
        "own": own,
        "other": other["seat_handle"],
    }


def _reactions_on(client, place: str, block_id: str) -> list[dict]:
    blocks = client.get(f"/topics/{place}/blocks").json()["data"]["data"]
    return next(b for b in blocks if b["id"] == block_id)["reactions"]


def _await_reaction(client, place: str, block_id: str, emoji: str) -> dict:
    """``block_id``'s ``emoji`` reaction, once it lands.

    A receipt is reported by the harness on its own task, so it is not ordered
    against the edit's own response; asking the durable state is the only way to
    ask without racing (the same looseness `test_reactions` documents).
    """
    for _ in range(200):
        for reaction in _reactions_on(client, place, block_id):
            if reaction["emoji"] == emoji:
                return reaction
        time.sleep(0.02)
    raise AssertionError(f"no {emoji} ever landed on {block_id}")


async def _thread_of(client, block_id: str) -> Thread | None:
    async with client.test_factory() as session:
        return await session.scalar(
            select(Thread).where(Thread.root_block_id == uuid.UUID(block_id))
        )


def test_an_edit_that_newly_names_a_teammate_starts_its_turn(client, stub_hooks):
    """@-ing a teammate in an edit does what @-ing it in the message did: the
    seat is recorded on the ledger a message summons through and runs a turn."""
    room = _two_teammates(client)
    place, other = room["place"], room["other"]
    said = _say(client, place, "alice", "接口先别动")
    wait_work_idle()
    arrivals = _record_arrivals(stub_hooks)

    _edit(client, said, f"<@{other}> 接口先别动", session_auth_headers("alice"))
    wait_work_idle()

    [row] = _deliveries(client, place)
    assert row.recipient_handle == other
    assert row.payload["eventType"] == mention.BY_PERSON
    assert row.payload["blockId"] == said
    assert row.state == "received", row.last_error
    assert len(arrivals) == 1, arrivals
    [(_, prompt)] = arrivals
    assert f"<@{other}> 接口先别动" in prompt
    assert "alice" in prompt


def test_a_teammate_the_edit_names_leaves_its_seen_receipt(client):
    """被编辑点到的队友，和发消息时被点到的队友一样，在那条消息上留下自己的 👀。"""
    room = _two_teammates(client)
    place, other = room["place"], room["other"]
    said = _say(client, place, "alice", "接口先别动")
    wait_work_idle()

    _edit(client, said, f"<@{other}> 接口先别动", session_auth_headers("alice"))

    assert _await_reaction(client, place, said, "👀")["authors"] == [other]


def test_an_edit_that_names_nobody_wakes_nobody(client, stub_hooks):
    place = _two_teammates(client)["place"]
    said = _say(client, place, "alice", "接口先别动")
    wait_work_idle()
    arrivals = _record_arrivals(stub_hooks)

    _edit(client, said, "接口可以动了", session_auth_headers("alice"))
    wait_work_idle()

    assert arrivals == []
    assert _deliveries(client, place) == []


def test_an_edit_does_not_wake_a_teammate_the_message_already_named(client, stub_hooks):
    """点名只在**新点**的那一刻叫一次：同一位队友改前改后都在名单里，只起一轮。"""
    room = _two_teammates(client)
    place, other = room["place"], room["other"]
    said = _say(client, place, "alice", f"<@{other}> 接口先别动")
    wait_work_idle()
    before = len(_deliveries(client, place))
    arrivals = _record_arrivals(stub_hooks)

    _edit(client, said, f"<@{other}> 接口可以动了", session_auth_headers("alice"))
    wait_work_idle()

    assert arrivals == []
    assert len(_deliveries(client, place)) == before


def test_an_edit_to_a_main_line_message_hands_the_teammate_to_its_thread(
    client, stub_hooks
):
    """主线上那条消息被编辑后点到的队友，在那条消息的支线里开这一轮。"""
    room = _two_teammates(client)
    channel, other = room["channel"], room["other"]
    said = _say(client, channel, "alice", "接口先别动")
    wait_work_idle()
    arrivals = _record_arrivals(stub_hooks)

    _edit(client, said, f"<@{other}> 接口先别动", session_auth_headers("alice"))
    wait_work_idle()

    thread = asyncio.run(_thread_of(client, said))
    assert thread is not None, "a main-line message is answered in its 支线"
    [row] = _deliveries(client, str(thread.id))
    assert row.recipient_handle == other
    assert row.payload["eventType"] == mention.BY_PERSON
    assert row.state == "received", row.last_error
    assert len(arrivals) == 1, arrivals


def test_an_agents_edit_names_a_teammate_the_way_its_message_does(client, stub_hooks):
    """An agent's own edit is its own output too: the seat it newly names is
    recorded as an agent mention, the kind that counts against the hourly fuse."""
    room = _two_teammates(client)
    project, place = room["project"], room["place"]
    own, other = room["own"], room["other"]
    sent = _publish(client, place, _as(project, place, own), "看一下")
    wait_work_idle()
    arrivals = _record_arrivals(stub_hooks)

    response = _edit(client, sent["id"], f"<@{other}> 看一下", _as(project, place, own))
    assert response.status_code == 200, response.text
    wait_work_idle()

    [row] = _deliveries(client, place)
    assert row.recipient_handle == other
    assert row.payload["eventType"] == mention.BY_AGENT
    assert row.payload["blockId"] == sent["id"]
    assert row.state == "received", row.last_error
    assert len(arrivals) == 1, arrivals


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
        agent_handle=None,
        owes_reply=False,
    ):
        self.pushed.append(text)
        return True

    def working_seat(self, topic_id, agent_handle=None):
        """A session at work in the room, whichever seat is asked about."""
        return "cheese", "cheese"


@pytest.fixture
def running_turn(client):
    """A session at work in the room ``room``, and the screen it reads from."""

    def start(room: str) -> _Screen:
        screen = _Screen()
        service = ChatService(
            work_runner=session_turn_deps.get_work_runner(),
            session_factory=client.test_request_factory,
            base_system_prompt="你是芝士。",
            workspace_root="/tmp/message-edit-ws",
            compute=stub_compute(),
        )
        service._compute.working_seat = screen.working_seat  # type: ignore[method-assign]
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


def _site_lines(client, room: str) -> list[str]:
    """What a member of the room reads in 现场 that the platform wrote there."""
    site = client.get(f"/topics/{room}/transcript", headers=session_auth_headers("bob"))
    assert site.status_code == 200, site.text
    return [i["content"] for i in site.json()["data"]["data"] if i.get("run_record")]


def test_members_read_in_the_site_that_the_agent_was_told_of_an_edit(client):
    room, _ = _room(client)
    said = _say(client, room, "alice", "周五交初稿")
    _read_by_a_turn(client, said)
    assert not [line for line in _site_lines(client, room) if "alice" in line]

    _edit(client, said, "周六交初稿", session_auth_headers("alice"))

    assert len([line for line in _site_lines(client, room) if "alice" in line]) == 1


def test_an_edit_nobody_had_read_leaves_the_site_as_it_was(client):
    room, _ = _room(client)
    said = _say(client, room, "alice", "周五交初稿")
    before = _site_lines(client, room)

    _edit(client, said, "周六交初稿", session_auth_headers("alice"))

    assert _site_lines(client, room) == before


def _visible(client, room: str) -> list[dict]:
    blocks = client.get(f"/topics/{room}/blocks").json()["data"]["data"]
    return [b for b in blocks if (b.get("meta") or {}).get("in_room") is not False]


# ---- In a task ----


def _task(client, room: str) -> str:
    """A task of alice's in ``room``."""
    return open_task(client, room, "子活", owner="alice")["id"]


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
    with room_socket(client, task, "bob") as bob:
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
