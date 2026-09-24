"""后台「订阅导入」的全链路：device flow、刷新、额度、撤销。

跑在真的 HTTP 栈 + 真的库上（`client` / `as_admin`，形状照
`test_admin_models.py`）。两个外部面都打桩：OpenAI 那一侧经
`get_openai_oauth` 依赖换成 `OpenAICodexOAuth(transport=MockTransport)`，网关
经 `get_gateway_admin` 换成 `GatewayAdmin(transport=MockTransport)` —— 端点与
状态码语义照 cc-switch v3.20.4 钉住；没有真实 OpenAI 凭据实测过的点都在 PR
描述的清单里，这里钉的是「代码按规格书写」这件事本身。

钉住的三条要害：

1. **凭据绝不露面**。库里是密文（可解密还原）；审计的
   before/after/detail 与全部 API 响应里一个明文字母都没有。
2. **每一次状态转变都落审计行**（含失败），actor 是操作者 handle。
3. **失败语义不混**：连接性 503、上游判死凭据 502、不存在 404、状态不对
   400、已有一条活订阅 409 —— 每一码是不同的一句话。
"""

import asyncio
import base64
import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select

from app.api.routes.admin_models import get_gateway_admin
from app.api.routes.admin_subscriptions import get_openai_oauth
from app.core.config import settings
from app.domain.agent import gateway_catalog, gateway_models
from app.domain.agent.gateway_admin import GatewayAdmin
from app.domain.agent.models import GatewayAdminAudit
from app.domain.subscription.models import LlmSubscription
from app.domain.subscription.openai_codex import OpenAICodexOAuth
from app.domain.subscription.services import (
    open_subscription_token,
    seal_subscription_token,
)
from tests.integration.conftest import session_auth_headers

#: 放进管理员名单的那个 handle（与 test_admin_models.py 同一做法）。
ADMIN = "subs-admin"
STRANGER = "subs-stranger"

#: 这次测试里流动的「凭据」。断言「它们一个字母都不出现在审计与响应里」就靠
#: 这些一眼认得出的值。
ACCESS = "at-secret-test-value"
REFRESH = "rt-secret-test-value"


def _jwt(payload: dict) -> str:
    """拼一个形状正确的 JWT（不签名 —— 解析侧本来就不验签）。"""

    def segment(obj: dict) -> str:
        raw = json.dumps(obj).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return f"{segment({'alg': 'RS256', 'typ': 'JWT'})}.{segment(payload)}.sig"


ID_TOKEN = _jwt(
    {
        "sub": "sub-test-1",
        "email": "admin@example.com",
        "chatgpt_account_id": "acct-test-1",
    }
)


@pytest.fixture
def as_admin(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(settings, "platform_admin_handles", [ADMIN])
    return ADMIN


def _openai_transport(calls: list[httpx.Request], state: dict) -> httpx.MockTransport:
    """一台够订阅导入用的假 OpenAI：device flow 四步 + 刷新 + 额度。

    行为由 ``state`` 驱动，用例可以在两次请求之间改它（比如先 pending 后
    complete、先成功后 401）。没预料到的路径一律 404 —— 走错了会以 502 立刻
    炸，而不是静默拿到空数据。
    """

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        path = request.url.path
        if path == "/api/accounts/deviceauth/usercode":
            return httpx.Response(
                200,
                json={
                    "device_auth_id": "daid-1",
                    "user_code": "ABCD-EFGH",
                    "interval": 5,
                    "expires_in": 900,
                },
            )
        if path == "/api/accounts/deviceauth/token":
            poll_state = state.get("poll", "pending")
            if poll_state == "pending":
                return httpx.Response(403, json={"error": "authorization_pending"})
            if poll_state == "expired":
                return httpx.Response(410, json={})
            return httpx.Response(
                200,
                json={"authorization_code": "code-1", "code_verifier": "ver-1"},
            )
        if path == "/oauth/token":
            token_state = state.get("token", "ok")
            if token_state == "invalid":
                return httpx.Response(401, json={"error": "invalid_grant"})
            if token_state == "down":
                return httpx.Response(500, json={})
            return httpx.Response(
                200,
                json={
                    "access_token": state.get("access_token", ACCESS),
                    "refresh_token": state.get("refresh_token", REFRESH),
                    "id_token": state.get("id_token", ID_TOKEN),
                    "expires_in": 3600,
                },
            )
        if path == "/backend-api/wham/usage":
            quota_state = state.get("quota", "ok")
            if quota_state == "invalid":
                return httpx.Response(401, json={})
            if quota_state == "down":
                raise httpx.ConnectError("connection refused")
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
                            "used_percent": 7,
                            "limit_window_seconds": 604800,
                            "reset_at": 1790500000,
                        },
                    }
                },
            )
        return httpx.Response(404, json={"detail": f"stub has no {path}"})

    return httpx.MockTransport(handler)


