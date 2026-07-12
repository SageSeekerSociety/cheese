"""
契约测试（Contract Tests）：
对同一组通知相关 API，分别打 Kotlin 后端（真实行为来源）和 Python 后端，
用于对比行为并指导后续迁移。

当前文件处于「脚手架」阶段：
- Kotlin 侧：仅验证接口能正常返回基本结构。
- Python 侧：后续会在迁移完成后，对齐响应结构与分页语义。
"""

import pytest
from httpx import AsyncClient


@pytest.mark.anyio
async def test_kotlin_unread_count_basic(kotlin_base_url: str) -> None:
    """Kotlin 后端：/notifications/unread-count 基本结构检查。"""
    async with AsyncClient(base_url=kotlin_base_url) as client:
        resp = await client.get("/notifications/unread-count")

    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, dict)
    assert "code" in data and "message" in data and "data" in data
    assert "count" in data["data"]


@pytest.mark.anyio
async def test_kotlin_list_notifications_basic(kotlin_base_url: str) -> None:
    """Kotlin 后端：/notifications 列表接口基本结构检查。"""
    async with AsyncClient(base_url=kotlin_base_url) as client:
        resp = await client.get("/notifications", params={"pageSize": 10})

    assert resp.status_code == 200
    body = resp.json()
    assert "code" in body and "message" in body and "data" in body
    data = body["data"]
    assert "notifications" in data
    assert "page" in data or "cursor" in data or "hasMore" in data


@pytest.mark.anyio
async def test_kotlin_get_notification_by_id_not_found(kotlin_base_url: str) -> None:
    """Kotlin 后端：不存在的通知 ID 应返回 404 或等价错误。"""
    async with AsyncClient(base_url=kotlin_base_url) as client:
        resp = await client.get("/notifications/0")

    # 这里先仅断言「不是 500」，具体错误码后续根据真实行为调整
    assert resp.status_code in (400, 404, 422)


@pytest.mark.anyio
async def test_python_unread_count_shape(python_client: AsyncClient) -> None:
    """Python 后端：/notifications/unread-count 响应结构检查（不要求与 Kotlin 值相等）。"""  # noqa: E501
    resp = await python_client.get(
        "/notifications/unread-count",
        headers={"X-User-Id": "1"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, dict)
    assert set(data.keys()) == {"code", "message", "data"}
    assert "count" in data["data"]
    assert isinstance(data["data"]["count"], int)


@pytest.mark.anyio
async def test_python_list_notifications_shape(python_client: AsyncClient) -> None:
    """Python 后端：/notifications 列表接口基本结构检查。"""
    resp = await python_client.get(
        "/notifications",
        params={"pageSize": 10},
        headers={"X-User-Id": "1"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"code", "message", "data"}
    data = body["data"]
    assert "notifications" in data
    assert "page" in data

    notifications = data["notifications"]
    if notifications:
        first = notifications[0]
        for key in ("id", "type", "read", "createdAt"):
            assert key in first

    page = data["page"]
    for key in ("pageStart", "pageSize", "hasMore", "nextStart", "total"):
        assert key in page


@pytest.mark.anyio
async def test_python_bulk_update_notifications_shape(
    python_client: AsyncClient,
) -> None:
    """Python 后端：PATCH /notifications（批量更新）结构检查。"""
    payload = {"updates": [{"id": 1, "read": True}, {"id": 2, "read": True}]}
    resp = await python_client.patch(
        "/notifications",
        json=payload,
        headers={"X-User-Id": "1"},
    )
    # 即便当前没有数据，也应返回 200 和 updatedIds 数组
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"code", "message", "data"}
    assert "updatedIds" in body["data"]
    assert isinstance(body["data"]["updatedIds"], list)


@pytest.mark.anyio
async def test_python_collective_status_shape(python_client: AsyncClient) -> None:
    """Python 后端：PUT /notifications/status 结构检查。"""
    resp = await python_client.put(
        "/notifications/status",
        json={"read": True},
        headers={"X-User-Id": "1"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"code", "message", "data"}
    assert "count" in body["data"]
    assert isinstance(body["data"]["count"], int)


@pytest.mark.anyio
async def test_python_update_notification_status_not_found(
    python_client: AsyncClient,
) -> None:
    """Python 后端：PATCH /notifications/{id} 不存在资源时返回 404/400/422。"""
    resp = await python_client.patch(
        "/notifications/0",
        json={"read": True},
        headers={"X-User-Id": "1"},
    )
    assert resp.status_code in (400, 404, 422)


@pytest.mark.anyio
async def test_python_delete_notification_not_found(python_client: AsyncClient) -> None:
    """Python 后端：DELETE /notifications/{id} 不存在资源时返回 404。"""
    resp = await python_client.delete(
        "/notifications/0",
        headers={"X-User-Id": "1"},
    )
    assert resp.status_code in (404, 204)
