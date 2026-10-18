"""What a project lets its members' own Claude Code do (#2991).

A member's own Claude Code sends the project's content to a model through its
owner's own account. So a project is closed to them until someone who manages
it opens it; closing it again takes them out of the project, not merely out of
reach. A manager may also allow only some kinds of model connection — a Claude
subscription, an Anthropic API key, a custom model service: a member adds
their own only with a connection of an allowed kind, and one already added on
a kind the manager then refuses is not called, with the reason said in the
room. Everyone in the project sees whose agent each is and what kind of
connection it uses; the machine it runs on is its owner's business alone.
"""

import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy import event, select

from app.domain.agent_instance.models import AccountConnection
from app.domain.device.models import DeviceClaudeLoginRow
from app.domain.device.supply import Supply
from app.domain.device.wiring import sql_device_service
from app.domain.user.models import User
from tests.integration.conftest import (
    join_project_team,
    new_project,
    post_message,
    session_auth_headers,
)

SUBSCRIPTION = {"auth_method": "claude.ai", "subscription_type": "max"}


def _log_in(client, handle: str, machine: str, **login) -> str:
    """``handle`` has logged in their own Claude Code on ``machine``: the
    connection, named by the machine."""

    async def run():
        async with client.test_factory() as db:
            user = await db.scalar(select(User).where(User.username == handle))
            devices = sql_device_service(db)
            code = await devices.start(machine)
            device = await devices.approve(
                code, owner_user_id=user.id, supply=Supply.self_hosted
            )
            db.add(
                DeviceClaudeLoginRow(
                    device_id=device.device_id,
                    installed=True,
                    logged_in=True,
                    checked_at=datetime.now(UTC),
                    **login,
                )
            )
            await db.commit()
            return device.device_id

    return asyncio.run(run())


def _add(client, project_id, handle, connection):
    return client.post(
        f"/projects/{project_id}/own-agents",
        json={"connection_id": connection},
        headers=session_auth_headers(handle),
    )


def _project(client):
    """A project alice manages and bob is a member of, with a channel."""
    project = new_project(client, "Own rules", owner="alice")
    join_project_team(client, project["id"], "bob")
    room = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "Room"},
        headers=session_auth_headers("alice"),
    ).json()["data"]
    return project["id"], room["id"]


