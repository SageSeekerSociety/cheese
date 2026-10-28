"""一个模型、几条线路：管理页往网关里加、改、排、停、删线路时守的那几条规矩。

一个模型（`model_name`）在网关里可以有好几条部署 —— 官方 API、OpenCode Go 订阅、免费
模型、国内中转站 —— 网关按 `order` 从小到大试。这里钉住的是能脱离实现说出来的规则：

* 目录读 `/model/info` 列出的第一行，所以同一个模型每一行的展示元数据必须一样；加线路
  时从已有那一行抄，改模型时每一行都改。每一行的 model_info 是它自己的一份，部署 id
  不抄（共用一个 id，一行 429 冷却全组，#3294）。
* config.yaml 声明的线路只读，但可以在它旁边加运行时线路。
* 唯一合法的零价是声明出来的免费：`cheese_free` 且输入输出都写 0。
* 每条线路的请求头：名字合法、值里没有换行、认证头不能按线路覆盖；值不进审计。
* 停用一条线路不让整个模型从选择器里消失。

网关是一台有状态的假网关（`httpx.MockTransport`）：`/model/new` 真的加一行、PATCH 真的
合并、删除真的删，好让用例从「之后列出来是什么样」去看结果，而不是数调了几次哪个函数。
"""

import json
import uuid
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

from app.core.errors import BadRequestError, NotFoundError
from app.domain.agent import gateway_catalog
from app.domain.agent.gateway import LlmGateway
from app.domain.agent.gateway_admin import GatewayAdmin
from app.domain.agent.gateway_models import GatewayModelsService, reset_cache
from app.domain.agent.models import PlatformModelRole
from app.domain.agent.schemas import (
    ModelCreate,
    ModelUpdate,
    SourceCreate,
    SourceOrder,
    SourceUpdate,
    upstream_string,
)


class _StubDb:
    """服务层写审计那一小段够用的假会话：记下加了什么，flush/commit 空转。"""

    def __init__(self) -> None:
        self.added: list[object] = []
        # Platform model roles an administrator pointed (`platform_model_roles`).
        self.roles: dict[str, PlatformModelRole] = {}

    def add(self, obj: object) -> None:
        if isinstance(obj, PlatformModelRole):
            self.roles[obj.role] = obj
            return
        self.added.append(obj)

    async def scalars(self, _statement: object) -> object:
        rows = list(self.roles.values())
        return SimpleNamespace(all=lambda: rows)

    async def get(self, _cls: object, key: str) -> PlatformModelRole | None:
        return self.roles.get(key)

    async def delete(self, obj: PlatformModelRole) -> None:
        self.roles.pop(obj.role, None)

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        return None


#: config.yaml 里的 kimi-k3（形状照 deploy/gateway/config.yaml）。
_KIMI_CONFIG = {
    "model_name": "kimi-k3",
    "litellm_params": {
        "model": "anthropic/kimi-k3",
        "api_base": "https://api.moonshot.cn/anthropic",
        "input_cost_per_token": 2.8e-6,
        "output_cost_per_token": 1.4e-5,
    },
    "model_info": {
        "id": "cfg-kimi",
        "cheese_selectable": True,
        "cheese_label": "Kimi K3",
        "cheese_tier": "premium",
        "cheese_efforts": ["low", "high", "max"],
        "supports_reasoning": True,
        "supports_output_config": True,
        "supports_max_reasoning_effort": True,
        # Chat 线路要求整组都标（见下面那一节）；config 的行只能在 config.yaml 里标。
        "supports_mid_conversation_system": False,
    },
}


class _Gateway:
    """一台有状态的假网关，只认管理客户端会走的那几条路，其它一律 404。"""

    def __init__(self, rows: list[dict] | None = None) -> None:
        self.rows: list[dict] = json.loads(json.dumps(rows or []))
        self.requests: list[httpx.Request] = []
        # Virtual keys by alias -> the models each may call (`/key/update`).
        self.keys: dict[str, list[str]] = {}
        # The ledger by upstream model (`breakdown.models`), and the start date
        # of every usage window asked for.
        self.ledger: dict[str, float] = {}
        self.usage_starts: list[str] = []

    def _find(self, model_id: str) -> dict | None:
        return next((r for r in self.rows if r["model_info"]["id"] == model_id), None)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        body = json.loads(request.content) if request.content else {}
        if path == "/health/readiness":
            return httpx.Response(200, json={"status": "healthy"})
        if path == "/model/info":
            return httpx.Response(200, json={"data": self.rows})
        if path == "/user/daily/activity/aggregated":
            self.usage_starts.append(request.url.params["start_date"])
            models = {
                upstream: {"metrics": {"spend": spend}}
                for upstream, spend in self.ledger.items()
            }
            results = (
                [{"date": "2026-10-20", "breakdown": {"models": models}}]
                if models
                else []
            )
            return httpx.Response(200, json={"results": results, "metadata": {}})
        if path == "/model/new":
            model_id = str(uuid.uuid4())
            params = dict(body["litellm_params"])
            params.pop("api_key", None)  # 网关列表里本就不给凭据
            self.rows.append(
                {
                    "model_name": body["model_name"],
                    "litellm_params": params,
                    "model_info": {
                        **body["model_info"],
                        "id": model_id,
                        "db_model": True,
                    },
                }
            )
            return httpx.Response(200, json={"model_id": model_id})
        if request.method == "PATCH" and path.endswith("/update"):
            row = self._find(path.split("/")[2])
            if row is None or row["model_info"].get("db_model") is not True:
                return httpx.Response(400, json={"detail": {"error": "not a db model"}})
            params = dict(body.get("litellm_params") or {})
            params.pop("api_key", None)
            row["litellm_params"].update(params)
            row["model_info"].update(body.get("model_info") or {})
            return httpx.Response(200, json={})
        if path == "/model/delete":
            self.rows = [r for r in self.rows if r["model_info"]["id"] != body["id"]]
            return httpx.Response(200, json={})
        if path == "/key/update":
            alias = body.get("key_alias")
            if alias not in self.keys:
                return httpx.Response(404, json={"detail": "no key holds the alias"})
            self.keys[alias] = list(body["models"])
            return httpx.Response(200, json={})
        if path in ("/model/block", "/model/unblock"):
            row = self._find(body["model_id"])
            if row is None:
                return httpx.Response(404, json={"detail": "no such model"})
            row["model_info"]["blocked"] = path == "/model/block"
            return httpx.Response(200, json={})
        return httpx.Response(404, json={"detail": {"error": f"stub has no {path}"}})

    def admin(self) -> GatewayAdmin:
        return GatewayAdmin(
            "http://gw", "mk", transport=httpx.MockTransport(self._handle)
        )

    def catalogue(self) -> LlmGateway:
        return LlmGateway(
            "http://gw", "mk", transport=httpx.MockTransport(self._handle)
        )

    def writes(self) -> list[httpx.Request]:
        return [
            r
            for r in self.requests
            if r.url.path
            in ("/model/new", "/model/delete", "/model/block", "/model/unblock")
            or (r.method == "PATCH" and r.url.path.endswith("/update"))
        ]

    def group(self, name: str) -> list[dict]:
        return [r for r in self.rows if r["model_name"] == name]