def _gateway_transport(calls: list[httpx.Request], state: dict) -> httpx.MockTransport:
    """一台够订阅挂载用的假网关：模型清单 + 新建 + 编辑 + 停用。

    ``state["models"]`` 是 ``/model/info`` 会报的模型表 —— 空表时订阅导入走
    「新建」，放上 linked 模型则走「编辑」（刷新推进就是这条路）。
    """

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        path = request.url.path
        if path == "/health/readiness":
            return httpx.Response(200, json={"status": "healthy"})
        if path == "/model/info":
            return httpx.Response(200, json={"data": state.get("models", [])})
        if path == "/user/daily/activity/aggregated":
            return httpx.Response(200, json={"results": [], "metadata": {}})
        if path == "/model/new":
            return httpx.Response(200, json={"model_id": "mid-sub-1"})
        if (
            request.method == "PATCH"
            and path.startswith("/model/")
            and path.endswith("/update")
        ):
            return httpx.Response(200, json={})
        if path in ("/model/delete", "/model/block", "/model/unblock"):
            return httpx.Response(200, json={})
        return httpx.Response(404, json={"detail": f"stub has no {path}"})

    return httpx.MockTransport(handler)


@pytest.fixture
def rig(client, monkeypatch) -> SimpleNamespace:
    """OpenAI 与网关都打桩 + 被拦下的目录刷新。

    两个 ``state`` dict 是用例的旋钮：改它即改假上游的下一次回答。
    """
    gateway_models.reset_cache()
    openai_calls: list[httpx.Request] = []
    gateway_calls: list[httpx.Request] = []
    openai_state: dict = {}
    gateway_state: dict = {}
    client.app.dependency_overrides[get_openai_oauth] = lambda: OpenAICodexOAuth(
        transport=_openai_transport(openai_calls, openai_state)
    )
    client.app.dependency_overrides[get_gateway_admin] = lambda: GatewayAdmin(
        "http://gw",
        "mk",
        transport=_gateway_transport(gateway_calls, gateway_state),
    )
    refreshed: list[object] = []

    async def _refresh(g):
        refreshed.append(g)
        return True

    monkeypatch.setattr(gateway_catalog, "refresh", _refresh)
    yield SimpleNamespace(
        openai_calls=openai_calls,
        gateway_calls=gateway_calls,
        openai_state=openai_state,
        gateway_state=gateway_state,
        refreshed=refreshed,
    )
    client.app.dependency_overrides.pop(get_openai_oauth, None)
    client.app.dependency_overrides.pop(get_gateway_admin, None)
    gateway_models.reset_cache()


# --- 直接读库 / 改库的小工具 -----------------------------------------------
# （写库那半边是 `client` 那个库，见 test_admin_stats 的警告）


def _subs_rows(client) -> list[LlmSubscription]:
    async def _go():
        async with client.test_factory() as session:
            # detach：会话关掉之后字段还要读。
            rows = (await session.scalars(select(LlmSubscription))).all()
            return [
                SimpleNamespace(
                    **{c.name: getattr(r, c.name) for c in r.__table__.columns}
                )
                for r in rows
            ]

    return asyncio.run(_go())


def _audit_rows(client) -> list[SimpleNamespace]:
    async def _go():
        async with client.test_factory() as session:
            rows = (await session.scalars(select(GatewayAdminAudit))).all()
            return [
                SimpleNamespace(
                    action=r.action,
                    result=r.result,
                    actor=r.actor_handle,
                    target=r.target,
                    detail=r.detail,
                    before=r.before,
                    after=r.after,
                )
                for r in rows
            ]

    return asyncio.run(_go())


def _audit_blob(rows: list[SimpleNamespace]) -> str:
    """全部审计素材打成一个串：断言「凭据一个字母都不在」时只查这一处。"""
    return json.dumps(
        [
            {
                "action": r.action,
                "detail": r.detail,
                "before": r.before,
                "after": r.after,
            }
            for r in rows
        ],
        default=str,
    )


