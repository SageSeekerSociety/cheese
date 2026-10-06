"""@-ing an AI teammate starts its turn, whoever does the @-ing (I13).

An agent's `chat_send` used to land in the room and wake nobody, so two agents
in one room could not hand work to each other. A person's message woke only
the first agent it named. Both now record a delivery per named seat; agent
mentions are capped per room per hour so two agents cannot @ each other
forever. Only the session transport is stubbed: routes, the delivery ledger,
the dispatcher and the runner are the real ones.
"""

import uuid

import pytest
from sqlalchemy import select

from app.core.sandbox_auth import mint_scoped_token
from app.domain.delivery import mention
from app.domain.delivery.models import Delivery
from tests.conftest import wait_work_idle
from tests.integration.conftest import (
    chat_ws_url,
    in_thread,
    join_project_team,
    post_message,
    post_project,
    session_auth_headers,
)
from tests.support.quoted_context import prompt_quote, slide_quote


@pytest.mark.parametrize(
    "quoted_text", ["  @评审\n", "<@{other}>", "<@{current}> <@{other}>"]
)
def test_quoted_mentions_and_paths_never_name_an_ai_or_notify_a_person(
    client, stub_hooks, quoted_text
):
    project_id, room_id, cheese, reviewer = _room_with_two_agents(client)
    join_project_team(client, project_id, "bob")
    channel = client.get(f"/topics/{room_id}/thread").json()["data"]["room_id"]
    added = client.post(
        f"/topics/{channel}/members",
        json={"handle": "bob", "role": "member", "actor": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert added.status_code == 200, added.text
    arrivals = _record_arrivals(stub_hooks)
    text = quoted_text.format(other=reviewer, current=cheese) + "\n<@bob> 原文\n"
    quote = slide_quote(text)
    stored = post_message(
        client,
        room_id,
        "alice",
        {"content": f"<@{cheese}> 解释这一页", "quoted_context": quote},
    )
    wait_work_idle()
    assert stored["content"] == f"<@{cheese}> 解释这一页"
    assert stored["meta"]["quoted_context"] == quote
    assert len(arrivals) == 1, arrivals
    assert prompt_quote(arrivals[0][1]) == quote
    assert _deliveries(client, room_id) == []
    alerts = client.get(
        f"/projects/{project_id}/alerts", headers=session_auth_headers("bob")
    ).json()["data"]["data"]
    assert alerts == []


def test_a_quoted_ai_name_does_not_wake_anyone_without_an_authored_mention(
    client, stub_hooks
):
    _, room_id, cheese, reviewer = _room_with_two_agents(client)
    arrivals = _record_arrivals(stub_hooks)
    quote = slide_quote(f"@评审 <@{cheese}> <@{reviewer}>")
    stored = post_message(
        client, room_id, "alice", {"content": "只是留个说明", "quoted_context": quote}
    )
    wait_work_idle()
    assert not stored["meta"]["agent_recipient"]["mentioned"]
    assert arrivals == []
    assert _deliveries(client, room_id) == []


def test_authored_mentions_still_deliver_the_quote_to_each_named_ai(client, stub_hooks):
    _, room_id, cheese, reviewer = _room_with_two_agents(client)
    arrivals = _record_arrivals(stub_hooks)
    quote = slide_quote("  原页面文字\n")
    post_message(
        client,
        room_id,
        "alice",
        {"content": f"<@{cheese}> <@{reviewer}> 一起解释", "quoted_context": quote},
    )
    wait_work_idle()
    assert len(arrivals) == 2, arrivals
    assert all(prompt_quote(text) == quote for _, text in arrivals)
    [delivery] = _deliveries(client, room_id)
    assert delivery.recipient_handle == reviewer
    assert delivery.state == "received"


def test_an_agent_publication_keeps_quoted_mentions_inert(client, stub_hooks):
    project_id, room_id, cheese, reviewer = _room_with_two_agents(client)
    arrivals = _record_arrivals(stub_hooks)
    quote = slide_quote(f"<@{cheese}> @芝士")
    sent = client.post(
        f"/topics/{room_id}/messages",
        headers=_as(project_id, room_id, cheese),
        json={
            "content": f"<@{reviewer}> 复核页面",
            "request_id": str(uuid.uuid4()),
            "quoted_context": quote,
        },
    )
    assert sent.status_code == 200, sent.text
    wait_work_idle()
    assert sent.json()["data"]["meta"]["quoted_context"] == quote
    [delivery] = _deliveries(client, room_id)
    assert delivery.recipient_handle == reviewer
    assert len(arrivals) == 1, arrivals
    assert prompt_quote(arrivals[0][1]) == quote


def _room_with_two_agents(client):
    project = post_project(client, json={"name": "Mentions"}, owner="alice").json()[
        "data"
    ]
    room = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "Work"},
        headers=session_auth_headers("alice"),
    ).json()["data"]
    reviewer = client.post(
        f"/projects/{project['id']}/agents",
        json={"handle": "reviewer", "display_name": "评审"},
    ).json()["data"]
    r = client.post(
        f"/topics/{room['id']}/members",
        json={"handle": reviewer["seat_handle"], "role": "member", "actor": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text
    members = client.get(f"/topics/{room['id']}/members").json()["data"]["data"]
    seats = [m["member_handle"] for m in members if m["agent"]]
    other = next(s for s in seats if s != reviewer["seat_handle"])
    # Both sit on the channel's roster; they talk in a 支线 of it.
    thread = in_thread(client, room["id"], "alice")
    return project["id"], thread, other, reviewer["seat_handle"]


def _as(project_id, room_id, seat):
    token = mint_scoped_token(
        project_id=project_id, topic_id=room_id, agent_handle=seat
    )
    return {"X-Cheese-Token": token}


def _publish(client, room_id, headers, content):
    r = client.post(
        f"/topics/{room_id}/messages",
        json={"content": content, "request_id": str(uuid.uuid4())},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _deliveries(client, room_id):
    async def read():
        async with client.test_factory() as session:
            return list(
                await session.scalars(
                    select(Delivery).where(
                        Delivery.conversation_id == uuid.UUID(room_id)
                    )
                )
            )

    return client.portal.call(read)


def _record_arrivals(stub_hooks) -> list[tuple[str | None, str]]:
    seen: list[tuple[str | None, str]] = []
    arrive = stub_hooks.arrive

    def spy(topic_id, message, *, agent=None):
        content = message["message"]["content"]
        seen.append(
            (agent, content if isinstance(content, str) else content[0]["text"])
        )
        return arrive(topic_id, message, agent=agent)

    stub_hooks.arrive = spy
    return seen


def test_an_agent_naming_a_teammate_starts_the_teammates_turn(client, stub_hooks):
    project_id, room_id, cheese, reviewer = _room_with_two_agents(client)
    arrivals = _record_arrivals(stub_hooks)

    sent = _publish(
        client, room_id, _as(project_id, room_id, cheese), f"<@{reviewer}> 帮我复核这段"
    )
    wait_work_idle()

    [row] = _deliveries(client, room_id)
    assert row.recipient_handle == reviewer
    assert row.payload["eventType"] == mention.BY_AGENT
    assert row.payload["blockId"] == sent["id"]
    assert row.state == "received", row.last_error
    prompts = [text for _, text in arrivals]
    assert any("帮我复核这段" in p and cheese in p for p in prompts), prompts


def test_publishing_the_same_message_again_does_not_wake_twice(client):
    project_id, room_id, cheese, reviewer = _room_with_two_agents(client)
    headers = _as(project_id, room_id, cheese)
    body = {"content": f"<@{reviewer}> 看一下", "request_id": str(uuid.uuid4())}
    for _ in range(2):
        r = client.post(f"/topics/{room_id}/messages", json=body, headers=headers)
        assert r.status_code == 200, r.text
    wait_work_idle()
    assert len(_deliveries(client, room_id)) == 1


def test_naming_yourself_or_a_person_wakes_nobody(client):
    project_id, room_id, cheese, _ = _room_with_two_agents(client)
    _publish(
        client, room_id, _as(project_id, room_id, cheese), f"<@{cheese}> <@alice> 好了"
    )
    wait_work_idle()
    assert _deliveries(client, room_id) == []


def test_agent_mentions_stop_at_the_hourly_fuse_and_say_so(client, monkeypatch):
    monkeypatch.setattr(mention, "AGENT_MENTIONS_PER_HOUR", 1)
    project_id, room_id, cheese, reviewer = _room_with_two_agents(client)
    _publish(client, room_id, _as(project_id, room_id, cheese), f"<@{reviewer}> 一")
    _publish(client, room_id, _as(project_id, room_id, reviewer), f"<@{cheese}> 二")
    wait_work_idle()

    assert [d.recipient_handle for d in _deliveries(client, room_id)] == [reviewer]
    blocks = client.get(f"/topics/{room_id}/blocks").json()["data"]["data"]
    fused = [
        b for b in blocks if (b.get("meta") or {}).get("event_type") == "mention_fused"
    ]
    assert len(fused) == 1
    assert cheese in fused[0]["meta"]["detail"]


def test_a_person_naming_two_agents_wakes_both(client, stub_hooks):
    project_id, room_id, cheese, reviewer = _room_with_two_agents(client)
    arrivals = _record_arrivals(stub_hooks)
    with client.websocket_connect(chat_ws_url(room_id, "alice")) as ws:
        post_message(
            client,
            room_id,
            "alice",
            {"content": f"<@{cheese}> <@{reviewer}> 一起看一下"},
        )
        while ws.receive_json()["type"] not in ("done", "error"):
            pass
    wait_work_idle()

    # The first one named runs off the message itself, as it always has; the
    # second is a delivery on the same ledger an agent's mention uses.
    [row] = _deliveries(client, room_id)
    assert row.recipient_handle == reviewer
    assert row.payload["eventType"] == mention.BY_PERSON
    assert row.state == "received", row.last_error
    assert len(arrivals) == 2, arrivals
