"""OpenAI Codex 订阅的 OAuth / 上游客户端（device flow + refresh + 额度）。

端点与语义照 cc-switch v3.20.4（`src-tauri/src/proxy/providers/codex_oauth_auth.rs`
与 `services/subscription.rs`），不是凭记忆写的；**没有真实 OpenAI 凭据实测过**，
未验证点都在 PR 描述的实测清单里。所有常量（client_id、端点、originator、客户端
版本）从 settings 读 —— ChatGPT 按客户端版本门控模型可用性，硬编码就是把一次
上游策略变化变成一次发版。

这里只做 HTTP 与解析，不写库（状态机在 `services.py`）。两类失败语义分开：

- `SubscriptionTokenInvalid`：凭据被判死（401/403 或三个 refresh_token_* 错误码）。
  含义是「要重新授权」，服务层据此把订阅置成 `reauth_required`。
- `SubscriptionUnreachable`：连接性问题或上游 5xx / 意料之外的响应。含义是
  「过会儿再试有意义」，服务层据此保留现状、只记 `last_refresh_error`。

JWT 的解析**不验签**（见 `decode_jwt_payload` 的 docstring）：claims 只作元数据
（账号 id、email、身份校验），不作鉴权依据。
"""

import base64
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx

from app.core.config import settings

# OAuth 四个端点的单请求超时。认证请求卡住时应尽快失败，而不是挂着阻塞轮询
# （cc-switch 同样给这条路单独设了 30s，而不是用大模型流式响应那份长超时）。
_OAUTH_TIMEOUT = 30.0
# 额度读数的超时（照 cc-switch query_codex_quota 的 15s）。
_QUOTA_TIMEOUT = 15.0

# 判死 refresh_token 的三个上游错误码（照 cc-switch 的错误分类）。
_REFRESH_DEAD_CODES = {
    "refresh_token_expired",
    "refresh_token_reused",
    "refresh_token_invalidated",
}

# 打 ChatGPT 后端（额度与模型调用都走它）时认这个 UA；OAuth 端点不挑 UA。
_CHATGPT_UA = "codex-cli"


class SubscriptionOAuthError(Exception):
    """订阅的 OAuth / 上游调用失败的基类。``message`` 是给人看的原因。"""


class SubscriptionTokenInvalid(SubscriptionOAuthError):
    """凭据被上游判死：要重新授权，重试这个 token 没有意义。"""


class SubscriptionUnreachable(SubscriptionOAuthError):
    """连不上、超时、5xx、或上游返回了认不出的东西：过会儿再试。"""


@dataclass(frozen=True)
class DeviceFlowStart:
    """`start_device_flow` 的答案：给人看的 user_code + 轮询要用的句柄。"""

    device_auth_id: str
    user_code: str
    expires_in: int
    interval: int


@dataclass(frozen=True)
class PollResult:
    """`poll_device_flow` 的答案。``state`` 三值：

    - ``pending``：人还没授权完，继续等。
    - ``expired``：这个 flow 死了，要重新开一个。
    - ``complete``：授权成功，带上服务端返回的授权码与 **code_verifier**（PKCE
      verifier 由 OpenAI 服务端在轮询成功时返回，不在本地生成 —— cc-switch
      `codex_oauth_auth.rs` 的 DevicePollSuccess）。
    """

    state: str
    authorization_code: str | None = None
    code_verifier: str | None = None


@dataclass(frozen=True)
class TokenSet:
    """一次 code 交换或 refresh 的答案。``refresh_token`` 缺失表示沿用旧的。"""

    access_token: str
    refresh_token: str | None
    id_token: str | None
    expires_in: int | None


@dataclass(frozen=True)
class QuotaTier:
    """一个速率窗口的额度读数：``utilization`` 是 0-100 的已用百分比。"""

    name: str
    utilization: float
    resets_at: str | None


@dataclass(frozen=True)
class QuotaSnapshot:
    tiers: list[QuotaTier] = field(default_factory=list)