@pytest.fixture(autouse=True)
def _clean_module_state(monkeypatch):
    reset_cache()

    async def _refresh(gateway):
        return True

    monkeypatch.setattr(gateway_catalog, "refresh", _refresh)
    yield
    reset_cache()


def _service(gw: _Gateway, db: _StubDb | None = None) -> GatewayModelsService:
    return GatewayModelsService(db or _StubDb(), gw.admin())


async def _listed(service: GatewayModelsService, name: str) -> dict:
    reset_cache()
    listing = await service.listing(days=7)
    return next(m for m in listing["models"] if m["name"] == name)


_GO = {
    "protocol": "openai-chat",
    "upstream_model": "kimi-k3",
    "api_base": "https://opencode.ai/zen/go/v1",
    "api_key": "sk-go-secret",
    "order": 1,
    "prices": {"input": 1e-6, "output": 4e-6},
    "upstream_headers": {"user-agent": "cheese-gateway/1.0"},
    "session_header": "x-opencode-session",
    # Go 排在官方 API 前面是有意为之：config 那一行没有 order，新线路一来就是首选。
    "make_primary": True,
}


# --- 加线路：元数据跟着模型走，部署 id 与请求头是这一行自己的 --------------------


@pytest.mark.anyio
async def test_a_source_added_beside_a_config_model_carries_the_models_metadata():
    gw = _Gateway([_KIMI_CONFIG])
    service = _service(gw)

    await service.add_source(
        handle="admin", name="kimi-k3", payload=SourceCreate(**_GO)
    )

    rows = gw.group("kimi-k3")
    assert len(rows) == 2
    config_row, new_row = rows
    for key in (
        "cheese_selectable",
        "cheese_label",
        "cheese_tier",
        "cheese_efforts",
        "supports_reasoning",
        # 思考档送不送得到上游看这两项；少抄一项，落到新线路的请求就丢了 effort。
        "supports_output_config",
        "supports_max_reasoning_effort",
    ):
        assert new_row["model_info"][key] == config_row["model_info"][key], key
    # 自己的部署 id：抄来同一个，一行 429 就把全组冷却了。
    assert new_row["model_info"]["id"] != config_row["model_info"]["id"]
    sent = json.loads(
        next(r for r in gw.requests if r.url.path == "/model/new").content
    )
    assert "id" not in sent["model_info"]
    # 请求头只属于这一行：官方那一行不该收到 Go 的 User-Agent 与会话头。静态头的值
    # 在 litellm_params 里（/model/info 对每把虚拟 key 都开放，model_info 原样回，
    # litellm_params 过 LiteLLM 的掩码），model_info 里只有会话头的名字。
    assert new_row["litellm_params"]["extra_headers"] == {
        "user-agent": "cheese-gateway/1.0"
    }
    assert "cheese-gateway/1.0" not in json.dumps(new_row["model_info"])
    assert new_row["model_info"]["cheese_session_header"] == "x-opencode-session"
    assert "extra_headers" not in config_row["litellm_params"]
    assert new_row["litellm_params"]["order"] == 1


@pytest.mark.anyio
async def test_a_source_added_beside_a_row_that_cannot_search_is_not_marked_so():
    # Whether a deployment runs web search is a fact about its server (RUC's vLLM
    # does not), measured per row; a new route must not inherit "cannot search",
    # or the gateway never sends a search to it.
    config = json.loads(json.dumps(_KIMI_CONFIG))
    config["model_info"]["supports_web_search"] = False
    gw = _Gateway([config])
    service = _service(gw)

    await service.add_source(
        handle="admin", name="kimi-k3", payload=SourceCreate(**_GO)
    )

    _, new_row = gw.group("kimi-k3")
    assert "supports_web_search" not in new_row["model_info"]


@pytest.mark.anyio
async def test_the_listing_shows_every_source_in_the_order_the_gateway_tries_them():
    config = json.loads(json.dumps(_KIMI_CONFIG))
    config["litellm_params"]["order"] = 2
    gw = _Gateway([config])
    service = _service(gw)

    await service.add_source(
        handle="admin", name="kimi-k3", payload=SourceCreate(**_GO)
    )

    item = await _listed(service, "kimi-k3")
    sources = item["sources"]
    assert [(s["origin"], s["order"]) for s in sources] == [
        ("runtime", 1),
        ("config", 2),
    ]
    go = sources[0]
    assert go["protocol"] == "openai-chat"
    assert go["upstream_model"] == "kimi-k3"
    assert go["host"] == "opencode.ai"
    assert go["header_names"] == ["user-agent"]
    assert go["session_header"] == "x-opencode-session"
    # 请求头的值与上游凭据都不出现在页面拿到的东西里。
    assert "cheese-gateway/1.0" not in json.dumps(item)
    assert "sk-go-secret" not in json.dumps(item)
    # 模型仍是 config 声明的那个：它的元数据在页面上只读。
    assert item["origin"] == "config"


