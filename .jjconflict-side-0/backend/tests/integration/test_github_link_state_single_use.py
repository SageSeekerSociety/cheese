"""连接 GitHub 账号的 state 只能用一次（#222）。

2026-08-10 dev 上的真事：小鱼儿点「连接 GitHub 账号」拿到 authorize URL，她自己
那边没走完，就把链接贴进群里求助。29 秒后，**另一个登录着别人账号的浏览器**带着
她的 state 打到了回调。那次是运气好——点的人 GitHub 已经绑过了，撞上
`already_linked` 静默失败。反过来才是危险的那半：点的人 GitHub 没绑过的话，他的
GitHub 身份会被绑到**她的**平台账号上，两边都不会知道。

签名和 TTL 在这里一点忙都帮不上：转发出去的 state 签名完好、也没过期。能拦住它
的只有「这张票已经用过了」这件被服务端记住的事。

下面这些测试钉的就是这条：签名对不对之外，**第二次必须不行**。
"""

import uuid

import pytest

from app.core.github_install_state import (
    ACCOUNT_LINK_TTL_S,
    mint_account_link_state,
    verify_account_link_state,
)
from app.core.single_use_state import (
    SingleUseUnavailableError,
    claim,
    reserve,
)

pytestmark = pytest.mark.anyio

SCOPE = "test_github_account_link"


@pytest.fixture(autouse=True)
def _redis_client_per_loop():
    """`get_redis_client` is `@lru_cache`d, so the connection it hands out is
    bound to whichever event loop asked first. The app has exactly one loop, so
    that is correct in production — but every anyio test gets a fresh loop, and
    reusing the cached client across them fails with `attached to a different
    loop`. Drop the cache around each test rather than weaken the app's.
    """
    from app.core.redis import get_redis_client

    get_redis_client.cache_clear()
    yield
    get_redis_client.cache_clear()


async def test_a_reserved_token_can_be_spent_exactly_once():
    jti = uuid.uuid4().hex
    await reserve(SCOPE, jti, ttl_s=60)

    assert await claim(SCOPE, jti) is True
    # 这一行就是整个 issue：转发出去的那次点击落在这里。
    assert await claim(SCOPE, jti) is False


async def test_a_token_that_was_never_reserved_cannot_be_spent():
    """一个签名完好、但平台没发过预约的 state（比如换了签名密钥之前铸的、或者
    伪造 jti 的），不能因为「redis 里查无此人」就当成没用过而放行。"""
    assert await claim(SCOPE, uuid.uuid4().hex) is False


async def test_two_callbacks_racing_produce_exactly_one_winner():
    """两个人同时点同一条链接。DELETE 是原子的，所以只可能有一个 True。"""
    import asyncio

    jti = uuid.uuid4().hex
    await reserve(SCOPE, jti, ttl_s=60)

    results = await asyncio.gather(*[claim(SCOPE, jti) for _ in range(8)])

    assert sum(1 for r in results if r) == 1


async def test_redis_being_down_is_a_refusal_not_a_pass(monkeypatch):
    """去重缓存挂了可以降级成「放行」，单次有效不行——那等于在没人看着的时候
    把这层保护静默摘掉。"""

    class Dead:
        async def set(self, *a, **k):
            raise ConnectionError("boom")

        async def delete(self, *a, **k):
            raise ConnectionError("boom")

    monkeypatch.setattr(
        "app.core.single_use_state.get_redis_client", lambda: Dead(), raising=True
    )

    with pytest.raises(SingleUseUnavailableError):
        await reserve(SCOPE, "x", ttl_s=60)
    with pytest.raises(SingleUseUnavailableError):
        await claim(SCOPE, "x")


async def test_redis_not_configured_is_also_a_refusal(monkeypatch):
    monkeypatch.setattr(
        "app.core.single_use_state.get_redis_client", lambda: None, raising=True
    )

    with pytest.raises(SingleUseUnavailableError):
        await claim(SCOPE, "x")


async def test_the_minted_state_carries_the_jti_it_reserved():
    """铸出来的 jti 和 state 里的必须是同一个，否则预约的是一张、花掉的是另一张，
    两边都对不上——链接会变成一次都用不了。"""
    pid = uuid.uuid4()
    minted = mint_account_link_state(470, return_project_id=pid)

    claims = verify_account_link_state(minted.state)

    assert claims is not None
    assert claims.jti == minted.jti
    assert claims.user_id == 470
    assert claims.return_project_id == pid


async def test_two_mints_never_share_a_jti():
    a = mint_account_link_state(470)
    b = mint_account_link_state(470)

    assert a.jti != b.jti


async def test_a_state_without_a_jti_is_not_valid():
    """上一版铸出来的 state 没有 jti，没法被花掉一次——那它就不是有效 state，
    不能因为「其余字段都对」而放行。600 秒内全部自然消失。"""
    import time

    import jwt

    from app.core.config import settings

    now = int(time.time())
    legacy = jwt.encode(
        {
            "uid": 470,
            "rpid": None,
            "type": "github_account_link",
            "iat": now,
            "exp": now + ACCOUNT_LINK_TTL_S,
        },
        settings.jwt_secret,
        algorithm="HS256",
    )

    assert verify_account_link_state(legacy) is None
