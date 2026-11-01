"""Credentials and their team references have independent ownership and lifetime."""

import uuid

from sqlalchemy import select

from app.domain.agent.harness.claude_code import owner_login
from app.domain.agent_instance.models import AccountConnection
from app.domain.device.models import DeviceClaudeLoginRow, DeviceRow
from app.domain.device.wiring import sql_device_service
from app.domain.project.models import Project
from tests.integration.conftest import new_project, session_auth_headers
from tests.integration.test_team_model_connections import keep, path, setup_team
from tests.integration.test_team_model_connections import service as service


def personal_team(client, owner):
    project = new_project(client, "Personal credentials", owner=owner)

    async def read():
        async with client.test_factory() as db:
            return (await db.get(Project, uuid.UUID(project["id"]))).team_id

    return client.portal.call(read)


def test_team_credential_creates_a_teammate_without_a_personal_owner_or_private_assistant_role(
    client, service
):
    from app.domain.agent_instance.models import AgentInstance
    from app.domain.agent_instance.own import current_role
    from app.domain.identity.handles import agent_instance_handle

    project, team, device, _ = setup_team(client)
    connection = keep(client, team)
    response = client.post(
        f"/projects/{project}/own-agents",
        headers=session_auth_headers("bob"),
        json={"connection_id": connection, "model": "sonnet", "device_id": device},
    )
    assert response.status_code == 200, response.text
    agent = next(a for a in response.json()["data"]["agents"] if a["connection"])
    assert agent["owner_handle"] is None
    assert agent["owner_name"] is None
    assert "bob的 Claude Code" not in agent["name"]

    async def role():
        async with client.test_factory() as db:
            instances = await db.scalars(
                select(AgentInstance).where(
                    AgentInstance.project_id == uuid.UUID(project)
                )
            )
            instance = next(
                a for a in instances if agent_instance_handle(a.id) == agent["handle"]
            )
            return await current_role(db, instance.id)

    assert client.portal.call(role) is None


def test_member_references_one_key_in_a_team_and_only_instantiates_their_own_assistant(
    client, service
):
    project, team, device, _ = setup_team(client)
    home = personal_team(client, "carol")
    connection = keep(client, home, "carol")
    endpoint = f"/connector/teams/{team}/personal-model-resources/{connection}"
    response = client.put(endpoint, headers=session_auth_headers("carol"))
    assert response.status_code == 200, response.text
    row = response.json()["account_connections"][0]
    assert row["id"] == connection
    assert row["owner_name"] == "carol"
    assert row["can_manage"] is True
    assert client.put(endpoint, headers=session_auth_headers("bob")).status_code == 403
    admin = client.get(path(team), headers=session_auth_headers("bob")).json()[
        "account_connections"
    ][0]
    assert admin["can_manage"] is False
    assert admin["can_remove"] is True
    assert (
        client.patch(
            path(team) + f"/{connection}",
            headers=session_auth_headers("bob"),
            json={"name": "override"},
        ).status_code
        == 403
    )
    body = {"connection_id": connection, "model": "sonnet", "device_id": device}
    assert (
        client.post(
            f"/projects/{project}/own-agents",
            headers=session_auth_headers("bob"),
            json=body,
        ).status_code
        == 403
    )
    added = client.post(
        f"/projects/{project}/own-agents",
        headers=session_auth_headers("carol"),
        json=body,
    )
    assert added.status_code == 200, added.text
    agent = next(a for a in added.json()["data"]["agents"] if a["connection"])
    assert agent["owner_handle"] == "carol"
    assert "carol" in agent["name"]
    instances = client.get(
        f"/projects/{project}/agents", headers=session_auth_headers("bob")
    ).json()["data"]["data"]
    instance = next(
        a for a in instances if a["configuration"].get("execution_device_id") == device
    )
    configured = client.put(
        f"/projects/{project}/agents/{instance['id']}",
        headers=session_auth_headers("bob"),
        json={"configuration": {"model": "sonnet", "execution_device_id": device}},
    )
    assert configured.status_code == 200, configured.text
    # Another member can address the assistant: resource ownership is prompt
    # context, whereas credential selection is the owner's management action.
    roster = client.get(
        f"/projects/{project}/members", headers=session_auth_headers("bob")
    )
    assert roster.status_code == 200, roster.text
    assert agent["handle"] in roster.text
    removed = client.delete(endpoint, headers=session_auth_headers("bob"))
    assert removed.status_code == 200, removed.text
    assert (
        client.get(path(home), headers=session_auth_headers("carol")).json()[
            "account_connections"
        ][0]["id"]
        == connection
    )

    async def remaining():
        async with client.test_factory() as db:
            return list(await db.scalars(select(AccountConnection.id)))

    assert client.portal.call(remaining) == [uuid.UUID(connection)]


