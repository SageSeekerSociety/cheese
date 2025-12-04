from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.anyio
async def test_python_get_project_not_found(python_client: AsyncClient) -> None:
    """Python 后端：GET /projects/{id} 不存在时返回 404/400/422。"""
    resp = await python_client.get("/projects/0")
    assert resp.status_code in (404, 400, 422)


@pytest.mark.anyio
async def test_python_get_project_shape(python_client: AsyncClient) -> None:
    """GET /projects/{id} 响应结构检查。"""
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
async def test_python_get_projects_shape(python_client: AsyncClient) -> None:
    """GET /projects 列表接口结构检查。"""
    resp = await python_client.get("/projects", params={"team_id": 1})
    assert resp.status_code in (200, 400)
    if resp.status_code != 200:
        return

    body = resp.json()
    assert set(body.keys()) == {"code", "message", "data"}
    data = body["data"]
    assert "projects" in data