@pytest.mark.anyio
async def test_a_config_source_cannot_be_changed_paused_reordered_or_removed():
    gw = _Gateway([_KIMI_CONFIG])
    service = _service(gw)

    for attempt in (
        lambda: service.update_source(
            handle="admin",
            name="kimi-k3",
            source_id="cfg-kimi",
            payload=SourceUpdate(order=3),
        ),
        lambda: service.set_source_blocked(
            handle="admin", name="kimi-k3", source_id="cfg-kimi", blocked=True
        ),
        lambda: service.delete_source(
            handle="admin", name="kimi-k3", source_id="cfg-kimi"
        ),
        lambda: service.reorder_sources(
            handle="admin", name="kimi-k3", orders={"cfg-kimi": 5}
        ),
    ):
        with pytest.raises(BadRequestError) as refused:
            await attempt()
        assert "config.yaml" in str(refused.value)
    assert gw.writes() == []


@pytest.mark.anyio
async def test_a_source_of_an_unknown_model_or_id_is_not_found():
    gw = _Gateway([_KIMI_CONFIG])
    service = _service(gw)

    with pytest.raises(NotFoundError):
        await service.add_source(
            handle="admin", name="no-such", payload=SourceCreate(**_GO)
        )
    with pytest.raises(NotFoundError):
        await service.update_source(
            handle="admin",
            name="kimi-k3",
            source_id="nope",
            payload=SourceUpdate(order=2),
        )
    assert gw.writes() == []


@pytest.mark.anyio
async def test_a_second_model_of_the_same_name_is_refused():
    """同名新建会变成这个模型的又一条线路，却带着另一份展示元数据。"""
    gw = _Gateway([_KIMI_CONFIG])
    service = _service(gw)

    with pytest.raises(BadRequestError):
        await service.add(
            handle="admin",
            payload=ModelCreate(name="kimi-k3", upstream_model="anthropic/k"),
        )
    assert gw.writes() == []


# --- 改模型：每一行都改，上游那几项要落到具体的线路 ------------------------------


async def _two_runtime_sources(gw: _Gateway, service: GatewayModelsService) -> None:
    await service.add(
        handle="admin",
        payload=ModelCreate(
            name="glm",
            protocol="anthropic",
            upstream_model="glm-5.2",
            api_base="https://open.bigmodel.cn/api/anthropic",
            label="GLM",
            selectable=True,
            prices={"input": 1e-6, "output": 2e-6},
        ),
    )
    await service.add_source(
        handle="admin",
        name="glm",
        payload=SourceCreate(
            protocol="openai-chat",
            upstream_model="glm-5.2",
            api_base="https://relay.example.cn/v1",
            order=2,
            prices={"input": 5e-7, "output": 1e-6},
        ),
    )
    reset_cache()


@pytest.mark.anyio
async def test_relabelling_a_model_relabels_every_source():
    gw = _Gateway()
    service = _service(gw)
    await _two_runtime_sources(gw, service)

    await service.update(
        handle="admin", name="glm", payload=ModelUpdate(label="GLM-5.2", tier="premium")
    )

    infos = [r["model_info"] for r in gw.group("glm")]
    assert [i["cheese_label"] for i in infos] == ["GLM-5.2", "GLM-5.2"]
    assert [i["cheese_tier"] for i in infos] == ["premium", "premium"]


@pytest.mark.anyio
async def test_an_upstream_edit_on_a_model_with_several_sources_is_refused():
    gw = _Gateway()
    service = _service(gw)
    await _two_runtime_sources(gw, service)
    before = len(gw.writes())

    with pytest.raises(BadRequestError):
        await service.update(
            handle="admin",
            name="glm",
            payload=ModelUpdate(api_base="https://elsewhere.example/v1"),
        )
    assert len(gw.writes()) == before


@pytest.mark.anyio
async def test_reordering_puts_the_relay_first():
    gw = _Gateway()
    service = _service(gw)
    await _two_runtime_sources(gw, service)
    ids = {r["litellm_params"]["model"]: r["model_info"]["id"] for r in gw.group("glm")}
    relay = ids[upstream_string("openai-chat", "glm-5.2")]
    official = ids["anthropic/glm-5.2"]

    out = await service.reorder_sources(
        handle="admin",
        name="glm",
        orders=SourceOrder(orders={relay: 1, official: 2}).orders,
    )

    assert [s["source_id"] for s in out["model"]["sources"]] == [relay, official]


@pytest.mark.anyio
async def test_a_reorder_naming_a_config_source_writes_nothing_at_all():
    gw = _Gateway([_KIMI_CONFIG])
    service = _service(gw)
    await service.add_source(
        handle="admin", name="kimi-k3", payload=SourceCreate(**_GO)
    )
    go = gw.group("kimi-k3")[1]["model_info"]["id"]
    before = len(gw.writes())

    with pytest.raises(BadRequestError):
        await service.reorder_sources(
            handle="admin", name="kimi-k3", orders={go: 3, "cfg-kimi": 1}
        )
    assert len(gw.writes()) == before
    assert gw.group("kimi-k3")[1]["litellm_params"]["order"] == 1


# --- 停用一条线路，模型还在 --------------------------------------------------------


@pytest.mark.anyio
async def test_pausing_one_source_leaves_the_model_offered():
    # Another offered model: pausing the last one is refused.
    gw = _Gateway([_KIMI_CONFIG])
    service = _service(gw)
    await _two_runtime_sources(gw, service)
    first = gw.group("glm")[0]["model_info"]["id"]

    await service.set_source_blocked(
        handle="admin", name="glm", source_id=first, blocked=True
    )

    item = await _listed(service, "glm")
    assert item["offered"] is True
    assert [s["blocked"] for s in item["sources"]] == [True, False]
    picker = {m.id: m for m in await gw.catalogue().models()}
    assert picker["glm"].selectable is True

    # 两条都停了，模型才算停。
    second = gw.group("glm")[1]["model_info"]["id"]
    await service.set_source_blocked(
        handle="admin", name="glm", source_id=second, blocked=True
    )
    picker = {m.id: m for m in await gw.catalogue().models()}
    assert picker["glm"].selectable is False


