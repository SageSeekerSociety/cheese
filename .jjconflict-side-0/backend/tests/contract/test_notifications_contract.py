"""契约测试（Contract Tests）：通知相关 API。

两类用例：

* ``test_kotlin_*`` —— 跨后端契约，直接打真实的 Kotlin/NT 后端（行为来源）。
  需要外部服务，靠 ``KOTLIN_BASE_URL`` 环境变量开关：设置了就跑（CI 里挂了 NT
  服务时），没设就干净跳过。
* ``test_python_*`` —— 针对**知是扁平** ``/notifications`` 资源
  （``/notifications``、``/notifications/unread-count``、``PATCH /notifications``、
  ``PUT /notifications/status``、``PATCH|DELETE /notifications/{id}``）。该资源由
  ``app.api.routes.notifications_flat`` 提供，把合并时被漏接的
  ``NotificationService``（知是 int 收件箱）重新接到 HTTP 上——顶栏通知铃调的就是
  这套。与 cheesex 的 ``/api/projects/{id}/alerts``（uuid、项目范围）是另一
  套、互不相干。用 ``authed_client`` 打（这些端点要求登录用户），断言响应形状。
"""

import os

import pytest
from httpx import AsyncClient

_KOTLIN_BASE_URL = os.environ.get("KOTLIN_BASE_URL")

kotlin_only = pytest.mark.skipif(
    not _KOTLIN_BASE_URL,
    reason="cross-backend contract needs the Kotlin/NT backend (set KOTLIN_BASE_URL)",
)


@pytest.fixture
def kotlin_base_url() -> str:
    # Only reached when KOTLIN_BASE_URL is set (tests are otherwise skipped).
    assert _KOTLIN_BASE_URL is not None
    return _KOTLIN_BASE_URL


@kotlin_only
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


@kotlin_only
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


@kotlin_only
@pytest.mark.anyio
async def test_kotlin_get_notification_by_id_not_found(kotlin_base_url: str) -> None:
    """Kotlin 后端：不存在的通知 ID 应返回 404 或等价错误。"""
    async with AsyncClient(base_url=kotlin_base_url) as client:
        resp = await client.get("/notifications/0")

    assert resp.status_code in (400, 404, 422)


@pytest.mark.anyio
async def test_python_unread_count_shape(authed_client: AsyncClient) -> None:
    """Python 后端：/notifications/unread-count 响应结构检查。"""
    resp = await authed_client.get("/notifications/unread-count")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, dict)
    assert set(data.keys()) == {"code", "message", "data"}
    assert "count" in data["data"]
    assert isinstance(data["data"]["count"], int)


@pytest.mark.anyio
async def test_python_list_notifications_shape(authed_client: AsyncClient) -> None:
    """Python 后端：/notifications 列表接口基本结构检查。"""
    resp = await authed_client.get("/notifications", params={"pageSize": 10})
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
    authed_client: AsyncClient,
) -> None:
    """Python 后端：PATCH /notifications（批量更新）结构检查。"""
    payload = {"updates": [{"id": 1, "read": True}, {"id": 2, "read": True}]}
    resp = await authed_client.patch("/notifications", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"code", "message", "data"}
    assert "updatedIds" in body["data"]
    assert isinstance(body["data"]["updatedIds"], list)


@pytest.mark.anyio
async def test_python_collective_status_shape(authed_client: AsyncClient) -> None:
    """Python 后端：PUT /notifications/status 结构检查。"""
    resp = await authed_client.put("/notifications/status", json={"read": True})
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"code", "message", "data"}
    assert "count" in body["data"]
    assert isinstance(body["data"]["count"], int)


@pytest.mark.anyio
async def test_python_update_notification_status_not_found(
    authed_client: AsyncClient,
) -> None:
    """Python 后端：PATCH /notifications/{id} 不存在资源时返回 404/400/422。"""
    resp = await authed_client.patch("/notifications/0", json={"read": True})
    assert resp.status_code in (400, 404, 422)


@pytest.mark.anyio
async def test_python_delete_notification_not_found(authed_client: AsyncClient) -> None:
    """Python 后端：DELETE /notifications/{id} 不存在资源时返回 404。"""
    resp = await authed_client.delete("/notifications/0")
    assert resp.status_code in (404, 204)
