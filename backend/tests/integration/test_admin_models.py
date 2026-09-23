"""后台「模型管理」这一页的门、参数界、失败语义，以及一次写留下的痕迹。

跑在真的 HTTP 栈 + 真的库上（`client` / `as_admin`，夹具用法照
`tests/integration/test_admin_stats.py`）。网关用 `httpx.MockTransport` 打桩，经
`get_gateway_admin` 这个依赖换进去 —— 测试不该把管理动作打到真网关上。

三个边界各一条：非管理员 403、`days` 越界、网关不可达 503。写那两条更重：一次成功
的写要在 `gateway_admin_audit` 留一行、并让选择器那份目录重读；一次失败的写**也要**
留一行（`result="failed"`）——「我点了停用，为什么没生效」正是这一页要回答的问题，
只记成功的话失败在界面上和在库里都等同于「什么都没发生」。
"""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select

from app.api.routes.admin_models import get_gateway_admin
from app.core.config import settings
from app.domain.agent import gateway_catalog, gateway_models
from app.domain.agent.gateway_admin import GatewayAdmin
from app.domain.agent.models import GatewayAdminAudit
from tests.integration.conftest import session_auth_headers

#: 放进管理员名单的那个 handle。平台管理员是平台级的事实，和任何项目角色无关 ——
#: 与 `test_admin_stats.py` 用的一模一样的做法。
ADMIN = "models-admin"
STRANGER = "models-stranger"

#: 网关里那条 `config.yaml` 声明的模型。写它一律被拒，用来验「失败的写也落一行」。
_CONFIG_MODEL = {
    "model_name": "declared",
    "litellm_params": {
        "model": "anthropic/x",
        "input_cost_per_token": 1e-6,
        "output_cost_per_token": 2e-6,
    },
    "model_info": {"id": "cfg-1", "cheese_selectable": True},
}

#: 一次合法的新建：上架 + 两个单价都给。
_NEW_MODEL = {
    "name": "test-model",
    "upstream_model": "anthropic/glm-4.7",
    "api_base": "https://open.bigmodel.cn/api/anthropic",
    "api_key": "sk-upstream-secret",
    "label": "GLM-4.7",
    "selectable": True,
    "prices": {
        "input": 1e-6,
        "output": 2e-6,
        "cache_read": None,
        "cache_creation": None,
    },
    "capabilities": {"reasoning": True, "vision": False},
}

#: 网关里一条**运行时**模型（`db_model` 为真，origin=runtime，可改）。订阅导入
#: 挂的那种模型就长这样：`extra_headers` 三件套已经在 litellm_params 里。
_RUNTIME_MODEL = {
    "model_name": "runtime-x",
    "litellm_params": {
        "model": "openai/gpt-5.2-codex",
        "input_cost_per_token": 2.5e-6,
        "output_cost_per_token": 1e-5,
        "extra_headers": {
            "chatgpt-account-id": "acct-1",
            "originator": "codex_cli_rs",
            "version": "0.153.4",
        },
    },
    "model_info": {"id": "rt-1", "cheese_selectable": True, "db_model": True},
}


@pytest.fixture
def as_admin(monkeypatch: pytest.MonkeyPatch) -> str:
    """让 ``ADMIN`` 成为这一条用例里的平台管理员。`admin_handles()` 每次重读
    settings，所以这个改动不用重启进程。"""
    monkeypatch.setattr(settings, "platform_admin_handles", [ADMIN])
    return ADMIN


def _stub_transport(
    calls: list[httpx.Request], models: list | None = None
) -> httpx.MockTransport:
    """一台够管理页用的假网关：模型表里有一条 config 模型，用量与写接口各答一句。

    只实现管理客户端真正会用的那几条路，其它一律 404 —— 实现要是走了一条没预料到的
    路，用例会以 `GatewayRefused`（502）立刻失败，而不是静默拿到空数据。
    """

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        path = request.url.path
        if path == "/health/readiness":
            return httpx.Response(200, json={"status": "healthy"})
        if path == "/model/info":
            return httpx.Response(
                200, json={"data": [_CONFIG_MODEL] if models is None else models}
            )
        if path == "/user/daily/activity/aggregated":
            return httpx.Response(200, json={"results": [], "metadata": {}})
        if path == "/model/new":
            return httpx.Response(200, json={"model_id": "mid-1"})
        # 编辑走 PATCH /model/{model_id}/update：POST /model/update 落库时不写
        # model_info，改标签/上架/能力会被静默丢弃。
        if (
            request.method == "PATCH"
            and path.startswith("/model/")
            and path.endswith("/update")
        ):
            return httpx.Response(200, json={})
        if path in ("/model/delete", "/model/block", "/model/unblock"):
            return httpx.Response(200, json={})
        return httpx.Response(404, json={"detail": {"error": f"stub has no {path}"}})

    return httpx.MockTransport(handler)