@pytest.mark.anyio
async def test_a_serving_source_without_a_price_takes_the_model_off_the_menu():
    """目录只看第一行时，排在后面的一条没价线路会让落到它那里的流量按零计费。"""
    rows = [
        {
            "model_name": "m",
            "litellm_params": {
                "model": "anthropic/m",
                "input_cost_per_token": 1e-6,
                "output_cost_per_token": 2e-6,
            },
            "model_info": {"id": "a", "cheese_selectable": True},
        },
        {
            "model_name": "m",
            "litellm_params": {"model": "anthropic/m"},
            "model_info": {"id": "b", "cheese_selectable": True},
        },
    ]
    gw = _Gateway(rows)

    picker = {m.id: m for m in await gw.catalogue().models()}
    assert picker["m"].priced is False


# --- 免费：唯一合法的零价 -----------------------------------------------------------


@pytest.mark.anyio
async def test_a_free_source_is_written_with_explicit_zero_prices_and_stays_offered():
    gw = _Gateway()
    service = _service(gw)

    await service.add(
        handle="admin",
        payload=ModelCreate(
            name="cat-jam",
            protocol="openai-chat",
            upstream_model="cat-jam",
            api_base="https://opencode.ai/zen/go/v1",
            selectable=True,
            free=True,
        ),
    )

    (row,) = gw.group("cat-jam")
    assert row["model_info"]["cheese_free"] is True
    assert row["litellm_params"]["input_cost_per_token"] == 0
    assert row["litellm_params"]["output_cost_per_token"] == 0
    item = await _listed(service, "cat-jam")
    assert item["offered"] is True
    assert item["sources"][0]["free"] is True
    picker = {m.id: m for m in await gw.catalogue().models()}
    assert picker["cat-jam"].selectable and picker["cat-jam"].priced


@pytest.mark.anyio
async def test_a_zero_price_without_the_free_mark_is_refused():
    gw = _Gateway([_KIMI_CONFIG])
    service = _service(gw)

    for prices in ({"input": 0, "output": 1e-6}, {"input": 1e-6, "output": 0}):
        with pytest.raises(BadRequestError) as refused:
            await service.add_source(
                handle="admin",
                name="kimi-k3",
                payload=SourceCreate(**{**_GO, "prices": prices}),
            )
        assert "max_budget" in str(refused.value)
    assert gw.writes() == []


@pytest.mark.anyio
async def test_a_free_source_with_a_price_is_refused():
    gw = _Gateway([_KIMI_CONFIG])
    service = _service(gw)

    with pytest.raises(BadRequestError):
        await service.add_source(
            handle="admin",
            name="kimi-k3",
            payload=SourceCreate(**{**_GO, "free": True}),
        )
    assert gw.writes() == []


@pytest.mark.anyio
async def test_an_offered_model_takes_no_unpriced_source():
    gw = _Gateway([_KIMI_CONFIG])
    service = _service(gw)

    with pytest.raises(BadRequestError):
        await service.add_source(
            handle="admin",
            name="kimi-k3",
            payload=SourceCreate(**{**_GO, "prices": None}),
        )
    assert gw.writes() == []


@pytest.mark.anyio
async def test_unmarking_free_needs_real_prices():
    gw = _Gateway()
    service = _service(gw)
    await service.add(
        handle="admin",
        payload=ModelCreate(
            name="cat-jam", upstream_model="hosted_vllm/cat-jam", free=True
        ),
    )
    source = gw.group("cat-jam")[0]["model_info"]["id"]
    reset_cache()

    with pytest.raises(BadRequestError):
        await service.update_source(
            handle="admin",
            name="cat-jam",
            source_id=source,
            payload=SourceUpdate(free=False),
        )

    await service.update_source(
        handle="admin",
        name="cat-jam",
        source_id=source,
        payload=SourceUpdate(free=False, prices={"input": 1e-6, "output": 2e-6}),
    )
    (row,) = gw.group("cat-jam")
    assert row["model_info"]["cheese_free"] is False
    assert row["litellm_params"]["output_cost_per_token"] == 2e-6


# --- 请求头 ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "headers",
    [
        {"Authorization": "Bearer x"},
        {"x-api-key": "k"},
        {"X-Api-Key": "k"},
        {"user-agent": "a\r\nx-api-key: stolen"},
        {"user-agent": "a\nb"},
        {"user agent": "a"},
        {"user-agent": "a" * 513},
        {"": "a"},
        {"user-agent": "a", "User-Agent": "b"},
    ],
)
def test_headers_that_could_override_auth_or_split_a_request_are_refused(headers):
    with pytest.raises(ValidationError):
        SourceCreate(upstream_model="m", order=1, upstream_headers=headers)
    with pytest.raises(ValidationError):
        SourceUpdate(upstream_headers=headers)


@pytest.mark.parametrize(
    "name",
    ["Authorization", "x-api-key", "bad header", "x\r\ny", "X-Claude-Code-Session-Id"],
)
def test_the_session_header_follows_the_same_rule(name):
    with pytest.raises(ValidationError):
        SourceUpdate(session_header=name)


def test_ordinary_headers_are_accepted():
    body = SourceCreate(
        upstream_model="m",
        order=1,
        upstream_headers={"user-agent": "cheese-gateway/1.0", "x-title": "Cheese"},
        session_header="x-opencode-session",
    )
    assert body.upstream_headers == {
        "user-agent": "cheese-gateway/1.0",
        "x-title": "Cheese",
    }
    # 空串是「不再转会话」，与不传（别碰）是两件事。
    assert SourceUpdate(session_header="").session_header == ""
    assert SourceUpdate().session_header is None


