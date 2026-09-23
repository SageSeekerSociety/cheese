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

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.crypto import decrypt_text, encrypt_text
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
            target.status = "superseded"
        else:
            live = await self._repo.live_for_provider(provider)
            if live is not None and live.status != "pending":
                raise ConflictError(
                    "这个来源已有一条订阅：先移除它，或从它发起重新授权"
                )
            for row in await self._repo.pendings_for_provider(provider):
                row.status = "superseded"

        now = _utcnow()
        sub = self._repo.add(
            LlmSubscription(
                provider=provider,
                label=(label or "")[:200],
                status="pending",
                linked_model_name=settings.subscription_linked_model_name,
                flow_device_auth_id=started.device_auth_id,
                flow_user_code=started.user_code,
                flow_expires_at=now + timedelta(seconds=started.expires_in),
                flow_target_id=target_id,
                created_by_handle=(handle or "")[:64],
            )
        )
        await self._db.flush()
        await self._record(
            handle=handle,
            action="subscription.start",
            target=str(sub.id),
            result="ok",
            after={
                "provider": provider,
                "label": sub.label,
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
        sub.access_token_enc = encrypt_text(tokens.access_token)
        sub.refresh_token_enc = encrypt_text(tokens.refresh_token)
        sub.id_token_enc = encrypt_text(tokens.id_token) if tokens.id_token else None
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
            sub.last_refresh_error = f"凭据已入库，但推进网关失败：{exc}"
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
                "quota": _quota_dto(row),
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
            if locked is None or locked.status not in _REFRESHABLE:
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
        was_expired = (
            sub.token_expires_at is None or sub.token_expires_at <= now
        )
        stored_refresh = _decrypt(sub.refresh_token_enc, sub_id=sub.id)
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
        sub.access_token_enc = encrypt_text(tokens.access_token)
        if tokens.refresh_token:
            sub.refresh_token_enc = encrypt_text(tokens.refresh_token)
        if tokens.id_token:
            sub.id_token_enc = encrypt_text(tokens.id_token)
        if tokens.expires_in:
            sub.token_expires_at = now + timedelta(seconds=tokens.expires_in)
        sub.last_refresh_at = now
        sub.last_refresh_error = None
        sub.status = "active"
        try:
            await self._push_to_gateway(sub, tokens.access_token)
        except GatewayAdminError as exc:
            sub.last_refresh_error = f"凭据已刷新，但推进网关失败：{exc}"
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
            await self._mark_reauth(sub, "凭据已失效，需要重新授权")
            raise SubscriptionTokenInvalid("凭据已失效，需要重新授权")

        access = _decrypt(sub.access_token_enc, sub_id=sub.id)
        if not access:
            await self._mark_reauth(sub, "凭据无法解密（密钥可能已轮换），需要重新授权")
            raise SubscriptionTokenInvalid("凭据已失效，需要重新授权")

        try:
            snapshot = await self._oauth.fetch_quota(access, sub.chatgpt_account_id)
        except SubscriptionTokenInvalid as exc:
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
        sub.status = "revoked"
        _clear_flow(sub)

        gateway_error: GatewayAdminError | None = None
        if self._admin is not None and sub.linked_model_name:
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
        headers = {
            "chatgpt-account-id": sub.chatgpt_account_id or "",
            "originator": settings.codex_originator,
            "version": settings.codex_client_version,
        }
        models = await self._admin.models()
        found = next((m for m in models if m.name == name), None)
        if found is not None:
            await self._admin.update_model(
                model_id=found.model_id,
                api_key=access_token,
                extra_headers=headers,
            )
        else:
            await self._admin.add_model(
                name=name,
                upstream_model=settings.subscription_upstream_model,
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


def _decrypt(stored: str | None, *, sub_id: uuid.UUID) -> str | None:
    """解一列密文；解不开记日志、降级 None（oauth/services.py 同规）。

    绝不抛给调用方、绝不返回密文：轮换后的旧密文解不开是**预期内**的事，
    调用方按「凭据不可用 → reauth_required」的路径走。
    """
    if not stored:
        return None
    try:
        return decrypt_text(stored)
    except Exception:
        logger.exception(
            "llm subscription %s: stored token could not be decrypted "
            "(key rotated, or a legacy plaintext row?) — treating the "
            "credential as unavailable",
            sub_id,
        )
        return None


def _audit_snapshot(sub: LlmSubscription) -> dict:
    """审计的 before/after 快照：非凭据、非 PII（email 只存表里那一处）。"""
    return {
        "provider": sub.provider,
        "label": sub.label,
        "status": sub.status,
        "linked_model_name": sub.linked_model_name,
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