def decode_jwt_payload(token: str) -> dict:
    """解一个 JWT 的 payload（base64url），**不验签**。

    不验签是刻意的：这个 token 由 OpenAI 的 token 端点经 TLS 直接返回给我们，
    传输链已经认证过它；这里读 claims 只作**元数据**（chatgpt_account_id、email、
    sub），从不作鉴权依据 —— 鉴权永远发生在上游拿着 access_token 的那一侧。
    解不出来（不是三段、base64 坏、JSON 坏）返回空 dict，由调用方决定拒绝。
    """
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    segment = parts[1]
    # base64url 缺 padding 是合法的，补回再解。
    segment += "=" * (-len(segment) % 4)
    try:
        raw = base64.urlsafe_b64decode(segment.encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def extract_account_claims(id_token: str) -> dict:
    """从 id_token 里取账号三元组：``chatgpt_account_id`` / ``email`` / ``sub``。

    ``chatgpt_account_id`` 认两个位置：顶层 claim，以及
    ``https://api.openai.com/auth`` 命名空间（cc-switch 两个都认，因为上游在
    不同阶段两个位置都发过）。``sub`` 缺失则返回空 dict —— 没有一个稳定身份
    的账号不许入库（cc-switch 同规：不能确认身份时整条登录不算数）。
    """
    claims = decode_jwt_payload(id_token)
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject:
        return {}
    account_id = claims.get("chatgpt_account_id")
    if not isinstance(account_id, str) or not account_id:
        auth_claim = claims.get("https://api.openai.com/auth")
        if isinstance(auth_claim, dict):
            nested = auth_claim.get("chatgpt_account_id")
            account_id = nested if isinstance(nested, str) and nested else None
        else:
            account_id = None
    email = claims.get("email")
    return {
        "sub": subject,
        "chatgpt_account_id": account_id,
        "email": email if isinstance(email, str) and email else None,
    }


def window_tier_name(seconds: int) -> str:
    """窗口秒数 → tier 名（照 cc-switch `subscription.rs` 的映射）。

    18000 → ``five_hour``，604800 → ``seven_day``，2592000 → ``thirty_day``；
    其它按小时/天动态拼（``N_hour`` / ``N_day``）。
    """
    if seconds == 18000:
        return "five_hour"
    if seconds == 604800:
        return "seven_day"
    if seconds == 2592000:
        return "thirty_day"
    hours = seconds // 3600
    if hours >= 24:
        return f"{hours // 24}_day"
    return f"{hours}_hour"


def _short_body(response: httpx.Response) -> str:
    text = (response.text or "").strip()
    return " ".join(text.split())[:200]


class OpenAICodexOAuth:
    """OpenAI 的 device-flow OAuth 客户端 + ChatGPT 后端额度读取。

    ``transport`` 是测试缝（httpx.MockTransport），None = 真网络。常量全部
    在调用时从 settings 读：版本号之类要热配的，不该被 import 时的一次快照
    钉死（本模块从不在 import 期读配置）。
    """

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    def _client(self, timeout: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=timeout, transport=self._transport)

    @staticmethod
    def _oauth_url(path: str) -> str:
        return f"{settings.openai_oauth_base.rstrip('/')}{path}"

    @staticmethod
    def _refresh_error_code(body: str) -> str | None:
        """错误体里的 refresh 错误码（照 cc-switch `extract_refresh_error_code`）：
        ``error`` 可以是对象（取其 ``code``）也可以是字符串，顶层 ``code`` 兜底。"""
        try:
            value = json.loads(body)
        except ValueError:
            return None
        if not isinstance(value, dict):
            return None
        error = value.get("error")
        code: object = None
        if isinstance(error, dict):
            code = error.get("code")
        elif isinstance(error, str):
            code = error
        if not isinstance(code, str):
            fallback = value.get("code")
            code = fallback if isinstance(fallback, str) else None
        return code.lower() if isinstance(code, str) else None

    async def start_device_flow(self) -> DeviceFlowStart:
        """POST …/deviceauth/usercode，拿到给人看的 ``user_code``。"""
        try:
            async with self._client(_OAUTH_TIMEOUT) as client:
                response = await client.post(
                    self._oauth_url("/api/accounts/deviceauth/usercode"),
                    json={"client_id": settings.openai_codex_client_id},
                )
        except httpx.HTTPError as exc:
            raise SubscriptionUnreachable(
                f"OpenAI 授权服务不可达：{_short(exc)}"
            ) from exc
        if response.status_code >= 500:
            raise SubscriptionUnreachable(
                f"OpenAI 授权服务暂时不可用（HTTP {response.status_code}）"
            )
        if response.status_code != 200:
            raise SubscriptionUnreachable(
                f"OpenAI 拒绝了授权请求（HTTP {response.status_code}）："
                f"{_short_body(response)}"
            )
        payload = _json_object(response)
        device_auth_id = payload.get("device_auth_id")
        user_code = payload.get("user_code")
        if not isinstance(device_auth_id, str) or not isinstance(user_code, str):
            raise SubscriptionUnreachable("OpenAI 返回的授权会话缺字段")
        interval = payload.get("interval")
        expires_in = payload.get("expires_in")
        return DeviceFlowStart(
            device_auth_id=device_auth_id,
            user_code=user_code,
            # OpenAI 文档约定的默认 15 分钟（cc-switch 同值）。
            expires_in=expires_in if isinstance(expires_in, int) else 900,
            interval=interval if isinstance(interval, int) and interval > 0 else 5,
        )

    async def poll_device_flow(self, device_auth_id: str, user_code: str) -> PollResult:
        """POST …/deviceauth/token。403/404 = 还在等，410 = flow 死了。"""
        try:
            async with self._client(_OAUTH_TIMEOUT) as client:
                response = await client.post(
                    self._oauth_url("/api/accounts/deviceauth/token"),
                    json={"device_auth_id": device_auth_id, "user_code": user_code},
                )
        except httpx.HTTPError as exc:
            raise SubscriptionUnreachable(
                f"OpenAI 授权服务不可达：{_short(exc)}"
            ) from exc
        if response.status_code in (403, 404):
            return PollResult(state="pending")
        if response.status_code == 410:
            return PollResult(state="expired")
        if response.status_code != 200:
            raise SubscriptionUnreachable(
                f"轮询授权状态失败（HTTP {response.status_code}）："
                f"{_short_body(response)}"
            )
        payload = _json_object(response)
        code = payload.get("authorization_code")
        verifier = payload.get("code_verifier")
        if not isinstance(code, str) or not isinstance(verifier, str):
            raise SubscriptionUnreachable("OpenAI 返回的授权结果缺字段")
        return PollResult(
            state="complete", authorization_code=code, code_verifier=verifier
        )

    async def exchange_code(self, code: str, code_verifier: str) -> TokenSet:
        """授权码换 token 三件套。redirect_uri 是 OpenAI 服务端约定的固定值。"""
        return await self._token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": "https://auth.openai.com/deviceauth/callback",
                "client_id": settings.openai_codex_client_id,
                "code_verifier": code_verifier,
            }
        )

    async def refresh(self, refresh_token: str) -> TokenSet:
        """刷新 access_token。

        失败分类照 cc-switch：401/403、或错误体带三个 ``refresh_token_*`` 错误码
        之一 → `SubscriptionTokenInvalid`（要重新授权）；网络错误与 5xx →
        `SubscriptionUnreachable`（连接性，过会儿再试）；其它非 2xx 同样按
        连接性处理 —— 一个认不出的 4xx 不该把订阅误判成「要重新授权」。
        """
        return await self._token_request(
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": settings.openai_codex_client_id,
                "scope": "openid profile email",
            },
            invalid_token=refresh_token,
        )

    async def _token_request(
        self, form: dict[str, str], *, invalid_token: str | None = None
    ) -> TokenSet:
        try:
            async with self._client(_OAUTH_TIMEOUT) as client:
                response = await client.post(
                    self._oauth_url("/oauth/token"),
                    data=form,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
        except httpx.HTTPError as exc:
            raise SubscriptionUnreachable(
                f"OpenAI 授权服务不可达：{_short(exc)}"
            ) from exc
        if response.status_code != 200:
            code = self._refresh_error_code(response.text)
            if (
                response.status_code in (401, 403) or code in _REFRESH_DEAD_CODES
            ) and invalid_token is not None:
                raise SubscriptionTokenInvalid(
                    "订阅凭据已被 OpenAI 判为失效，需要重新授权"
                )
            if response.status_code >= 500:
                raise SubscriptionUnreachable(
                    f"OpenAI 授权服务暂时不可用（HTTP {response.status_code}）"
                )
            raise SubscriptionUnreachable(
                f"OpenAI 拒绝了这次凭据操作（HTTP {response.status_code}）："
                f"{_short_body(response)}"
            )
        payload = _json_object(response)
        access_token = payload.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise SubscriptionUnreachable("OpenAI 的 token 响应缺 access_token")
        refresh_token = payload.get("refresh_token")
        id_token = payload.get("id_token")
        expires_in = payload.get("expires_in")
        return TokenSet(
            access_token=access_token,
            refresh_token=refresh_token if isinstance(refresh_token, str) else None,
            id_token=id_token if isinstance(id_token, str) else None,
            expires_in=expires_in if isinstance(expires_in, int) else None,
        )

    async def fetch_quota(
        self, access_token: str, chatgpt_account_id: str | None
    ) -> QuotaSnapshot:
        """GET {chatgpt_base}/wham/usage → 逐窗口的额度读数。

        解析 ``rate_limit.primary_window / secondary_window`` 的
        ``{used_percent, limit_window_seconds, reset_at}``。401/403 →
        `SubscriptionTokenInvalid`（cc-switch 的「认证错误清缓存」语义由服务层
        兑现）；传输类错误 → `SubscriptionUnreachable`（服务层据此回旧快照）。
        """
        base = settings.chatgpt_backend_base.rstrip("/")
        headers = {
            "Authorization": f"Bearer {access_token}",
            "User-Agent": _CHATGPT_UA,
            "Accept": "application/json",
        }
        if chatgpt_account_id:
            headers["ChatGPT-Account-Id"] = chatgpt_account_id
        try:
            async with self._client(_QUOTA_TIMEOUT) as client:
                response = await client.get(f"{base}/wham/usage", headers=headers)
        except httpx.HTTPError as exc:
            raise SubscriptionUnreachable(f"额度接口不可达：{_short(exc)}") from exc
        if response.status_code in (401, 403):
            raise SubscriptionTokenInvalid("订阅凭据已被 OpenAI 判为失效，需要重新授权")
        if response.status_code != 200:
            raise SubscriptionUnreachable(
                f"额度接口返回 HTTP {response.status_code}：{_short_body(response)}"
            )
        payload = _json_object(response)
        rate_limit = payload.get("rate_limit")
        tiers: list[QuotaTier] = []
        if isinstance(rate_limit, dict):
            for key in ("primary_window", "secondary_window"):
                window = rate_limit.get(key)
                if not isinstance(window, dict):
                    continue
                used = window.get("used_percent")
                if not isinstance(used, int | float) or isinstance(used, bool):
                    continue
                seconds = window.get("limit_window_seconds")
                resets_at = window.get("reset_at")
                tiers.append(
                    QuotaTier(
                        name=(
                            window_tier_name(seconds)
                            if isinstance(seconds, int)
                            else "unknown"
                        ),
                        utilization=float(used),
                        resets_at=(
                            _unix_to_iso(resets_at)
                            if isinstance(resets_at, int)
                            else None
                        ),
                    )
                )
        return QuotaSnapshot(tiers=tiers)

    async def list_models(
        self, access_token: str, chatgpt_account_id: str | None
    ) -> list[dict]:
        """GET {chatgpt_base}/codex/models → 这个账号当下可用的模型清单。

        codex 后端按账号门控模型可用性（``supported_in_api`` 与
        ``visibility``），所以「能上架哪几个」只有它能答 —— 写死一份清单在平
        台侧，账号不支持时就是轮次上的 400（gpt-5.2-codex 那次）。这里只回
        ``visibility == "list"`` 且 ``supported_in_api`` 的项；返回
        ``{slug, display_name, description, priority}``，按 priority 排序。

        ``client_version`` 是必带的 query 参数（缺了 400），且后端按它门控
        清单内容 —— 走 settings 热配，与 extra_headers 里的版本同源。
        401/403 → `SubscriptionTokenInvalid`；传输类 → `SubscriptionUnreachable`。
        """
        base = settings.chatgpt_backend_base.rstrip("/")
        headers = {
            "Authorization": f"Bearer {access_token}",
            "User-Agent": _CHATGPT_UA,
            "Accept": "application/json",
            "originator": settings.codex_originator,
            "version": settings.codex_client_version,
        }
        if chatgpt_account_id:
            headers["ChatGPT-Account-Id"] = chatgpt_account_id
        try:
            async with self._client(_QUOTA_TIMEOUT) as client:
                response = await client.get(
                    f"{base}/codex/models",
                    headers=headers,
                    params={"client_version": settings.codex_client_version},
                )
        except httpx.HTTPError as exc:
            raise SubscriptionUnreachable(f"模型清单接口不可达：{_short(exc)}") from exc
        if response.status_code in (401, 403):
            raise SubscriptionTokenInvalid("订阅凭据已被 OpenAI 判为失效，需要重新授权")
        if response.status_code != 200:
            raise SubscriptionUnreachable(
                f"模型清单接口返回 HTTP {response.status_code}：{_short_body(response)}"
            )
        payload = _json_object(response)
        raw = payload.get("models")
        if not isinstance(raw, list):
            raise SubscriptionUnreachable("模型清单响应里没有 models 列表")
        items: list[dict] = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            slug = entry.get("slug")
            if not isinstance(slug, str) or not slug:
                continue
            if entry.get("visibility") != "list":
                continue
            if entry.get("supported_in_api") is not True:
                continue
            items.append(
                {
                    "slug": slug,
                    "display_name": entry.get("display_name") or slug,
                    "description": entry.get("description") or "",
                    "priority": entry.get("priority")
                    if isinstance(entry.get("priority"), int)
                    else 999,
                }
            )
        items.sort(key=lambda item: item["priority"])
        return items


def _unix_to_iso(timestamp: int) -> str | None:
    try:
        return datetime.fromtimestamp(timestamp, UTC).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _json_object(response: httpx.Response) -> dict:
    try:
        payload = response.json()
    except ValueError as exc:
        raise SubscriptionUnreachable("OpenAI 返回了非 JSON 响应") from exc
    if not isinstance(payload, dict):
        raise SubscriptionUnreachable("OpenAI 返回了意料之外的响应")
    return payload


def _short(exc: Exception) -> str:
    return " ".join(str(exc).split())[:200] or type(exc).__name__