@pytest.mark.anyio
async def test_clearing_a_sources_headers_reaches_the_gateway():
    """网关合并 model_info 时丢掉 null，所以「清空」必须以空值送达，而不是 null。"""
    gw = _Gateway([_KIMI_CONFIG])
    service = _service(gw)
    await service.add_source(
        handle="admin", name="kimi-k3", payload=SourceCreate(**_GO)
    )
    go = gw.group("kimi-k3")[1]["model_info"]["id"]
    reset_cache()

    await service.update_source(
        handle="admin",
        name="kimi-k3",
        source_id=go,
        payload=SourceUpdate(upstream_headers={}, session_header=""),
    )

    item = await _listed(service, "kimi-k3")
    go_item = next(s for s in item["sources"] if s["source_id"] == go)
    assert go_item["header_names"] == []
    assert go_item["session_header"] is None


@pytest.mark.anyio
async def test_header_values_and_keys_never_reach_the_audit_trail():
    gw = _Gateway([_KIMI_CONFIG])
    db = _StubDb()
    service = _service(gw, db)

    await service.add_source(
        handle="admin", name="kimi-k3", payload=SourceCreate(**_GO)
    )

    (row,) = db.added
    assert (row.action, row.result) == ("source.add", "ok")
    trail = json.dumps([row.before, row.after, row.detail])
    assert "cheese-gateway/1.0" not in trail
    assert "sk-go-secret" not in trail
    assert "user-agent" in trail  # 名字留着：审计要说得出改了哪个头


# --- 协议 -------------------------------------------------------------------------


def test_the_chat_protocol_is_not_routed_to_the_responses_api():
    """LiteLLM 把 `openai/` 前缀的 Messages 请求翻成 Responses API；只有
    chat/completions 的上游（中转站、OpenCode Go）走那条就是 404。"""
    assert upstream_string("anthropic", "glm-5.2") == "anthropic/glm-5.2"
    assert upstream_string("openai-responses", "gpt-5") == "openai/gpt-5"
    chat = upstream_string("openai-chat", "kimi-k3")
    assert chat.endswith("/kimi-k3") and not chat.startswith("openai/")
    # 不给协议就是完整的 LiteLLM 模型串，三种之外的提供方照样能填。
    assert upstream_string(None, "deepseek/deepseek-chat") == "deepseek/deepseek-chat"


# --- Chat 线路：对话中间的 system 消息整组都并进第一条用户消息 --------------------


@pytest.mark.anyio
async def test_a_chat_source_marks_every_row_of_its_model():
    """Messages 翻成 chat/completions 会丢掉对话中间的 system 消息；请求可能落到组里
    任何一行，目录只读第一行，所以整组都得标
    `supports_mid_conversation_system: false`。"""
    gw = _Gateway()
    service = _service(gw)
    await _two_runtime_sources(gw, service)

    infos = [r["model_info"] for r in gw.group("glm")]
    assert [i.get("supports_mid_conversation_system") for i in infos] == [False, False]

    # 没提这一项的模型编辑不能把它冲回去；明说要开就拒。
    await service.update(
        handle="admin",
        name="glm",
        payload=ModelUpdate(capabilities={"reasoning": True}),
    )
    infos = [r["model_info"] for r in gw.group("glm")]
    assert [i.get("supports_mid_conversation_system") for i in infos] == [False, False]
    with pytest.raises(BadRequestError):
        await service.update(
            handle="admin",
            name="glm",
            payload=ModelUpdate(
                capabilities={"reasoning": True, "mid_conversation_system": True}
            ),
        )


@pytest.mark.anyio
async def test_a_chat_source_beside_an_unmarked_config_row_is_refused():
    """config.yaml 的行改不了；它没标，加一条 Chat 线路就会让组里两种说法并存。"""
    config = json.loads(json.dumps(_KIMI_CONFIG))
    del config["model_info"]["supports_mid_conversation_system"]
    gw = _Gateway([config])
    service = _service(gw)

    with pytest.raises(BadRequestError) as refused:
        await service.add_source(
            handle="admin", name="kimi-k3", payload=SourceCreate(**_GO)
        )
    assert "config.yaml" in str(refused.value)
    assert gw.writes() == []

    # 原生 Messages 的线路不受影响。
    await service.add_source(
        handle="admin",
        name="kimi-k3",
        payload=SourceCreate(
            **{
                **_GO,
                "protocol": "anthropic",
                "api_base": "https://opencode.ai/inference/go",
            }
        ),
    )
    assert len(gw.group("kimi-k3")) == 2


@pytest.mark.anyio
async def test_a_messages_base_with_v1_is_not_sent_twice():
    """LiteLLM 给原生 Messages 的地址自己拼 `/v1/messages`；填了 `/v1` 就成了
    `/v1/v1/messages`。"""
    gw = _Gateway([_KIMI_CONFIG])
    service = _service(gw)

    await service.add_source(
        handle="admin",
        name="kimi-k3",
        payload=SourceCreate(
            **{
                **_GO,
                "protocol": "anthropic",
                "api_base": "https://opencode.ai/inference/go/v1/",
            }
        ),
    )

    assert gw.group("kimi-k3")[1]["litellm_params"]["api_base"] == (
        "https://opencode.ai/inference/go"
    )


# --- 加线路默认是兜底，停着的模型加线路也还停着 -----------------------------------

_PAID = {"prices": {"input": 1e-6, "output": 2e-6}}


@pytest.mark.anyio
async def test_a_new_source_does_not_take_the_traffic_unless_asked():
    """config 那一行没有 order：带 order 的新线路一来就是首选（LiteLLM 只在带 order
    的都不可用时才轮到不带的）。不说「让它当首选」就拒，一行都不写。"""
    gw = _Gateway([_KIMI_CONFIG])
    service = _service(gw)

    with pytest.raises(BadRequestError):
        await service.add_source(
            handle="admin",
            name="kimi-k3",
            payload=SourceCreate(**{**_GO, "make_primary": False}),
        )
    assert gw.writes() == []


