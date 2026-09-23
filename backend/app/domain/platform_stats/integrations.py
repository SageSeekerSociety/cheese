"""集成、凭据与准入那一块 —— 专治**静默降级**。

这一块里的每一行都对应一种「坏了但没人会发现」的状态：GitHub App 少一个权限、
用户的 OAuth 过期、投递发不出去、计量不落账。它们平时不出错、不报警，只是让产品
一点一点变得不能用。

**能查的和不能查的分开给**。能查的直接出数；不能查的进 `unavailable`，带一句
「要先加什么埋点」。绝不放一张印着 0 的卡片 —— 0 是「查过了、确实没有」，
「查不了」是另一件事（见 `AdminKpiCard`：空串画破折号，只有真 0 才画 0）。

**三条容易读错的口径**：

1. `deliveries.sent_at IS NULL` 是**存量积压**，不是失败率 —— 这张表每发一条通知
   就插一行，绝大多数下一刻就 `sent_at` 非空了。而且它把两种失败叠在一起（没有
   渠道收下 vs 渠道收了但批量标记未送出），行上分不开。
2. OAuth 的 `token_expires` 是**写死的过期时刻**，不是「刚刚用失败了」。一个还没
   到期也可能已经被服务端吊销了 —— 真正的失败信号在调用点的异常里，那一侧没有计数。
3. passkey 覆盖率的分母是**全部未删的真人账号**，不是活跃账号（`user` 没有
   `last_login`，今天没有「活跃」的判据）——也**不含 agent**：agent 不登录、
   不能被发密钥，算进分母就是一个管理员永远补不上的缺口。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.delivery.ledger import MAX_ATTEMPTS
from app.domain.delivery.models import Delivery
from app.domain.identity.models import AgentBinding
from app.domain.oauth.models import UserOAuthConnection
from app.domain.passkey.models import PasskeyCredential
from app.domain.user.models import User


class IntegrationsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def snapshot(self) -> dict[str, Any]:
        now = datetime.now(UTC)
        return {
            "oauth": await self.oauth_health(now=now),
            "passkey": await self.passkey_coverage(),
            "delivery": await self.delivery_backlog(now=now),
            "unavailable": self._unavailable(),
        }

    # ---- OAuth 凭据 --------------------------------------------------------

    async def oauth_health(self, *, now: datetime) -> dict[str, Any]:
        """过期的、快过期的、以及**根本没有 refresh_token** 的连接各多少。

        「没有 refresh_token」这一档单独列，是因为它和「过期」不是一件事：过期的还
        能刷，没有 refresh 的到期那天就**永远**接不上了，而账号看起来仍然连着。
        """
        total = await self._count(UserOAuthConnection)
        expired = await self._count(
            UserOAuthConnection, UserOAuthConnection.token_expires < now
        )
        expiring_soon = await self._count(
            UserOAuthConnection,
            UserOAuthConnection.token_expires >= now,
            UserOAuthConnection.token_expires < now + timedelta(days=7),
        )
        no_refresh = await self._count(
            UserOAuthConnection,
            or_(
                UserOAuthConnection.refresh_token.is_(None),
                UserOAuthConnection.refresh_token == "",
            ),
        )
        return {
            "total": total,
            "expired": expired,
            "expiring_7d": expiring_soon,
            "no_refresh_token": no_refresh,
            "note_key": "integrations.oauthNote",
        }

    # ---- passkey / 2FA 覆盖率 ----------------------------------------------

    async def passkey_coverage(self) -> dict[str, Any]:
        """有 passkey 的账号占比。分母是**全部未删的真人账号**，不是活跃账号。

        **agent 不进分母**：agent 没有密码、不登录、也不能被发一把密钥。把它们
        算进分母会让覆盖率永远低一截，而管理员没有任何动作能把那个缺口补上。判据
        和 people 块的真人/agent 拆分是同一份（`agent_bindings`），不在这里另写
        一种「什么算 agent」。

        TOTP 的开关住在 Redis（`login_security.py` 的前缀键），**不在库里** ——
        所以「2FA 覆盖率」今天只能按 passkey 数，页面上写的是「passkey 覆盖率」，
        不写「2FA 覆盖率」。要两个都报得先把 TOTP 的启用状态落到库。
        """
        agent = exists().where(AgentBinding.user_id == User.id)
        users = int(
            (
                await self._session.execute(
                    select(func.count(User.id)).where(User.deleted_at.is_(None), ~agent)
                )
            ).scalar_one()
            or 0
        )
        excluded_agents = int(
            (
                await self._session.execute(
                    select(func.count(User.id)).where(User.deleted_at.is_(None), agent)
                )
            ).scalar_one()
            or 0
        )
        with_passkey = int(
            (
                await self._session.execute(
                    select(func.count(func.distinct(PasskeyCredential.user_id)))
                )
            ).scalar_one()
            or 0
        )
        return {
            "accounts": users,
            "with_passkey": with_passkey,
            "coverage": (with_passkey / users) if users else None,
            "agent_accounts_excluded": excluded_agents,
            "note_key": "integrations.passkeyNote",
        }

    # ---- 投递账本 ----------------------------------------------------------

    async def delivery_backlog(self, *, now: datetime) -> dict[str, Any]:
        """没送出去的投递、以及**死信**（试满 `MAX_ATTEMPTS` 仍没送出去）。

        两档必须分开：前者补发扫描还会再试，后者按设计已放弃 —— 合成一个「未送达」
        会让「有救的」和「没救的」看起来一样，而管理员的动作完全不同。
        """
        unsent = await self._count(
            Delivery,
            Delivery.sent_at.is_(None),
            Delivery.attempts < MAX_ATTEMPTS,
        )
        dead = await self._count(
            Delivery,
            Delivery.sent_at.is_(None),
            Delivery.attempts >= MAX_ATTEMPTS,
        )
        oldest = (
            await self._session.execute(
                select(func.min(Delivery.recorded_at)).where(Delivery.sent_at.is_(None))
            )
        ).scalar_one()
        return {
            "unsent": unsent,
            "dead_letters": dead,
            "oldest_unsent_at": oldest.isoformat() if oldest else None,
            "max_attempts": MAX_ATTEMPTS,
            "note_key": "integrations.deliveryNote",
        }

    # ---- 今天算不出来的那些 -------------------------------------------------

    @staticmethod
    def _unavailable() -> list[dict[str, str]]:
        return [
            {
                "name": "github_app_permission_gaps",
                "reason_key": "integrations.unavailable.githubPerms",
                "needs": (
                    "遍历 project_git_installations 调 granted_permissions() "
                    "并落库（结果目前只活在进程内存 10 分钟）"
                ),
            },
            {
                "name": "github_app_mint_failure_rate",
                "reason_key": "integrations.unavailable.githubMint",
                "needs": "在 GitHubAppTokens._mint 失败处计数（现在只写日志）",
            },
            {
                "name": "login_lockout_stock_and_rate",
                "reason_key": "integrations.unavailable.lockout",
                "needs": (
                    "登录限流/锁定事件落库"
                    "（login_security 的限流器是 Redis 键，没有历史）"
                ),
            },
            {
                "name": "metering_post_freeze",
                "reason_key": "integrations.unavailable.metering",
                "needs": (
                    "计量代理落账的心跳或对账落库"
                    "（admission 传输失败是放行的，日志之外无痕迹）"
                ),
            },
        ]

    async def _count(self, model: Any, *where: Any) -> int:
        stmt = select(func.count()).select_from(model)
        if where:
            stmt = stmt.where(*where)
        return int((await self._session.execute(stmt)).scalar_one() or 0)
