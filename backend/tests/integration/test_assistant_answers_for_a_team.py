"""A person's 芝士 answers what waits on them from a team: an invitation into
one, someone asking to join one they run. Let through, it is done as the
person, on that team and no other."""

import uuid

from app.domain.assistant import gate
from tests.conftest import seed_user
from tests.integration.conftest import ask_to_join, create_approved_space
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
        assert 200 <= sent.status_code < 300, sent.text


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


def _space(client, owner: str) -> int:
    made = create_approved_space(
        client,
        json={
            "name": f"课{uuid.uuid4().hex[:4]}",
            "intro": "一门课",
            "description": "题目板",
            "avatarId": 1,
            "taskTemplates": [],
        },
        headers=_bearer(client, owner),
    )
    assert made.status_code == 201, made.text
    return made.json()["data"]["space"]["id"]


def _do(client, monkeypatch, handle: str, said: str, act: dict) -> None:
    token = _acting(client, handle, _asked_in(client, handle, said))
    _carry_out(client, token, _let_through(client, monkeypatch, token, act))


def test_the_person_manages_their_teams_members(client, monkeypatch):
    team = _team(client, "alice")
    seed_user(client, "dave")
    dave = _ledger_user(client, "dave")[0]

    _do(
        client,
        monkeypatch,
        "alice",
        "邀请 dave 进我的团队",
        {"kind": "member", "where": f"teams/{team}", "who": "dave"},
    )
    [invitation] = client.get(
        "/users/me/team-invitations",
        params={"status": "PENDING"},
        headers=_bearer(client, "dave"),
    ).json()["data"]["invitations"]
    assert invitation["team"]["id"] == team
    client.post(
        f"/users/me/team-invitations/{invitation['id']}/accept",
        headers=_bearer(client, "dave"),
    )
    _do(
        client,
        monkeypatch,
        "alice",
        "把 dave 设成团队管理员",
        {"kind": "member", "where": f"teams/{team}", "who": "dave", "role": "admin"},
    )

    roles = {
        m["user"]["id"]: m["role"]
        for m in client.get(
            f"/teams/{team}/members", headers=_bearer(client, "alice")
        ).json()["data"]["members"]
    }
    assert roles[dave] == "ADMIN"


def test_the_person_manages_their_spaces_members_and_managers(client, monkeypatch):
    space = _space(client, "alice")
    seed_user(client, "erin")
    erin = _ledger_user(client, "erin")[0]
    where = f"spaces/{space}"

    _do(
        client,
        monkeypatch,
        "alice",
        "把 erin 加进我的空间",
        {"kind": "member", "where": where, "who": "erin"},
    )
    members = client.get(
        f"/spaces/{space}/members", headers=_bearer(client, "alice")
    ).json()["data"]
    assert erin in {m["user"]["id"] for m in members["members"]}

    _do(
        client,
        monkeypatch,
        "alice",
        "让 erin 当空间管理员",
        {"kind": "member", "where": where, "who": "erin", "role": "admin"},
    )
    assert erin in _managers(client, space, "alice")

    # Stepping someone down is asked first, by the person's default rule.
    token = _acting(client, "alice", _asked_in(client, "alice", "撤掉 erin 的管理员"))
    asked = client.post(
        "/assistant/agent/act",
        json={"kind": "member", "where": where, "who": "erin", "role": "member"},
        headers=_as(token),
    ).json()["data"]
    assert asked["done"] is False
    assert (
        asked["card"]["summary"]
        == f"撤掉 erin 在空间「{_name(client, space)}」的管理员身份"
    )
    assert erin in _managers(client, space, "alice")


def _name(client, space: int) -> str:
    got = client.get(f"/spaces/{space}", headers=_bearer(client, "alice"))
    return got.json()["data"]["space"]["name"]


def _managers(client, space: int, owner: str) -> set[int]:
    got = client.get(f"/spaces/{space}/managers", headers=_bearer(client, owner))
    assert got.status_code == 200, got.text
    return {m["userId"] for m in got.json()["data"]["managers"]}


def _read(client, handle: str, ref: str) -> tuple[int, str]:
    token = _acting(client, handle, _asked_in(client, handle, "看看"))
    got = client.post("/assistant/agent/read", json={"ref": ref}, headers=_as(token))
    return got.status_code, got.json().get("data", {}).get("text", "")


def test_it_reads_the_teams_and_spaces_it_manages_people_in(client):
    """What 芝士 reads before managing people: the person's teams and their
    role, a team's members, a space's managers and members — and nothing of
    a team or space the person is not in."""
    team = _team(client, "alice")
    space = _space(client, "alice")
    seed_user(client, "frank")

    status, mine = _read(client, "alice", "teams")
    assert status == 200 and f"teams/{team}" in mine and "所有者" in mine
    status, members = _read(client, "alice", f"teams/{team}")
    assert status == 200 and "alice" in members
    status, people = _read(client, "alice", f"spaces/{space}/people")
    assert status == 200 and "管理员：alice" in people

    assert _read(client, "frank", f"teams/{team}")[0] == 404
    assert _read(client, "frank", f"spaces/{space}/people")[0] == 404


def test_it_lists_the_spaces_the_person_is_in_with_the_ref_that_acts_on_each(client):
    """Asked to change a space by its name, 芝士 finds its ref by listing the
    person's spaces, and a space they are not in is not among them."""
    mine = _space(client, "alice")
    theirs = _space(client, "bob")

    status, listed = _read(client, "alice", "spaces")

    assert status == 200
    assert f"spaces/{mine}" in listed and "你是管理员" in listed
    assert f"spaces/{theirs}" not in listed