@pytest.mark.anyio
async def test_a_source_ordered_after_the_others_needs_no_say_so():
    gw = _Gateway()
    service = _service(gw)
    await _two_runtime_sources(gw, service)  # 优先级 1 与 2

    await service.add_source(
        handle="admin",
        name="glm",
        payload=SourceCreate(**_PAID, upstream_model="anthropic/glm-5.2-b", order=3),
    )
    assert len(gw.group("glm")) == 3

    reset_cache()
    with pytest.raises(BadRequestError):
        await service.add_source(
            handle="admin",
            name="glm",
            payload=SourceCreate(
                **_PAID, upstream_model="anthropic/glm-5.2-c", order=1
            ),
        )


@pytest.mark.anyio
async def test_a_source_added_to_a_paused_model_stays_paused():
    # Another offered model: pausing the last one is refused.
    gw = _Gateway([_KIMI_CONFIG])
    service = _service(gw)
    await _two_runtime_sources(gw, service)
    await service.set_blocked(handle="admin", name="glm", blocked=True)
    reset_cache()

    await service.add_source(
        handle="admin",
        name="glm",
        payload=SourceCreate(**_PAID, upstream_model="anthropic/glm-5.2-b", order=3),
    )

    assert [r["model_info"].get("blocked") for r in gw.group("glm")] == [
        True,
        True,
        True,
    ]
    picker = {m.id: m for m in await gw.catalogue().models()}
    assert picker["glm"].selectable is False


@pytest.mark.anyio
async def test_marking_a_model_as_needing_approval_marks_every_source():
    """The catalogue reads the first row; every row must say the same."""
    gw = _Gateway()
    service = _service(gw)
    await _two_runtime_sources(gw, service)

    await service.update(handle="admin", name="glm", payload=ModelUpdate(overseas=True))

    infos = [r["model_info"] for r in gw.group("glm")]
    assert [i.get("cheese_overseas") for i in infos] == [True, True]


def _argon_like_kimi() -> dict:
    config = json.loads(json.dumps(_KIMI_CONFIG))
    config["model_info"].update(
        cheese_overseas=True, cheese_spend_cap_usd=1000, cheese_no_explicit_cache=True
    )
    return config


@pytest.mark.anyio
async def test_a_source_on_another_upstream_needs_approval_but_no_cap_or_cache_rule():
    """Approval is the model's; a spend cap and the cache-mark strip are what one
    upstream model needs, so a source on another upstream does not take them."""
    gw = _Gateway([_argon_like_kimi()])
    service = _service(gw)

    await service.add_source(
        handle="admin", name="kimi-k3", payload=SourceCreate(**_GO)
    )

    _, new_row = gw.group("kimi-k3")
    assert new_row["model_info"]["cheese_overseas"] is True
    assert "cheese_spend_cap_usd" not in new_row["model_info"]
    assert "cheese_no_explicit_cache" not in new_row["model_info"]


@pytest.mark.anyio
async def test_a_source_on_the_same_upstream_keeps_its_cap_and_cache_rule():
    """Another key for the same upstream model must not escape the cap, nor send
    the cache marks that upstream refuses."""
    gw = _Gateway([_argon_like_kimi()])
    service = _service(gw)
    same = {
        "protocol": "anthropic",
        "upstream_model": "kimi-k3",
        "api_base": "https://api.moonshot.cn/anthropic",
        "api_key": "sk-second-key",
        "order": 2,
        "prices": {"input": 2.8e-6, "output": 1.4e-5},
        "make_primary": True,
    }

    await service.add_source(
        handle="admin", name="kimi-k3", payload=SourceCreate(**same)
    )

    config_row, new_row = gw.group("kimi-k3")
    assert new_row["litellm_params"]["model"] == config_row["litellm_params"]["model"]
    assert new_row["model_info"]["cheese_spend_cap_usd"] == 1000
    assert new_row["model_info"]["cheese_no_explicit_cache"] is True


# --- Spend cap: one per upstream model, set from the page ----------------------

_ARGON = "gemini/gemini-4-argon"


async def _argon_rows(service: GatewayModelsService) -> None:
    """Two runtime models on the argon upstream (one with a second source), and
    one source on another upstream."""
    for name in ("argon", "argon-b"):
        await service.add(
            handle="admin",
            payload=ModelCreate(name=name, upstream_model=_ARGON, **_PAID),
        )
    await service.add_source(
        handle="admin",
        name="argon",
        payload=SourceCreate(
            **_PAID, upstream_model=_ARGON, order=1, make_primary=True
        ),
    )
    await service.add_source(
        handle="admin",
        name="argon",
        payload=SourceCreate(**_PAID, upstream_model="gemini/other", order=2),
    )
    reset_cache()


def _caps(gw: _Gateway) -> list[object]:
    return [r["model_info"].get("cheese_spend_cap_usd") for r in gw.rows]


async def _cap_on_page(service: GatewayModelsService, name: str) -> list[tuple]:
    item = await _listed(service, name)
    return [(s["upstream_raw"], s["spend_cap_usd"]) for s in item["sources"]]


async def _set_cap(
    service: GatewayModelsService, name: str, source_id: str, cap: float | None
) -> None:
    await service.update_source(
        handle="admin",
        name=name,
        source_id=source_id,
        payload=SourceUpdate(spend_cap_usd=cap),
    )
    reset_cache()


@pytest.mark.anyio
async def test_a_cap_set_on_one_source_holds_on_every_source_of_its_upstream():
    """spend_cap.py counts a cap against the upstream's whole bill, so a second
    key or model name on the same upstream must not run under another cap."""
    gw = _Gateway()
    db = _StubDb()
    service = _service(gw, db)
    await _argon_rows(service)

    await _set_cap(service, "argon", gw.group("argon")[0]["model_info"]["id"], 2500)

    on_argon = [r for r in gw.rows if r["litellm_params"]["model"] == _ARGON]
    assert len(on_argon) == 3
    assert [r["model_info"]["cheese_spend_cap_usd"] for r in on_argon] == [2500] * 3
    other = next(r for r in gw.rows if r["litellm_params"]["model"] == "gemini/other")
    assert "cheese_spend_cap_usd" not in other["model_info"]
    assert sorted(await _cap_on_page(service, "argon")) == [
        (_ARGON, 2500.0),
        (_ARGON, 2500.0),
        ("gemini/other", None),
    ]
    audit = db.added[-1]
    assert (audit.action, audit.result) == ("source.update", "ok")
    assert audit.after["spend_cap_usd"] == 2500
    assert len(audit.after["spend_cap_also_on"]) == 2


