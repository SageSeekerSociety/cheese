import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.common.auth import create_access_token
from app.domain.user.models import User


async def _agent_identity(
    authed_client: AsyncClient,
) -> tuple[int, dict[str, str]]:
    """The seeded agent user's real id, plus a token signed for that id.

    ``authed_client`` signs its token for id 1, which is the agent's id only
    when the per-worker database has never handed out another one; on a fresh
    clone the seeded row can be 9. Read the row instead of assuming the id.
    """
    async with authed_client.test_factory() as session:  # type: ignore[attr-defined]
        user = (
            await session.execute(select(User).where(User.username == "cheese"))
        ).scalar_one()
    headers = {
        "Authorization": f"Bearer {create_access_token(user.id, handle=user.username)}"
    }
    return user.id, headers


@pytest.mark.anyio
async def test_python_get_user_identity_shape(authed_client: AsyncClient) -> None:
    """GET /users/{userId}/identity 响应结构检查。"""
    resp = await authed_client.get("/users/1/identity")
    assert resp.status_code == 200

    body = resp.json()
    assert set(body.keys()) == {"code", "message", "data"}
    data = body["data"]
    assert "hasIdentity" in data
    assert "identity" in data


@pytest.mark.anyio
async def test_python_put_user_identity_without_ticket_shape(
    authed_client: AsyncClient,
) -> None:
    """PUT /users/{userId}/identity 不带 sudo 票时的拒绝结构。

    带票成功的那条要 Redis 预约票据，由 integration 的 test_realname_sudo 覆盖。
    """
    payload = {
        "realName": "Alice",
        "studentId": "20250001",
        "grade": "2025",
        "major": "CS",
        "className": "1",
    }
    resp = await authed_client.put("/users/1/identity", json=payload)
    assert resp.status_code == 403

    body = resp.json()
    assert body["error"]["name"] == "SudoRequiredError"


@pytest.mark.anyio
async def test_python_patch_and_put_user_profile_agree(
    authed_client: AsyncClient,
) -> None:
    """PATCH 与 PUT /users/{userId} 是同一次更新的两种写法。

    同一个请求体必须得到同一个响应结构，否则客户端换一个动词就会看到
    不一样的结果。
    """
    user_id, headers = await _agent_identity(authed_client)
    before = await authed_client.get(f"/users/{user_id}", headers=headers)
    profile = before.json()["data"]["user"]
    body = {"nickname": "Contract Profile", "intro": "contract test"}

    patch_resp = await authed_client.patch(
        f"/users/{user_id}", json=body, headers=headers
    )
    put_resp = await authed_client.put(f"/users/{user_id}", json=body, headers=headers)

    assert patch_resp.status_code == 200
    assert put_resp.status_code == 200
    assert set(patch_resp.json().keys()) == {"code", "message", "data"}
    assert patch_resp.json() == put_resp.json()

    # 把预置的 agent 资料还回去，后面的用例还要用它。
    restored = await authed_client.put(
        f"/users/{user_id}",
        json={"nickname": profile["nickname"], "intro": profile["intro"]},
        headers=headers,
    )
    assert restored.status_code == 200


@pytest.mark.anyio
async def test_python_patch_and_put_user_profile_reject_the_same_invalid_body(
    authed_client: AsyncClient,
) -> None:
    """PATCH 与 PUT 对同一个非法请求体给出同一个拒绝。"""
    user_id, headers = await _agent_identity(authed_client)
    for verb in ("patch", "put"):
        resp = await getattr(authed_client, verb)(
            f"/users/{user_id}", json={"nickname": "  "}, headers=headers
        )
        assert resp.status_code == 422


@pytest.mark.anyio
async def test_python_get_user_identity_access_logs_shape(
    authed_client: AsyncClient,
) -> None:
    """GET /users/{userId}/identity/access-logs 响应结构检查。"""
    resp = await authed_client.get(
        "/users/1/identity/access-logs", params={"pageSize": 10}
    )
    assert resp.status_code == 200

    body = resp.json()
    assert set(body.keys()) == {"code", "message", "data"}
    data = body["data"]
    assert "logs" in data
    assert "page" in data

    page = data["page"]
    for key in ("pageStart", "pageSize", "hasMore", "nextStart", "total"):
        assert key in page
