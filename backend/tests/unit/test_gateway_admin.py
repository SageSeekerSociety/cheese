"""管理端网关客户端（会抛的那一半）与服务层不变式。

`LlmGateway` 与 `GatewayAdmin` 是两种失败语义：回合路径那个吞掉错误、返回 None，
一条 turn 不该因为网关抽风就失败；管理路径这个必须抛，因为人点了「新增模型」，失
败了就得在页面上看到原因，一个沉默的 None 只会变成一片空白。这个文件钉住的是它抛
出来的东西长什么样，以及服务层在把请求送出去之前先判死了什么。

用 `httpx.MockTransport` 把网关换成一个按路径答话的桩，不碰真网络。三件事：

* **两条不变式在发请求之前就判死**：上架（`cheese_selectable`）必须同时有输入与输
  出两个单价；`config.yaml` 来源的模型一律拒写。判死之后 `/model/new`、
  `/model/update` 一次都不该被打到 —— 打了就说明拦晚了。
* **失败分两类**：连不上/超时是 `GatewayUnreachable`（网关没答话），4xx/5xx 是
  `GatewayRefused`（网关答了、拒绝了），后者要把网关自己的原话（`detail.error`）
  带给页面。
* **请求体的形状**：字段名与契约一致，`api_key` 只在请求体里出现，缺的单价不补 0。

服务层那两条不变式的判死发生在落库之前，所以这里用一个不连库的假会话 —— 这一层跑
在没有 Postgres 的机器上，而用例要验的正是「还没碰任何外部东西就先拒绝了」。
"""

import json

import httpx
import pytest

from app.core.errors import BadRequestError
from app.domain.agent import gateway_catalog
from app.domain.agent.gateway_admin import (
    GatewayAdmin,
    GatewayRefused,
    GatewayUnreachable,
)
from app.domain.agent.gateway_models import GatewayModelsService, reset_cache
from app.domain.agent.schemas import ModelCreate, ModelUpdate


class _StubDb:
    """服务层写审计那一小段够用的假会话：记下加了什么，flush/commit 空转。

    不连库是刻意的：两条不变式都在落库之前就判死了，真会话只会把「先拒绝」和
    「数据库」两件事混在一起，而前者才是这些用例问的。
    """

    def __init__(self) -> None:
        self.added: list[object] = []

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        return None


def _row(
    name: str,
    upstream: str,
    *,
    db_model: bool | None = None,
    selectable: bool = True,
    label: str | None = None,
    input_cost: float | None = None,
    output_cost: float | None = None,
) -> dict:
    """`/model/info` 的一行，按契约 §0 的形状拼。`db_model` 缺省即 config 模型。"""
    info: dict = {"id": f"{name}-id", "cheese_selectable": selectable}
    if label is not None:
        info["cheese_label"] = label
    if db_model is not None:
        info["db_model"] = db_model
    params: dict = {"model": upstream}
    if input_cost is not None:
        params["input_cost_per_token"] = input_cost
    if output_cost is not None:
        params["output_cost_per_token"] = output_cost
    return {"model_name": name, "litellm_params": params, "model_info": info}


class _Gateway:
    """一台可编排的假网关：按路径答话，并把收到的每一个请求记下来。

    只实现管理客户端真正会用的那几条路，其它一律 404 —— 实现要是走了一条没预料到
    的路，用例会以 `GatewayRefused` 立刻失败，而不是静默拿到空数据。
    """

    def __init__(
        self,
        *,
        models: list[dict] | None = None,
        activity: dict | None = None,
        new_id: str = "mid-1",
    ) -> None:
        self.models = models or []
        self.activity = activity or {"results": [], "metadata": {}}
        self.new_id = new_id
        self.requests: list[httpx.Request] = []

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/health/readiness":
            return httpx.Response(200, json={"status": "healthy"})
        if path == "/model/info":
            return httpx.Response(200, json={"data": self.models})
        if path == "/user/daily/activity/aggregated":
            return httpx.Response(200, json=self.activity)
        if path == "/model/new":
            return httpx.Response(200, json={"model_id": self.new_id})
        if (
            request.method == "PATCH"
            and path.startswith("/model/")
            and path.endswith("/update")
        ):
            return httpx.Response(200, json={})
        if path in (
            "/model/delete",
            "/model/block",
            "/model/unblock",
            "/key/update",
        ):
            return httpx.Response(200, json={})
        return httpx.Response(404, json={"detail": {"error": f"stub has no {path}"}})

    def admin(self) -> GatewayAdmin:
        return GatewayAdmin(
            "http://gw", "mk", transport=httpx.MockTransport(self._handle)
        )

    def paths(self) -> list[str]:
        return [r.url.path for r in self.requests]


