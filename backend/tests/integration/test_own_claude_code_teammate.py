"""A member's own Claude Code as a teammate in a project (#2991).

It belongs to its owner and follows them into every project they are in, as
long as the project lets members bring their own and the owner has logged it
in on a machine. Only its owner may call it — by naming it, giving it a task
they are responsible for, or chatting with it — because it runs on its owner's
own account, which may serve its owner alone. It is not one of the project's
teammates the others pick from or the agents name.
"""

import asyncio
import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from app.domain.device.models import DeviceClaudeLoginRow
from app.domain.device.supply import Supply
from app.domain.device.wiring import sql_device_service
from app.domain.user.models import User, UserProfile
from tests.integration.conftest import (
    join_project_team,
    new_project,
    open_task,
    post_message,
    session_auth_headers,
)


def _log_in_claude_code(client, handle: str) -> None:
    """``handle`` has logged in their own Claude Code on a machine of theirs."""

    async def run():
        async with client.test_factory() as db:
            user = await db.scalar(select(User).where(User.username == handle))
            devices = sql_device_service(db)
            code = await devices.start(f"{handle}-laptop")
            device = await devices.approve(
                code, owner_user_id=user.id, supply=Supply.self_hosted
            )
            db.add(
                DeviceClaudeLoginRow(
                    device_id=device.device_id,
                    installed=True,
                    logged_in=True,
                    checked_at=datetime.now(UTC),
                )
            )
            await db.commit()

    asyncio.run(run())


def _project_with_bob(client):
    project = new_project(client, "Own teammate", owner="alice")
    join_project_team(client, project["id"], "bob")
    room = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "Room"},
        headers=session_auth_headers("alice"),
    ).json()["data"]
    return project["id"], room["id"]


