"""订阅 token 的后台刷新循环。

这是对 metering-proxy「无本地刷新循环」纪律的**有意例外**，论证写在这里
（与 PR 描述同一份）：

那条纪律（`deploy/metering-proxy/README.md`）针对的是把人态 OAuth 凭据当服务
凭据用、再**散养**一个本地刷新 daemon 的形态，它的结论是「服务凭据用耐用
setup-token、人工年度轮换」。而 ChatGPT 订阅**只有** OAuth 一种凭据形态，
刷新链是它的本质 —— 刷新责任必须有一个受控属主。收在 backend 受管代码里
（PG 行锁串行化、每次刷新落 `gateway_admin_audit`、失败落在订阅行的可读状态
字段上），正是那条禁令想要的「受控」，而不是「散养」。

守卫核实：README 描述的守卫范围是 `deploy/` 下，且它点名的脚本
（`.claude/scripts/check-metering-proxy.sh`）在当前树上**不存在**；本循环在
`backend/`，不在其扫描面内。若日后该守卫恢复，需确认其 glob 仍限 `deploy/`。

形状照 `gateway_catalog.keep_fresh`：`while True` + 整圈 try/except（一次
刷新炸了不许杀死循环）+ interval sleep。每一圈开一个**新** session（循环
有自己的 session_factory，不碰请求路径的会话）。
"""

import asyncio
import logging
from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.domain.agent.gateway_admin import GatewayAdmin
from app.domain.subscription.openai_codex import OpenAICodexOAuth
from app.domain.subscription.services import SubscriptionService

logger = logging.getLogger(__name__)


def _default_admin() -> GatewayAdmin | None:
    """没注入工厂时按配置现造；没配管理凭据给 None（循环照跑，推进那一步
    会被 `_refresh_locked` 记成可读状态）。"""
    if settings.llm_gateway_admin_base and settings.llm_gateway_admin_key:
        return GatewayAdmin(
            settings.llm_gateway_admin_base,
            settings.llm_gateway_admin_key,
        )
    return None


async def keep_tokens_fresh(
    session_factory: async_sessionmaker[AsyncSession],
    oauth_factory: Callable[[], OpenAICodexOAuth] = OpenAICodexOAuth,
    admin_factory: Callable[[], GatewayAdmin | None] = _default_admin,
) -> None:
    """永远刷新下去，马上开始。

    工厂而不是实例：循环长寿，客户端短命（`GatewayAdmin` 只是 base+header 的
    壳，每圈现造零成本）。
    """
    while True:
        try:
            async with session_factory() as session:
                service = SubscriptionService(session, oauth_factory(), admin_factory())
                await service.refresh_due()
        except Exception:  # noqa: BLE001 — 一圈失败绝不能杀死刷新循环
            logger.warning("llm subscription refresh pass failed", exc_info=True)
        await asyncio.sleep(settings.subscription_refresh_interval_s)