@pytest.fixture(autouse=True)
def _clean_module_state(monkeypatch):
    """每个用例从没有缓存开始，并让写后的目录刷新不发真网络请求。

    缓存是模块级的，一个用例留下的答案会渗进下一个；刷新则要真去问网关
    （`gateway_catalog.refresh`），这一层没有网关，换成记名即可。
    """
    reset_cache()
    refreshed: list[object] = []

    async def _refresh(gateway):
        refreshed.append(gateway)
        return True

    monkeypatch.setattr(gateway_catalog, "refresh", _refresh)
    yield refreshed
    reset_cache()


# --- 不变式：判死发生在请求发出去之前 ----------------------------------------


@pytest.mark.anyio
async def test_a_half_priced_model_is_refused_as_a_menu_item():
    """只有一半单价和完全没有单价一样坏：没定价的那一半流量按 0 计费，项目
    `max_budget` 这道刹车会静默失效。判死在服务层，`/model/new` 一次都不该被打到。"""
    gw = _Gateway()
    service = GatewayModelsService(_StubDb(), gw.admin())

    with pytest.raises(BadRequestError) as refused:
        await service.add(
            handle="admin",
            payload=ModelCreate(
                name="half-priced",
                upstream_model="anthropic/x",
                selectable=True,
                prices={"input": 1e-6},  # 缺 output
            ),
        )

    assert "刹车" in str(refused.value)  # 那句理由要给到人
    assert gw.paths() == []  # 还没问网关就先拒了


@pytest.mark.anyio
async def test_an_unpriced_model_may_exist_so_long_as_it_is_not_offered():
    """没价本身不是错 —— 错的是「没价还上架」。不上架的模型照常建。"""
    gw = _Gateway()
    service = GatewayModelsService(_StubDb(), gw.admin())

    out = await service.add(
        handle="admin",
        payload=ModelCreate(
            name="quiet", upstream_model="anthropic/x", selectable=False
        ),
    )

    assert "/model/new" in gw.paths()
    assert out["model"]["name"] == "quiet"


@pytest.mark.anyio
async def test_a_config_model_is_refused_before_the_gateway_is_asked_to_write():
    """`config.yaml` 声明的模型改不了。网关自己也会拒，但那句话是说给我们听的；
    先把「要改就改 `deploy/gateway/config.yaml` 并发布网关」说清楚，别把这个错丢给
    用户，也别白打一次写请求。"""
    gw = _Gateway(models=[_row("declared", "anthropic/x")])  # 无 db_model ⇒ config
    service = GatewayModelsService(_StubDb(), gw.admin())

    for verb, call in (
        (
            "改",
            lambda: service.update(
                handle="admin", name="declared", payload=ModelUpdate(label="x")
            ),
        ),
        ("删", lambda: service.delete(handle="admin", name="declared")),
        (
            "停用",
            lambda: service.set_blocked(handle="admin", name="declared", blocked=True),
        ),
    ):
        with pytest.raises(BadRequestError) as refused:
            await call()
        assert "config.yaml" in str(refused.value), verb

    # 一条写请求都不该发出去 —— 连 PATCH 那条新路也不许露头。
    assert all(
        p not in ("/model/delete", "/model/block")
        and not (p.startswith("/model/") and p.endswith("/update"))
        for p in gw.paths()
    )


# --- 失败分类：连不上与「被拒绝」是两件事 ------------------------------------


@pytest.mark.anyio
async def test_an_unreachable_gateway_raises_instead_of_answering_empty():
    """空目录和「这次没读到」在对人说的话里正相反：前者是「本部署没有模型」，
    后者是「再问一次」。连不上必须抛，不能返回一张空表。"""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    admin = GatewayAdmin("http://gw", "mk", transport=httpx.MockTransport(handler))
    with pytest.raises(GatewayUnreachable) as err:
        await admin.models()
    assert err.value.status is None  # 没答话，就没有 HTTP 码


