"""An agent reaches the rest of the platform API through `platform_request`.

Everything a member can do in the web app and no dedicated tool covers is one
`platform_request` away: found in the backend's own OpenAPI document, then
called on the credential the agent's session is launched with. Whether the call
goes through is the route's answer, from the caller's seat and role — the same
answer a person in that seat gets. These run the tool against this app with that
credential, so what is checked is what the room gets.
"""

import importlib.util
import json
import uuid
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

from app.domain.agent.harness.channel import mint_session_token
from tests.ask_fixtures import legacy_question
from tests.delivery import delivery_headers, delivery_task_id
from tests.integration.conftest import (
    join_project_team,
    post_message,
    post_project,
    room_agent_seat,
    session_auth_headers,
)

_CHEESE = Path(__file__).resolve().parents[2] / "sandbox" / "cheese"


def _load():
    loader = SourceFileLoader("cheese_platform_tools", str(_CHEESE))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


cheese = _load()


class AgentHost:
    """A session host for the agent in `seat`, on the credential it launches with."""

    def __init__(self, client, project, topic, seat):
        self.client = client
        self.environ = {"CHEESE_TOPIC": topic, "CHEESE_PROJECT": project}
        self.headers = {"X-Cheese-Token": mint_session_token(project, topic, seat)}
        self.doc_versions: dict = {}

    def request(self, plan):
        response = self.client.request(
            plan["method"], plan["path"], json=plan.get("body"), headers=self.headers
        )
        if not 200 <= response.status_code < 300:
            raise cheese.PlatformHTTPError(response.status_code, response.text)
        return response.json()

    def call(self, method, path, body=None):
        arguments = {"method": method, "path": path}
        if body is not None:
            arguments["body"] = body
        return json.loads(cheese.run_platform_tool("platform_request", arguments, self))

    def refused(self, method, path, body=None) -> int:
        with pytest.raises(cheese.PlatformHTTPError) as refusal:
            self.call(method, path, body)
        self.reason = refusal.value.body
        return refusal.value.status


@pytest.fixture
def room(client):
    """alice's room in alice's project, its agent, and bob on the project's team."""
    project = post_project(client, json={"name": "P"}, owner="alice")
    pid = project.json()["data"]["id"]
    tid = client.post(
        "/topics",
        json={"project_id": pid, "title": "T"},
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]
    join_project_team(client, pid, "bob")
    seat = room_agent_seat(client, tid)
    return pid, tid, seat, AgentHost(client, pid, tid, seat)


def _roster(client, tid) -> dict[str, str]:
    rows = client.get(f"/topics/{tid}/members").json()["data"]["data"]
    return {row["member_handle"]: row["role"] for row in rows}


