"""Teams own credentials; projects instantiate them without member-owner gates."""

import uuid
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import select

from app.domain.agent_instance import account_connections
from app.domain.agent_instance.own.ownership import owned_instance, connection_of
from app.domain.device.models import DeviceClaudeLoginRow
from app.domain.device.supply import Supply
from app.domain.device.wiring import sql_device_service
from app.domain.project.models import Project
from app.domain.team.models import TeamUserRelation
from app.domain.user.models import User
from tests.integration.conftest import (
    new_project,
    join_project_team,
    session_auth_headers,
)

KEY = "sk-team-only-secret-1234"


def test_team_model_page_exists_without_project_opt_in(client):
    project = new_project(client, "Team catalog", owner="alice")

    async def team_id():
        async with client.test_factory() as db:
            return (await db.get(Project, uuid.UUID(project["id"]))).team_id

    response = client.get(
        path(client.portal.call(team_id)), headers=session_auth_headers("alice")
    )
    assert response.status_code == 200, response.text
    assert response.json()["can_manage"] is True
    assert response.json()["account_connections"] == []


@pytest.fixture
def service(monkeypatch):
    monkeypatch.setattr(
        account_connections,
        "_client",
        lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"data": []})
            )
        ),
    )
    monkeypatch.setattr(account_connections, "refuse_internal_host", lambda *args: None)


def setup_team(client):
    project = new_project(client, "Team models", owner="alice")

    async def shared_team():
        from app.domain.team.services import TeamService
        from app.domain.team.repositories import TeamRepository

        async with client.test_factory() as db:
            alice = await db.scalar(select(User).where(User.username == "alice"))
            team = await TeamService(TeamRepository(db)).create_team(
                name="Model lab",
                intro="",
                description="",
                avatar_id=0,
                owner_id=alice.id,
            )
            row = await db.get(Project, uuid.UUID(project["id"]))
            row.team_id = team.id
            await db.commit()

    client.portal.call(shared_team)
    join_project_team(client, project["id"], "bob", admin=True)
    join_project_team(client, project["id"], "carol")

    async def seed():
        async with client.test_factory() as db:
            p = await db.get(Project, uuid.UUID(project["id"]))
            alice = await db.scalar(select(User).where(User.username == "alice"))
            devices = sql_device_service(db)
            code = await devices.start("Team executor")
            device = await devices.approve(
                code,
                owner_user_id=alice.id,
                team_id=p.team_id,
                supply=Supply.self_hosted,
            )
            login = DeviceClaudeLoginRow(
                team_id=p.team_id,
                device_id=device.device_id,
                local_id="default",
                installed=True,
                logged_in=True,
                auth_method="claude.ai",
                subscription_type="max",
                checked_at=datetime.now(UTC),
                allowed_models=["sonnet", "opus"],
            )
            db.add(login)
            await db.commit()
            return p.team_id, device.device_id, login.id

    team, device, login = client.portal.call(seed)
    return project["id"], team, device, login


def path(team):
    return f"/connector/teams/{team}/model-connections"