@pytest.mark.anyio
async def test_a_capped_source_shows_what_its_upstream_has_cost_all_time():
    """Used is the ledger's all-time total for the upstream, the number
    spend_cap.py refuses on; a source without a cap shows none."""
    gw = _Gateway()
    service = _service(gw)
    await _argon_rows(service)
    await _set_cap(service, "argon", gw.group("argon")[0]["model_info"]["id"], 2500)
    gw.ledger = {_ARGON: 2100.0, "gemini/other": 5.0}

    item = await _listed(service, "argon")

    assert sorted(
        (s["upstream_raw"], s["spend_cap_used_usd"]) for s in item["sources"]
    ) == [(_ARGON, 2100.0), (_ARGON, 2100.0), ("gemini/other", None)]
    assert "2000-01-01" in gw.usage_starts


@pytest.mark.anyio
async def test_a_cap_on_an_upstream_with_no_calls_has_used_nothing():
    gw = _Gateway()
    service = _service(gw)
    await _argon_rows(service)
    await _set_cap(service, "argon", gw.group("argon")[0]["model_info"]["id"], 2500)

    item = await _listed(service, "argon-b")

    assert [s["spend_cap_used_usd"] for s in item["sources"]] == [0.0]


@pytest.mark.anyio
async def test_without_a_cap_the_all_time_ledger_is_not_read():
    gw = _Gateway()
    service = _service(gw)
    await _argon_rows(service)
    gw.usage_starts.clear()

    item = await _listed(service, "argon")

    assert {s["spend_cap_used_usd"] for s in item["sources"]} == {None}
    assert "2000-01-01" not in gw.usage_starts


@pytest.mark.anyio
async def test_clearing_the_cap_lifts_it_on_every_source_of_its_upstream():
    gw = _Gateway()
    service = _service(gw)
    await _argon_rows(service)
    edited = gw.group("argon")[0]["model_info"]["id"]
    await _set_cap(service, "argon", edited, 2500)

    await _set_cap(service, "argon", edited, None)

    assert [cap for _, cap in await _cap_on_page(service, "argon")] == [None] * 3
    assert [cap for _, cap in await _cap_on_page(service, "argon-b")] == [None]


@pytest.mark.anyio
async def test_an_edit_that_does_not_name_the_cap_leaves_it():
    gw = _Gateway()
    service = _service(gw)
    await _argon_rows(service)
    edited = gw.group("argon")[0]["model_info"]["id"]
    await _set_cap(service, "argon", edited, 2500)
    before = _caps(gw)

    await service.update_source(
        handle="admin", name="argon", source_id=edited, payload=SourceUpdate(order=3)
    )

    assert _caps(gw) == before


@pytest.mark.anyio
async def test_a_cap_that_disagrees_with_a_config_row_on_its_upstream_is_refused():
    """The config.yaml row cannot be rewritten from the page; letting the two
    differ would refuse the same bill at two totals."""
    gw = _Gateway([_argon_like_kimi()])
    service = _service(gw)
    await service.add_source(
        handle="admin",
        name="kimi-k3",
        payload=SourceCreate(
            protocol="anthropic",
            upstream_model="kimi-k3",
            api_base="https://api.moonshot.cn/anthropic",
            order=1,
            prices={"input": 2.8e-6, "output": 1.4e-5},
            make_primary=True,
        ),
    )
    runtime = gw.group("kimi-k3")[1]["model_info"]["id"]
    reset_cache()
    before = len(gw.writes())

    with pytest.raises(BadRequestError) as refused:
        await service.update_source(
            handle="admin",
            name="kimi-k3",
            source_id=runtime,
            payload=SourceUpdate(spend_cap_usd=1500, order=4),
        )

    assert "config.yaml" in str(refused.value)
    assert len(gw.writes()) == before
    # The config row's own figure is accepted: nothing disagrees.
    await _set_cap(service, "kimi-k3", runtime, 1000)
    assert _caps(gw) == [1000, 1000]


@pytest.mark.parametrize("cap", [0, -5, float("inf")])
def test_a_cap_must_be_a_positive_amount(cap):
    with pytest.raises(ValidationError):
        SourceUpdate(spend_cap_usd=cap)


@pytest.mark.parametrize(
    "field", ["input", "output", "cache_read", "cache_creation", "cache_creation_1h"]
)
@pytest.mark.parametrize("amount", [-1e-6, float("nan"), float("inf"), -float("inf")])
@pytest.mark.parametrize(
    "body,required",
    [
        (ModelCreate, {"name": "model", "upstream_model": "anthropic/model"}),
        (ModelUpdate, {}),
        (SourceCreate, {"upstream_model": "anthropic/model", "order": 2}),
        (SourceUpdate, {}),
    ],
)
def test_model_and_source_writes_refuse_invalid_token_prices(
    field, amount, body, required
):
    """Token prices cannot subtract spend or make the bill non-finite."""
    prices = {"input": 1e-6, "output": 2e-6, field: amount}
    with pytest.raises(ValidationError):
        body(**required, prices=prices)


# --- What config.yaml's check held the file to, held at the write -------------


