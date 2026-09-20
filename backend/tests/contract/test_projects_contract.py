"""Contract tests for the 知是 team-project resource (``/team-projects``).

The shape asserted here (``colorCode``/``team``/``leader`` …) is the one the
legacy Kotlin service served; the fusion merge dropped the resource, migration
41224effe32b brought it back, and #370 moved it off the generic name ``/projects``
onto ``/team-projects`` so the cheesex workspace resource can have that word.

Every endpoint here is behind ``require_auth_user``, so the probes run on
``authed_client`` and the resource they read is seeded first: on ``python_client``
they all answered 401 before the shape was ever reached, and on an empty database
a shape assertion guarded by ``if resp.status_code != 200: return`` passes without
looking at anything.
"""

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient

_PROJECT_KEYS = {
    "id",
    "name",
    "description",
    "colorCode",
    "startDate",
    "endDate",
    "team",
    "leader",
    "parentId",
    "externalTaskId",
    "githubRepo",
    "archived",
    "members",
    "createdAt",
    "updatedAt",
}


@pytest.fixture
async def seeded_project(authed_client: AsyncClient, seeded_team: int) -> dict:
    """A team project owned by the authed user, created through the real route."""
    start = int(datetime.now(UTC).timestamp() * 1000)
    resp = await authed_client.post(
        "/team-projects",
        json={
            "name": "Contract Project",
            "description": "seeded by the contract suite",
            "colorCode": "#123456",
            "startDate": start,
            "endDate": start + 86_400_000,
            "teamId": seeded_team,
            "leaderId": 1,
        },
    )
    assert resp.status_code == 200, f"seeding failed: {resp.status_code} {resp.text}"
    return resp.json()["data"]["project"]


@pytest.mark.anyio
async def test_python_get_project_not_found(authed_client: AsyncClient) -> None:
    resp = await authed_client.get("/team-projects/0")
    assert resp.status_code in (404, 400, 422)


@pytest.mark.anyio
async def test_python_get_project_shape(
    authed_client: AsyncClient, seeded_project: dict
) -> None:
    resp = await authed_client.get(f"/team-projects/{seeded_project['id']}")
    assert resp.status_code == 200, resp.text

    body = resp.json()
    assert set(body.keys()) == {"code", "message", "data"}
    project = body["data"]["project"]
    assert _PROJECT_KEYS <= set(project)
    assert set(project["team"]) >= {"id", "name", "intro", "avatarId"}
    assert set(project["leader"]) >= {"id", "username", "nickname"}
    assert set(project["members"]) == {"count", "examples"}


@pytest.mark.anyio
async def test_python_get_projects_shape(
    authed_client: AsyncClient, seeded_team: int, seeded_project: dict
) -> None:
    resp = await authed_client.get("/team-projects", params={"team_id": seeded_team})
    assert resp.status_code == 200, resp.text

    body = resp.json()
    assert set(body.keys()) == {"code", "message", "data"}
    projects = body["data"]["projects"]
    assert [p["id"] for p in projects] == [seeded_project["id"]]
    assert _PROJECT_KEYS <= set(projects[0])
