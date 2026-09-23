"""保留字在真实注册路径上确实被挡住，而平台自己的行不受影响（#345）。

`tests/unit/test_reserved_usernames.py` 钉的是那个判定函数。这里钉的是它有没有被
**接到路上**——一个只被单元测试覆盖、没有任何调用方的判定函数，等于没做。

最后一条是这个改动最危险的地方：芝士的用户行由 `IdentityService.ensure_agent_user`
在每次启动时创建，走的是仓储层而不是注册。要是保留字检查被放进仓储或
`create_user`，平台就再也建不出 `cheese`，启动直接炸。所以这条真的去建一次。
"""

import pytest

from app.domain.identity.handles import CHEESE_HANDLE
from tests.support.consent import SIGNUP_CONSENT

pytestmark = pytest.mark.anyio


def _payload(username: str) -> dict:
    return {
        "username": username,
        "nickname": "n-" + username,
        "email": f"{username}-reserved-test@example.com",
        "emailCode": "123456",
        "password": "TestPassword123!",
        "consent": SIGNUP_CONSENT,
    }


@pytest.mark.parametrize("name", ["anonymous", "system", "cheese", "Anonymous"])
async def test_registration_refuses_the_platforms_own_words(client, name: str):
    r = client.post("/users", json=_payload(name))

    assert r.status_code == 422, r.text
    assert "保留" in r.json()["message"], r.text


async def _arm_email_code(email: str) -> str:
    """走真实的发码路径拿到验证码 —— 注册路由是真的会校验它的。

    不 monkeypatch 校验函数：那样对照组就不再证明「整条注册路径是通的」，而这正是
    它存在的唯一理由。只替换发信这一步，验证码从信里读出来。
    """
    import re

    from redis.asyncio import Redis as AsyncRedis

    from app.core.config import settings
    from app.domain.user.verification_service import EmailVerificationService

    class _Outbox:
        is_configured = True
        body = ""

        async def send(self, **kwargs) -> bool:
            self.body = kwargs["body_text"]
            return True

    outbox = _Outbox()
    redis = AsyncRedis.from_url(settings.redis_url, decode_responses=False)
    try:
        service = EmailVerificationService(redis)
        service._sender = outbox
        await service.send_verification_code(email)
    finally:
        await redis.aclose()

    match = re.search(r"\b(\d{6})\b", outbox.body)
    assert match, outbox.body
    return match.group(1)


async def test_an_ordinary_name_still_registers(client):
    """对照组：上面那条要是因为**任何**别的原因 422（邮箱验证码、邀请码、密码
    规则），这条会跟着红——否则「注册被挡住了」可能根本不是保留字挡的。

    第一版就是这么翻的：五条全 422，其中挂掉的原因是验证码，跟保留字毫无关系。"""
    payload = _payload("anonymouszhang")
    payload["emailCode"] = await _arm_email_code(payload["email"])

    r = client.post("/users", json=payload)

    assert r.status_code == 200, r.text


async def test_the_platform_can_still_create_its_own_agent_row(db_factory):
    """芝士自己那行必须照建不误——不然平台起不来。"""
    from app.domain.identity.services import IdentityService

    async with db_factory() as session:
        user = await IdentityService(session).ensure_agent_user()
        await session.commit()

    assert user.username == CHEESE_HANDLE