def _install(client, admin: GatewayAdmin) -> None:
    client.app.dependency_overrides[get_gateway_admin] = lambda: admin


@pytest.fixture
def gateway(client, monkeypatch) -> SimpleNamespace:
    """打桩的网关 + 被拦下的目录刷新。

    目录刷新（`gateway_catalog.refresh`）会真去问网关，这一层只验「有没有被叫到」，
    所以换成记名。缓存是模块级的，前后各清一次，免得上一个用例的答案渗进来。
    """
    gateway_models.reset_cache()
    calls: list[httpx.Request] = []
    _install(client, GatewayAdmin("http://gw", "mk", transport=_stub_transport(calls)))
    refreshed: list[object] = []

    async def _refresh(g):
        refreshed.append(g)
        return True

    monkeypatch.setattr(gateway_catalog, "refresh", _refresh)
    yield SimpleNamespace(calls=calls, refreshed=refreshed)
    client.app.dependency_overrides.pop(get_gateway_admin, None)
    gateway_models.reset_cache()


@pytest.fixture
def unreachable_gateway(client) -> None:
    """一台连不上的网关：任何请求都抛连接错误。"""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    gateway_models.reset_cache()
    _install(
        client, GatewayAdmin("http://gw", "mk", transport=httpx.MockTransport(handler))
    )
    yield
    client.app.dependency_overrides.pop(get_gateway_admin, None)
    gateway_models.reset_cache()


def _audit_rows(client) -> list[tuple]:
    """审计表的全部行（写库那半边是 `client` 那个库，见 `test_admin_stats.py` 的
    警告）。取成普通元组，免得会话关掉后留下游离的 ORM 对象。"""

    async def _go():
        async with client.test_factory() as session:
            rows = (await session.scalars(select(GatewayAdminAudit))).all()
            return [
                (r.action, r.result, r.actor_handle, r.target, r.detail, r.after)
                for r in rows
            ]

    return asyncio.run(_go())


# --- 门与边界 ----------------------------------------------------------------


def test_a_stranger_reads_nothing_but_an_admin_does(client, as_admin, gateway):
    stranger = client.get(
        "/admin/gateway/models", headers=session_auth_headers(STRANGER)
    )
    assert stranger.status_code == 403, stranger.text

    allowed = client.get(
        "/admin/gateway/models", headers=session_auth_headers(as_admin)
    )
    assert allowed.status_code == 200, allowed.text


@pytest.mark.parametrize("days", [0, 91])
def test_days_outside_the_readable_window_is_refused(client, as_admin, gateway, days):
    """窗口下界是 1（0 天画不出折线），上界有成本理由（读用量本身要花钱）。签名上
    写死了 `ge=1` / `le=90`，越界在进服务、更没碰网关之前就被挡下。

    状态码是 400 而不是 FastAPI 默认的 422：这个部署把 `RequestValidationError` 统一
    折进 400（`errors.validation_exception_handler`，契约 §2.3 的「请求体不合法 →
    400」也是这一句）。契约 §5 写的是 422，实测与那份 handler 都不是 —— 这里钉实测。"""
    r = client.get(
        "/admin/gateway/models",
        params={"days": days},
        headers=session_auth_headers(as_admin),
    )
    assert r.status_code == 400, r.text
    assert gateway.calls == []  # 参数不过关，一次网关都不该问


def test_an_unreachable_gateway_is_a_503_not_an_empty_board(
    client, as_admin, unreachable_gateway
):
    """网关没答话时不能说「这个部署一个模型都没有」—— 那是另一句话。503 明说
    「这次没读到，过会儿再试」，并带上服务给的原因。"""
    r = client.get("/admin/gateway/models", headers=session_auth_headers(as_admin))
    assert r.status_code == 503, r.text
    assert "网关" in r.json()["message"]


# --- 写留下的痕迹 ------------------------------------------------------------


def test_a_successful_write_leaves_a_row_in_the_audit_table(client, as_admin, gateway):
    r = client.post(
        "/admin/gateway/models",
        json=_NEW_MODEL,
        headers=session_auth_headers(as_admin),
    )
    assert r.status_code == 200, r.text

    rows = _audit_rows(client)
    assert len(rows) == 1
    action, result, actor, target, detail, after = rows[0]
    assert (action, result) == ("model.add", "ok")
    assert actor == as_admin  # 谁干的
    assert target == "test-model"  # 对谁
    assert detail is None
    # 上游凭据绝不进审计素材：它是网关的东西，复制进另一处可读的地方就是泄漏。
    assert "sk-upstream-secret" not in json.dumps(after)


