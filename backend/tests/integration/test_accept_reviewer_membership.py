"""验收卡递给谁：那个人得是这道门放得进来的人。

A card names the person who will 验收 it, and the door that lets that person
accept asks one question (`accept.py` 的 `_card_actor` → `resolver.authorize_topic`):
are they a member of the card's room? Filing the card asked nothing at all —
`reviewer_handle` went from the request body (or from the project's default
reviewer, which is a setting and remembers who verifies, not who is a member)
straight onto the row. A card could therefore be routed to a handle the room
refuses, and that card is acceptable by nobody: it looks correctly routed on the
card face, and 403s whoever it names at the one step that matters.

Both sources are covered here — the handle named when filing, and the one the
task itself carries (负责人开始时点名的那个) — because they meet the same rule
at the same place.
"""

import itertools
import uuid

from tests.conftest import wait_work_idle
from tests.delivery import delivery_artifact
from tests.integration.conftest import (
    join_project_team,
    post_project,
    room_agent_seat,
    session_auth_headers,
    teammate_task,
)
from tests.machine_work import declare_task, machine_commits

_written = itertools.count()

OWNER = "alice"
MEMBER = "bob"
OTHER_MEMBER = "carol"
# On no team, in no room: a real person handle that this project has never seen.
STRANGER = "mallory"