@pytest.mark.anyio
async def test_the_last_offered_model_cannot_be_paused_deleted_or_unlisted():
    """With no offered model, every agent settings page is bare."""
    gw = _Gateway()
    service = _service(gw)
    await _two_runtime_sources(gw, service)
    first, second = (r["model_info"]["id"] for r in gw.group("glm"))
    await service.set_source_blocked(
        handle="admin", name="glm", source_id=first, blocked=True
    )
    reset_cache()
    before = len(gw.writes())

    for attempt in (
        lambda: service.set_blocked(handle="admin", name="glm", blocked=True),
        lambda: service.delete(handle="admin", name="glm"),
        lambda: service.update(
            handle="admin", name="glm", payload=ModelUpdate(selectable=False)
        ),
        lambda: service.set_source_blocked(
            handle="admin", name="glm", source_id=second, blocked=True
        ),
        lambda: service.delete_source(handle="admin", name="glm", source_id=second),
    ):
        with pytest.raises(BadRequestError):
            await attempt()
    assert len(gw.writes()) == before

    # With another offered model the same pause goes through.
    gw.rows.append(json.loads(json.dumps(_KIMI_CONFIG)))
    reset_cache()
    await service.set_blocked(handle="admin", name="glm", blocked=True)


_CLAUDE_PRICES = {
    "input": 2e-6,
    "output": 1e-5,
    "cache_read": 2e-7,
    "cache_creation": 2.5e-6,
    "cache_creation_1h": 4e-6,
}


async def _claude_price_row(service: GatewayModelsService) -> str:
    """A subscription model's price row: unlisted, paused, no credential."""
    await service.add(
        handle="admin",
        payload=ModelCreate(
            name="claude-sonnet-5",
            protocol="anthropic",
            upstream_model="claude-sonnet-5",
            prices=_CLAUDE_PRICES,
        ),
    )
    reset_cache()
    await service.set_blocked(handle="admin", name="claude-sonnet-5", blocked=True)
    reset_cache()
    return (await _listed(service, "claude-sonnet-5"))["sources"][0]["source_id"]


@pytest.mark.anyio
async def test_the_one_hour_cache_write_price_is_read_and_edited_on_the_page():
    """Subscription billing reads it (`usage.pricing.Rate.cache_write_1h`)."""
    gw = _Gateway([_KIMI_CONFIG])
    service = _service(gw)
    source_id = await _claude_price_row(service)

    (row,) = gw.group("claude-sonnet-5")
    assert row["litellm_params"]["cache_creation_input_token_cost_above_1hr"] == 4e-6
    assert "api_key" not in row["litellm_params"]
    item = await _listed(service, "claude-sonnet-5")
    assert item["sources"][0]["prices"]["cache_creation_1h"] == 4e-6

    await service.update_source(
        handle="admin",
        name="claude-sonnet-5",
        source_id=source_id,
        payload=SourceUpdate(prices={**_CLAUDE_PRICES, "cache_creation_1h": 8e-6}),
    )
    assert (
        gw.group("claude-sonnet-5")[0]["litellm_params"][
            "cache_creation_input_token_cost_above_1hr"
        ]
        == 8e-6
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "prices",
    [
        {**_CLAUDE_PRICES, "output": None},
        {**_CLAUDE_PRICES, "cache_creation_1h": None},
        {"input": 2e-6, "output": 1e-5},
    ],
)
async def test_a_price_a_row_has_can_be_changed_but_never_cleared(prices):
    """Paused and unlisted or not, every price a row has is what usage under its
    name is billed by; an edit that empties one is refused, nothing written."""
    gw = _Gateway([_KIMI_CONFIG])
    service = _service(gw)
    source_id = await _claude_price_row(service)
    before = len(gw.writes())

    with pytest.raises(BadRequestError):
        await service.update_source(
            handle="admin",
            name="claude-sonnet-5",
            source_id=source_id,
            payload=SourceUpdate(prices=prices),
        )
    assert len(gw.writes()) == before


@pytest.mark.anyio
async def test_a_source_without_both_prices_is_refused_on_any_model():
    """Not only on an offered model: an unlisted model is billed by its prices too."""
    gw = _Gateway([_KIMI_CONFIG])
    service = _service(gw)
    await _claude_price_row(service)
    before = len(gw.writes())

    with pytest.raises(BadRequestError):
        await service.add_source(
            handle="admin",
            name="claude-sonnet-5",
            payload=SourceCreate(
                protocol="anthropic",
                upstream_model="claude-sonnet-5",
                order=5,
                prices={"input": 2e-6},
            ),
        )
    assert len(gw.writes()) == before


@pytest.mark.anyio
async def test_a_transcription_row_priced_on_input_only_is_kept_and_stays_editable():
    """Speech to text charges for the audio in and nothing out: one zero is a
    price. Saving the row with its prices as they are (the page sends them on
    every save) rewrites nothing and is not refused."""
    gw = _Gateway([_KIMI_CONFIG])
    service = _service(gw)
    await service.add(
        handle="admin",
        payload=ModelCreate(
            name="asr",
            upstream_model="qwen-asr/flash",
            prices={"input": 1.2394e-6, "output": 0},
        ),
    )
    reset_cache()
    source = gw.group("asr")[0]["model_info"]["id"]
    before = len(gw.writes())

    await service.update_source(
        handle="admin",
        name="asr",
        source_id=source,
        payload=SourceUpdate(
            order=4, free=False, prices={"input": 1.2394e-6 * (1 + 1e-12), "output": 0}
        ),
    )

    (sent,) = [json.loads(r.content) for r in gw.writes()[before:]]
    assert sent["litellm_params"] == {"order": 4}
    with pytest.raises(BadRequestError):
        await service.add(
            handle="admin",
            payload=ModelCreate(
                name="zeros", upstream_model="x/y", prices={"input": 0, "output": 0}
            ),
        )


@pytest.mark.anyio
async def test_a_row_priced_before_todays_rules_takes_an_unrelated_edit():
    gw = _Gateway(
        [
            _KIMI_CONFIG,
            {
                "model_name": "legacy",
                "litellm_params": {"model": "anthropic/legacy"},
                "model_info": {"id": "rt-legacy", "db_model": True},
            },
        ]
    )
    service = _service(gw)

    await service.update_source(
        handle="admin",
        name="legacy",
        source_id="rt-legacy",
        payload=SourceUpdate(order=2, free=False, prices={"input": None}),
    )

    assert gw.group("legacy")[0]["litellm_params"]["order"] == 2