def test_a_successful_write_refreshes_the_picker_catalogue(client, as_admin, gateway):
    """选择器用的是 `gateway_catalog` 那份目录，写完不刷，用户要等最长一个刷新周期
    才看得到新模型（或还看得到一个已停用的）。"""
    r = client.post(
        "/admin/gateway/models",
        json=_NEW_MODEL,
        headers=session_auth_headers(as_admin),
    )
    assert r.status_code == 200, r.text
    assert gateway.refreshed, "写成功后应让选择器目录重读"


def test_a_failed_write_is_recorded_too(client, as_admin, gateway):
    """对 `config.yaml` 里的模型写：网关不支持，服务层先拦下（400）。这一条失败也
    必须在审计表里留一行，否则页面上的「最近操作」对这次点击一声不吭。"""
    r = client.patch(
        "/admin/gateway/models/declared",
        json={"label": "改个名字"},
        headers=session_auth_headers(as_admin),
    )
    assert r.status_code == 400, r.text

    rows = _audit_rows(client)
    assert len(rows) == 1
    action, result, actor, target, detail, _after = rows[0]
    assert (action, result, actor, target) == (
        "model.update",
        "failed",
        as_admin,
        "declared",
    )
    assert "config.yaml" in detail


# --- extra_headers 的 PATCH 合并语义 与 审计读回 -------------------------------


@pytest.fixture
def runtime_gateway(client, monkeypatch) -> SimpleNamespace:
    """模型表里有一条**运行时**模型的假网关（订阅导入挂的那种，头上已带三件套）。"""
    gateway_models.reset_cache()
    calls: list[httpx.Request] = []
    _install(
        client,
        GatewayAdmin(
            "http://gw", "mk", transport=_stub_transport(calls, [_RUNTIME_MODEL])
        ),
    )
    refreshed: list[object] = []

    async def _refresh(g):
        refreshed.append(g)
        return True

    monkeypatch.setattr(gateway_catalog, "refresh", _refresh)
    yield SimpleNamespace(calls=calls, refreshed=refreshed)
    client.app.dependency_overrides.pop(get_gateway_admin, None)
    gateway_models.reset_cache()


def _patches(calls: list[httpx.Request]) -> list[dict]:
    return [
        json.loads(c.content)
        for c in calls
        if c.method == "PATCH" and c.url.path.endswith("/update")
    ]


def test_a_patch_without_extra_headers_leaves_the_gateway_side_alone(
    client, as_admin, runtime_gateway
):
    """PATCH 合并语义在**客户端**兑现：表单不知道订阅头（v1 不暴露编辑），改
    标签的 PATCH 里不带 `extra_headers` —— 带了就是整组替换，会把订阅导入
    写进去的三件套弄丢。"""
    r = client.patch(
        "/admin/gateway/models/runtime-x",
        json={"label": "新标签"},
        headers=session_auth_headers(as_admin),
    )
    assert r.status_code == 200, r.text
    sent = _patches(runtime_gateway.calls)
    assert len(sent) == 1
    # 标签只动 model_info：litellm_params 整个不出现（或出现了也不带头），
    # 网关侧的既有三件套因此原样保留。
    assert "extra_headers" not in sent[0].get("litellm_params", {})


def test_a_patch_with_extra_headers_replaces_the_whole_set(
    client, as_admin, runtime_gateway
):
    """给了才整组替换（订阅导入/刷新推进走的就是这条路）。"""
    headers = {
        "chatgpt-account-id": "acct-2",
        "originator": "codex_cli_rs",
        "version": "0.154.0",
    }
    r = client.patch(
        "/admin/gateway/models/runtime-x",
        json={"extra_headers": headers},
        headers=session_auth_headers(as_admin),
    )
    assert r.status_code == 200, r.text
    sent = _patches(runtime_gateway.calls)
    assert len(sent) == 1
    assert sent[0]["litellm_params"]["extra_headers"] == headers


def test_the_audit_endpoint_answers_what_changed(client, as_admin, gateway):
    """审计读回 before/after：审计区从「谁改了」升级成「改了什么」就靠这两个
    字段。写入时已 `_redact`，所以这里也不该见到凭据。"""
    r = client.post(
        "/admin/gateway/models",
        json=_NEW_MODEL,
        headers=session_auth_headers(as_admin),
    )
    assert r.status_code == 200, r.text

    audit = client.get("/admin/gateway/audit", headers=session_auth_headers(as_admin))
    assert audit.status_code == 200, audit.text
    items = audit.json()["data"]["items"]
    assert len(items) == 1
    item = items[0]
    assert item["before"] is None
    assert item["after"]["name"] == "test-model"
    assert "sk-upstream-secret" not in json.dumps(item)
