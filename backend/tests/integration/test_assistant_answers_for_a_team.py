"""A person's 芝士 answers what waits on them from a team: an invitation into
one, someone asking to join one they run. Let through, it is done as the
person, on that team and no other."""

import uuid

from app.domain.assistant import gate
from tests.conftest import seed_user
from tests.integration.conftest import ask_to_join
from tests.integration.test_assistant import _ledger_user
from tests.integration.test_assistant_agent import (
    _acting,
    _as,
    _asked_in,
    _Gatekeeper,
)


def _bearer(client, handle: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {seed_user(client, handle)}"}


def _team(client, owner: str) -> int:
    made = client.post(
        "/teams",
        json={"name": f"组{uuid.uuid4().hex[:4]}", "intro": "", "description": ""},
        headers=_bearer(client, owner),
    )
    assert made.status_code == 201, made.text
    return made.json()["data"]["team"]["id"]


def _members(client, team: int, owner: str) -> set[int]:
    got = client.get(f"/teams/{team}/members", headers=_bearer(client, owner))
    return {m["user"]["id"] for m in got.json()["data"]["members"]}


def _let_through(client, monkeypatch, token: str, act: dict) -> dict:
    async def looks(session):
        return _Gatekeeper("放", "放")

    monkeypatch.setattr(gate, "gatekeeper", looks)
    got = client.post("/assistant/agent/act", json=act, headers=_as(token))
    assert got.status_code == 200, got.text
    got = got.json()["data"]
    assert got["done"] is True, got
    return got


def _carry_out(client, token: str, got: dict) -> None:
    for call in got["calls"]:
        sent = client.request(
            call["method"],
            call["path"],
            json=call.get("body"),
            headers={**_as(token), "X-Cheese-Pass": got["pass"]},
        )
        assert sent.status_code in (200, 204), sent.text


def test_a_team_invitation_is_accepted_for_the_person(client, monkeypatch):
    team = _team(client, "bob")
    seed_user(client, "alice")
    alice = _ledger_user(client, "alice")[0]
    invited = client.post(
        f"/teams/{team}/invitations",
        json={"userId": alice},
        headers=_bearer(client, "bob"),
    )
    assert invited.status_code in (200, 201), invited.text
    [invitation] = client.get(
        "/users/me/team-invitations",
        params={"status": "PENDING"},
        headers=_bearer(client, "alice"),
    ).json()["data"]["invitations"]
    token = _acting(client, "alice", _asked_in(client, "alice", "接受 bob 的团队邀请"))

    got = _let_through(
        client,
        monkeypatch,
        token,
        {
            "kind": "respond",
            "ref": f"team-invites/{invitation['id']}",
            "choice": "accept",
        },
    )
    _carry_out(client, token, got)

    assert alice in _members(client, team, "bob")


def test_a_request_to_join_the_persons_team_is_approved_there_only(client, monkeypatch):
    """The pass the gate gave for one team's request does not open another's."""
    mine, other = _team(client, "alice"), _team(client, "alice")
    seed_user(client, "carol")
    carol = _ledger_user(client, "carol")[0]
    carols = seed_user(client, "carol")
    asked = ask_to_join(client, mine, carols, "想加入")
    elsewhere = ask_to_join(client, other, carols, "也想加入")
    token = _acting(
        client, "alice", _asked_in(client, "alice", "批准 carol 加入我的团队")
    )

    got = _let_through(
        client,
        monkeypatch,
        token,
        {
            "kind": "respond",
            "ref": f"join-requests/{mine}/{asked}",
            "choice": "accept",
        },
    )
    assert len(got["calls"]) == 1
    stray = client.post(
        f"/teams/{other}/join-requests/{elsewhere}/approve",
        headers={**_as(token), "X-Cheese-Pass": got["pass"]},
    )
    _carry_out(client, token, got)

    assert stray.status_code == 403
    assert carol in _members(client, mine, "alice")
    assert carol not in _members(client, other, "alice")
