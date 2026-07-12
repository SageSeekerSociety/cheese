"""Contract tests for the 知是-style flat ``/projects`` resource.

These assert a 知是 project shape (``colorCode``/``team``/``leader`` …) served at
``/projects`` and ``/projects/{id}``. That resource is NOT present in the fusion-
merged app: the surviving projects router is cheesex's, mounted at ``/api/projects``
with a different shape (``{data: [...], total}``). The 知是 flat project endpoints
were superseded in the merge, so there is nothing here to contract-test — the whole
module is skipped until (and unless) that resource is re-introduced.
"""

import pytest

pytestmark = pytest.mark.skip(
    reason="知是 flat /projects resource not present in the merged app "
    "(superseded by cheesex /api/projects — see module docstring)"
)


@pytest.mark.anyio
async def test_python_get_project_not_found(python_client) -> None:
    resp = await python_client.get("/projects/0")
    assert resp.status_code in (404, 400, 422)


@pytest.mark.anyio
async def test_python_get_project_shape(python_client) -> None:
    resp = await python_client.get("/projects/1")
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
    resp = await python_client.get("/projects", params={"team_id": 1})
    assert resp.status_code in (200, 400)
    if resp.status_code != 200:
        return

    body = resp.json()
    assert set(body.keys()) == {"code", "message", "data"}
    data = body["data"]
    assert "projects" in data
