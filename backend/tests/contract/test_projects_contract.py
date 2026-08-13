"""Contract tests for the 知是 team-project resource (``/team-projects``).

The shape asserted here (``colorCode``/``team``/``leader`` …) is the one the
legacy Kotlin service served; the fusion merge dropped the resource, migration
41224effe32b brought it back, and #370 moved it off the generic name ``/projects``
onto ``/team-projects`` so the cheesex workspace resource can have that word.

Still skipped, but NOT for the reason this file used to give ("not present in the
merged app" — it is, and ``tests/integration/test_team_projects.py`` exercises it
end to end). The real blocker is this harness: every endpoint here needs
``require_auth_user`` and ``python_client`` carries no credential, so each probe
answers 401 and the shape is never reached. Porting these onto ``authed_client``
is the fix; it is a test-harness job, not a contract question.
"""

import pytest

pytestmark = pytest.mark.skip(
    reason="needs an authenticated client — python_client has no credential, so "
    "every probe 401s before the shape is reached (see module docstring)"
)


@pytest.mark.anyio
async def test_python_get_project_not_found(python_client) -> None:
    resp = await python_client.get("/team-projects/0")
    assert resp.status_code in (404, 400, 422)


@pytest.mark.anyio
async def test_python_get_project_shape(python_client) -> None:
    resp = await python_client.get("/team-projects/1")
    assert resp.status_code in (200, 404)
    if resp.status_code != 200:
        return

    body = resp.json()
    assert set(body.keys()) == {"code", "message", "data"}
    data = body["data"]
    assert "project" in data

    project = data["project"]
    for key in (
        "id",
        "name",
        "description",
        "colorCode",
        "startDate",
        "endDate",
        "team",
        "leader",
        "createdAt",
        "updatedAt",
    ):
        assert key in project


@pytest.mark.anyio
async def test_python_get_projects_shape(python_client) -> None:
    resp = await python_client.get("/team-projects", params={"team_id": 1})
    assert resp.status_code in (200, 400)
    if resp.status_code != 200:
        return

    body = resp.json()
    assert set(body.keys()) == {"code", "message", "data"}
    data = body["data"]
    assert "projects" in data
