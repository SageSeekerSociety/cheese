"""铃铛未读数这条路由的 401 到底意味着什么。

2026-08-11 有人拿线上 console 里的 `GET /api/notifications/unread-count → 401`
断定「本仓后端根本没有这条路由，路由不存在被鉴权中间件先挡下所以返回 401」，
据此要去改前端路径。改了会把一条本来是好的路由改坏。

这条路由是有的（2026-07-12 的 notifications_flat.py 接上的），带合法令牌就是
200。这里把四种凭证下的返回码钉住，让「401 = 凭证问题，不是路由问题」这句话
有个可执行的凭据：

- 无凭证 / 乱码令牌 / 过期令牌 → 401
- 合法令牌                     → 200

顺带钉住对照组：`/notifications/<非数字>` 这种真不存在的路径，未认证时确实会
先返回 401（它匹配到了 `/notifications/{notification_id}`，鉴权依赖先跑），
带合法令牌才露出 400。所以「401」这个信号本身分辨不了路由在不在——只有带上
合法令牌再打一次才分辨得了。
"""

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from httpx import AsyncClient

from app.common.auth import create_access_token
from app.core.config import settings

_UNREAD_COUNT = "/notifications/unread-count"
_AGENT_USER_ID = 1


def _expired_token() -> str:
    """一个签名合法、但 15 分钟有效期早已过完的访问令牌。"""
    issued = datetime.now(UTC) - timedelta(hours=2)
    return jwt.encode(
        {
            "sub": str(_AGENT_USER_ID),
            "type": "access",
            "handle": "cheese",
            "iat": int(issued.timestamp()),
            "exp": int((issued + timedelta(minutes=15)).timestamp()),
        },
        settings.jwt_secret,
        algorithm="HS256",
    )


@pytest.mark.anyio
async def test_valid_token_gets_a_real_count(python_client: AsyncClient) -> None:
    token = create_access_token(_AGENT_USER_ID, handle="cheese")
    resp = await python_client.get(
        _UNREAD_COUNT, headers={"Authorization": f"Bearer {token}"}
    )

    assert resp.status_code == 200
    assert resp.json()["data"]["count"] == 0


@pytest.mark.anyio
@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({}, id="no-credential"),
        pytest.param({"Authorization": "Bearer not-a-jwt"}, id="garbage-token"),
    ],
)
async def test_bad_credential_is_401(
    python_client: AsyncClient, headers: dict[str, str]
) -> None:
    assert (await python_client.get(_UNREAD_COUNT, headers=headers)).status_code == 401


@pytest.mark.anyio
async def test_expired_token_is_401(python_client: AsyncClient) -> None:
    """前端存着的令牌一旦过期，铃铛就是这个 401——和路由在不在没有关系。"""
    resp = await python_client.get(
        _UNREAD_COUNT, headers={"Authorization": f"Bearer {_expired_token()}"}
    )
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_401_alone_cannot_tell_you_the_route_is_missing(
    python_client: AsyncClient,
) -> None:
    """对照组：一条真不存在的路径，未认证时同样是 401。

    所以线上看到 401 不能推出「路由没了」。带上合法令牌再打一次才有分辨力——
    真不存在的路径这时会露出 400（`{notification_id}` 转 int 失败），而
    unread-count 会给 200。
    """
    missing = "/notifications/does-not-exist-xyz"

    assert (await python_client.get(missing)).status_code == 401

    token = create_access_token(_AGENT_USER_ID, handle="cheese")
    authed = {"Authorization": f"Bearer {token}"}
    assert (await python_client.get(missing, headers=authed)).status_code == 400
    assert (await python_client.get(_UNREAD_COUNT, headers=authed)).status_code == 200
