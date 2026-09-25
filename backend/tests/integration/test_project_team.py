"""项目归团队 (execution-architecture v4).

Every project belongs to a team: an explicit team_id sticks, a personal
project (no team given) is linked to its owner's personal team at CREATE time
(个人 = 单人真团队), and an owner who is no registered person, with no team
given, has nowhere for the project to belong — it is refused.
"""

import asyncio
import uuid
from datetime import UTC, datetime

from app.domain.team.models import Team
from app.domain.team.repositories import TeamRepository
from app.domain.user.models import User
from tests.integration.conftest import post_project


def _now() -> datetime:
    return datetime.now(UTC)


def _create(client, name: str, owner: str, team_id: int | None = None) -> dict:
    body: dict = {"name": name, "owner_handle": owner}
    if team_id is not None:
        body["team_id"] = team_id
    resp = post_project(client, json=body)
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
    listing = client.get(f"/projects?team_id={tid}").json()["data"]["data"]
    names = [p["name"] for p in listing]
    assert "gina-personal" in names


def test_explicit_team_id_sticks_and_an_unknown_owner_is_refused(client):
    async def seed_team() -> int:
        async with client.test_factory() as s:
            team = Team(
                name="Proj Owners",
                handle=f"t-{uuid.uuid4().hex[:12]}",
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
    # Links to the owning team go by its handle, so the project carries it.
    assert shared["team_handle"].startswith("t-")

    # An owner who is no registered person and no team named: nowhere to belong.
    # Posted raw — the post_project helper would register the owner first.
    refused = client.post(
        "/projects", json={"name": "orphan-proj", "owner_handle": "ghost-agent-42"}
    )
    assert refused.status_code == 422, refused.text
    assert "项目需要归属一个团队" in refused.text

    listing = client.get(f"/projects?team_id={tid}").json()["data"]["data"]
    assert [p["name"] for p in listing] == ["shared-proj"]
