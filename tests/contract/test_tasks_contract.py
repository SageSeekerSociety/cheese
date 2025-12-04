from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.anyio
async def test_python_get_task_not_found(python_client: AsyncClient) -> None:
    """Python 后端：GET /tasks/{id} 不存在时返回 404/400/422。"""
    resp = await python_client.get("/tasks/0")
    assert resp.status_code in (404, 400, 422)


@pytest.mark.anyio
async def test_python_get_task_shape(python_client: AsyncClient) -> None:
    """Python 后端：GET /tasks/{id} 响应结构检查（仅结构，不要求具体业务语义）。"""
    # 使用一个占位 ID；当有真实数据时再细化测试
    resp = await python_client.get("/tasks/1")
    # 允许 200 或 404（无数据时）
    assert resp.status_code in (200, 404)
    if resp.status_code != 200:
        return

    body = resp.json()
    assert set(body.keys()) == {"code", "message", "data"}
    data = body["data"]
    assert "task" in data
    assert "participation" in data

    task = data["task"]
    for key in ("id", "name", "intro", "description", "defaultDeadline", "resubmittable", "editable", "createdAt", "updatedAt"):
        assert key in task


@pytest.mark.anyio
async def test_python_get_tasks_shape(python_client: AsyncClient) -> None:
    """Python 后端：GET /tasks 列表接口基本结构检查。"""
    # space 为必填，根据实际数据选值；这里先用 1 作为占位
    resp = await python_client.get("/tasks", params={"space": 1, "pageSize": 10})
    # 允许 200 或 400（例如 space 不存在时的业务错误）；只在 200 时检查结构
    assert resp.status_code in (200, 400)
    if resp.status_code != 200:
        return

    body = resp.json()
    assert set(body.keys()) == {"code", "message", "data"}
    data = body["data"]
    assert "tasks" in data
    assert "page" in data

    page = data["page"]
    for key in ("pageStart", "pageSize", "hasMore", "nextStart", "total"):
        assert key in page


@pytest.mark.anyio
async def test_python_get_task_participants_shape(python_client: AsyncClient) -> None:
    """Python 后端：GET /tasks/{taskId}/participants 结构检查。"""
    resp = await python_client.get(
        "/tasks/1/participants",
        params={"approved": None, "queryRealNameInfo": False},
    )
    # 允许 200 或 404（task 是否存在取决于数据），只在 200 时检查结构
    assert resp.status_code in (200, 404)
    if resp.status_code != 200:
        return

    body = resp.json()
    assert set(body.keys()) == {"code", "message", "data"}
    data = body["data"]
    assert "participants" in data
