"""#189: `git log` can name the 芝士 that wrote a change, and the work it was.

The commit already said who ASKED (`Requested-by`) and who approved
(`Reviewed-by`), and Claude Code signs every commit it writes for anybody
anywhere with `Co-authored-by: Claude Fable 5`. So a reader of the project's
history could learn what model typed the change and nothing at all about which
instance of this platform ran it, or which piece of work inside that instance it
came from — which is what a bad delivery has to be traced back to.

This goes through a real accept on an unbound project, so what it reads is the
commit that actually landed on `main`, not a string a builder returned.
"""

import subprocess
import uuid

from tests.integration.conftest import session_auth_headers
from tests.machine_work import machine_commits


def _project(client) -> str:
    return client.post("/projects", json={"name": "P"}).json()["data"]["id"]


def _room(client, project_id: str) -> str:
    r = client.post(
        "/topics",
        json={"project_id": project_id, "title": "做一个东西", "created_by": "alice"},
    )
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _dispatch(client, room_id: str, title: str) -> str:
    """One piece of work in the room, through the only door that makes one."""
    r = client.post(f"/topics/{room_id}/split", json={"title": title})
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _bind(client, room_id: str, task_id: str, agent_id: str) -> None:
    """认领: the room reports which worker in its session took the work — the id
    Claude Code minted inside the container, which is the whole of what says
    WHICH machine did this."""
    r = client.post(
        f"/topics/{room_id}/tasks/{task_id}/bind",
        json={"agent_id": agent_id},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text


def _deliver(client, room_id: str, subject: str) -> dict:
    r = client.post(
        f"/topics/{room_id}/accept-card",
        json={
            "change_subject": subject,
            "change_body": "Two workers, one delivery.",
            "reviewer_handle": "alice",
            "routing_reason": "最懂",
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _accept(client, card_id: str) -> None:
    r = client.post(
        f"/accept-cards/{card_id}/accept",
        json={"decided_by": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text


def _landed_body(project_id: str) -> str:
    from app.domain.workspace import service as ws

    return subprocess.run(
        ["git", "log", "-1", "--format=%B", "main"],
        cwd=ws.ensure_repo(uuid.UUID(project_id)),
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def test_the_landed_commit_names_the_agent_and_every_worker_in_the_batch(client):
    pid = _project(client)
    room = _room(client, pid)
    mine = _dispatch(client, room, "补 trailer")
    theirs = _dispatch(client, room, "顺手修 flaky 测试")
    _bind(client, room, mine, "ac2c038d44616a2f2")
    _bind(client, room, theirs, "9f1b7c22e0d341a80")
    machine_commits(uuid.UUID(pid), uuid.UUID(room), {"a.txt": "one\n"})

    card = _deliver(client, room, "feat: deliver what two workers made")
    _accept(client, card["id"])

    body = _landed_body(pid)
    assert f"Cheese-Agent: cheese-{uuid.UUID(room).hex[:12]}" in body
    assert f"Cheese-Task: {mine} ac2c038d44616a2f2 补 trailer" in body
    assert f"Cheese-Task: {theirs} 9f1b7c22e0d341a80 顺手修 flaky 测试" in body
    # The trailers that were already there did not move over to make room.
    assert f"Cheese-Topic: {room}" in body
    assert f"Cheese-Card: {card['id']}" in body
    assert "Reviewed-by: alice" in body


def test_work_no_worker_ever_took_still_appears_in_the_history(client):
    """A task row exists from the moment it is dispatched, and a worker is bound
    a moment later — so a delivered task with no `subagent_id` is ordinary, and
    dropping its line would make the batch in the commit smaller than the batch
    that landed."""
    pid = _project(client)
    room = _room(client, pid)
    unclaimed = _dispatch(client, room, "没人认领的活")
    machine_commits(uuid.UUID(pid), uuid.UUID(room), {"a.txt": "one\n"})

    card = _deliver(client, room, "feat: deliver work nobody claimed")
    _accept(client, card["id"])

    body = _landed_body(pid)
    assert f"Cheese-Task: {unclaimed} - 没人认领的活" in body


def test_a_delivery_with_no_work_at_all_still_lands(client):
    """The trailers are a nicety; a room that dispatched nothing must merge
    exactly as it did before they existed."""
    pid = _project(client)
    room = _room(client, pid)
    machine_commits(uuid.UUID(pid), uuid.UUID(room), {"a.txt": "one\n"})

    card = _deliver(client, room, "feat: deliver without a task row")
    _accept(client, card["id"])

    body = _landed_body(pid)
    assert body.splitlines()[0] == "feat: deliver without a task row"
    assert "Cheese-Task" not in body
    assert f"Cheese-Agent: cheese-{uuid.UUID(room).hex[:12]}" in body


def test_unreadable_work_costs_the_trailers_and_not_the_merge(client, monkeypatch):
    """Best-effort, like every other part of attribution: whatever else is wrong,
    a trailer must never be the reason a delivery fails to land."""
    from app.domain.room_task.services import WorkTreeService

    pid = _project(client)
    room = _room(client, pid)
    _dispatch(client, room, "补 trailer")
    machine_commits(uuid.UUID(pid), uuid.UUID(room), {"a.txt": "one\n"})

    async def _blow_up(_self, _tree_id):
        raise RuntimeError("the batch could not be read")

    monkeypatch.setattr(WorkTreeService, "tasks_on", _blow_up)

    card = _deliver(client, room, "feat: land even when the batch is unreadable")
    _accept(client, card["id"])

    body = _landed_body(pid)
    assert body.splitlines()[0] == "feat: land even when the batch is unreadable"
    assert "Cheese-Task" not in body
    assert f"Cheese-Card: {card['id']}" in body
