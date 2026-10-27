"""A personal assistant is addressable by the team and opens tasks on its owner's word."""

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.core.sandbox_auth import mint_scoped_token
from app.domain.agent.harness.claude_code import owner_login
from app.domain.agent_instance.models import OwnAgent
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
from tests.support.devices import enrollment_team, place_in_project_team


def _log_in_claude_code(client, handle: str, machine: str = "", **login) -> str:
    """``handle`` has logged in their own Claude Code on a machine of theirs:
    one model connection, named by the machine."""

    login.setdefault("auth_method", "claude.ai")

    async def run():
        async with client.test_factory() as db:
            user = await db.scalar(select(User).where(User.username == handle))
            devices = sql_device_service(db)
            code = await devices.start(machine or f"{handle}-laptop")
            device = await devices.approve(
                code,
                owner_user_id=user.id,
                supply=Supply.self_hosted,
                team_id=await enrollment_team(devices, user.id),
            )
            db.add(
                DeviceClaudeLoginRow(
                    team_id=device.team_id,
                    owner_user_id=user.id,
                    device_id=device.device_id,
                    installed=True,
                    logged_in=True,
                    checked_at=datetime.now(UTC),
                    allowed_models=[login.get("model") or "sonnet"],
                    **login,
                )
            )
            await db.commit()
            return device.device_id

    return asyncio.run(run())


def _add(client, project_id, handle: str, connection: str):
    # These roster scenarios enroll the fixture device in the project's team.
    async def setup():
        async with client.test_factory() as db:
            row = await db.get(DeviceClaudeLoginRow, connection)
            if row is None:
                return "sonnet", "missing"
            await place_in_project_team(
                sql_device_service(db), row.device_id, uuid.UUID(project_id)
            )
            # 这份连接跟着机器归队：`logins(team_id)` 认的是**登录行**的 team_id，
            # 只把机器挪过去，连接还在原来那个团队名下，加队友时就看不着它。
            from app.domain.project.models import Project

            team = await db.get(Project, uuid.UUID(project_id))
            row.team_id = team.team_id
            await db.commit()
            return "sonnet", row.device_id

    model, device = client.portal.call(setup)
    return client.post(
        f"/projects/{project_id}/own-agents",
        json={"connection_id": connection, "model": model, "device_id": device},
        headers=session_auth_headers(handle),
    )


def _bring_in(client, project_id, handle: str) -> str:
    """``handle`` logs in their own Claude Code and adds it to the project;
    the seat it sits on there."""
    added = _add(client, project_id, handle, _log_in_claude_code(client, handle))
    assert added.status_code == 200, added.text
    return _own_row(_members(client, project_id, handle), handle)["handle"]


def _project_with_bob(client):
    """A project alice manages and bob is in, which lets members bring their
    own Claude Code."""
    project = new_project(client, "Own teammate", owner="alice")
    join_project_team(client, project["id"], "bob")
    room = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "Room"},
        headers=session_auth_headers("alice"),
    ).json()["data"]

    async def team_id():
        from app.domain.project.models import Project

        async with client.test_factory() as db:
            return (await db.get(Project, uuid.UUID(project["id"]))).team_id

    client.own_team_id = client.portal.call(team_id)
    return project["id"], room["id"]


@pytest.mark.parametrize("last_active", [False, True])
def test_removing_a_default_connection_teammate_keeps_an_active_default(
    client, last_active
):
    project, _ = _project_with_bob(client)
    seat = _bring_in(client, project, "alice")
    headers = session_auth_headers("alice")
    endpoint = f"/projects/{project}/agents"
    agents = client.get(endpoint, headers=headers).json()["data"]["data"]
    target = next(row for row in agents if row["seat_handle"] == seat)
    chosen = client.put(
        f"/projects/{project}/default-agent",
        headers=headers,
        json={"instance_id": target["id"]},
    )
    assert chosen.status_code == 200, chosen.text
    if last_active:
        for row in agents:
            if row["id"] != target["id"] and row["is_active"]:
                retired = client.delete(f"{endpoint}/{row['id']}", headers=headers)
                assert retired.status_code == 200, retired.text

    removed = client.delete(f"/projects/{project}/own-agents/{seat}", headers=headers)
    assert removed.status_code == (422 if last_active else 200), removed.text
    agents = client.get(endpoint, headers=headers).json()["data"]["data"]
    defaults = [row for row in agents if row["is_default"]]
    assert len(defaults) == 1
    assert defaults[0]["is_active"]
    assert (defaults[0]["id"] == target["id"]) is last_active