@pytest.mark.anyio
async def test_a_refusal_carries_the_gateways_own_words():
    """网关答了、而且拒绝了：把它的 `detail.error` 原样带给页面 —— 页面要显示的就
    是那句话，不是「502」这个数字。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400, json={"detail": {"error": "Can't edit model. Model in config."}}
        )

    admin = GatewayAdmin("http://gw", "mk", transport=httpx.MockTransport(handler))
    with pytest.raises(GatewayRefused) as err:
        await admin.set_blocked("cfg-1", True)
    assert err.value.status == 400
    assert str(err.value) == "Can't edit model. Model in config."
    assert not isinstance(err.value, GatewayUnreachable)


@pytest.mark.anyio
async def test_a_proxy_exception_message_is_used_when_there_is_no_detail_key():
    """真网关的 ``ProxyException`` 走的是另一条路：顶层就是 ``{"error": {"message":
    ..., "type": "auth_error"}}``，**没有 ``detail`` 键**。少认这一支，页面就会看到
    整坨 dict 的 repr，而人只想看到那一句原因。"""
    original = "Only proxy admins can change a model's blocked flag."

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={
                "error": {
                    "message": original,
                    "type": "auth_error",
                    "param": "blocked",
                    "code": "403",
                }
            },
        )

    admin = GatewayAdmin("http://gw", "mk", transport=httpx.MockTransport(handler))
    with pytest.raises(GatewayRefused) as err:
        await admin.set_blocked("runtime-id", True)
    assert (err.value.status, str(err.value)) == (403, original)


@pytest.mark.anyio
async def test_an_error_dict_without_a_message_falls_back_to_its_string_form():
    """``error`` 不是 ``{message}`` 这个形状时也不能把整坨 dict 当哑巴丢掉：退回它的
    字符串化，总比只剩一个状态码强。"""
    handler = lambda request: httpx.Response(  # noqa: E731 — 一行桩
        500, json={"error": "upstream exploded"}
    )

    admin = GatewayAdmin("http://gw", "mk", transport=httpx.MockTransport(handler))
    with pytest.raises(GatewayRefused) as err:
        await admin.models()
    assert (err.value.status, str(err.value)) == (500, "upstream exploded")


@pytest.mark.anyio
async def test_a_plain_detail_string_is_used_when_there_is_no_error_field():
    """不是每个失败体都长 LiteLLM 那个 `{detail:{error}}` 的形状；拿不到 `error`
    就退到 `detail` 本身，再拿不到退状态码 —— 页面永远有一句话可读。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "upstream boom"})

    admin = GatewayAdmin("http://gw", "mk", transport=httpx.MockTransport(handler))
    with pytest.raises(GatewayRefused) as err:
        await admin.models()
    assert (err.value.status, str(err.value)) == (500, "upstream boom")


# --- 读：用量按模型、按项目各切一份 ------------------------------------------


@pytest.mark.anyio
async def test_usage_reads_models_by_group_and_projects_by_key():
    """按模型只用 `breakdown.model_groups`（键 = `model_name`），按项目用
    `breakdown.api_keys`（键 = key 的 sha256）；平台级总量信网关自己的 `metadata`。
    `breakdown.models` 用的是上游名，取它整张表就对不上号 —— 它必须被无视。"""
    activity = {
        "results": [
            {
                "date": "2026-09-16",
                "metrics": {"spend": 1.0, "api_requests": 10, "failed_requests": 1},
                "breakdown": {
                    "model_groups": {
                        "m-a": {
                            "metrics": {
                                "spend": 0.6,
                                "api_requests": 6,
                                "total_tokens": 90,
                            }
                        },
                        "m-b": {
                            "metrics": {
                                "spend": 0.4,
                                "api_requests": 4,
                                "total_tokens": 60,
                            }
                        },
                    },
                    "models": {
                        "anthropic/upstream-x": {
                            "metrics": {
                                "spend": 9.9,
                                "api_requests": 99,
                                "total_tokens": 999,
                            }
                        }
                    },
                    "api_keys": {
                        "hash-1": {
                            "metrics": {
                                "spend": 0.6,
                                "api_requests": 6,
                                "total_tokens": 90,
                            }
                        }
                    },
                },
            },
            {
                "date": "2026-09-17",
                "metrics": {"spend": 0.2, "api_requests": 2, "failed_requests": 0},
                "breakdown": {
                    "model_groups": {
                        "m-a": {
                            "metrics": {
                                "spend": 0.2,
                                "api_requests": 2,
                                "total_tokens": 40,
                            }
                        }
                    },
                    "api_keys": {
                        "hash-1": {
                            "metrics": {
                                "spend": 0.2,
                                "api_requests": 2,
                                "total_tokens": 40,
                            }
                        }
                    },
                },
            },
        ],
        "metadata": {
            "total_spend": 1.2,
            "total_api_requests": 12,
            "total_failed_requests": 1,
            "total_tokens": 130,
        },
    }
    gw = _Gateway(activity=activity)

    window = await gw.admin().usage("2026-09-16", "2026-09-17")

    assert (window.start_date, window.end_date) == ("2026-09-16", "2026-09-17")
    # 平台级总量是网关的 metadata，不是把逐日重加一遍。
    assert window.totals.spend_usd == pytest.approx(1.2)
    assert window.totals.requests == 12
    # 按模型：只认 model_groups，上游名那一组不许露头。
    assert set(window.by_model) == {"m-a", "m-b"}
    assert window.by_model["m-a"].requests == 8
    assert window.by_model["m-a"].total_tokens == 130
    # 按项目：键就是 key hash。
    assert window.by_key["hash-1"].requests == 8
    # 逐日明细里也带一份按模型。
    assert window.daily[0]["date"] == "2026-09-16"
    assert window.daily[0]["by_model"]["m-a"].requests == 6


