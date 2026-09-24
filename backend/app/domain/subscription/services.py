"""订阅的服务层：device flow 状态机、token 生命周期、推进网关、审计、脱敏。

`SubscriptionService(db, oauth, admin)`：`oauth` 是 OpenAI 那一侧的客户端（测试缝
是 `OpenAICodexOAuth(transport=…)`），`admin` 是网关管理客户端（None = 这个部署
没配管理凭据，订阅凭据可以入库但挂不上模型 —— 路由把它翻成 503）。写 OpenAI 与
写网关的连接性失败都原样抛（`SubscriptionUnreachable` / `GatewayUnreachable` /
`GatewayRefused`），由路由翻 503/502（照 `admin_models._answered` 的翻译表）。

几条贯穿全文件的约定：

- **凭据绝不进响应与审计**。DTO（`_dto`）一个 token 字段都没有；审计快照
  （`_audit_snapshot`）不含 token、也不含 `account_email`（PII 只存表里那一处，
  审计 detail 是第二处可读的地方）。token 值经 `_record` 的 `secrets` 参数过
  `_scrub`，错误消息里若带出来一律抹成 `***`。
- **改状态的路径都先拿行锁**（`SELECT … FOR UPDATE`）。手动刷新、后台循环、撤销、
  轮询完成在多副本下并发到达时，行锁把它们串行化 —— 它替代的是 cc-switch 的
  进程内 mutex，数据库是唯一写入者。
- **审计先于异常落地**。`_record` flush 后独立 commit（gateway_models 同规）：
  路由的 `get_db` 在异常时回滚会话，若不在抛错前先 commit，「刷新出的新
  refresh_token 已落库」这类状态会跟着回滚 —— 而旧的 refresh_token 可能已被
  上游轮换作废，丢掉新的等于把这条订阅弄丢。
- **自动动作的审计 actor 是 `"system"`**。`gateway_admin_audit` 此前只记人动作；
  后台循环的自动刷新失败也要留痕（页面上「为什么变成刷新失败」要有人答得上），
  这是本模块引入的新约定：手动路径 actor 是管理员 handle，自动路径是 `system`。
- **解密失败降级不抛**（oauth/services.py 的 `_decrypt_stored_token` 模式）：
  密钥轮换后旧密文解不开，记日志、按 `reauth_required` 路径处理 —— 把「凭据
  读不出来」变成页面上一个可读的状态，而不是一次 500。
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.crypto import Purpose, decrypt, encrypt
from app.core.errors import BadRequestError, ConflictError, NotFoundError
from app.domain.agent import gateway_catalog
from app.domain.agent.gateway import LlmGateway
from app.domain.agent.gateway_admin import (
    GatewayAdmin,
    GatewayAdminError,
    GatewayUnreachable,
)
from app.domain.agent.models import GatewayAdminAudit
from app.domain.subscription.models import LlmSubscription
from app.domain.subscription.openai_codex import (
    OpenAICodexOAuth,
    QuotaSnapshot,
    SubscriptionOAuthError,
    SubscriptionTokenInvalid,
    SubscriptionUnreachable,
    extract_account_claims,
)
from app.domain.subscription.repositories import LlmSubscriptionRepository

logger = logging.getLogger(__name__)

#: 轮询节流：距上次轮询不足这个秒数直接答 pending，不打扰 OpenAI（上游对高频
#: 轮询会翻脸，而前端的轮询间隔本来就比它宽）。
_POLL_THROTTLE_S = 2.0

#: 可发起手动刷新的状态。pending 没有凭据可刷，reauth_required 的 refresh_token
#: 已被判死（再刷只是再打一次脸），终态行不在服务。
_REFRESHABLE = ("active", "refresh_failed")

#: 定向重授权允许从哪里出发。旧行必须是「还有凭据或曾经有过凭据」的非终态。
_REAUTHABLE = ("active", "refresh_failed", "reauth_required")


class SubscriptionService:
    def __init__(
        self,
        db: AsyncSession,
        oauth: OpenAICodexOAuth | None = None,
        admin: GatewayAdmin | None = None,
        *,
        gateway: LlmGateway | None = None,
    ) -> None:
        self._db = db
        self._oauth = oauth or OpenAICodexOAuth()
        self._admin = admin
        self._gateway = gateway
        self._repo = LlmSubscriptionRepository(db)

    # ------------------------------------------------------------------
    # device flow：start / poll / cancel
    # ------------------------------------------------------------------
    async def start_flow(
        self,
        *,
        handle: str,
        provider: str,
        label: str | None,
        target_id: uuid.UUID | None,
        upstream_model: str | None = None,
    ) -> dict:
        """开一次导入（或定向重授权）。

        「一座一订阅」在这里兑现：同一个 provider 同时至多一条非终态行。定向
        重授权时旧行**在本事务内置 superseded** —— 部分唯一索引不许新旧两条
        非终态行并存，而旧行的状态（refresh 判死、或管理员明确要换号）决定
        了「留着它继续服务」已经不是选项；流程若最终没完成，重新导入即可。
        非定向开启时若已有一条活跃订阅，拒绝并说明两条出路（先移除，或从它
        发起重新授权）——静默顶掉一条还在干活的订阅不是按钮该有的语义。
        """
        try:
            started = await self._oauth.start_device_flow()
        except SubscriptionOAuthError as exc:
            await self._record(
                handle=handle,
                action="subscription.start",
                target=provider,
                result="failed",
                detail=str(exc),
            )
            raise

        if target_id is not None:
            target = await self._repo.get_locked(target_id)
            if target is None or target.provider != provider:
                raise NotFoundError("要重新授权的订阅不存在")
            if target.status not in _REAUTHABLE:
                raise BadRequestError("这条订阅当前不在可重新授权的状态")
            # 重授权换的是凭据，不是配置：请求没带显式上游时继承旧行的选择，
            # 否则一次换号会把网关上的模型静默打回部署默认。
            if upstream_model is None:
                upstream_model = target.upstream_model
            target.status = "superseded"
        else:
            live = await self._repo.live_for_provider(provider)
            if live is not None and live.status != "pending":
                raise ConflictError(
                    "这个来源已有一条订阅：先移除它，或从它发起重新授权"
                )
            for row in await self._repo.pendings_for_provider(provider):
                row.status = "superseded"
        # 部分唯一索引在**同一事务内**也算数，而一次 flush 里 INSERT 先于
        # UPDATE —— 先把旧行的终态单独落下去，再插新的 pending 行，否则新行
        # 落库那一刻旧行在库里还是非终态，直接撞 `uq_..._live_provider`。
        await self._db.flush()

        now = _utcnow()
        sub = self._repo.add(
            LlmSubscription(
                provider=provider,
                label=(label or "")[:200],
                status="pending",
                linked_model_name=settings.subscription_linked_model_name,
                upstream_model=_normalize_upstream(upstream_model),
                flow_device_auth_id=started.device_auth_id,
                flow_user_code=started.user_code,
                flow_expires_at=now + timedelta(seconds=started.expires_in),
                flow_target_id=target_id,
                created_by_handle=(handle or "")[:64],
            )
        )
        try:
            await self._db.flush()
        except IntegrityError:
            # 两个并发导入都通过了上面的 live 检查，第二个在唯一索引上撞墙 ——
            # 这是「已有一条进行中的流程」，答 409，不是 500。
            await self._db.rollback()
            raise ConflictError(
                "这个来源已有一条进行中的授权流程：完成或取消它之后再开新的"
            ) from None
        await self._record(
            handle=handle,
            action="subscription.start",
            target=str(sub.id),
            result="ok",
            after={
                "provider": provider,
                "label": sub.label,
                "upstream_model": sub.upstream_model,
                "reauth_target": str(target_id) if target_id else None,
            },
        )
        return {
            "flow_id": str(sub.id),
            "user_code": started.user_code,
            "verification_uri": settings.openai_device_verification_uri,
            "expires_in": started.expires_in,
            "interval": started.interval,
        }

    async def poll_flow(self, *, handle: str, flow_id: uuid.UUID) -> dict:
        """前端轮询一次。三态：pending / complete（带订阅 DTO）/ expired。

        完成那一步最重：换 token、解身份、定向重授权校验同号、加密落库、推进
        网关，全在一把行锁里做完。任何一步失败，已落库的状态先经 `_record`
        commit 再抛 —— 尤其换到手的 token，丢了没法向人要第二次。
        """
        sub = await self._repo.get_locked(flow_id)
        if sub is None or sub.status != "pending":
            raise NotFoundError("这个授权流程不存在或已结束")
        now = _utcnow()
        if sub.flow_expires_at is not None and sub.flow_expires_at <= now:
            sub.status = "flow_expired"
            _clear_flow(sub)
            return {"state": "expired"}
        if _poll_throttled(sub.flow_last_poll_at, now):
            return {"state": "pending"}
        sub.flow_last_poll_at = now

        assert sub.flow_device_auth_id is not None and sub.flow_user_code is not None
        result = await self._oauth.poll_device_flow(
            sub.flow_device_auth_id, sub.flow_user_code
        )
        if result.state == "pending":
            return {"state": "pending"}
        if result.state == "expired":
            sub.status = "flow_expired"
            _clear_flow(sub)
            return {"state": "expired"}

        assert result.authorization_code is not None
        assert result.code_verifier is not None
        tokens = await self._oauth.exchange_code(
            result.authorization_code, result.code_verifier
        )
        secrets = {
            value
            for value in (
                tokens.access_token,
                tokens.refresh_token,
                tokens.id_token,
            )
            if value
        }
        claims = extract_account_claims(tokens.id_token or "")
        account_id = claims.get("chatgpt_account_id")
        if not tokens.refresh_token or not claims or not account_id:
            # cc-switch 同规：没有 refresh_token、或确认不了稳定身份与账号，
            # 这条登录不算数 —— 存下来只会养出一条刷不了的订阅。
            sub.status = "superseded"
            _clear_flow(sub)
            await self._record(
                handle=handle,
                action="subscription.complete",
                target=str(sub.id),
                result="failed",
                detail="授权结果里确认不了账号身份，这次导入不算数",
                secrets=secrets,
            )
            raise BadRequestError("授权结果里确认不了账号身份，请重新导入")

        if sub.flow_target_id is not None:
            target = await self._repo.get(sub.flow_target_id)
            if target is not None and (
                target.id_token_subject != claims["sub"]
                or target.chatgpt_account_id != account_id
            ):
                sub.status = "superseded"
                _clear_flow(sub)
                await self._record(
                    handle=handle,
                    action="subscription.complete",
                    target=str(sub.id),
                    result="failed",
                    detail="检测到不同的账号",
                    secrets=secrets,
                )
                raise BadRequestError(
                    "检测到不同的账号：这次登录的 ChatGPT 账号与要重新授权的"
                    "订阅不是同一个，请用原账号重新授权"
                )

        sub.account_email = claims.get("email")
        sub.chatgpt_account_id = account_id
        sub.id_token_subject = claims["sub"]
        sub.access_token_enc = seal_subscription_token(
            sub.id, "access_token_enc", tokens.access_token
        )
        sub.refresh_token_enc = seal_subscription_token(
            sub.id, "refresh_token_enc", tokens.refresh_token
        )
        sub.id_token_enc = (
            seal_subscription_token(sub.id, "id_token_enc", tokens.id_token)
            if tokens.id_token
            else None
        )
        sub.token_expires_at = (
            now + timedelta(seconds=tokens.expires_in) if tokens.expires_in else None
        )
        sub.status = "active"
        sub.last_refresh_at = now
        sub.last_refresh_error = None
        _clear_flow(sub)

        try:
            await self._push_to_gateway(sub, tokens.access_token)
        except GatewayAdminError as exc:
            # 凭据已在库（这是这次授权最值钱的东西），网关这一下没推上去可以
            # 之后用手动刷新补 —— 状态照 active 落，错误写进可读字段。
            # 网关错误原文可能回显 token，落库前过和审计一样的脱敏。
            from app.domain.agent import gateway_models

            sub.last_refresh_error = gateway_models._scrub(
                f"凭据已入库，但推进网关失败：{exc}", secrets
            )
            await self._record(
                handle=handle,
                action="subscription.complete",
                target=str(sub.id),
                result="failed",
                detail=f"推进网关失败：{exc}",
                after=_audit_snapshot(sub),
                secrets=secrets,
            )
            raise
        await self._record(
            handle=handle,
            action="subscription.complete",
            target=str(sub.id),
            result="ok",
            after=_audit_snapshot(sub),
            secrets=secrets,
        )
        return {"state": "complete", "subscription": self._dto(sub)}

    async def cancel_flow(self, *, handle: str, flow_id: uuid.UUID) -> dict:
        sub = await self._repo.get_locked(flow_id)
        if sub is None or sub.status != "pending":
            raise NotFoundError("这个授权流程不存在或已结束")
        sub.status = "superseded"
        _clear_flow(sub)
        await self._record(
            handle=handle,
            action="subscription.cancel",
            target=str(sub.id),
            result="ok",
        )
        return {"cancelled": True}

    # ------------------------------------------------------------------
    # 读
    # ------------------------------------------------------------------
    async def list(self) -> dict:
        rows = await self._repo.list_all()
        return {"items": [self._dto(row) for row in rows]}

    def _dto(self, sub: LlmSubscription) -> dict:
        """对外形状（§3.3）。token / 密文字段一个字母都不出现 —— 与
        `gateway_admin.py` 顶部那条铁律同级。"""
        return {
            "id": str(sub.id),
            "provider": sub.provider,
            "label": sub.label,
            "status": sub.status,
            "account_email": sub.account_email,
            "chatgpt_account_id": sub.chatgpt_account_id,
            "token_expires_at": _iso(sub.token_expires_at),
            "last_refresh_at": _iso(sub.last_refresh_at),
            "last_refresh_error": sub.last_refresh_error,
            "linked_model_name": sub.linked_model_name,
            "upstream_model": sub.upstream_model,
            "quota": _quota_dto(sub),
            "created_by_handle": sub.created_by_handle,
            "created_at": _iso(sub.created_at),
        }

    async def status_by_linked_model(self) -> dict[str, dict]:
        """模型列表 overlay 的门：linked_model_name → 订阅状态子集。

        终态行不 overlay（一条 revoked 的订阅不该再给模型贴「订阅」徽章）。
        service → service 的门（GatewayModelsService 调这里），不碰对方仓储。
        """
        rows = await self._repo.live_linked()
        return {
            row.linked_model_name: {
                "id": str(row.id),
                "status": row.status,
                "account_email": row.account_email,
                "upstream_model": row.upstream_model,
                "quota": _quota_dto(row),
                # 详情抽屉的订阅块要这两样（凭据过期时间、上次刷新失败的原话）；
                # token / 密文字段照旧一个字母都不出现。
                "token_expires_at": _iso(row.token_expires_at),
                "last_refresh_error": row.last_refresh_error,
            }
            for row in rows
            if row.linked_model_name
        }

    # ------------------------------------------------------------------
    # 刷新（手动 + 后台循环共用）
    # ------------------------------------------------------------------
    async def refresh_now(self, *, handle: str, subscription_id: uuid.UUID) -> dict:
        sub = await self._repo.get_locked(subscription_id)
        if sub is None:
            raise NotFoundError("这条订阅不存在")
        if sub.status == "reauth_required":
            raise BadRequestError("这条订阅的凭据已被判失效，请重新授权")
        if sub.status not in _REFRESHABLE:
            raise BadRequestError("这条订阅当前不在可刷新的状态")
        return await self._refresh_locked(sub, actor=handle)

    async def refresh_due(self) -> int:
        """后台循环的一圈：把临近过期（或已过期）的非终态订阅逐条刷新。

        返回成功刷新的条数。单条的失败已由 `_refresh_locked` 落库并落审计，
        这里不让它打断整圈 —— 一条订阅坏了不该耽误其它订阅。
        """
        now = _utcnow()
        margin = timedelta(seconds=settings.subscription_refresh_margin_s)
        rows = await self._repo.due_for_refresh(_REFRESHABLE, now + margin)
        done = 0
        for row in rows:
            locked = await self._repo.get_locked(row.id)
            if (
                locked is None
                or locked.status not in _REFRESHABLE
                or (
                    locked.token_expires_at is not None
                    and locked.token_expires_at > now + margin
                )
            ):
                # 锁等待期间别人可能已经刷过或把它移出可刷状态 —— 以锁后读到的为准。
                continue
            try:
                await self._refresh_locked(locked, actor="system")
                done += 1
            except (SubscriptionOAuthError, GatewayAdminError):
                # 状态与审计都已由 _refresh_locked 落库，这一圈继续。
                continue
        return done

    async def _refresh_locked(self, sub: LlmSubscription, *, actor: str) -> dict:
        """行锁内的一次刷新 + 推进网关。手动与后台共用。

        返回 `{status, token_expires_at, last_refresh_error}`。三类结局：

        - 凭据被判死（`SubscriptionTokenInvalid`，含解密失败）：置
          `reauth_required`，落 failed 审计，**正常返回**（这是一个状态，
          不是一次事故；页面靠状态灯告诉人）。
        - 连接性失败：记 `last_refresh_error`；token 未过期维持 `active`
          （继续服务），已过期置 `refresh_failed`。落 failed 审计后**原样抛**
          —— 手动路径要让点的人看到 503，后台路径由循环吞掉。
        - 成功：换密文、记 `last_refresh_at`、清错误、置 `active`，把新
          access_token 推进网关；推进失败同连接性失败处理（凭据已到手，
          网关那一下可以补推）。
        """
        now = _utcnow()
        was_expired = sub.token_expires_at is None or sub.token_expires_at <= now
        stored_refresh = _decrypt(sub, "refresh_token_enc")
        if not stored_refresh:
            sub.status = "reauth_required"
            sub.last_refresh_error = "凭据无法解密（密钥可能已轮换），需要重新授权"
            await self._record(
                handle=actor,
                action="subscription.refresh",
                target=str(sub.id),
                result="failed",
                detail=sub.last_refresh_error,
                after=_audit_snapshot(sub),
            )
            return _refresh_state(sub)

        try:
            tokens = await self._oauth.refresh(stored_refresh)
        except SubscriptionTokenInvalid as exc:
            sub.status = "reauth_required"
            sub.last_refresh_error = str(exc)
            await self._record(
                handle=actor,
                action="subscription.refresh",
                target=str(sub.id),
                result="failed",
                detail=str(exc),
                after=_audit_snapshot(sub),
            )
            return _refresh_state(sub)
        except SubscriptionUnreachable as exc:
            sub.last_refresh_error = str(exc)
            if was_expired:
                sub.status = "refresh_failed"
            await self._record(
                handle=actor,
                action="subscription.refresh",
                target=str(sub.id),
                result="failed",
                detail=str(exc),
                after=_audit_snapshot(sub),
            )
            raise

        secrets = {
            value
            for value in (
                tokens.access_token,
                tokens.refresh_token,
                tokens.id_token,
            )
            if value
        }
        sub.access_token_enc = seal_subscription_token(
            sub.id, "access_token_enc", tokens.access_token
        )
        if tokens.refresh_token:
            sub.refresh_token_enc = seal_subscription_token(
                sub.id, "refresh_token_enc", tokens.refresh_token
            )
        if tokens.id_token:
            sub.id_token_enc = seal_subscription_token(
                sub.id, "id_token_enc", tokens.id_token
            )
        if tokens.expires_in:
            sub.token_expires_at = now + timedelta(seconds=tokens.expires_in)
        sub.last_refresh_at = now
        sub.last_refresh_error = None
        sub.status = "active"
        try:
            await self._push_to_gateway(sub, tokens.access_token)
        except GatewayAdminError as exc:
            # 网关错误原文可能回显请求里的 token —— 落可读字段前过和审计一样的脱敏。
            from app.domain.agent import gateway_models

            sub.last_refresh_error = gateway_models._scrub(
                f"凭据已刷新，但推进网关失败：{exc}", secrets
            )
            if was_expired:
                sub.status = "refresh_failed"
            await self._record(
                handle=actor,
                action="subscription.refresh",
                target=str(sub.id),
                result="failed",
                detail=sub.last_refresh_error,
                after=_audit_snapshot(sub),
                secrets=secrets,
            )
            raise
        await self._record(
            handle=actor,
            action="subscription.refresh",
            target=str(sub.id),
            result="ok",
            after=_audit_snapshot(sub),
            secrets=secrets,
        )
        return _refresh_state(sub)

    # ------------------------------------------------------------------
    # 额度读数
    # ------------------------------------------------------------------
    async def fetch_quota(self, *, handle: str, subscription_id: uuid.UUID) -> dict:
        """按需查一次 wham/usage。

        缓存语义照 cc-switch：传输错误留旧值（有快照就回旧快照标
        `stale: true`，没有才 503）；认证错误清缓存（置 `reauth_required`、
        清快照，抛出去由路由翻 502「凭据已失效，需要重新授权」）。
        token 过期时先刷再查 —— 刷不动时同样走旧快照 / 重新授权两条路。
        """
        sub = await self._repo.get_locked(subscription_id)
        if sub is None:
            raise NotFoundError("这条订阅不存在")
        if sub.status == "reauth_required":
            raise BadRequestError("这条订阅的凭据已被判失效，请重新授权")
        if sub.status not in _REFRESHABLE:
            raise BadRequestError("这条订阅当前没有可用凭据")

        now = _utcnow()
        if sub.token_expires_at is None or sub.token_expires_at <= now:
            try:
                await self._refresh_locked(sub, actor=handle)
            except SubscriptionUnreachable:
                # 刷新路上断线不挡读数：下面优先试手里的 token，不行还有旧快照。
                pass
            except GatewayAdminError:
                # 推进网关失败不等于 token 没刷成 —— 继续读额度。
                pass

        if sub.status == "reauth_required":
            # _refresh_locked 刚写入的具体原因别用笼统文案盖掉。
            await self._mark_reauth(
                sub, sub.last_refresh_error or "凭据已失效，需要重新授权"
            )
            raise SubscriptionTokenInvalid("凭据已失效，需要重新授权")

        access = _decrypt(sub, "access_token_enc")
        if not access:
            await self._mark_reauth(sub, "凭据无法解密（密钥可能已轮换），需要重新授权")
            raise SubscriptionTokenInvalid("凭据已失效，需要重新授权")

        # 刷新试过之后 access_token 仍然过期，说明待会儿的 401 什么都不证明
        # （它本来就该 401），不能据此把凭据判死。
        access_known_stale = (
            sub.token_expires_at is None or sub.token_expires_at <= _utcnow()
        )
        try:
            snapshot = await self._oauth.fetch_quota(access, sub.chatgpt_account_id)
        except SubscriptionTokenInvalid as exc:
            if access_known_stale:
                if sub.quota_snapshot:
                    return {
                        "tiers": _quota_tiers(sub.quota_snapshot),
                        "queried_at": _iso(sub.quota_fetched_at),
                        "stale": True,
                    }
                raise SubscriptionUnreachable(
                    "访问令牌已过期且刷新暂不可用，请稍后重试"
                ) from exc
            await self._mark_reauth(sub, str(exc))
            raise
        except SubscriptionUnreachable:
            if sub.quota_snapshot:
                return {
                    "tiers": _quota_tiers(sub.quota_snapshot),
                    "queried_at": _iso(sub.quota_fetched_at),
                    "stale": True,
                }
            raise
        sub.quota_snapshot = _snapshot_dict(snapshot)
        sub.quota_fetched_at = _utcnow()
        await self._db.flush()
        return {
            "tiers": _quota_tiers(sub.quota_snapshot),
            "queried_at": _iso(sub.quota_fetched_at),
            "stale": False,
        }

    async def _mark_reauth(self, sub: LlmSubscription, error: str) -> None:
        """认证错误的「清缓存」一半：置 reauth_required、清快照、写可读错误。

        直接 commit：随后就要抛错，路由的回滚不该把这次状态转变吃掉。
        """
        sub.status = "reauth_required"
        sub.last_refresh_error = error
        sub.quota_snapshot = None
        sub.quota_fetched_at = None
        await self._db.flush()
        await self._db.commit()

    # ------------------------------------------------------------------
    # 移除
    # ------------------------------------------------------------------
    async def revoke(self, *, handle: str, subscription_id: uuid.UUID) -> dict:
        """移除一条订阅：置 revoked，并 best-effort 停用它挂在网关上的模型。

        断开订阅是第一诉求：网关那一下失败，订阅照常被移除（状态先经
        `_record` 落库），失败原因落审计并把网关原话抛给路由 —— 页面会
        显示错误，但下一次读列表时这条订阅已经是终态。
        """
        sub = await self._repo.get_locked(subscription_id)
        if sub is None:
            raise NotFoundError("这条订阅不存在")
        if sub.status == "revoked":
            raise BadRequestError("这条订阅已经移除")
        before = _audit_snapshot(sub)
        was_credential_source = sub.status in _REAUTHABLE
        sub.status = "revoked"
        _clear_flow(sub)

        gateway_error: GatewayAdminError | None = None
        if (
            self._admin is not None
            and sub.linked_model_name
            and was_credential_source
            # 只有被删行真是这个模型最后的凭据来源时才停用它 —— 重授权后删旧行，
            # 模型正由新的活跃行服务，block 会把在服模型当场断流。
            and not await self._repo.has_other_live_for_model(
                sub.linked_model_name, exclude_id=sub.id
            )
        ):
            try:
                models = await self._admin.models()
                found = next(
                    (m for m in models if m.name == sub.linked_model_name), None
                )
                if found is not None and not found.blocked:
                    await self._admin.set_blocked(found.model_id, True)
                    await self._after_gateway_write()
            except GatewayAdminError as exc:
                gateway_error = exc
        if gateway_error is not None:
            detail = f"订阅已移除，但停用网关模型失败：{gateway_error}"
            sub.last_refresh_error = detail
            await self._record(
                handle=handle,
                action="subscription.revoke",
                target=str(sub.id),
                result="failed",
                detail=detail,
                before=before,
                after=_audit_snapshot(sub),
            )
            raise gateway_error
        await self._record(
            handle=handle,
            action="subscription.revoke",
            target=str(sub.id),
            result="ok",
            before=before,
            after=_audit_snapshot(sub),
        )
        return {"revoked": True}

    # ------------------------------------------------------------------
    # 改上游模型
    # ------------------------------------------------------------------
    async def update_upstream_model(
        self,
        *,
        handle: str,
        subscription_id: uuid.UUID,
        upstream_model: str | None,
    ) -> dict:
        """改一条订阅的上游模型，并把解析值推进网关。``None`` = 清除显式选择、
        回落部署默认。

        订阅行是这条链接模型上游的**唯一权威**：每次刷新与每次本调用都以行上的
        值重断网关（`_push_to_gateway` 两条分支都带上游）——经通用模型编辑页改
        这条模型的上游，下一次刷新就会被这里的值盖回去。

        失败语义照 `revoke`：网关那一下失败，行上的选择照样落库（审计落
        failed、原因写进 `last_refresh_error`），把网关原话抛给路由；下一次
        刷新会拿行上的值补推。
        """
        sub = await self._repo.get_locked(subscription_id)
        if sub is None:
            raise NotFoundError("这条订阅不存在")
        if sub.status not in _REAUTHABLE:
            raise BadRequestError("这条订阅当前不在可改上游模型的状态")
        if upstream_model is not None and not upstream_model.strip():
            raise BadRequestError("上游模型标识不能为空")
        before = _audit_snapshot(sub)
        sub.upstream_model = _normalize_upstream(upstream_model)

        access_token = _decrypt(sub, "access_token_enc")
        if not access_token:
            # 凭据读不出来时不推网关，按凭据不可用的既有路径走：置
            # reauth_required（与 `_refresh_locked` 的解密失败分支同规）。
            # 选择已落库 —— 重授权继承它（start_flow 的定向分支），凭据
            # 恢复后的第一次推进会带上它。
            sub.status = "reauth_required"
            sub.last_refresh_error = "凭据无法解密（密钥可能已轮换），需要重新授权"
            await self._record(
                handle=handle,
                action="subscription.update_upstream",
                target=str(sub.id),
                result="failed",
                detail=sub.last_refresh_error,
                before=before,
                after=_audit_snapshot(sub),
            )
            return self._dto(sub)

        try:
            await self._push_to_gateway(sub, access_token)
        except GatewayAdminError as exc:
            from app.domain.agent import gateway_models

            detail = gateway_models._scrub(
                f"上游模型已改，但推进网关失败：{exc}", {access_token}
            )
            sub.last_refresh_error = detail
            await self._record(
                handle=handle,
                action="subscription.update_upstream",
                target=str(sub.id),
                result="failed",
                detail=detail,
                before=before,
                after=_audit_snapshot(sub),
                secrets={access_token},
            )
            raise
        await self._record(
            handle=handle,
            action="subscription.update_upstream",
            target=str(sub.id),
            result="ok",
            before=before,
            after=_audit_snapshot(sub),
            secrets={access_token},
        )
        return self._dto(sub)

    # ------------------------------------------------------------------
    # 网关那一半
    # ------------------------------------------------------------------
    async def _push_to_gateway(self, sub: LlmSubscription, access_token: str) -> None:
        """把这个订阅的 access_token 推进网关的运行时模型（存在即改，不在即建）。

        运行时模型落 LiteLLM 自己的库，即改即生效、零重启；计量 / 项目 key /
        max_budget 刹车因此零改动复用现有管道（估计价就是给刹车与读数用的，
        mimo 先例）。`extra_headers` 三件套是 ChatGPT codex 后端认账的门票
        （账号、originator、客户端版本 —— 版本按它门控模型可用性，所以走
        settings 热配）。没配管理凭据时导入**不能完成**：抛 503 语义，把补救
        路径（配置后手动刷新）写进原因里。
        """
        if self._admin is None:
            raise GatewayUnreachable(
                "未配置网关管理凭据（llm_gateway_admin_base / key）："
                "订阅凭据已入库，但挂不上网关模型；配好后用手动刷新完成挂载"
            )
        name = sub.linked_model_name or settings.subscription_linked_model_name
        sub.linked_model_name = name
        upstream = _effective_upstream(sub)
        headers = {
            "chatgpt-account-id": sub.chatgpt_account_id or "",
            "originator": settings.codex_originator,
            "version": settings.codex_client_version,
        }
        models = await self._admin.models()
        found = next((m for m in models if m.name == name), None)
        if found is not None:
            # 上游模型两条分支都要带：订阅行是它的唯一权威来源，只建模型时传，
            # 之后改上游就永远到不了网关。合并语义下 api_key / api_base /
            # extra_headers / prices 缺省即不动（gateway_admin.update_model）。
            await self._admin.update_model(
                model_id=found.model_id,
                upstream_model=upstream,
                api_key=access_token,
                extra_headers=headers,
            )
        else:
            await self._admin.add_model(
                name=name,
                upstream_model=upstream,
                api_base=f"{settings.chatgpt_backend_base.rstrip('/')}/codex",
                api_key=access_token,
                prices={
                    "input": settings.subscription_estimate_input_usd,
                    "output": settings.subscription_estimate_output_usd,
                },
                label="GPT · ChatGPT 订阅",
                selectable=True,
                capabilities={},
                extra_headers=headers,
            )
        await self._after_gateway_write()

    async def _after_gateway_write(self) -> None:
        """与 `GatewayModelsService._after_write` 同一件事：先失效缓存，再让
        选择器那份目录重读网关。"""
        from app.domain.agent import gateway_models

        gateway_models.reset_cache()
        await gateway_catalog.refresh(self._catalog_gateway())

    def _catalog_gateway(self) -> LlmGateway | None:
        if self._gateway is not None:
            return self._gateway
        if not (settings.llm_gateway_admin_base and settings.llm_gateway_admin_key):
            return None
        return LlmGateway(
            settings.llm_gateway_admin_base, settings.llm_gateway_admin_key
        )

    # ------------------------------------------------------------------
    # 审计
    # ------------------------------------------------------------------
    async def _record(
        self,
        *,
        handle: str,
        action: str,
        target: str,
        result: str,
        detail: str | None = None,
        before: dict | None = None,
        after: dict | None = None,
        secrets: set[str] | None = None,
    ) -> None:
        """落一行审计并**独立 commit**（gateway_models 同规，理由见文件头）。

        脱敏复用 gateway_models 的三件套：`_redact` 按字段名递归剔凭据（字段
        名单已扩三 token），`_secrets_in` + `_scrub` 把错误消息里带出的同串
        值抹掉。`secrets` 是本流程里经手过的明文 token —— 它们绝不落库，但
        上游的错误消息有可能原样引用。
        """
        from app.domain.agent import gateway_models

        all_secrets = set(secrets or ())
        all_secrets |= gateway_models._secrets_in(after)
        all_secrets |= gateway_models._secrets_in(before)
        self._db.add(
            GatewayAdminAudit(
                actor_handle=(handle or "")[:64],
                target=(target or "")[:128],
                action=action[:32],
                result=result[:16],
                detail=gateway_models._scrub(detail, all_secrets),
                before=gateway_models._redact(before),
                after=gateway_models._redact(after),
            )
        )
        await self._db.flush()
        await self._db.commit()


# ----------------------------------------------------------------------
# 模块级小工具
# ----------------------------------------------------------------------
def _utcnow() -> datetime:
    return datetime.now(UTC)


def _normalize_upstream(value: str | None) -> str | None:
    """入库存储形：去空白，空串归一为 None（None = 跟随部署默认）。"""
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _effective_upstream(sub: LlmSubscription) -> str:
    """推进网关时用的解析值：行上的显式选择优先，缺省跟随部署默认（热配）。"""
    return sub.upstream_model or settings.subscription_upstream_model


def _poll_throttled(last_poll_at: datetime | None, now: datetime) -> bool:
    """距上次轮询不足 `_POLL_THROTTLE_S` 就答 pending，不打扰 OpenAI。

    纯函数，单独可测：上游对高频轮询会翻脸，而节流决策不该藏在状态机中间。
    """
    if last_poll_at is None:
        return False
    return (now - last_poll_at).total_seconds() < _POLL_THROTTLE_S


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _clear_flow(sub: LlmSubscription) -> None:
    """flow 终结（完成/过期/取消/撤销）后清掉进行态 —— 留着只会让一行
    已完成的数据看起来还像「正在授权」。"""
    sub.flow_device_auth_id = None
    sub.flow_user_code = None
    sub.flow_expires_at = None
    sub.flow_last_poll_at = None


def seal_subscription_token(sub_id: uuid.UUID, field: str, value: str) -> str:
    """加密一列凭据，绑定到这条订阅和这一列。"""
    return encrypt(
        Purpose.LLM_SUBSCRIPTION_TOKEN,
        value,
        bound_to=f"llm-subscription:{sub_id}:{field}",
    )


def open_subscription_token(sub: LlmSubscription, field: str) -> str:
    return decrypt(
        Purpose.LLM_SUBSCRIPTION_TOKEN,
        getattr(sub, field),
        bound_to=f"llm-subscription:{sub.id}:{field}",
    )


def _decrypt(sub: LlmSubscription, field: str) -> str | None:
    """解一列密文；解不开记日志、降级 None（oauth/services.py 同规）。

    绝不抛给调用方、绝不返回密文：写它的密钥已不在 DATA_ENCRYPTION_KEY 里时
    解不开是**预期内**的事，调用方按「凭据不可用 → reauth_required」的路径走。
    """
    if not getattr(sub, field):
        return None
    try:
        return open_subscription_token(sub, field)
    except Exception:
        logger.exception(
            "llm subscription %s: stored token could not be decrypted "
            "(was its key removed from DATA_ENCRYPTION_KEY?) — treating the "
            "credential as unavailable",
            sub.id,
        )
        return None


def _audit_snapshot(sub: LlmSubscription) -> dict:
    """审计的 before/after 快照：非凭据、非 PII（email 只存表里那一处）。"""
    return {
        "provider": sub.provider,
        "label": sub.label,
        "status": sub.status,
        "linked_model_name": sub.linked_model_name,
        "upstream_model": sub.upstream_model,
        "chatgpt_account_id": sub.chatgpt_account_id,
        "token_expires_at": _iso(sub.token_expires_at),
    }


def _refresh_state(sub: LlmSubscription) -> dict:
    return {
        "status": sub.status,
        "token_expires_at": _iso(sub.token_expires_at),
        "last_refresh_error": sub.last_refresh_error,
    }


def _snapshot_dict(snapshot: QuotaSnapshot) -> dict:
    return {
        "tiers": [
            {
                "name": tier.name,
                "utilization": tier.utilization,
                "resets_at": tier.resets_at,
            }
            for tier in snapshot.tiers
        ]
    }


def _quota_tiers(snapshot: dict | None) -> list[dict]:
    if not isinstance(snapshot, dict):
        return []
    tiers = snapshot.get("tiers")
    return tiers if isinstance(tiers, list) else []


def _quota_dto(sub: LlmSubscription) -> dict | None:
    if not sub.quota_snapshot:
        return None
    return {
        "tiers": _quota_tiers(sub.quota_snapshot),
        "fetched_at": _iso(sub.quota_fetched_at),
    }