def _rules(client, project_id, as_whom="alice", **rules):
    r = client.put(
        f"/projects/{project_id}/own-agents",
        json=rules,
        headers=session_auth_headers(as_whom),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _settings(client, project_id, viewer):
    r = client.get(
        f"/projects/{project_id}/own-agents", headers=session_auth_headers(viewer)
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _own_row(client, project_id, viewer, owner):
    r = client.get(
        f"/projects/{project_id}/members", headers=session_auth_headers(viewer)
    )
    assert r.status_code == 200, r.text
    found = [m for m in r.json()["data"]["data"] if m.get("owner_handle") == owner]
    return found[0] if found else None


def _room_handles(client, room_id, viewer):
    r = client.get(f"/topics/{room_id}/members", headers=session_auth_headers(viewer))
    assert r.status_code == 200, r.text
    return {row["member_handle"] for row in r.json()["data"]["data"]}


def test_a_project_is_closed_to_them_until_a_manager_opens_it(client):
    project_id, _room = _project(client)
    laptop = _log_in(client, "alice", "alice-laptop")

    assert _settings(client, project_id, "alice")["allowed"] is False
    assert _add(client, project_id, "alice", laptop).status_code == 403
    assert _own_row(client, project_id, "alice", "alice") is None

    _rules(client, project_id, allowed=True)

    assert _add(client, project_id, "alice", laptop).status_code == 200
    assert _own_row(client, project_id, "alice", "alice") is not None


def test_a_connection_of_a_refused_kind_is_not_added(client):
    project_id, _room = _project(client)
    laptop = _log_in(client, "alice", "alice-laptop", **SUBSCRIPTION)
    _rules(client, project_id, allowed=True, kinds=["api_key"])

    offered = _settings(client, project_id, "alice")["my_connections"]
    refused = _add(client, project_id, "alice", laptop)

    assert [(c["id"], c["allowed"]) for c in offered] == [(laptop, False)]
    assert refused.status_code == 403, refused.text
    assert _own_row(client, project_id, "alice", "alice") is None


def test_only_a_manager_opens_it(client):
    project_id, _room = _project(client)

    r = client.put(
        f"/projects/{project_id}/own-agents",
        json={"allowed": True},
        headers=session_auth_headers("bob"),
    )

    assert r.status_code == 403, r.text
    assert _settings(client, project_id, "bob")["allowed"] is False


def test_an_agent_on_a_kind_the_project_refuses_is_not_called_and_its_owner_is_told(
    client,
):
    project_id, room_id = _project(client)
    laptop = _log_in(client, "alice", "alice-laptop", **SUBSCRIPTION)
    _rules(client, project_id, allowed=True)
    assert _add(client, project_id, "alice", laptop).status_code == 200
    _rules(client, project_id, kinds=["api_key", "model_service"])
    seat = _own_row(client, project_id, "alice", "alice")["handle"]

    called = post_message(client, room_id, "alice", {"content": f"<@{seat}> 看一下"})

    assert (called["meta"] or {})["agent_recipient"]["mentioned"] is False
    blocks = client.get(
        f"/topics/{room_id}/blocks", headers=session_auth_headers("alice")
    ).json()["data"]["data"]
    told = [
        b
        for b in blocks
        if b["kind"] == "event" and "Claude 订阅" in (b["content"] or "")
    ]
    assert told, "the room says which kind of connection the project refuses"


def test_an_agent_on_a_kind_the_project_allows_is_called(client):
    project_id, room_id = _project(client)
    laptop = _log_in(client, "alice", "alice-laptop", **SUBSCRIPTION)
    _rules(client, project_id, allowed=True, kinds=["subscription"])
    assert _add(client, project_id, "alice", laptop).status_code == 200
    seat = _own_row(client, project_id, "alice", "alice")["handle"]

    called = post_message(client, room_id, "alice", {"content": f"<@{seat}> 看一下"})

    assert (called["meta"] or {})["agent_recipient"]["mentioned"] is True


def test_a_manager_cannot_allow_no_kind_at_all(client):
    project_id, _room = _project(client)

    r = client.put(
        f"/projects/{project_id}/own-agents",
        json={"kinds": []},
        headers=session_auth_headers("alice"),
    )

    assert r.status_code == 400, r.text
    assert _settings(client, project_id, "alice")["kinds"] == [
        "subscription",
        "api_key",
        "model_service",
    ]


def test_closing_the_project_withdraws_them_and_opening_it_brings_them_back(client):
    project_id, room_id = _project(client)
    laptop = _log_in(client, "alice", "alice-laptop")
    _rules(client, project_id, allowed=True)
    assert _add(client, project_id, "alice", laptop).status_code == 200
    seat = _own_row(client, project_id, "alice", "alice")["handle"]
    post_message(client, room_id, "alice", {"content": f"<@{seat}> 你好"})
    assert seat in _room_handles(client, room_id, "alice")

    _rules(client, project_id, allowed=False)

    for viewer in ("alice", "bob"):
        assert _own_row(client, project_id, viewer, "alice") is None
    assert seat not in _room_handles(client, room_id, "alice")
    assert _settings(client, project_id, "alice")["agents"] == []

    _rules(client, project_id, allowed=True)

    assert _own_row(client, project_id, "alice", "alice")["handle"] == seat


def test_everyone_sees_its_kind_and_only_its_owner_sees_its_machines(client):
    project_id, _room = _project(client)
    laptop = _log_in(client, "bob", "bobs-secret-laptop", **SUBSCRIPTION)
    _rules(client, project_id, allowed=True)
    assert _add(client, project_id, "bob", laptop).status_code == 200

    def bobs(viewer):
        rows = _settings(client, project_id, viewer)["agents"]
        return next(a for a in rows if a["owner_handle"] == "bob")

    # alice manages the project and still does not see bob's machines.
    seen_by_alice = bobs("alice")
    seen_by_bob = bobs("bob")

    for row in (seen_by_alice, seen_by_bob):
        assert row["connection"] == {
            "kind": "subscription",
            "plan": "max",
            "model": None,
            "on_account": False,
            "allowed": True,
        }
    assert "bobs-secret-laptop" not in str(_settings(client, project_id, "alice"))
    assert seen_by_bob["machine"]["name"] == "bobs-secret-laptop"


def _account_login(client, handle):
    async def run():
        async with client.test_factory() as db:
            owner = await db.scalar(select(User).where(User.username == handle))
            row = AccountConnection(
                owner_user_id=owner.id,
                kind="api_key",
                key="unused-in-listing",
                key_last4="test",
            )
            db.add(row)
            await db.commit()
            return str(row.id)

    return asyncio.run(run())


@pytest.mark.parametrize("on_account", [False, True])
@pytest.mark.parametrize("different_owners", [False, True])
def test_listing_selects_do_not_grow_with_the_number_of_agents(
    client, different_owners, on_account
):
    project_id, _room = _project(client)
    _rules(client, project_id, allowed=True)
    counts = []
    engine = client.test_app_engine.sync_engine
    statements = []

    def record(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    owners = []
    for index in range(12):
        owner = f"owner-{index}" if different_owners else "alice"
        if different_owners:
            join_project_team(client, project_id, owner)
        owners.append(owner)
        connection = (
            _account_login(client, owner)
            if on_account
            else _log_in(client, owner, f"private-laptop-{index}", **SUBSCRIPTION)
        )
        response = _add(client, project_id, owner, connection)
        assert response.status_code == 200, response.text
        if index not in (2, 11):
            continue

        statements.clear()
        event.listen(engine, "before_cursor_execute", record)
        try:
            result = _settings(client, project_id, "alice")
        finally:
            event.remove(engine, "before_cursor_execute", record)
        counts.append(len(statements))
        rows = result["agents"]
        assert [row["owner_handle"] for row in rows] == owners
        assert [row["owner_name"] for row in rows] == owners
        assert all(
            row["connection"]["kind"] == ("api_key" if on_account else "subscription")
            and row["connection"]["on_account"] is on_account
            for row in rows
        )
        if different_owners or on_account:
            assert all(row["machine"] is None for row in rows)
            assert "private-laptop" not in str(result)
        else:
            assert [row["machine"]["name"] for row in rows] == [
                f"private-laptop-{i}" for i in range(index + 1)
            ]

    assert counts[0] > 0
    assert counts[0] == counts[1], (
        f"3 agents: {counts[0]} SELECTs; 12 agents: {counts[1]}"
    )


@pytest.mark.parametrize("revoked", [False, True])
def test_an_unusable_login_keeps_its_machine_private_and_visible_to_its_owner(
    client, revoked
):
    project_id, _room = _project(client)
    device_id = _log_in(client, "bob", "bobs-laptop", **SUBSCRIPTION)
    _rules(client, project_id, allowed=True)
    assert _add(client, project_id, "bob", device_id).status_code == 200

    async def remove_login():
        async with client.test_factory() as db:
            login = await db.get(DeviceClaudeLoginRow, device_id)
            if revoked:
                login.revoked_at = datetime.now(UTC)
            else:
                login.logged_in = False
            await db.commit()

    asyncio.run(remove_login())
    (mine,) = _settings(client, project_id, "bob")["agents"]
    (theirs,) = _settings(client, project_id, "alice")["agents"]
    assert mine["connection"] is None
    assert mine["machine"]["name"] == "bobs-laptop"
    assert theirs["connection"] is None
    assert theirs["machine"] is None


def test_a_soft_deleted_owner_keeps_their_handle_in_the_agent_list(client):
    project_id, _room = _project(client)
    device_id = _log_in(client, "bob", "bobs-laptop", **SUBSCRIPTION)
    _rules(client, project_id, allowed=True)
    assert _add(client, project_id, "bob", device_id).status_code == 200

    async def delete_owner():
        async with client.test_factory() as db:
            user = await db.scalar(select(User).where(User.username == "bob"))
            user.deleted_at = datetime.now(UTC)
            await db.commit()

    asyncio.run(delete_owner())
    (row,) = _settings(client, project_id, "alice")["agents"]
    assert row["owner_handle"] == row["owner_name"] == "bob"
    assert row["machine"] is None


KIMI = {
    "auth_method": "model_service",
    "model": "kimi-k2",
    "service_host": "api.moonshot.cn",
}


def test_a_model_service_at_a_host_the_project_does_not_list_is_not_added(client):
    project_id, _room = _project(client)
    kimi = _log_in(client, "alice", "alice-desk", **KIMI)
    laptop = _log_in(client, "alice", "alice-laptop", **SUBSCRIPTION)
    _rules(client, project_id, allowed=True, service_hosts=["api.deepseek.com"])

    offered = {
        c["id"]: c["allowed"]
        for c in _settings(client, project_id, "alice")["my_connections"]
    }
    refused = _add(client, project_id, "alice", kimi)

    assert offered == {kimi: False, laptop: True}
    assert refused.status_code == 403, refused.text
    assert "api.moonshot.cn" in refused.text
    # The list names model services; it leaves a subscription alone.
    assert _add(client, project_id, "alice", laptop).status_code == 200


def test_a_model_service_at_a_listed_host_is_added(client):
    project_id, _room = _project(client)
    kimi = _log_in(client, "alice", "alice-desk", **KIMI)
    _rules(
        client,
        project_id,
        allowed=True,
        service_hosts=["api.deepseek.com", "api.moonshot.cn"],
    )

    assert _add(client, project_id, "alice", kimi).status_code == 200


def test_an_agent_on_a_service_no_longer_listed_is_not_called_and_the_room_names_it(
    client,
):
    project_id, room_id = _project(client)
    kimi = _log_in(client, "alice", "alice-desk", **KIMI)
    _rules(client, project_id, allowed=True)
    assert _add(client, project_id, "alice", kimi).status_code == 200
    _rules(client, project_id, service_hosts=["api.deepseek.com"])
    seat = _own_row(client, project_id, "alice", "alice")["handle"]

    called = post_message(client, room_id, "alice", {"content": f"<@{seat}> 看一下"})

    assert (called["meta"] or {})["agent_recipient"]["mentioned"] is False
    blocks = client.get(
        f"/topics/{room_id}/blocks", headers=session_auth_headers("alice")
    ).json()["data"]["data"]
    told = [
        b
        for b in blocks
        if b["kind"] == "event" and "api.moonshot.cn" in (b["content"] or "")
    ]
    assert told, "the room names the model service the project does not allow"
    # Its row in the settings says the project refuses it, without its host.
    (row,) = _settings(client, project_id, "bob")["agents"]
    assert row["connection"]["allowed"] is False
    assert "api.moonshot.cn" not in str(row)


def test_a_manager_lists_hosts_and_only_hosts(client):
    project_id, _room = _project(client)

    listed = _rules(
        client, project_id, service_hosts=[" API.Moonshot.cn ", "api.moonshot.cn"]
    )
    assert listed["service_hosts"] == ["api.moonshot.cn"]

    for not_a_host in (
        "https://api.moonshot.cn/anthropic",
        "api.moonshot.cn:443",
        "api moonshot.cn",
        "localhost",
    ):
        r = client.put(
            f"/projects/{project_id}/own-agents",
            json={"service_hosts": [not_a_host]},
            headers=session_auth_headers("alice"),
        )
        assert r.status_code == 400, (not_a_host, r.text)
    assert _settings(client, project_id, "alice")["service_hosts"] == [
        "api.moonshot.cn"
    ]

    # Empty lifts the restriction.
    assert _rules(client, project_id, service_hosts=[])["service_hosts"] == []


def test_only_a_manager_lists_hosts(client):
    project_id, _room = _project(client)

    r = client.put(
        f"/projects/{project_id}/own-agents",
        json={"service_hosts": ["api.moonshot.cn"]},
        headers=session_auth_headers("bob"),
    )

    assert r.status_code == 403, r.text
    assert _settings(client, project_id, "bob")["service_hosts"] == []