def _project(client) -> str:
    client.headers.update(session_auth_headers(OWNER))
    r = post_project(client, json={"name": "P"})
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _room(client, pid: str) -> str:
    r = client.post(
        "/topics",
        json={"project_id": pid, "title": "房间"},
        headers=session_auth_headers(OWNER),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _task(client, room: str, title: str, reviewer: str) -> dict:
    """一件负责人开着的活，验收的人是 ``reviewer``。

    停着的那件（芝士自己想的，`teammate_task`）只有这里等得到：人建的活建出来
    即已开始，没有再点一次「开始」的机会 —— 所以「任务上写着谁审」这件事，今天
    只有在这一下里写得上去。"""
    task = teammate_task(client, room, title, owner=OWNER)
    r = client.post(
        f"/topics/{task['id']}/start",
        json={"reviewer_handle": reviewer},
        headers=session_auth_headers(OWNER),
    )
    assert r.status_code == 200, r.text
    wait_work_idle()
    return r.json()["data"]


def _file(client, pid: str, room: str, task_id: str, subject: str, **kw):
    """真的写点东西再递卡 —— 一次没有代码的交付不是交付。"""
    nth = next(_written)
    declare_task(uuid.UUID(pid), uuid.UUID(task_id))
    machine_commits(uuid.UUID(pid), uuid.UUID(task_id), {f"work-{nth}.txt": subject})
    body: dict = {
        "change_subject": subject,
        "focus": "最懂",
        **delivery_artifact(client, room),
    }
    body.update(kw)
    return client.post(f"/topics/{task_id}/accept-card", json=body)


def _cards(client, room: str) -> list[dict]:
    return client.get(f"/topics/{room}/accept-card").json()["data"]["data"]


def _reassign(client, card_id: str, reviewer: str, *, by: str = OWNER):
    return client.post(
        f"/accept-cards/{card_id}/reassign",
        json={"reviewer_handle": reviewer, "focus": "换个人更合适"},
        headers=session_auth_headers(by),
    )


# --- 递卡：名字写在请求体里 -------------------------------------------------


def test_a_card_cannot_be_routed_to_somebody_the_room_refuses(client):
    pid = _project(client)
    room = _room(client, pid)
    task = _task(client, room, "一条活", reviewer=MEMBER)

    r = _file(
        client, pid, room, task["id"], "feat(x): deliver it", reviewer_handle=STRANGER
    )

    assert r.status_code == 403, r.text
    assert STRANGER in r.json()["message"]
    assert r.json()["error"]["i18n"]["key"] == "reviewerNotInTopic"
    # 拒的是这次递卡，不是嘴上说说：卡没落行。
    assert _cards(client, room) == []


def test_a_card_can_be_routed_to_a_member_of_the_room(client):
    pid = _project(client)
    join_project_team(client, pid, MEMBER)
    room = _room(client, pid)
    task = _task(client, room, "一条活", reviewer=MEMBER)

    r = _file(
        client, pid, room, task["id"], "feat(x): deliver it", reviewer_handle=MEMBER
    )

    assert r.status_code == 200, r.text
    assert r.json()["data"]["reviewer_handle"] == MEMBER


def test_a_card_cannot_be_routed_to_an_ai_teammate(client):
    """The room's AI teammate is in the room, but it cannot accept a change in a
    collaborative project — a card routed to it is acceptable by nobody."""
    pid = _project(client)
    room = _room(client, pid)
    seat = room_agent_seat(client, room)
    task = _task(client, room, "一条活", reviewer=OWNER)

    r = _file(
        client, pid, room, task["id"], "feat(x): deliver it", reviewer_handle=seat
    )

    assert r.status_code == 422, r.text
    assert r.json()["error"]["i18n"]["key"] == "reviewerNotAPerson"
    assert _cards(client, room) == []


def test_an_autonomous_project_can_route_a_card_to_its_ai_teammate(client):
    """Autonomous mode is where the AI may accept, so there it may review."""
    client.headers.update(session_auth_headers(OWNER))
    r = post_project(client, json={"name": "P", "ai_mode": "autonomous"})
    assert r.status_code == 200, r.text
    pid = r.json()["data"]["id"]
    room = _room(client, pid)
    seat = room_agent_seat(client, room)
    task = _task(client, room, "一条活", reviewer=seat)

    r = _file(
        client, pid, room, task["id"], "feat(x): deliver it", reviewer_handle=seat
    )

    assert r.status_code == 200, r.text
    assert r.json()["data"]["reviewer_handle"] == seat


def test_a_task_cannot_start_with_an_ai_teammate_reviewing_it(client):
    pid = _project(client)
    room = _room(client, pid)
    seat = room_agent_seat(client, room)
    task = teammate_task(client, room, "一条活", owner=OWNER)

    r = client.post(
        f"/topics/{task['id']}/start",
        json={"reviewer_handle": seat},
        headers=session_auth_headers(OWNER),
    )

    assert r.status_code == 422, r.text
    assert r.json()["error"]["i18n"]["key"] == "reviewerNotAPerson"
    got = client.get(f"/topics/{task['id']}/task", headers=session_auth_headers(OWNER))
    assert got.json()["data"]["started_at"] is None


# --- 改派 -------------------------------------------------------------------


def test_a_card_cannot_be_reassigned_to_somebody_the_room_refuses(client):
    pid = _project(client)
    join_project_team(client, pid, MEMBER)
    room = _room(client, pid)
    task = _task(client, room, "一条活", reviewer=MEMBER)
    card = _file(
        client, pid, room, task["id"], "feat(x): deliver it", reviewer_handle=MEMBER
    ).json()["data"]

    r = _reassign(client, card["id"], STRANGER)

    assert r.status_code == 403, r.text
    assert r.json()["error"]["i18n"]["key"] == "reviewerNotInTopic"
    # 原审阅人还在：一次没生效的改派不该悄悄留下半张卡。
    assert _cards(client, room)[0]["reviewer_handle"] == MEMBER


def test_a_card_cannot_be_reassigned_to_an_ai_teammate(client):
    pid = _project(client)
    join_project_team(client, pid, MEMBER)
    room = _room(client, pid)
    seat = room_agent_seat(client, room)
    task = _task(client, room, "一条活", reviewer=MEMBER)
    card = _file(
        client, pid, room, task["id"], "feat(x): deliver it", reviewer_handle=MEMBER
    ).json()["data"]

    r = _reassign(client, card["id"], seat)

    assert r.status_code == 422, r.text
    assert r.json()["error"]["i18n"]["key"] == "reviewerNotAPerson"
    assert _cards(client, room)[0]["reviewer_handle"] == MEMBER


def test_a_card_can_be_reassigned_to_a_member_of_the_room(client):
    pid = _project(client)
    join_project_team(client, pid, MEMBER)
    join_project_team(client, pid, OTHER_MEMBER)
    room = _room(client, pid)
    task = _task(client, room, "一条活", reviewer=MEMBER)
    card = _file(
        client, pid, room, task["id"], "feat(x): deliver it", reviewer_handle=MEMBER
    ).json()["data"]

    r = _reassign(client, card["id"], OTHER_MEMBER)

    assert r.status_code == 200, r.text
    assert r.json()["data"]["reviewer_handle"] == OTHER_MEMBER


# --- 递卡：名字由任务带着（开始那一刻点名的那个） ---------------------------


def test_a_card_the_task_carries_cannot_go_to_somebody_the_room_refuses(client):
    """任务上写着谁验收，递卡时照抄下来 —— 它记的是「谁验收」，从不问「谁
    是成员」。这条不挡住的话，同一个死卡从另一条路照样进得来。"""
    pid = _project(client)
    room = _room(client, pid)
    task = _task(client, room, "一条活", STRANGER)
    assert task["reviewer_handle"] == STRANGER

    r = _file(client, pid, room, task["id"], "feat(x): deliver it")

    assert r.status_code == 403, r.text
    assert r.json()["error"]["i18n"]["key"] == "reviewerNotInTopic"
    assert _cards(client, room) == []


def test_a_card_the_task_carries_to_a_member_still_goes_through(client):
    pid = _project(client)
    join_project_team(client, pid, MEMBER)
    room = _room(client, pid)
    task = _task(client, room, "一条活", MEMBER)

    r = _file(client, pid, room, task["id"], "feat(x): deliver it")

    assert r.status_code == 200, r.text
    assert r.json()["data"]["reviewer_handle"] == MEMBER
