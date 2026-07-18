"""项目归团队 (execution-architecture v4).

Every project belongs to a team: an explicit team_id sticks, and a personal
project (no team given) is linked to its owner's personal team at CREATE time
(个人 = 单人真团队). The personal team's 项目 page folds in legacy NULL-team
rows so pre-existing projects stay visible.
"""

import asyncio
from datetime import UTC, datetime

from app.domain.team.models import Team
from app.domain.team.repositories import TeamRepository
from app.domain.user.models import User


def _now() -> datetime:
    return datetime.now(UTC)


def _create(client, name: str, owner: str, team_id: int | None = None) -> dict:
    body: dict = {"name": name, "owner_handle": owner}
    if team_id is not None:
        body["team_id"] = team_id
    resp = client.post("/api/projects", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def test_personal_project_links_to_personal_team_at_create(client):
    async def seed_user() -> int:
        async with client.test_factory() as s:
            user = User(
                username="gina",
                email="gina@example.io",
                created_at=_now(),
                updated_at=_now(),
            )
            s.add(user)
            await s.commit()
            return user.id

    user_id = asyncio.run(seed_user())

    created = _create(client, "gina-personal", "gina")

    async def personal_team_id() -> int | None:
        async with client.test_factory() as s:
            team = await TeamRepository(session=s).get_personal_team(user_id)
            return team.id if team else None

    tid = asyncio.run(personal_team_id())
    # The personal team was auto-provisioned and the project belongs to it.
    assert tid is not None
    assert created["team_id"] == tid

    # The personal team's 项目 page lists it…
    listing = client.get(f"/api/projects?team_id={tid}").json()["data"]["data"]
    names = [p["name"] for p in listing]
    assert "gina-personal" in names

    # …and also folds in a legacy NULL-team row of the same owner.
    async def add_legacy() -> None:
        async with client.test_factory() as s:
            from app.domain.project.repositories import ProjectRepository

            await ProjectRepository(s).add(name="gina-legacy", owner_handle="gina")
            await s.commit()

    asyncio.run(add_legacy())
    listing = client.get(f"/api/projects?team_id={tid}").json()["data"]["data"]
    names = [p["name"] for p in listing]
    assert "gina-legacy" in names and "gina-personal" in names


def test_explicit_team_id_sticks_and_unknown_owner_stays_null(client):
    async def seed_team() -> int:
        async with client.test_factory() as s:
            team = Team(
                name="Proj Owners",
                intro="i",
                description="d",
                avatar_id=0,
                created_at=_now(),
                updated_at=_now(),
            )
            s.add(team)
            await s.flush()
            tid = team.id
            await s.commit()
            return tid

    tid = asyncio.run(seed_team())

    shared = _create(client, "shared-proj", "nobody-here", team_id=tid)
    assert shared["team_id"] == tid

    # owner_handle that is no real user → legacy NULL (agent handles, fixtures).
    orphan = _create(client, "orphan-proj", "ghost-agent-42")
    assert orphan["team_id"] is None

    listing = client.get(f"/api/projects?team_id={tid}").json()["data"]["data"]
    assert [p["name"] for p in listing] == ["shared-proj"]