def _seed_active(
    client,
    *,
    linked: str | None = "gpt-codex-subscription",
    refresh_token: str = REFRESH,
    subject: str = "sub-test-1",
    account: str = "acct-test-1",
    snapshot: dict | None = None,
    upstream: str | None = None,
) -> uuid.UUID:
    """直接落一条 `active` 订阅（刷新/额度/撤销这些用例的起点不是导入本身）。"""

    async def _go() -> uuid.UUID:
        async with client.test_factory() as session:
            row_id = uuid.uuid4()
            row = LlmSubscription(
                id=row_id,
                provider="openai_codex",
                label="团队的 ChatGPT",
                status="active",
                account_email="admin@example.com",
                chatgpt_account_id=account,
                id_token_subject=subject,
                access_token_enc=seal_subscription_token(
                    row_id, "access_token_enc", ACCESS
                ),
                refresh_token_enc=seal_subscription_token(
                    row_id, "refresh_token_enc", refresh_token
                ),
                id_token_enc=seal_subscription_token(row_id, "id_token_enc", ID_TOKEN),
                token_expires_at=datetime.now(UTC) + timedelta(hours=1),
                linked_model_name=linked,
                upstream_model=upstream,
                quota_snapshot=snapshot,
                quota_fetched_at=(
                    datetime.now(UTC) - timedelta(minutes=5) if snapshot else None
                ),
                created_by_handle=ADMIN,
            )
            session.add(row)
            await session.commit()
            return row.id

    return asyncio.run(_go())


def _unthrottle(client, flow_id: str) -> None:
    """把 flow 的上次轮询时间拨回节流窗口之外。

    服务层对 <2s 的连续轮询直接答 pending（保护上游）；用例里的两次轮询只隔
    几毫秒，不拨一下第二次永远打不到 OpenAI。
    """

    async def _go():
        async with client.test_factory() as session:
            row = await session.get(LlmSubscription, uuid.UUID(flow_id))
            row.flow_last_poll_at = datetime.now(UTC) - timedelta(seconds=5)
            await session.commit()

    asyncio.run(_go())


# --- 门 ---------------------------------------------------------------------


def test_a_stranger_gets_403_everywhere(client, as_admin, rig):
    some_id = uuid.uuid4()
    for method, path in [
        ("GET", "/admin/subscriptions"),
        ("POST", "/admin/subscriptions/device-flows"),
        ("POST", f"/admin/subscriptions/device-flows/{some_id}/poll"),
        ("POST", f"/admin/subscriptions/device-flows/{some_id}/cancel"),
        ("POST", f"/admin/subscriptions/{some_id}/refresh"),
        ("GET", f"/admin/subscriptions/{some_id}/quota"),
        ("PATCH", f"/admin/subscriptions/{some_id}/upstream-model"),
        ("DELETE", f"/admin/subscriptions/{some_id}"),
    ]:
        r = client.request(
            method, path, json={}, headers=session_auth_headers(STRANGER)
        )
        assert r.status_code == 403, (method, path, r.text)