def keep(client, team, who="alice"):
    response = client.post(
        path(team),
        headers=session_auth_headers(who),
        json={"kind": "api_key", "key": KEY, "allowed_models": ["sonnet"]},
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


def test_team_members_read_and_admins_manage_connections(client, service):
    _, team, _, _ = setup_team(client)
    connection = keep(client, team)
    listed = client.get(path(team), headers=session_auth_headers("carol"))
    assert listed.status_code == 200, listed.text
    assert listed.json()["account_connections"][0]["id"] == connection
    assert listed.json()["can_manage"] is False
    assert KEY not in listed.text
    assert listed.json()["account_connections"][0]["key_last4"] == "1234"
    for method, suffix, body in [
        ("post", "/check", None),
        ("patch", "", {"name": "stolen"}),
        ("delete", "", None),
        ("put", "/models", {"allowed_models": ["opus"]}),
    ]:
        response = client.request(
            method,
            path(team) + f"/{connection}" + suffix,
            headers=session_auth_headers("carol"),
            json=body,
        )
        assert response.status_code == 403, response.text
    renamed = client.patch(
        path(team) + f"/{connection}",
        headers=session_auth_headers("bob"),
        json={"name": "Shared key"},
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "Shared key"


def test_same_key_in_two_teams_is_independent_and_cross_team_ids_are_refused(
    client, service
):
    _, team, _, _ = setup_team(client)
    other = new_project(client, "Other", owner="dave")

    async def team_of():
        async with client.test_factory() as db:
            return (await db.get(Project, uuid.UUID(other["id"]))).team_id

    other_team = client.portal.call(team_of)
    first, second = keep(client, team), keep(client, other_team, "dave")
    assert first != second
    denied = client.get(path(team), headers=session_auth_headers("dave"))
    assert denied.status_code == 403, denied.text
    wrong_id = client.delete(
        path(other_team) + f"/{first}", headers=session_auth_headers("dave")
    )
    assert wrong_id.status_code == 404, wrong_id.text
    assert (
        client.delete(
            path(team) + f"/{first}", headers=session_auth_headers("bob")
        ).status_code
        == 200
    )
    assert (
        client.get(path(other_team), headers=session_auth_headers("dave")).json()[
            "account_connections"
        ][0]["id"]
        == second
    )


def test_project_uses_team_connection_model_and_device_and_keeps_it_after_enroller_leaves(
    client, service
):
    project, team, device, login = setup_team(client)
    connection = keep(client, team)
    endpoint = f"/projects/{project}/own-agents"
    for source in (connection, login):
        response = client.post(
            endpoint,
            headers=session_auth_headers("bob"),
            json={"connection_id": source, "model": "sonnet", "device_id": device},
        )
        assert response.status_code == 200, response.text
    agents = client.get(
        f"/projects/{project}/agents", headers=session_auth_headers("bob")
    ).json()["data"]["data"]
    chosen = [
        a for a in agents if a["configuration"].get("execution_device_id") == device
    ]
    assert len(chosen) == 2
    assert all(a["configuration"]["model"] == "sonnet" for a in chosen)

    async def leave():
        async with client.test_factory() as db:
            alice = await db.scalar(select(User).where(User.username == "alice"))
            relation = await db.scalar(
                select(TeamUserRelation).where(
                    TeamUserRelation.team_id == team,
                    TeamUserRelation.user_id == alice.id,
                    TeamUserRelation.deleted_at.is_(None),
                )
            )
            relation.deleted_at = datetime.now(UTC)
            await db.commit()

    client.portal.call(leave)
    assert (
        client.get(path(team), headers=session_auth_headers("bob")).status_code == 200
    )
    assert (
        client.patch(
            path(team) + f"/{connection}",
            headers=session_auth_headers("bob"),
            json={"name": "Retained"},
        ).status_code
        == 200
    )

    async def usable():
        async with client.test_factory() as db:
            return [
                await connection_of(db, await owned_instance(db, uuid.UUID(a["id"])))
                is not None
                for a in chosen
            ]

    assert client.portal.call(usable) == [True, True]


def test_project_cannot_select_disallowed_model_or_another_teams_device(
    client, service
):
    project, team, device, _ = setup_team(client)
    connection = keep(client, team)
    endpoint = f"/projects/{project}/own-agents"
    denied = client.post(
        endpoint,
        headers=session_auth_headers("bob"),
        json={"connection_id": connection, "model": "opus", "device_id": device},
    )
    assert denied.status_code == 403, denied.text
    other = new_project(client, "Elsewhere", owner="dave")
    denied = client.post(
        f"/projects/{other['id']}/own-agents",
        headers=session_auth_headers("dave"),
        json={"connection_id": connection, "model": "sonnet", "device_id": device},
    )
    assert denied.status_code == 404, denied.text


def test_saved_execution_device_is_used_and_never_replaced_by_another_team_device(
    client, service
):
    from app.domain.agent_instance.models import AgentInstance
    from app.domain.machine.environments import device_for_spec
    from app.domain.agent.environment import TEAM_DEVICES

    project, team, device, _ = setup_team(client)
    connection = keep(client, team)
    response = client.post(
        f"/projects/{project}/own-agents",
        headers=session_auth_headers("bob"),
        json={"connection_id": connection, "model": "sonnet", "device_id": device},
    )
    assert response.status_code == 200, response.text

    async def pick():
        async with client.test_factory() as db:
            p = await db.get(Project, uuid.UUID(project))
            instance = await db.scalar(
                select(AgentInstance).where(
                    AgentInstance.project_id == p.id,
                    AgentInstance.configuration["execution_device_id"].as_string()
                    == device,
                )
            )
            devices = sql_device_service(db)
            alice = await db.scalar(select(User).where(User.username == "alice"))
            other = await devices.approve(
                await devices.start("Other executor"),
                owner_user_id=alice.id,
                team_id=team,
                supply=Supply.self_hosted,
            )
            await db.commit()
            selected = await device_for_spec(
                db,
                spec=TEAM_DEVICES,
                project=p,
                task=None,
                held=other.device_id,
                is_online=lambda _: True,
                agent_handle=instance.handle,
            )
            offline = await device_for_spec(
                db,
                spec=TEAM_DEVICES,
                project=p,
                task=None,
                held=other.device_id,
                is_online=lambda value: value != device,
                agent_handle=instance.handle,
            )
            return selected.device_id if selected else None, offline

    assert client.portal.call(pick) == (device, None)


def test_changing_allowed_models_stops_an_existing_teammate_from_using_a_removed_model(
    client, service
):
    from app.domain.agent_instance.models import AgentInstance
    from app.domain.agent_instance.own import unusable

    project, team, device, _ = setup_team(client)
    connection = keep(client, team)
    response = client.post(
        f"/projects/{project}/own-agents",
        headers=session_auth_headers("bob"),
        json={"connection_id": connection, "model": "sonnet", "device_id": device},
    )
    assert response.status_code == 200, response.text

    async def allowed():
        async with client.test_factory() as db:
            instance = await db.scalar(
                select(AgentInstance).where(
                    AgentInstance.project_id == uuid.UUID(project),
                    AgentInstance.configuration["execution_device_id"].as_string()
                    == device,
                )
            )
            return await unusable(db, await owned_instance(db, instance.id), {})

    assert client.portal.call(allowed) is None
    changed = client.put(
        path(team) + f"/{connection}/models",
        headers=session_auth_headers("bob"),
        json={"allowed_models": ["opus"]},
    )
    assert changed.status_code == 200, changed.text
    assert client.portal.call(allowed) is not None


def test_account_closure_keeps_shared_team_connections_and_teammates(client, service):
    from app.domain.account.deletion import close_account
    from app.domain.agent_instance.models import AgentInstance, AccountConnection
    from app.domain.team.protocol import TeamMemberRole

    project, team, device, login = setup_team(client)
    connection = keep(client, team)
    response = client.post(
        f"/projects/{project}/own-agents",
        headers=session_auth_headers("alice"),
        json={"connection_id": connection, "model": "sonnet", "device_id": device},
    )
    assert response.status_code == 200, response.text

    async def close():
        async with client.test_factory() as db:
            alice = await db.scalar(select(User).where(User.username == "alice"))
            bob = await db.scalar(select(User).where(User.username == "bob"))
            relation = await db.scalar(
                select(TeamUserRelation).where(
                    TeamUserRelation.team_id == team,
                    TeamUserRelation.user_id == bob.id,
                    TeamUserRelation.deleted_at.is_(None),
                )
            )
            relation.role = TeamMemberRole.OWNER
            await db.flush()
            assert await close_account(db, alice.id) is not None
            await db.commit()
            held = await db.get(AccountConnection, uuid.UUID(connection))
            instance = await db.scalar(
                select(AgentInstance).where(
                    AgentInstance.project_id == uuid.UUID(project),
                    AgentInstance.configuration["execution_device_id"].as_string()
                    == device,
                )
            )
            return (
                held is not None,
                instance.is_active,
                await connection_of(db, await owned_instance(db, instance.id))
                is not None,
                await db.get(DeviceClaudeLoginRow, login) is not None,
            )

    assert client.portal.call(close) == (True, True, True, True)
