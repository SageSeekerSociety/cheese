"""Team enrollment, management and project isolation over the public API."""

import asyncio
from datetime import UTC, datetime

from sqlalchemy import select

from app.domain.device.wiring import sql_device_service
from app.domain.project.models import Project
from app.domain.team.repositories import TeamRepository
from app.domain.user.models import User
from tests.conftest import seed_user
from tests.integration.conftest import a_team


def setup_team(client):
    tokens = {
        name: {"Authorization": f"Bearer {seed_user(client, name)}"}
        for name in ("enroller", "admin", "member", "outsider")
    }

    async def seed():
        async with client.test_factory() as db:
            ids = dict(
                (
                    await db.execute(
                        select(User.username, User.id).where(User.username.in_(tokens))
                    )
                ).all()
            )
            team = await a_team(db)
            other = await a_team(db)
            repo = TeamRepository(db)
            await repo.add_member(team, ids["enroller"], 1)
            await repo.add_member(team, ids["admin"], 1)
            await repo.add_member(team, ids["member"], 2)
            await repo.add_member(other, ids["outsider"], 0)
            project = Project(team_id=team, name="Team work", owner_handle="admin")
            outside = Project(team_id=other, name="Other work", owner_handle="outsider")
            db.add_all([project, outside])
            await db.commit()
            return team, other, project.id, outside.id

    return tokens, asyncio.run(seed())


def enroll(client, headers, team_id=None):
    code = client.post(
        "/connector/auth/device/start", json={"device_name": "Computer"}
    ).json()["device_code"]
    response = client.post(
        "/connector/connect",
        headers=headers,
        json={"device_code": code, "team_id": team_id},
    )
    assert response.status_code == 200, response.text
    return response.json(), code


def test_team_admin_can_manage_another_members_enrollment(client):
    headers, (team, _, _, _) = setup_team(client)
    device, _ = enroll(client, headers["enroller"], team)
    renamed = client.patch(
        f"/connector/my/devices/{device['device_id']}",
        headers=headers["admin"],
        json={"name": "Team computer"},
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "Team computer"


def test_enroller_loses_management_when_leaving_and_leaves_resource_with_team(client):
    headers, (team, _, project, outside) = setup_team(client)
    device, code = enroll(client, headers["enroller"], team)
    did = device["device_id"]

    async def leave():
        async with client.test_factory() as db:
            user = await db.scalar(select(User.id).where(User.username == "enroller"))
            relation = await TeamRepository(db).get_member_relation(team, user)
            relation.deleted_at = datetime.now(UTC)
            await db.commit()

    asyncio.run(leave())
    for name in ("enroller", "member", "outsider"):
        response = client.patch(
            f"/connector/my/devices/{did}", headers=headers[name], json={"name": "No"}
        )
        assert response.status_code == 403, (name, response.text)
    listed = client.get(f"/connector/teams/{team}/devices", headers=headers["admin"])
    assert [d["device_id"] for d in listed.json()["devices"]] == [did]
    assert listed.json()["can_manage"] is True
    member_view = client.get(
        f"/connector/teams/{team}/devices", headers=headers["member"]
    )
    assert member_view.json()["can_manage"] is False
    assert (
        client.get(
            f"/connector/teams/{team}/devices", headers=headers["enroller"]
        ).status_code
        == 403
    )

    async def routing():
        async with client.test_factory() as db:
            service = sql_device_service(db)
            return await service.serves_project(
                did, project
            ), await service.serves_project(did, outside)

    assert asyncio.run(routing()) == (True, False)
    for name in ("enroller", "member", "outsider"):
        assert (
            client.delete(
                f"/connector/my/devices/{did}", headers=headers[name]
            ).status_code
            == 403
        )
    assert (
        client.delete(
            f"/connector/my/devices/{did}", headers=headers["admin"]
        ).status_code
        == 200
    )
    poll = client.post("/connector/auth/device/poll", json={"device_code": code})
    assert poll.json() == {"status": "pending"}


def test_personal_default_and_independent_second_team_enrollment(client):
    headers, (team, other, _, _) = setup_team(client)
    personal, _ = enroll(client, headers["enroller"])
    shared, _ = enroll(client, headers["enroller"], team)
    assert personal["team_id"] != shared["team_id"]
    assert personal["device_id"] != shared["device_id"]
    assert shared["team_id"] == team
    code = client.post("/connector/auth/device/start", json={}).json()["device_code"]
    for actor, tid in (("member", team), ("enroller", other)):
        response = client.post(
            "/connector/connect",
            headers=headers[actor],
            json={"device_code": code, "team_id": tid},
        )
        assert response.status_code == 403, response.text


def test_team_lending_endpoints_are_gone(client):
    headers, (team, _, _, _) = setup_team(client)
    device, _ = enroll(client, headers["enroller"], team)
    did = device["device_id"]
    assert client.post(
        f"/connector/my/devices/{did}/teams",
        headers=headers["enroller"],
        json={"team_id": team},
    ).status_code in (404, 405)
    assert client.delete(
        f"/connector/my/devices/{did}/teams/{team}", headers=headers["enroller"]
    ).status_code in (404, 405)


def test_closing_the_enroller_keeps_the_shared_teams_device(client):
    from app.domain.account.deletion import close_account

    headers, (team, _, project, _) = setup_team(client)
    shared, _ = enroll(client, headers["enroller"], team)
    personal, _ = enroll(client, headers["enroller"])

    async def close_and_read():
        async with client.test_factory() as db:
            user = await db.scalar(select(User.id).where(User.username == "enroller"))
            await close_account(db, user)
            await db.commit()
        async with client.test_factory() as db:
            devices = sql_device_service(db)
            return (
                await devices.serves_project(shared["device_id"], project),
                await devices.get_device(personal["device_id"]),
            )

    assert asyncio.run(close_and_read()) == (True, None)
    listed = client.get(f"/connector/teams/{team}/devices", headers=headers["admin"])
    assert [d["device_id"] for d in listed.json()["devices"]] == [shared["device_id"]]
