"""订阅的纯逻辑：JWT claims、额度窗口映射、刷新错误分类、轮询节流。

这一层不碰数据库（`tests/unit` 的纪律）。OAuth 客户端用 `httpx.MockTransport`
打桩 —— 端点、表单字段、状态码语义都照 cc-switch v3.20.4 钉住（真实 OpenAI
凭据的实测清单在 PR 描述里，这里钉的是「代码按规格书写」这件事本身）。
"""

import base64
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.domain.subscription.openai_codex import (
    OpenAICodexOAuth,
    SubscriptionTokenInvalid,
    SubscriptionUnreachable,
    decode_jwt_payload,
    extract_account_claims,
    window_tier_name,
)
from app.domain.subscription.services import _poll_throttled


def _jwt(payload: dict) -> str:
    """拼一个形状正确的 JWT（不签名 —— 解析侧本来就不验签，见
    `decode_jwt_payload` 的 docstring）。"""

    def segment(obj: dict) -> str:
        raw = json.dumps(obj).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return f"{segment({'alg': 'RS256', 'typ': 'JWT'})}.{segment(payload)}.sig"


# --- JWT claims ---------------------------------------------------------------


def test_jwt_payload_round_trips():
    token = _jwt({"sub": "user-1", "email": "a@example.com"})
    assert decode_jwt_payload(token)["sub"] == "user-1"


def test_jwt_payload_rejects_malformed_tokens():
    assert decode_jwt_payload("not-a-jwt") == {}
    assert decode_jwt_payload("a.!!!.c") == {}
    assert decode_jwt_payload("a.e30.c") == {}  # "{}" 不是带 claims 的对象


def test_account_claims_prefers_the_top_level_account_id():
    token = _jwt(
        {
            "sub": "user-1",
            "email": "a@example.com",
            "chatgpt_account_id": "acct-top",
            "https://api.openai.com/auth": {"chatgpt_account_id": "acct-nested"},
        }
    )
    claims = extract_account_claims(token)
    assert claims["chatgpt_account_id"] == "acct-top"
    assert claims["email"] == "a@example.com"
    assert claims["sub"] == "user-1"


def test_account_claims_falls_back_to_the_namespaced_claim():
    token = _jwt(
        {
            "sub": "user-1",
            "https://api.openai.com/auth": {"chatgpt_account_id": "acct-nested"},
        }
    )
    assert extract_account_claims(token)["chatgpt_account_id"] == "acct-nested"


def test_account_claims_refuses_a_token_without_subject():
    """没有稳定身份的账号不许入库（cc-switch 同规）—— 它会让「同一身份
    校验」在重新授权时无从谈起。"""
    token = _jwt({"chatgpt_account_id": "acct-1"})
    assert extract_account_claims(token) == {}


# --- 额度窗口映射 ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("seconds", "name"),
    [
        (18000, "five_hour"),
        (604800, "seven_day"),
        (2592000, "thirty_day"),
        (7200, "2_hour"),
        (172800, "2_day"),
    ],
)
def test_window_seconds_map_to_tier_names(seconds, name):
    assert window_tier_name(seconds) == name


# --- device flow 与刷新的传输语义（MockTransport 打桩 OpenAI） ------------------


def _oauth(handler) -> OpenAICodexOAuth:
    return OpenAICodexOAuth(transport=httpx.MockTransport(handler))


async def test_start_device_flow_posts_client_id_and_parses_answer():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "device_auth_id": "daid-1",
                "user_code": "ABCD-EFGH",
                "interval": 5,
                "expires_in": 900,
            },
        )

    started = await _oauth(handler).start_device_flow()
    assert started.user_code == "ABCD-EFGH"
    assert started.interval == 5
    assert seen[0].url.path == "/api/accounts/deviceauth/usercode"
    body = json.loads(seen[0].content)
    assert body == {"client_id": "app_EMoamEEZ73f0CkXaXp7hrann"}


@pytest.mark.parametrize("status", [403, 404])
async def test_poll_while_waiting_is_pending(status):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": "authorization_pending"})

    result = await _oauth(handler).poll_device_flow("daid", "code")
    assert result.state == "pending"


async def test_poll_gone_is_expired():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(410, json={})

    result = await _oauth(handler).poll_device_flow("daid", "code")
    assert result.state == "expired"