def test_subscription_login_registers_without_any_device_and_keeps_its_own_team(
    client, monkeypatch
):
    _, team, _, _ = setup_team(client)
    endpoint = f"/connector/teams/{team}/claude-login"
    assert (
        client.post(endpoint, headers=session_auth_headers("carol")).status_code == 403
    )
    start = client.post(endpoint, headers=session_auth_headers("bob"))
    assert start.status_code == 200, start.text
    command = start.json()["command"]
    assert "--ticket " in command and "--server " in command
    assert "--profile" not in command
    registered = client.post(
        "/connector/model-login/register",
        json={"ticket": start.json()["ticket"], "computer_name": "Private laptop"},
    )
    assert registered.status_code == 200, registered.text
    login = registered.json()
    assert login["credential_host"] is True

    async def recovered(_):
        pass

    monkeypatch.setattr(
        "app.api.routes.connector.devices.recover_business_state", recovered
    )
    with client.websocket_connect(
        "/connector/agent", headers={"Authorization": f"Bearer {login['token']}"}
    ) as ws:
        ws.close()
    assert (
        client.post(
            "/connector/model-login/register",
            json={"ticket": start.json()["ticket"], "computer_name": "Second laptop"},
        ).status_code
        == 409
    )

    async def report():
        async with client.test_factory() as db:
            assert await db.get(DeviceRow, login["device_id"]) is None
            row = await db.get(DeviceClaudeLoginRow, login["login_id"])
            assert row.team_id == team
            assert row.device_id is None
            await owner_login.remember(
                db,
                login["device_id"],
                [
                    owner_login.ClaudeLogin(
                        installed=True,
                        logged_in=True,
                        auth_method="claude.ai",
                        subscription_type="max",
                        account_email="team-seat@example.com",
                        local_id=login["login_id"],
                    )
                ],
            )
            await db.commit()

    client.portal.call(report)
    listed = client.get(path(team), headers=session_auth_headers("carol")).json()[
        "connections"
    ]
    row = next(c for c in listed if c["id"] == login["login_id"])
    assert row["device_name"] == login["device_id"]
    assert "Private laptop" not in str(row)
    # Which Claude account the team's own subscription is, the team sees.
    assert row["account_email"] == "team-seat@example.com"


def test_personal_subscription_in_another_team_hides_computer_name_and_keeps_team_when_device_moves(
    client,
):
    _, team, device, _ = setup_team(client)
    home = personal_team(client, "carol")
    start = client.post(
        f"/connector/teams/{home}/claude-login", headers=session_auth_headers("carol")
    ).json()

    async def token():
        async with client.test_factory() as db:
            issued = await sql_device_service(db).issue_token(device)
            await db.commit()
            return issued

    registered = client.post(
        "/connector/model-login/register",
        json={
            "ticket": start["ticket"],
            "computer_name": "storage",
            "transport_token": client.portal.call(token),
        },
    )
    assert registered.status_code == 200, registered.text
    login = registered.json()["login_id"]

    async def report():
        async with client.test_factory() as db:
            await owner_login.remember(
                db,
                device,
                [
                    owner_login.ClaudeLogin(
                        installed=True,
                        logged_in=True,
                        auth_method="claude.ai",
                        subscription_type="max",
                        local_id=login,
                    )
                ],
            )
            await db.commit()

    client.portal.call(report)
    response = client.put(
        f"/connector/teams/{team}/personal-model-resources/{login}",
        headers=session_auth_headers("carol"),
    )
    assert response.status_code == 200, response.text
    assert (
        client.get(path(home), headers=session_auth_headers("carol")).json()[
            "connections"
        ][0]["id"]
        == login
    )

    async def forget_device():
        async with client.test_factory() as db:
            await db.delete(await db.get(DeviceRow, device))
            await db.commit()
            row = await db.get(DeviceClaudeLoginRow, login, populate_existing=True)
            assert row.team_id == home
            assert row.transport_id == device

    client.portal.call(forget_device)
    listed = client.get(path(team), headers=session_auth_headers("bob")).json()[
        "connections"
    ]
    assert next(c for c in listed if c["id"] == login)["device_name"] == device


def test_a_personal_subscription_names_its_claude_account_only_to_its_owner(client):
    """Which Claude account a member's personal subscription is stays theirs:
    teammates in a team it is shared into see that it is the member's, not the
    member's Claude email."""
    _, team, device, _ = setup_team(client)
    home = personal_team(client, "carol")
    start = client.post(
        f"/connector/teams/{home}/claude-login", headers=session_auth_headers("carol")
    ).json()

    async def token():
        async with client.test_factory() as db:
            issued = await sql_device_service(db).issue_token(device)
            await db.commit()
            return issued

    registered = client.post(
        "/connector/model-login/register",
        json={
            "ticket": start["ticket"],
            "computer_name": "storage",
            "transport_token": client.portal.call(token),
        },
    )
    assert registered.status_code == 200, registered.text
    login = registered.json()["login_id"]

    async def report():
        async with client.test_factory() as db:
            await owner_login.remember(
                db,
                device,
                [
                    owner_login.ClaudeLogin(
                        installed=True,
                        logged_in=True,
                        auth_method="claude.ai",
                        subscription_type="max",
                        account_email="carol.private@example.com",
                        account_org="Carol's Organization",
                        local_id=login,
                    )
                ],
            )
            await db.commit()

    client.portal.call(report)
    shared = client.put(
        f"/connector/teams/{team}/personal-model-resources/{login}",
        headers=session_auth_headers("carol"),
    )
    assert shared.status_code == 200, shared.text

    def row(viewer: str) -> dict:
        listed = client.get(path(team), headers=session_auth_headers(viewer)).json()
        return next(c for c in listed["connections"] if c["id"] == login)

    assert "carol.private@example.com" not in str(row("bob"))
    assert "Carol's Organization" not in str(row("bob"))
    assert row("carol")["account_email"] == "carol.private@example.com"
