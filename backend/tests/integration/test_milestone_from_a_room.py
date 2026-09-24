"""钉里程碑 from inside a room, with the credential a turn is actually minted.

A turn's token is scoped to one room. `cheese_milestone` names that room as the
milestone's source, and the room is where the write is authorized — so the
token reaches exactly its own room's project and nothing wider.
"""

import uuid

import pytest

from app.api.deps import get_work_runner
from app.core.sandbox_auth import mint_scoped_token

CONTINUATION = uuid.UUID("22222222-3333-4444-5555-666666666666")


def _room(client, owner: str = "alice") -> tuple[str, str]:
    pid = client.post("/projects", json={"name": "P", "owner_handle": owner}).json()[
        "data"
    ]["id"]
    rid = client.post(
        "/topics", json={"project_id": pid, "title": "房间", "created_by": owner}
    ).json()["data"]["id"]
    return pid, rid


def _turn(pid: str, rid: str) -> dict[str, str]:
    return {"X-Cheese-Token": mint_scoped_token(project_id=pid, topic_id=rid)}


def _titles(client, pid: str) -> list[str]:
    listed = client.get(f"/projects/{pid}/milestones").json()["data"]["data"]
    return [m["title"] for m in listed]


def test_a_turn_pins_a_milestone_for_its_own_room(client):
    pid, rid = _room(client)

    r = client.post(
        f"/projects/{pid}/milestones",
        json={
            "title": "交初稿",
            "due_date": "2026-10-20T00:00:00Z",
            "source_topic_id": rid,
        },
        headers=_turn(pid, rid),
    )

    assert r.status_code == 200, r.text
    assert r.json()["data"]["source_topic_id"] == rid
    assert _titles(client, pid) == ["交初稿"]


def test_a_turn_cannot_pin_one_for_another_room_of_the_same_project(client):
    pid, mine = _room(client)
    other = client.post(
        "/topics", json={"project_id": pid, "title": "隔壁", "created_by": "alice"}
    ).json()["data"]["id"]

    r = client.post(
        f"/projects/{pid}/milestones",
        json={"title": "替隔壁钉", "source_topic_id": other},
        headers=_turn(pid, mine),
    )

    assert r.status_code == 403, r.text
    assert _titles(client, pid) == []


def test_a_turn_cannot_pin_one_without_naming_its_room(client):
    pid, rid = _room(client)

    r = client.post(
        f"/projects/{pid}/milestones",
        json={"title": "不点名"},
        headers=_turn(pid, rid),
    )

    assert r.status_code == 403, r.text
    assert _titles(client, pid) == []


@pytest.mark.parametrize("names", ["its_own_room", "their_room"])
def test_a_turn_cannot_pin_one_in_another_project(client, names):
    pid, rid = _room(client)
    theirs, their_room = _room(client, owner="bob")
    source = rid if names == "its_own_room" else their_room

    r = client.post(
        f"/projects/{theirs}/milestones",
        json={"title": "别人的里程碑", "source_topic_id": source},
        headers=_turn(pid, rid),
    )

    # Refused before any write, whichever layer notices the wrong project first.
    assert r.status_code in (401, 403), r.text
    assert _titles(client, theirs) == []
    assert _titles(client, pid) == []


def test_a_resent_milestone_from_the_same_turn_is_pinned_once(client, monkeypatch):
    pid, rid = _room(client)
    monkeypatch.setattr(get_work_runner(), "continuation_for", lambda _t: CONTINUATION)
    body = {
        "title": "中期汇报",
        "due_date": "2026-10-20T00:00:00Z",
        "source_topic_id": rid,
    }

    first = client.post(
        f"/projects/{pid}/milestones", json=body, headers=_turn(pid, rid)
    )
    again = client.post(
        f"/projects/{pid}/milestones", json=body, headers=_turn(pid, rid)
    )

    assert first.status_code == 200, first.text
    assert again.status_code == 200, again.text
    assert again.json()["data"]["id"] == first.json()["data"]["id"]
    assert _titles(client, pid) == ["中期汇报"]