def test_poll_after_upstream_410_marks_the_flow_expired(client, as_admin, rig):
    """上游宣告 device 会话作废（410）→ 流程落 `flow_expired`、答 expired。"""
    started = client.post(
        "/admin/subscriptions/device-flows",
        json={"provider": "openai_codex", "label": "团队的 ChatGPT"},
        headers=session_auth_headers(as_admin),
    )
    assert started.status_code == 200, started.text
    flow_id = started.json()["data"]["flow_id"]

    rig.openai_state["poll"] = "expired"
    r = client.post(
        f"/admin/subscriptions/device-flows/{flow_id}/poll",
        headers=session_auth_headers(as_admin),
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["state"] == "expired"
    (row,) = [s for s in _subs_rows(client) if str(s.id) == flow_id]
    assert row.status == "flow_expired"
    assert row.flow_device_auth_id is None


# --- device flow 全链 ---------------------------------------------------------


def test_import_full_chain(client, as_admin, rig):
    """start → poll(pending) → poll(complete)：一条订阅从「没有」到「挂上网关」。

    钉的是这条链上最值钱的三件事：凭据密文落库可还原、网关收到带三件套头的
    新建模型、审计两行且一个字明文都不沾。
    """
    started = client.post(
        "/admin/subscriptions/device-flows",
        json={"provider": "openai_codex", "label": "团队的 ChatGPT"},
        headers=session_auth_headers(as_admin),
    )
    assert started.status_code == 200, started.text
    body = started.json()["data"]
    flow_id = body["flow_id"]
    assert body["user_code"] == "ABCD-EFGH"
    assert body["verification_uri"] == "https://auth.openai.com/codex/device"

    # 第一次轮询：人还没授权完 → pending，上游只收到一次 deviceauth/token。
    first = client.post(
        f"/admin/subscriptions/device-flows/{flow_id}/poll",
        headers=session_auth_headers(as_admin),
    )
    assert first.status_code == 200, first.text
    assert first.json()["data"] == {"state": "pending"}

    # 人在授权页点完了。两次轮询只隔几毫秒，先把节流拨过去（见 _unthrottle）。
    rig.openai_state["poll"] = "complete"
    _unthrottle(client, flow_id)
    done = client.post(
        f"/admin/subscriptions/device-flows/{flow_id}/poll",
        headers=session_auth_headers(as_admin),
    )
    assert done.status_code == 200, done.text
    data = done.json()["data"]
    assert data["state"] == "complete"
    sub_dto = data["subscription"]
    assert sub_dto["status"] == "active"
    assert sub_dto["account_email"] == "admin@example.com"
    assert sub_dto["upstream_model"] is None  # 没显式选 = 跟随部署默认
    # DTO 脱敏：token / 密文字段一个字母都不出现。
    dto_blob = json.dumps(sub_dto)
    for leaked in (ACCESS, REFRESH, ID_TOKEN, "token_enc"):
        assert leaked not in dto_blob

    # 库里是密文，且能解密还原 —— 「密文」本身不该等于明文。
    rows = _subs_rows(client)
    assert len(rows) == 1
    row = rows[0]
    assert row.status == "active"
    assert row.access_token_enc != ACCESS
    assert row.flow_device_auth_id is None  # flow 进行态已清
    assert open_subscription_token(row, "access_token_enc") == ACCESS
    assert open_subscription_token(row, "refresh_token_enc") == REFRESH

    # 网关收到一次新建：api_key 是 access_token，extra_headers 三件套齐全。
    news = [c for c in rig.gateway_calls if c.url.path == "/model/new"]
    assert len(news) == 1
    sent = json.loads(news[0].content)
    params = sent["litellm_params"]
    # 没显式选：上游是部署默认（settings.subscription_upstream_model）。
    assert params["model"] == "openai/gpt-5.2-codex"
    assert params["api_key"] == ACCESS
    assert params["api_base"] == "https://chatgpt.com/backend-api/codex"
    headers = params["extra_headers"]
    assert headers["chatgpt-account-id"] == "acct-test-1"
    assert headers["originator"] == "codex_cli_rs"
    assert headers["version"]
    assert rig.refreshed, "写完应让选择器目录重读"

    # 审计两行：start 与 complete；素材里一个明文字母都没有。
    rows = _audit_rows(client)
    assert [(r.action, r.result) for r in rows] == [
        ("subscription.start", "ok"),
        ("subscription.complete", "ok"),
    ]
    assert all(r.actor == as_admin for r in rows)
    blob = _audit_blob(rows)
    for leaked in (ACCESS, REFRESH, ID_TOKEN):
        assert leaked not in blob


def test_a_second_live_subscription_is_a_conflict(client, as_admin, rig):
    """「一座一订阅」：已有一条活跃订阅时，非定向开启是 409，并给出两条出路。"""
    _seed_active(client)
    r = client.post(
        "/admin/subscriptions/device-flows",
        json={"provider": "openai_codex"},
        headers=session_auth_headers(as_admin),
    )
    assert r.status_code == 409, r.text
    assert "重新授权" in r.json()["message"]


def test_targeted_reauth_with_a_different_account_is_refused(client, as_admin, rig):
    """定向重授权回来时换了一个账号：400「检测到不同的账号」——把别人的订阅
    接到这条线上，比导入失败糟得多（cc-switch `add_account_internal` 同规）。"""
    old_id = _seed_active(client)
    started = client.post(
        "/admin/subscriptions/device-flows",
        json={
            "provider": "openai_codex",
            "target_subscription_id": str(old_id),
        },
        headers=session_auth_headers(as_admin),
    )
    assert started.status_code == 200, started.text
    flow_id = started.json()["data"]["flow_id"]

    rig.openai_state["poll"] = "complete"
    rig.openai_state["id_token"] = _jwt(
        {"sub": "sub-someone-else", "chatgpt_account_id": "acct-someone-else"}
    )
    r = client.post(
        f"/admin/subscriptions/device-flows/{flow_id}/poll",
        headers=session_auth_headers(as_admin),
    )
    assert r.status_code == 400, r.text
    assert "不同的账号" in r.json()["message"]
    # 失败也落审计，且带回来的新凭据一个字母都不沾。
    rows = _audit_rows(client)
    complete = [r for r in rows if r.action == "subscription.complete"]
    assert complete and complete[-1].result == "failed"
    assert "at-secret-test-value" not in _audit_blob(rows)


def test_cancel_flow(client, as_admin, rig):
    started = client.post(
        "/admin/subscriptions/device-flows",
        json={"provider": "openai_codex"},
        headers=session_auth_headers(as_admin),
    )
    flow_id = started.json()["data"]["flow_id"]
    cancelled = client.post(
        f"/admin/subscriptions/device-flows/{flow_id}/cancel",
        headers=session_auth_headers(as_admin),
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["data"] == {"cancelled": True}

    # 取消后再轮询是 404 —— 这个 flow 已经结束，不是「还在等」。
    poll = client.post(
        f"/admin/subscriptions/device-flows/{flow_id}/poll",
        headers=session_auth_headers(as_admin),
    )
    assert poll.status_code == 404, poll.text
    rows = _audit_rows(client)
    assert ("subscription.cancel", "ok") in [(r.action, r.result) for r in rows]


# --- 刷新 ---------------------------------------------------------------------


def test_refresh_dead_token_marks_reauth_required(client, as_admin, rig):
    """refresh_token 被判死：置 reauth_required、落 failed 审计 —— 这是一个
    **状态**（页面拿它点亮「需重新授权」），不是一次 5xx。"""
    sub_id = _seed_active(client)
    rig.openai_state["token"] = "invalid"
    r = client.post(
        f"/admin/subscriptions/{sub_id}/refresh",
        headers=session_auth_headers(as_admin),
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["status"] == "reauth_required"

    rows = _subs_rows(client)
    assert rows[0].status == "reauth_required"
    audit = [r for r in _audit_rows(client) if r.action == "subscription.refresh"]
    assert audit and audit[-1].result == "failed"
    assert REFRESH not in _audit_blob(audit)


def test_refresh_success_rotates_and_pushes_to_gateway(client, as_admin, rig):
    """刷新成功：换新密文、把新 access_token 经 PATCH 推进已挂的网关模型。"""
    sub_id = _seed_active(client)
    rig.gateway_state["models"] = [
        {
            "model_name": "gpt-codex-subscription",
            "litellm_params": {"model": "openai/gpt-5.2-codex"},
            "model_info": {"id": "mid-sub-1", "cheese_selectable": True},
        }
    ]
    rig.openai_state["access_token"] = "at-rotated-value"
    rig.openai_state["refresh_token"] = "rt-rotated-value"

    r = client.post(
        f"/admin/subscriptions/{sub_id}/refresh",
        headers=session_auth_headers(as_admin),
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["status"] == "active"

    rows = _subs_rows(client)
    assert open_subscription_token(rows[0], "access_token_enc") == "at-rotated-value"
    assert open_subscription_token(rows[0], "refresh_token_enc") == "rt-rotated-value"

    patches = [
        c
        for c in rig.gateway_calls
        if c.method == "PATCH" and c.url.path.endswith("/update")
    ]
    assert len(patches) == 1
    sent = json.loads(patches[0].content)
    assert sent["litellm_params"]["api_key"] == "at-rotated-value"
    assert sent["litellm_params"]["extra_headers"]["chatgpt-account-id"] == (
        "acct-test-1"
    )
    audit = [r for r in _audit_rows(client) if r.action == "subscription.refresh"]
    assert audit and audit[-1].result == "ok"
    blob = _audit_blob(audit)
    for leaked in ("at-rotated-value", "rt-rotated-value"):
        assert leaked not in blob


def test_refresh_unreachable_gateway_is_a_503(client, as_admin, rig):
    """OpenAI 刷成功了、网关推不上去：凭据已在库（这半边不能丢），路由把网关
    的不可达翻成 503，订阅状态仍可读。"""
    sub_id = _seed_active(client)
    rig.gateway_state["models"] = []

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client.app.dependency_overrides[get_gateway_admin] = lambda: GatewayAdmin(
        "http://gw", "mk", transport=httpx.MockTransport(down)
    )
    r = client.post(
        f"/admin/subscriptions/{sub_id}/refresh",
        headers=session_auth_headers(as_admin),
    )
    assert r.status_code == 503, r.text
    rows = _subs_rows(client)
    # token 已换新且可还原 —— 丢了这个等于把这条订阅弄丢。
    assert rows[0].status == "active"
    assert open_subscription_token(rows[0], "access_token_enc") == ACCESS


# --- 上游模型：导入时指定 + 授权后修改 --------------------------------------------


def test_import_with_explicit_upstream_model(client, as_admin, rig):
    """导入时指定上游模型：行上存显式选择，新建网关模型的 model 就是选的那个。"""
    started = client.post(
        "/admin/subscriptions/device-flows",
        json={"provider": "openai_codex", "upstream_model": "openai/gpt-5.6-luna"},
        headers=session_auth_headers(as_admin),
    )
    assert started.status_code == 200, started.text
    flow_id = started.json()["data"]["flow_id"]

    rig.openai_state["poll"] = "complete"
    done = client.post(
        f"/admin/subscriptions/device-flows/{flow_id}/poll",
        headers=session_auth_headers(as_admin),
    )
    assert done.status_code == 200, done.text
    sub_dto = done.json()["data"]["subscription"]
    assert sub_dto["upstream_model"] == "openai/gpt-5.6-luna"

    news = [c for c in rig.gateway_calls if c.url.path == "/model/new"]
    assert len(news) == 1
    sent = json.loads(news[0].content)
    assert sent["litellm_params"]["model"] == "openai/gpt-5.6-luna"

    (row,) = _subs_rows(client)
    assert row.upstream_model == "openai/gpt-5.6-luna"


def _linked_model_fixture(rig, upstream: str = "openai/gpt-5.2-codex") -> None:
    """让假网关的 /model/info 报出那条已挂载的链接模型（编辑分支会被走到）。"""
    rig.gateway_state["models"] = [
        {
            "model_name": "gpt-codex-subscription",
            "litellm_params": {"model": upstream},
            "model_info": {"id": "mid-sub-1", "cheese_selectable": True},
        }
    ]


def _update_patches(rig) -> list[httpx.Request]:
    return [
        c
        for c in rig.gateway_calls
        if c.method == "PATCH" and c.url.path.endswith("/update")
    ]


def test_update_upstream_model_pushes_to_gateway(client, as_admin, rig):
    """授权后改上游：行上的选择落库，PATCH 把新上游连同现有凭据推进网关。"""
    sub_id = _seed_active(client, upstream="openai/gpt-5.2-codex")
    _linked_model_fixture(rig)

    r = client.patch(
        f"/admin/subscriptions/{sub_id}/upstream-model",
        json={"upstream_model": "openai/gpt-5.6-sol"},
        headers=session_auth_headers(as_admin),
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["upstream_model"] == "openai/gpt-5.6-sol"

    (row,) = _subs_rows(client)
    assert row.upstream_model == "openai/gpt-5.6-sol"

    patches = _update_patches(rig)
    assert len(patches) == 1
    sent = json.loads(patches[0].content)
    assert sent["litellm_params"]["model"] == "openai/gpt-5.6-sol"
    # 凭据随刷新同一管道重推；合并语义下 api_base / prices 不动。
    assert sent["litellm_params"]["api_key"] == ACCESS
    assert rig.refreshed, "写完应让选择器目录重读"

    audit = [
        r for r in _audit_rows(client) if r.action == "subscription.update_upstream"
    ]
    assert [(r.action, r.result) for r in audit] == [
        ("subscription.update_upstream", "ok")
    ]
    assert audit[0].before["upstream_model"] == "openai/gpt-5.2-codex"
    assert audit[0].after["upstream_model"] == "openai/gpt-5.6-sol"
    assert ACCESS not in _audit_blob(audit)


def test_update_upstream_model_to_null_falls_back_to_default(client, as_admin, rig):
    """清除显式选择（null）：行上回落 NULL，推进网关的是部署默认值。"""
    sub_id = _seed_active(client, upstream="openai/gpt-5.6-luna")
    _linked_model_fixture(rig, upstream="openai/gpt-5.6-luna")

    r = client.patch(
        f"/admin/subscriptions/{sub_id}/upstream-model",
        json={"upstream_model": None},
        headers=session_auth_headers(as_admin),
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["upstream_model"] is None

    (row,) = _subs_rows(client)
    assert row.upstream_model is None

    patches = _update_patches(rig)
    assert len(patches) == 1
    sent = json.loads(patches[0].content)
    assert sent["litellm_params"]["model"] == "openai/gpt-5.2-codex"


def test_update_upstream_model_rejected_shapes(client, as_admin, rig):
    """空串 400（形状层，仓库把 pydantic 错误翻成 BadRequestError）、不存在
    404、终态 400 —— 每一码是不同的一句话。"""
    sub_id = _seed_active(client)

    r = client.patch(
        f"/admin/subscriptions/{sub_id}/upstream-model",
        json={"upstream_model": ""},
        headers=session_auth_headers(as_admin),
    )
    assert r.status_code == 400, r.text

    # 字段整个缺省：必填（「忘了传」绝不能被读成「清除选择」）。
    r = client.patch(
        f"/admin/subscriptions/{sub_id}/upstream-model",
        json={},
        headers=session_auth_headers(as_admin),
    )
    assert r.status_code == 400, r.text

    # 全是空白：形状层过了 min_length，业务层拒（与网关模型新增同规）。
    r = client.patch(
        f"/admin/subscriptions/{sub_id}/upstream-model",
        json={"upstream_model": "   "},
        headers=session_auth_headers(as_admin),
    )
    assert r.status_code == 400, r.text

    # 行上的选择一路都没被动过。
    (row,) = _subs_rows(client)
    assert row.upstream_model is None

    r = client.patch(
        f"/admin/subscriptions/{uuid.uuid4()}/upstream-model",
        json={"upstream_model": "openai/gpt-5.6-sol"},
        headers=session_auth_headers(as_admin),
    )
    assert r.status_code == 404, r.text

    gone = client.delete(
        f"/admin/subscriptions/{sub_id}", headers=session_auth_headers(as_admin)
    )
    assert gone.status_code == 200, gone.text
    r = client.patch(
        f"/admin/subscriptions/{sub_id}/upstream-model",
        json={"upstream_model": "openai/gpt-5.6-sol"},
        headers=session_auth_headers(as_admin),
    )
    assert r.status_code == 400, r.text


def test_update_upstream_model_gateway_failure_is_a_502(client, as_admin, rig):
    """选择已落库（下一次刷新会补推）、审计落 failed，网关的原话翻成 502。"""
    sub_id = _seed_active(client)

    def refused(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/model/info":
            return httpx.Response(200, json={"data": []})
        return httpx.Response(400, json={"error": "bad upstream"})

    client.app.dependency_overrides[get_gateway_admin] = lambda: GatewayAdmin(
        "http://gw", "mk", transport=httpx.MockTransport(refused)
    )
    r = client.patch(
        f"/admin/subscriptions/{sub_id}/upstream-model",
        json={"upstream_model": "openai/gpt-5.6-sol"},
        headers=session_auth_headers(as_admin),
    )
    assert r.status_code == 502, r.text

    (row,) = _subs_rows(client)
    assert row.upstream_model == "openai/gpt-5.6-sol"
    assert "推进网关失败" in (row.last_refresh_error or "")
    audit = [
        r for r in _audit_rows(client) if r.action == "subscription.update_upstream"
    ]
    assert audit and audit[-1].result == "failed"
    assert ACCESS not in _audit_blob(audit)


def test_refresh_reasserts_the_rows_upstream(client, as_admin, rig):
    """网关上的上游被改走（比如通用编辑页）后，下一次刷新拿行上的值盖回去
    —— 订阅行是这条链接模型上游的唯一权威。"""
    sub_id = _seed_active(client, upstream="openai/gpt-5.6-luna")
    _linked_model_fixture(rig, upstream="openai/gpt-5.9-tampered")

    r = client.post(
        f"/admin/subscriptions/{sub_id}/refresh",
        headers=session_auth_headers(as_admin),
    )
    assert r.status_code == 200, r.text

    patches = _update_patches(rig)
    assert len(patches) == 1
    sent = json.loads(patches[0].content)
    assert sent["litellm_params"]["model"] == "openai/gpt-5.6-luna"


def test_targeted_reauth_inherits_the_old_rows_upstream(client, as_admin, rig):
    """重授权换凭据不换配置：请求没带上游时，新行继承被顶掉那行的显式选择，
    完成时推进网关的也是它 —— 换号不会把模型静默打回部署默认。"""
    old_id = _seed_active(client, upstream="openai/gpt-5.6-luna")
    _linked_model_fixture(rig, upstream="openai/gpt-5.6-luna")

    started = client.post(
        "/admin/subscriptions/device-flows",
        json={
            "provider": "openai_codex",
            "target_subscription_id": str(old_id),
        },
        headers=session_auth_headers(as_admin),
    )
    assert started.status_code == 200, started.text
    flow_id = started.json()["data"]["flow_id"]

    rig.openai_state["poll"] = "complete"
    done = client.post(
        f"/admin/subscriptions/device-flows/{flow_id}/poll",
        headers=session_auth_headers(as_admin),
    )
    assert done.status_code == 200, done.text
    assert done.json()["data"]["subscription"]["upstream_model"] == (
        "openai/gpt-5.6-luna"
    )

    rows = _subs_rows(client)
    active = [r for r in rows if r.status == "active"]
    assert len(active) == 1
    assert active[0].upstream_model == "openai/gpt-5.6-luna"

    patches = _update_patches(rig)
    assert len(patches) == 1
    sent = json.loads(patches[0].content)
    assert sent["litellm_params"]["model"] == "openai/gpt-5.6-luna"


def test_update_upstream_model_with_an_undecryptable_credential_marks_reauth(
    client, as_admin, rig
):
    """凭据解不开（密钥已轮换）：选择落库、行置 reauth_required（凭据不可用的
    既有路径）、网关一个包都不收；重授权继承这个选择。"""
    sub_id = _seed_active(client)

    async def _corrupt():
        async with client.test_factory() as session:
            row = await session.get(LlmSubscription, sub_id)
            row.access_token_enc = "not-a-valid-ciphertext"
            await session.commit()

    asyncio.run(_corrupt())

    r = client.patch(
        f"/admin/subscriptions/{sub_id}/upstream-model",
        json={"upstream_model": "openai/gpt-5.6-sol"},
        headers=session_auth_headers(as_admin),
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["status"] == "reauth_required"
    assert data["upstream_model"] == "openai/gpt-5.6-sol"

    (row,) = _subs_rows(client)
    assert row.status == "reauth_required"
    assert row.upstream_model == "openai/gpt-5.6-sol"
    # 网关一个包都不该收到（连 /model/info 都不读 —— 没有凭据可推）。
    assert rig.gateway_calls == []
    audit = [
        r for r in _audit_rows(client) if r.action == "subscription.update_upstream"
    ]
    assert audit and audit[-1].result == "failed"


# --- 额度读数 -----------------------------------------------------------------

_SNAPSHOT = {
    "tiers": [
        {
            "name": "five_hour",
            "utilization": 10.0,
            "resets_at": "2026-09-23T00:00:00+00:00",
        }
    ]
}


def test_quota_success_stores_a_snapshot(client, as_admin, rig):
    sub_id = _seed_active(client)
    r = client.get(
        f"/admin/subscriptions/{sub_id}/quota",
        headers=session_auth_headers(as_admin),
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert [t["name"] for t in data["tiers"]] == ["five_hour", "seven_day"]
    assert data["tiers"][0]["utilization"] == 42
    assert data["stale"] is False

    rows = _subs_rows(client)
    assert rows[0].quota_snapshot["tiers"][0]["name"] == "five_hour"
    assert rows[0].quota_fetched_at is not None


def test_quota_transport_error_falls_back_to_the_old_snapshot(client, as_admin, rig):
    """传输错误留旧值（cc-switch 同规）：有旧快照就回它并标 `stale`。"""
    sub_id = _seed_active(client, snapshot=_SNAPSHOT)
    rig.openai_state["quota"] = "down"
    r = client.get(
        f"/admin/subscriptions/{sub_id}/quota",
        headers=session_auth_headers(as_admin),
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["stale"] is True
    assert data["tiers"][0]["name"] == "five_hour"
    assert data["tiers"][0]["utilization"] == 10.0


def test_quota_auth_failure_clears_the_snapshot(client, as_admin, rig):
    """认证错误清缓存（同一枚硬币的另一面）：置 reauth_required、清快照，
    对外 502「凭据已失效，需要重新授权」。"""
    sub_id = _seed_active(client, snapshot=_SNAPSHOT)
    rig.openai_state["quota"] = "invalid"
    r = client.get(
        f"/admin/subscriptions/{sub_id}/quota",
        headers=session_auth_headers(as_admin),
    )
    assert r.status_code == 502, r.text
    assert "重新授权" in r.json()["message"]

    rows = _subs_rows(client)
    assert rows[0].status == "reauth_required"
    assert rows[0].quota_snapshot is None


# --- 撤销 ---------------------------------------------------------------------


def test_revoke_blocks_the_linked_model_and_audits(client, as_admin, rig):
    sub_id = _seed_active(client)
    rig.gateway_state["models"] = [
        {
            "model_name": "gpt-codex-subscription",
            "litellm_params": {"model": "openai/gpt-5.2-codex"},
            "model_info": {"id": "mid-sub-1", "cheese_selectable": True},
        }
    ]
    r = client.delete(
        f"/admin/subscriptions/{sub_id}",
        headers=session_auth_headers(as_admin),
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"] == {"revoked": True}

    rows = _subs_rows(client)
    assert rows[0].status == "revoked"
    # 网关收到停用那条模型的请求，选择器目录重读。
    blocks = [c for c in rig.gateway_calls if c.url.path == "/model/block"]
    assert len(blocks) == 1
    assert rig.refreshed
    audit = [r for r in _audit_rows(client) if r.action == "subscription.revoke"]
    assert audit and audit[-1].result == "ok"
    # 审计的 before/after 带状态转变，但不带 PII（email 只存表里那一处）。
    assert audit[-1].before["status"] == "active"
    assert audit[-1].after["status"] == "revoked"
    assert "admin@example.com" not in _audit_blob(audit)


def test_list_is_redacted_and_newest_first(client, as_admin, rig):
    _seed_active(client)
    r = client.get("/admin/subscriptions", headers=session_auth_headers(as_admin))
    assert r.status_code == 200, r.text
    items = r.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["status"] == "active"
    blob = json.dumps(items)
    for leaked in (ACCESS, REFRESH, ID_TOKEN, "token_enc"):
        assert leaked not in blob