def _members(client, project_id, viewer):
    r = client.get(
        f"/projects/{project_id}/members", headers=session_auth_headers(viewer)
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["data"]


def _own_row(rows, owner):
    found = [row for row in rows if row.get("owner_handle") == owner]
    return found[0] if found else None


@pytest.mark.parametrize("author", ["alice", "bob", None])
def test_its_task_cites_a_message_from_its_owner(client, author):
    project, room = _project_with_bob(client)
    seat = _bring_in(client, project, "alice")
    client.post(f"/topics/{room}/join", headers=session_auth_headers("bob"))
    post_message(client, room, "alice", {"content": f"<@{seat}> 你好"})
    message = (
        post_message(client, room, author, {"content": "做这件事"}) if author else None
    )
    response = client.post(
        f"/topics/{room}/teammate-tasks",
        json={
            "title": "更新说明",
            "summary": "更新说明文件",
            **({"source_message_id": message["id"]} if message else {}),
        },
        headers={
            "X-Cheese-Token": mint_scoped_token(
                project_id=project,
                topic_id=room,
                agent_handle=seat,
            )
        },
    )
    assert response.status_code == 200, response.text
    if message:
        assert response.json()["data"]["source_block_id"] == message["id"]


def _open_task_as(client, project, room, seat, message_id):
    return client.post(
        f"/topics/{room}/teammate-tasks",
        json={
            "title": "更新说明",
            "summary": "更新说明文件",
            "source_message_id": message_id,
        },
        headers={
            "X-Cheese-Token": mint_scoped_token(
                project_id=project, topic_id=room, agent_handle=seat
            )
        },
    )


def test_a_task_citing_its_owners_private_word_does_not_show_it_to_others(client):
    project, room = _project_with_bob(client)
    seat = _bring_in(client, project, "alice")
    client.post(f"/topics/{room}/join", headers=session_auth_headers("bob"))
    post_message(client, room, "alice", {"content": f"<@{seat}> 你好"})

    async def instance_handle():
        from app.domain.agent_instance.models import AgentInstance
        from app.domain.identity.handles import agent_instance_handle

        async with client.test_factory() as db:
            rows = await db.execute(
                select(AgentInstance.id, AgentInstance.handle).where(
                    AgentInstance.project_id == uuid.UUID(project)
                )
            )
            return next(h for i, h in rows if agent_instance_handle(i) == seat)

    private = client.get(
        f"/projects/{project}/private-chat",
        params={"user_handle": "alice", "agent_handle": asyncio.run(instance_handle())},
        headers=session_auth_headers("alice"),
    ).json()["data"]
    word = post_message(client, private["id"], "alice", {"content": "同意 私密内容"})

    made = _open_task_as(client, project, room, seat, word["id"])

    assert made.status_code == 200, made.text
    assert made.json()["data"]["owner_handle"] == "alice"
    related = client.get(
        f"/topics/{made.json()['data']['id']}/related",
        headers=session_auth_headers("bob"),
    )
    assert related.status_code == 200, related.text
    assert "私密内容" not in related.text


def test_a_task_cannot_cite_its_owners_message_from_another_project(client):
    project, room = _project_with_bob(client)
    seat = _bring_in(client, project, "alice")
    post_message(client, room, "alice", {"content": f"<@{seat}> 你好"})
    other = new_project(client, "Elsewhere", owner="carol")
    join_project_team(client, other["id"], "alice")
    there = client.post(
        "/topics",
        json={"project_id": other["id"], "title": "There"},
        headers=session_auth_headers("alice"),
    ).json()["data"]
    word = post_message(client, there["id"], "alice", {"content": "别处的话"})

    made = _open_task_as(client, project, room, seat, word["id"])

    assert made.status_code == 403, made.text
    assert made.json()["error"]["i18n"]["key"] == "resourceNotFound"


def test_added_to_a_room_it_asks_its_owner_privately(client):
    project, room = _project_with_bob(client)
    seat = _bring_in(client, project, "alice")
    joined = client.post(f"/topics/{room}/join", headers=session_auth_headers("bob"))
    assert joined.status_code == 200, joined.text
    added = client.post(
        f"/topics/{room}/members",
        json={"handle": seat},
        headers=session_auth_headers("bob"),
    )
    assert added.status_code == 200, added.text

    async def questions():
        from app.domain.block.models import Block
        from app.domain.topic.models import Topic
        from app.domain.topic_membership.services import TopicMemberService

        async with client.test_factory() as db:
            rows = await db.execute(
                select(Block, Topic)
                .join(Topic, Block.conversation_id == Topic.id)
                .where(
                    Block.project_id == uuid.UUID(project),
                    Block.author == seat,
                )
            )
            return [
                (
                    b.content,
                    b.meta,
                    t.id,
                    await TopicMemberService(db).private_seats(t.id),
                )
                for b, t in rows
            ]

    found = asyncio.run(questions())
    asks = [r for r in found if (r[1] or {}).get("options")]
    assert len(asks) == 1, found
    content, meta, conversation, participants = asks[0]
    assert str(conversation) != room
    assert set(participants) == {"alice", seat}
    assert meta["asked"] == ["alice"]
    assert room in content
    hidden = client.get(
        f"/topics/{conversation}/blocks", headers=session_auth_headers("bob")
    )
    assert hidden.status_code in (403, 404), hidden.text


def test_asking_its_owner_keeps_the_question_out_of_the_room_live_feed(
    client, monkeypatch
):
    from app.domain.agent.realtime.broker import get_broker

    project, room = _project_with_bob(client)
    seat = _bring_in(client, project, "alice")
    added = client.post(
        f"/topics/{room}/members",
        json={"handle": seat},
        headers=session_auth_headers("alice"),
    )
    assert added.status_code == 200, added.text
    frames = []

    async def capture(channel, frame):
        frames.append((str(channel), frame))

    monkeypatch.setattr(get_broker(), "publish", capture)
    response = client.post(
        f"/topics/{room}/asks",
        json={
            "to_owner": True,
            "questions": [
                {
                    "question": "私人请求是否同意",
                    "options": [{"text": "同意"}, {"text": "不同意"}],
                }
            ],
        },
        headers={
            "X-Cheese-Token": mint_scoped_token(
                project_id=project,
                topic_id=room,
                agent_handle=seat,
            )
        },
    )
    assert response.status_code == 200, response.text
    block = response.json()["data"]["blocks"][0]
    private = block["conversation_id"]
    assert private != room

    async def drain():
        await asyncio.sleep(0)

    client.portal.call(drain)
    matching = [
        (channel, frame)
        for channel, frame in frames
        if (frame.get("block") or {}).get("id") == block["id"]
    ]
    assert [channel for channel, frame in matching] == [private], frames
    answered = post_message(
        client, private, "alice", {"content": "同意", "reply_to": block["id"]}
    )
    assert answered["meta"]["agent_recipient"]["mentioned"] is True


def test_an_existing_personal_agent_uses_its_current_owner_role(client):
    from app.domain.agent_instance.models import AgentInstance, OwnAgent
    from app.domain.agent_instance.own import current_role

    project, _room = _project_with_bob(client)
    _bring_in(client, project, "alice")

    async def role():
        async with client.test_factory() as db:
            instance = await db.scalar(
                select(AgentInstance)
                .join(OwnAgent)
                .where(AgentInstance.project_id == uuid.UUID(project))
            )
            instance.configuration = {"body": "obsolete private-agent caller policy"}
            await db.commit()
        async with client.test_factory() as db:
            return await current_role(db, instance.id)

    instructions = asyncio.run(role())
    assert "obsolete private-agent caller policy" not in instructions
    assert "alice" in instructions
    assert "to_owner=true" in instructions
    assert "source_message_id" in instructions


def test_it_is_in_a_project_only_once_its_owner_adds_it(client):
    project_id, _room = _project_with_bob(client)
    laptop = _log_in_claude_code(client, "alice")

    # Logged in and reading the roster does not bring it in.
    assert _own_row(_members(client, project_id, "alice"), "alice") is None

    added = _add(client, project_id, "alice", laptop)
    assert added.status_code == 200, added.text
    own = _own_row(_members(client, project_id, "alice"), "alice")

    assert own is not None and own["agent"]
    # Everyone sees whose it is.
    assert _own_row(_members(client, project_id, "bob"), "alice") == own


def test_a_person_brings_one_per_connection_each_named_after_it(client):
    project_id, _room = _project_with_bob(client)
    max_plan = _log_in_claude_code(
        client,
        "alice",
        "alice-laptop",
        auth_method="claude.ai",
        subscription_type="max",
    )
    pro_plan = _log_in_claude_code(
        client,
        "alice",
        "alice-desk",
        auth_method="claude.ai",
        subscription_type="pro",
    )

    for connection in (max_plan, pro_plan):
        added = _add(client, project_id, "alice", connection)
        assert added.status_code == 200, added.text

    names = sorted(
        row["name"]
        for row in _members(client, project_id, "bob")
        if row.get("owner_handle") == "alice"
    )
    assert names == ["alice的 Claude Code（Max）", "alice的 Claude Code（Pro）"]


def test_another_member_can_remove_the_team_teammate(client):
    project_id, room_id = _project_with_bob(client)
    seat = _bring_in(client, project_id, "alice")
    post_message(client, room_id, "alice", {"content": f"<@{seat}> 你好"})

    refused = client.delete(
        f"/projects/{project_id}/own-agents/{seat}",
        headers=session_auth_headers("bob"),
    )
    assert refused.status_code == 200, refused.text
    assert _own_row(_members(client, project_id, "bob"), "alice") is None

    removed = client.delete(
        f"/projects/{project_id}/own-agents/{seat}",
        headers=session_auth_headers("alice"),
    )
    assert removed.status_code == 200, removed.text
    for viewer in ("alice", "bob"):
        assert _own_row(_members(client, project_id, viewer), "alice") is None
    seated = client.get(
        f"/topics/{room_id}/members", headers=session_auth_headers("alice")
    ).json()["data"]["data"]
    assert seat not in {row["member_handle"] for row in seated}
    called = post_message(client, room_id, "alice", {"content": f"<@{seat}> 在吗"})
    assert (called["meta"] or {}).get("agent_recipient", {}).get("mentioned") in (
        None,
        False,
    )


def test_another_member_can_mention_it(client):
    project_id, room_id = _project_with_bob(client)
    seat = _bring_in(client, project_id, "alice")

    joined = client.post(f"/topics/{room_id}/join", headers=session_auth_headers("bob"))
    assert joined.status_code == 200, joined.text
    mine = post_message(client, room_id, "alice", {"content": f"<@{seat}> 看一下"})
    theirs = post_message(client, room_id, "bob", {"content": f"<@{seat}> 帮我改"})

    assert (mine["meta"] or {})["agent_recipient"]["mentioned"] is True
    assert (theirs["meta"] or {})["agent_recipient"]["mentioned"] is True


def test_other_members_can_assign_it_without_a_platform_owner_gate(client):
    project_id, room_id = _project_with_bob(client)
    seat = _bring_in(client, project_id, "alice")
    bobs = open_task(client, room_id, "bob 的任务", owner="bob")
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
    assert refused.status_code == 200, refused.text
    assert given.status_code == 200, given.text

    handed = client.patch(
        f"/topics/{alices['id']}/task",
        json={"owner_handle": "bob"},
        headers=session_auth_headers("alice"),
    )
    assert handed.status_code == 200, handed.text
    assert handed.json()["data"]["agent_handle"] == seat


def test_another_member_can_chat_with_it_privately(client):
    project_id, _room = _project_with_bob(client)
    _bring_in(client, project_id, "alice")
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
    assert theirs.status_code == 200, theirs.text
    assert mine.status_code == 200, mine.text


def test_it_carries_its_owners_current_name(client):
    project_id, _room = _project_with_bob(client)
    laptop = _log_in_claude_code(client, "alice", auth_method="claude.ai")
    assert _add(client, project_id, "alice", laptop).status_code == 200

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

    async def machine_reports_again():
        # As it does when it connects, and before a session starts there.
        async with client.test_factory() as db:
            await owner_login.remember(
                db,
                laptop,
                [
                    owner_login.ClaudeLogin(
                        installed=True,
                        logged_in=True,
                        auth_method="claude.ai",
                        subscription_type="pro",
                    )
                ],
            )
            await db.commit()

    asyncio.run(machine_reports_again())

    own = _own_row(_members(client, project_id, "alice"), "alice")
    # Its owner's nickname, and the plan the connection is on now.
    assert own["name"] == "小艾的 Claude Code（Pro）"


def test_its_owner_arms_its_skills_without_a_manager(client):
    """What runs on your own machine under your own login is yours to arm: its
    owner picks the skills it carries and needs no manager to approve that,
    while another member's agent stays a manager's call."""
    project_id, _room = _project_with_bob(client)
    # 机器上的那次登录只是一个**连接**；要成为项目里的一位队友，得由它主人加进来
    # （#3611：一位队友 = 一个连接，加和删都只由主人做）。
    _bring_in(client, project_id, "bob")
    assert _own_row(_members(client, project_id, "bob"), "bob") is not None

    async def instance_id():
        async with client.test_factory() as db:
            user = await db.scalar(select(User).where(User.username == "bob"))
            # 席位 handle 带上了一个随机后缀（`own_handle`），认不出是谁的，
            # 所以从 `own_agents` 那一行按主人用户认这一位队友。
            return await db.scalar(
                select(OwnAgent.instance_id).where(
                    OwnAgent.owner_user_id == user.id,
                )
            )

    agent_id = asyncio.run(instance_id())
    bob = session_auth_headers("bob")
    armed = client.put(
        f"/projects/{project_id}/agents/{agent_id}",
        json={"configuration": {"bob's": "kept", "skills": ["wolfram"]}},
        headers=bob,
    )
    assert armed.status_code == 200, armed.text
    assert armed.json()["data"]["configuration"]["skills"] == ["wolfram"]


def _name_connection(client, device_id, name, as_whom="alice"):
    return client.patch(
        f"/connector/teams/{client.own_team_id}/claude-logins/{device_id}",
        json={"name": name},
        headers=session_auth_headers(as_whom),
    )


def _machine_reports(client, device_id, **login):
    """The machine says what its login is, as it does when it connects and
    before a session starts there."""

    async def report():
        async with client.test_factory() as db:
            await owner_login.remember(
                db,
                device_id,
                [owner_login.ClaudeLogin(installed=True, logged_in=True, **login)],
            )
            await db.commit()

    asyncio.run(report())


def test_its_owner_names_a_machines_connection_and_it_carries_that_name(client):
    project_id, _room = _project_with_bob(client)
    laptop = _log_in_claude_code(
        client, "alice", auth_method="claude.ai", subscription_type="max"
    )
    assert _add(client, project_id, "alice", laptop).status_code == 200

    named = _name_connection(client, laptop, "实验室的 Max")

    assert named.status_code == 200, named.text
    assert named.json()["name"] == "实验室的 Max"
    own = _own_row(_members(client, project_id, "bob"), "alice")
    assert own["name"] == "alice的 Claude Code（实验室的 Max）"

    # The machine reports its login again, on another plan: the name stays.
    _machine_reports(client, laptop, auth_method="claude.ai", subscription_type="pro")

    own = _own_row(_members(client, project_id, "bob"), "alice")
    assert own["name"] == "alice的 Claude Code（实验室的 Max）"
    connections = client.get(
        f"/connector/teams/{client.own_team_id}/model-connections",
        headers=session_auth_headers("alice"),
    ).json()["connections"]
    assert [c["name"] for c in connections] == ["实验室的 Max"]


def test_a_connection_whose_name_is_taken_away_is_named_after_its_plan_again(client):
    project_id, _room = _project_with_bob(client)
    laptop = _log_in_claude_code(
        client, "alice", auth_method="claude.ai", subscription_type="max"
    )
    assert _add(client, project_id, "alice", laptop).status_code == 200
    assert _name_connection(client, laptop, "实验室的 Max").status_code == 200

    cleared = _name_connection(client, laptop, "")

    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["name"] is None
    own = _own_row(_members(client, project_id, "alice"), "alice")
    assert own["name"] == "alice的 Claude Code（Max）"


def test_a_removed_connection_takes_its_name_with_it(client):
    project_id, _room = _project_with_bob(client)
    laptop = _log_in_claude_code(
        client, "alice", auth_method="claude.ai", subscription_type="max"
    )
    # The login belongs to alice's personal team when it is created; naming in
    # the project's team is the same connection brought in first, as in the
    # tests around this one. (The project used to sit in her personal team, so
    # the two were the same team and this step was not needed.)
    assert _add(client, project_id, "alice", laptop).status_code == 200
    assert _name_connection(client, laptop, "实验室的 Max").status_code == 200

    removed = client.delete(
        f"/connector/teams/{client.own_team_id}/claude-logins/{laptop}",
        headers=session_auth_headers("alice"),
    )

    assert removed.status_code == 200, removed.text
    assert removed.json()["name"] is None
    # Nothing is left to name until the machine logs in again.
    assert _name_connection(client, laptop, "又一个").status_code == 404


def test_only_its_owner_names_a_machines_connection(client):
    project_id, _room = _project_with_bob(client)
    laptop = _log_in_claude_code(
        client, "alice", auth_method="claude.ai", subscription_type="max"
    )
    assert _add(client, project_id, "alice", laptop).status_code == 200

    refused = _name_connection(client, laptop, "bob 起的名字", as_whom="bob")

    assert refused.status_code == 403, refused.text
    own = _own_row(_members(client, project_id, "alice"), "alice")
    assert own["name"] == "alice的 Claude Code（Max）"


ROUTINE = {
    "title": "每周进展",
    "instructions": "整理本周的进展",
    "trigger": "schedule",
    "spec": {"freq": "weekly", "weekdays": [0], "time": "09:00"},
    "timezone": "Asia/Shanghai",
}


def _rules_in(client, project_id, room_id, viewer):
    r = client.get(
        f"/projects/{project_id}/routines",
        params={"topic": room_id},
        headers=session_auth_headers(viewer),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["data"]


def test_another_member_can_schedule_it(client):
    """Scheduling has no personal-agent caller gate."""
    project_id, room_id = _project_with_bob(client)
    seat = _bring_in(client, project_id, "alice")
    post_message(client, room_id, "alice", {"content": f"<@{seat}> 看一下"})
    joined = client.post(f"/topics/{room_id}/join", headers=session_auth_headers("bob"))
    assert joined.status_code == 200, joined.text

    refused = client.post(
        f"/topics/{room_id}/routines",
        json={**ROUTINE, "agent_handle": seat, "owner_handle": "alice"},
        headers=session_auth_headers("bob"),
    )

    assert refused.status_code == 200, refused.text
    rules = _rules_in(client, project_id, room_id, "alice")
    assert [r for r in rules if r.get("agent_handle") == seat], rules

    mine = client.post(
        f"/topics/{room_id}/routines",
        json={**ROUTINE, "agent_handle": seat},
        headers=session_auth_headers("alice"),
    )
    assert mine.status_code == 200, mine.text
    assert (
        mine.json()["data"]["agent_handle"],
        mine.json()["data"]["owner_handle"],
    ) == (
        seat,
        "alice",
    )


def test_a_routine_can_use_the_rooms_personal_agent(client):
    """The room's personal agent remains selectable by another member."""
    project_id, room_id = _project_with_bob(client)
    seat = _bring_in(client, project_id, "alice")
    post_message(client, room_id, "alice", {"content": f"<@{seat}> 看一下"})
    roster = client.get(
        f"/topics/{room_id}/members", headers=session_auth_headers("alice")
    ).json()["data"]["data"]
    for row in roster:
        if row["agent"] and row["member_handle"] != seat:
            gone = client.delete(
                f"/topics/{room_id}/members/{row['member_handle']}",
                headers=session_auth_headers("alice"),
            )
            assert gone.status_code == 200, gone.text
    joined = client.post(f"/topics/{room_id}/join", headers=session_auth_headers("bob"))
    assert joined.status_code == 200, joined.text

    refused = client.post(
        f"/topics/{room_id}/routines",
        json=ROUTINE,
        headers=session_auth_headers("bob"),
    )

    assert refused.status_code == 200, refused.text
    assert len(_rules_in(client, project_id, room_id, "alice")) == 1