def _members(client, project_id, viewer):
    r = client.get(
        f"/projects/{project_id}/members", headers=session_auth_headers(viewer)
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["data"]


def _own_row(rows, owner):
    found = [row for row in rows if row.get("owner_handle") == owner]
    return found[0] if found else None


def test_it_follows_its_owner_into_the_project_once_logged_in(client):
    project_id, _room = _project_with_bob(client)

    assert _own_row(_members(client, project_id, "alice"), "alice") is None
    _log_in_claude_code(client, "alice")
    own = _own_row(_members(client, project_id, "alice"), "alice")

    assert own is not None and own["agent"]
    assert own["name"].endswith("的 Claude Code")
    # Everyone sees whose it is.
    assert _own_row(_members(client, project_id, "bob"), "alice") == own


def test_a_project_closed_to_them_has_none(client):
    project_id, _room = _project_with_bob(client)
    _log_in_claude_code(client, "alice")
    r = client.put(
        f"/projects/{project_id}/own-agents",
        json={"allowed": False},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text
    assert _own_row(_members(client, project_id, "alice"), "alice") is None


def test_only_a_manager_closes_the_project_to_them(client):
    project_id, _room = _project_with_bob(client)
    r = client.put(
        f"/projects/{project_id}/own-agents",
        json={"allowed": False},
        headers=session_auth_headers("bob"),
    )
    assert r.status_code == 403, r.text
    listed = client.get(
        f"/projects/{project_id}/own-agents", headers=session_auth_headers("bob")
    ).json()["data"]
    assert listed["allowed"] is True and listed["can_manage"] is False


def test_its_owner_calls_it_and_nobody_else_does(client):
    project_id, room_id = _project_with_bob(client)
    _log_in_claude_code(client, "alice")
    seat = _own_row(_members(client, project_id, "alice"), "alice")["handle"]

    joined = client.post(f"/topics/{room_id}/join", headers=session_auth_headers("bob"))
    assert joined.status_code == 200, joined.text
    mine = post_message(client, room_id, "alice", {"content": f"<@{seat}> 看一下"})
    theirs = post_message(client, room_id, "bob", {"content": f"<@{seat}> 帮我改"})

    assert (mine["meta"] or {})["agent_recipient"]["mentioned"] is True
    assert (theirs["meta"] or {})["agent_recipient"]["mentioned"] is False
    # bob is told, in the room, whose it is.
    blocks = client.get(
        f"/topics/{room_id}/blocks", headers=session_auth_headers("bob")
    ).json()["data"]["data"]
    told = [
        b
        for b in blocks
        if b["kind"] == "event"
        and "alice" in (b["content"] or "")
        and (b.get("meta") or {}).get("in_room", True) is not False
    ]
    assert told, "the room says why bob's call reached nobody"


def test_a_project_closed_to_them_stops_its_owner_calling_it(client):
    project_id, room_id = _project_with_bob(client)
    _log_in_claude_code(client, "alice")
    seat = _own_row(_members(client, project_id, "alice"), "alice")["handle"]
    post_message(client, room_id, "alice", {"content": f"<@{seat}> 你好"})
    client.put(
        f"/projects/{project_id}/own-agents",
        json={"allowed": False},
        headers=session_auth_headers("alice"),
    )

    after = post_message(client, room_id, "alice", {"content": f"<@{seat}> 再来"})

    assert (after["meta"] or {})["agent_recipient"]["mentioned"] is False


def test_it_works_only_its_owners_tasks(client):
    project_id, room_id = _project_with_bob(client)
    _log_in_claude_code(client, "alice")
    seat = _own_row(_members(client, project_id, "alice"), "alice")["handle"]
    bobs = open_task(client, room_id, "bob 的任务", owner="bob", reviewer="bob")
    alices = open_task(client, room_id, "alice 的任务", owner="alice")

    refused = client.patch(
        f"/topics/{bobs['id']}/task",
        json={"agent_handle": seat},
        headers=session_auth_headers("bob"),
    )
    given = client.patch(
        f"/topics/{alices['id']}/task",
        json={"agent_handle": seat},
        headers=session_auth_headers("alice"),
    )
    assert refused.status_code == 403, refused.text
    assert given.status_code == 200, given.text

    handed = client.patch(
        f"/topics/{alices['id']}/task",
        json={"owner_handle": "bob"},
        headers=session_auth_headers("alice"),
    )
    assert handed.status_code == 200, handed.text
    assert handed.json()["data"].get("agent_handle") in (None, "")


def test_nobody_else_chats_with_it_privately(client):
    project_id, _room = _project_with_bob(client)
    _log_in_claude_code(client, "alice")
    _members(client, project_id, "alice")
    own = client.get(
        f"/projects/{project_id}/own-agents", headers=session_auth_headers("alice")
    ).json()["data"]["agents"][0]

    async def own_handle():
        from app.domain.agent_instance.models import AgentInstance

        async with client.test_factory() as db:
            return await db.scalar(
                select(AgentInstance.handle).where(
                    AgentInstance.project_id == uuid.UUID(project_id),
                    AgentInstance.display_name == own["name"],
                )
            )

    handle = asyncio.run(own_handle())
    theirs = client.get(
        f"/projects/{project_id}/private-chat",
        params={"user_handle": "bob", "agent_handle": handle},
        headers=session_auth_headers("bob"),
    )
    mine = client.get(
        f"/projects/{project_id}/private-chat",
        params={"user_handle": "alice", "agent_handle": handle},
        headers=session_auth_headers("alice"),
    )
    assert theirs.status_code == 403, theirs.text
    assert mine.status_code == 200, mine.text


def test_a_project_closed_to_them_does_not_seat_it_in_a_room(client):
    project_id, room_id = _project_with_bob(client)
    _log_in_claude_code(client, "alice")
    seat = _own_row(_members(client, project_id, "alice"), "alice")["handle"]
    client.put(
        f"/projects/{project_id}/own-agents",
        json={"allowed": False},
        headers=session_auth_headers("alice"),
    )

    post_message(client, room_id, "alice", {"content": f"<@{seat}> 你好"})

    seated = client.get(
        f"/topics/{room_id}/members", headers=session_auth_headers("alice")
    ).json()["data"]["data"]
    assert seat not in {row["member_handle"] for row in seated}


def test_it_carries_its_owners_current_name(client):
    project_id, _room = _project_with_bob(client)
    _log_in_claude_code(client, "alice")
    _members(client, project_id, "alice")

    async def rename():
        async with client.test_factory() as db:
            user = await db.scalar(select(User).where(User.username == "alice"))
            profile = await db.scalar(
                select(UserProfile).where(
                    UserProfile.user_id == user.id, UserProfile.deleted_at.is_(None)
                )
            )
            if profile is None:
                now = datetime.now(UTC)
                db.add(
                    UserProfile(
                        user_id=user.id,
                        nickname="小艾",
                        intro="",
                        avatar_id=0,
                        created_at=now,
                        updated_at=now,
                    )
                )
            else:
                profile.nickname = "小艾"
            await db.commit()

    asyncio.run(rename())

    own = _own_row(_members(client, project_id, "alice"), "alice")
    assert own["name"] == "小艾的 Claude Code"