# --- 写：请求体里只有网关认识的字段，凭据只在请求体里 --------------------------


@pytest.mark.anyio
async def test_a_new_model_is_sent_with_only_the_fields_the_gateway_understands():
    gw = _Gateway()
    service = GatewayModelsService(_StubDb(), gw.admin())

    await service.add(
        handle="admin",
        payload=ModelCreate(
            name="my-model",
            upstream_model="anthropic/glm-4.7",
            api_base="https://open.bigmodel.cn/api/anthropic",
            api_key="sk-secret",
            label="GLM-4.7",
            selectable=True,
            prices={"input": 1e-6, "output": 2e-6, "cache_read": None},
            capabilities={"reasoning": True, "vision": False},
        ),
    )

    sent = next(r for r in gw.requests if r.url.path == "/model/new")
    body = json.loads(sent.content)
    assert set(body) == {"model_name", "litellm_params", "model_info"}
    params = body["litellm_params"]
    assert set(params) == {
        "model",
        "api_base",
        "api_key",
        "input_cost_per_token",
        "output_cost_per_token",
    }
    # 显式给了 null 的单价不能当 0 发下去：0 是「免费」，null 是「没定价」。
    assert "cache_read_input_token_cost" not in params
    info = body["model_info"]
    assert info["cheese_selectable"] is True
    assert info["cheese_label"] == "GLM-4.7"
    assert info["supports_reasoning"] is True
    assert info["supports_vision"] is False


@pytest.mark.anyio
async def test_an_edit_without_a_key_does_not_wipe_the_upstream_credential():
    """编辑界面不回显上游凭据，「没填 key」是常态。它必须意味着「别碰」，而不是
    「清空」—— 清空会让这条模型下一次调用直接失败。改走 PATCH 后靠网关的合并语义
    兑现：字段不出现，库里旧值就不动。"""
    gw = _Gateway(
        models=[
            _row(
                "runtime",
                "anthropic/x",
                db_model=True,
                selectable=False,
                input_cost=1e-6,
                output_cost=2e-6,
            )
        ]
    )
    service = GatewayModelsService(_StubDb(), gw.admin())

    await service.update(
        handle="admin", name="runtime", payload=ModelUpdate(label="换个名字")
    )

    # model_id 走路径，不再塞进 model_info.id —— 它已经不再承担定位职责。
    sent = next(
        r for r in gw.requests if r.method == "PATCH" and r.url.path.endswith("/update")
    )
    assert sent.url.path == "/model/runtime-id/update"
    body = json.loads(sent.content)
    assert set(body) == {"model_info"}  # 没给单价/上游，就没有 litellm_params 这一段
    assert body["model_info"] == {"cheese_label": "换个名字"}
    assert "api_key" not in sent.content.decode()


@pytest.mark.anyio
async def test_an_edit_goes_to_the_patch_endpoint_that_writes_model_info():
    """老 ``POST /model/update`` 在本版 LiteLLM 里落库时不写 ``model_info``，改标签/
    上架/能力会被静默丢掉；而且不带 ``litellm_params`` 时它直接 400。管理页改的多半
    就是 model_info 那几项，所以必须走 ``PATCH /model/{id}/update``。这条用例钉住：
    是个 PATCH、路径带上 model_id、body 里带着 model_info（且带上 litellm_params 时
    两段都在，不会被丢）。"""
    gw = _Gateway(
        models=[
            _row(
                "runtime",
                "anthropic/x",
                db_model=True,
                selectable=True,
                input_cost=1e-6,
                output_cost=2e-6,
            )
        ]
    )
    service = GatewayModelsService(_StubDb(), gw.admin())

    await service.update(
        handle="admin",
        name="runtime",
        payload=ModelUpdate(
            label="新标签",
            selectable=True,
            prices={"input": 2e-6, "output": 3e-6},
        ),
    )

    sent = next(
        r for r in gw.requests if r.method == "PATCH" and r.url.path.endswith("/update")
    )
    assert sent.url.path == "/model/runtime-id/update"
    body = json.loads(sent.content)
    assert body["model_info"] == {"cheese_label": "新标签", "cheese_selectable": True}
    assert body["litellm_params"] == {
        "input_cost_per_token": 2e-6,
        "output_cost_per_token": 3e-6,
    }