async def test_poll_success_carries_the_server_side_code_verifier():
    """PKCE verifier 由 OpenAI 服务端在轮询成功时返回，不在本地生成 ——
    钉住这个形状，免得有人照标准 PKCE 把它「修正」成本地生成。"""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/deviceauth/token"):
            return httpx.Response(
                200,
                json={"authorization_code": "code-1", "code_verifier": "ver-1"},
            )
        return httpx.Response(
            200,
            json={
                "access_token": "at-1",
                "refresh_token": "rt-1",
                "id_token": _jwt({"sub": "u", "chatgpt_account_id": "acct"}),
                "expires_in": 3600,
            },
        )

    oauth = _oauth(handler)
    result = await oauth.poll_device_flow("daid", "code")
    assert result.state == "complete"
    assert result.code_verifier == "ver-1"

    tokens = await oauth.exchange_code(result.authorization_code, result.code_verifier)
    assert tokens.access_token == "at-1"
    form = seen[1].content.decode()
    assert "grant_type=authorization_code" in form
    assert "code_verifier=ver-1" in form
    assert "redirect_uri=https%3A%2F%2Fauth.openai.com%2Fdeviceauth%2Fcallback" in form


async def test_refresh_posts_the_codex_form():
    def handler(request: httpx.Request) -> httpx.Response:
        form = request.content.decode()
        assert "grant_type=refresh_token" in form
        assert "refresh_token=rt-old" in form
        assert "scope=openid+profile+email" in form
        return httpx.Response(
            200, json={"access_token": "at-new", "refresh_token": "rt-new"}
        )

    tokens = await _oauth(handler).refresh("rt-old")
    assert tokens.access_token == "at-new"
    assert tokens.refresh_token == "rt-new"


@pytest.mark.parametrize("status", [401, 403])
async def test_refresh_auth_failure_means_reauth(status):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": "nope"})

    with pytest.raises(SubscriptionTokenInvalid):
        await _oauth(handler).refresh("rt-old")


@pytest.mark.parametrize(
    "code",
    ["refresh_token_expired", "refresh_token_reused", "refresh_token_invalidated"],
)
async def test_refresh_dead_error_codes_mean_reauth(code):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"code": code}})

    with pytest.raises(SubscriptionTokenInvalid):
        await _oauth(handler).refresh("rt-old")


async def test_refresh_server_error_is_connectivity_not_reauth():
    """5xx 是「过会儿再试」，不是「重新授权」—— 判错方向会把一条好端端
    的订阅在一次上游抖动时打进 reauth_required。"""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={})

    with pytest.raises(SubscriptionUnreachable):
        await _oauth(handler).refresh("rt-old")


async def test_refresh_network_error_is_connectivity():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(SubscriptionUnreachable):
        await _oauth(handler).refresh("rt-old")


# --- 额度读数 ----------------------------------------------------------------


async def test_fetch_quota_parses_both_windows():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer at-1"
        assert request.headers["chatgpt-account-id"] == "acct-1"
        assert request.url.path == "/backend-api/wham/usage"
        return httpx.Response(
            200,
            json={
                "rate_limit": {
                    "primary_window": {
                        "used_percent": 42,
                        "limit_window_seconds": 18000,
                        "reset_at": 1790000000,
                    },
                    "secondary_window": {
                        "used_percent": 7.5,
                        "limit_window_seconds": 604800,
                        "reset_at": 1790500000,
                    },
                }
            },
        )

    snapshot = await _oauth(handler).fetch_quota("at-1", "acct-1")
    assert [t.name for t in snapshot.tiers] == ["five_hour", "seven_day"]
    assert snapshot.tiers[0].utilization == 42
    assert snapshot.tiers[0].resets_at is not None


async def test_fetch_quota_auth_failure_means_reauth():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={})

    with pytest.raises(SubscriptionTokenInvalid):
        await _oauth(handler).fetch_quota("at-1", "acct-1")


# --- 轮询节流 ----------------------------------------------------------------


def test_poll_throttle_window():
    now = datetime.now(UTC)
    assert not _poll_throttled(None, now)  # 第一次轮询不节流
    assert _poll_throttled(now - timedelta(seconds=1), now)
    assert not _poll_throttled(now - timedelta(seconds=3), now)