def _set_role(client, tid, handle, role):
    r = client.put(
        f"/topics/{tid}/members/{handle}",
        json={"role": role},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text


def test_an_operation_is_found_with_what_it_takes(room):
    *_, agent = room

    found = cheese.run_platform_tool("platform_request", {"find": "reactions"}, agent)

    assert "POST /blocks/{block_id}/reactions" in found
    assert "emoji" in found
    answering = cheese.run_platform_tool(
        "platform_request", {"find": "option question"}, agent
    )
    assert "POST /topics/blocks/{block_id}/answers" in answering
    assert "option" in answering


def test_the_agent_reacts_to_a_message_in_its_room(client, room):
    _, tid, seat, agent = room
    block = post_message(client, tid, "alice", {"content": "分页用哪个？"})["id"]

    agent.call("POST", f"/blocks/{block}/reactions", {"emoji": "👍"})

    blocks = client.get(f"/topics/{tid}/blocks").json()["data"]["data"]
    reactions = next(b for b in blocks if b["id"] == block)["reactions"]
    assert [(r["emoji"], r["authors"]) for r in reactions] == [("👍", [seat])]


def test_the_agent_cannot_react_in_a_room_it_does_not_sit_in(client, room):
    pid, _, seat, agent = room
    other = client.post(
        "/topics",
        json={"project_id": pid, "title": "U"},
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]
    block = post_message(client, other, "alice", {"content": "要不要？"})["id"]
    r = client.delete(
        f"/topics/{other}/members/{seat}", headers=session_auth_headers("alice")
    )
    assert r.status_code == 200, r.text

    assert (
        agent.refused(
            "POST", f"/blocks/{block}/reactions", {"emoji": "👍", "author": seat}
        )
        == 403
    )


def test_the_agent_answers_another_members_question(client, room):
    pid, tid, seat, agent = room
    teammate = client.post(f"/projects/{pid}/agents", json={"handle": "opus"})
    assert teammate.status_code == 200, teammate.text
    other = teammate.json()["data"]["seat_handle"]
    joined = client.post(
        f"/topics/{tid}/members",
        json={"handle": other, "role": "member"},
        headers=session_auth_headers("alice"),
    )
    assert joined.status_code == 200, joined.text
    block = legacy_question(client, tid, seat=other)

    client_op_id = str(uuid.uuid4())
    answered = agent.call(
        "POST",
        f"/topics/blocks/{block['id']}/answers",
        {
            "kind": "option",
            "option": "cursor",
            "client_op_id": client_op_id,
            "expect_version": 0,
        },
    )

    (answer,) = answered["data"]["meta"]["answer_log"]
    assert answer["option"] == "cursor"
    assert answer["by"] == seat
    assert answer["client_op_id"] == client_op_id


def test_a_plain_member_agent_is_refused_the_roster(client, room):
    _, tid, seat, agent = room
    assert _roster(client, tid)[seat] == "member"

    assert agent.refused("POST", f"/topics/{tid}/members", {"handle": "bob"}) == 403
    assert (
        agent.refused("PUT", f"/topics/{tid}/members/alice", {"role": "member"}) == 403
    )
    assert agent.refused("DELETE", f"/topics/{tid}/members/alice") == 403
    assert "bob" not in _roster(client, tid)
    assert _roster(client, tid)["alice"] == "owner"


def test_an_admin_agent_manages_the_roster_as_an_admin_would(client, room):
    _, tid, seat, agent = room
    _set_role(client, tid, seat, "admin")

    agent.call("POST", f"/topics/{tid}/members", {"handle": "bob"})
    assert _roster(client, tid)["bob"] == "member"
    agent.call("PUT", f"/topics/{tid}/members/bob", {"role": "admin"})
    assert _roster(client, tid)["bob"] == "admin"
    agent.call("DELETE", f"/topics/{tid}/members/bob")
    assert "bob" not in _roster(client, tid)

    # What no admin may do, it may not either: leave the room without an owner,
    # or seat someone the project does not have.
    assert agent.refused("DELETE", f"/topics/{tid}/members/alice") == 422
    assert agent.refused("POST", f"/topics/{tid}/members", {"handle": "eve"}) == 422
    assert _roster(client, tid)["alice"] == "owner"


def test_the_agent_reads_its_own_inbox_and_nobody_elses(client, room):
    pid, tid, seat, agent = room
    sent = client.post(
        f"/projects/{pid}/alerts",
        json={
            "level": "light",
            "kind": "change_alert",
            "title": "看一下那条 PR",
            "target_handle": seat,
            "topic_id": tid,
        },
        headers=session_auth_headers("alice"),
    )
    assert sent.status_code == 200, sent.text

    inbox = agent.call("GET", f"/projects/{pid}/inbox")["data"]["data"]

    assert [n["title"] for n in inbox] == ["看一下那条 PR"]
    assert agent.refused("GET", f"/projects/{pid}/inbox?target_handle=alice") == 403


def test_acceptance_stays_with_people(client, room):
    """采纳只归人：the generic path is the routes, and this route says no to an
    agent even when the card names it as the reviewer."""
    _, tid, seat, agent = room
    card = client.post(
        f"/topics/{delivery_task_id(client, tid)}/accept-card",
        headers=delivery_headers(client, tid),
        json={
            "change_subject": "chore(test): file an accept card",
            "reviewer_handle": seat,
            "routing_reason": "自己验",
        },
    )
    assert card.status_code == 200, card.text
    card_id = card.json()["data"]["id"]

    accept = f"/accept-cards/{card_id}/accept"
    assert agent.refused("POST", accept, {"decided_by": seat}) == 422
    assert "AI 不能采纳" in agent.reason
    cards = client.get(f"/topics/{tid}/accept-card").json()["data"]["data"]
    assert cards[0]["status"] == "pending"


def test_a_path_off_the_platform_is_refused_before_anything_is_sent(room):
    *_, agent = room
    sent = []
    agent.request = lambda plan: sent.append(plan)

    for path in ("https://elsewhere.test/x", "//elsewhere.test/x", "topics"):
        with pytest.raises(cheese.PlatformToolError, match="relative API path"):
            agent.call("GET", path)
    assert sent == []
