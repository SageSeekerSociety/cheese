"""这个项目能用哪些模型，以及这个项目跑的骨架指得到哪些。

模型不是参与者的属性（结论 3），所以这里没有一条是在问「这个 agent 用什么」：
问的全是「这套部署、这个项目，能用的是哪一批」。按骨架筛不是参与者这一级的事
（结论 3「部署决定可选列表（按 harness 筛）」，结论 28「harness 是部署/项目级的
开发者设置」）——跑哪个骨架由部署设置加项目设置答，目录就按那个答案筛。
"""

import asyncio

import httpx
import pytest

from app.core.config import settings
from app.core.errors import ValidationError
from app.domain.agent import gateway as gw
from app.domain.agent import gateway_catalog
from app.domain.agent.harness import CODEX, HARNESS_SETTING, PI
from app.domain.agent.market import subscription_model_alias
from app.domain.agent_instance.configuration import model_choices
from tests.support.stand_in_harness import registered


def _offered(project_settings: dict | None = None) -> set[str]:
    return {item["id"] for item in model_choices(project_settings)}


def _pool_models(project_settings: dict | None = None) -> set[str]:
    """池子提供的那些。订阅那几个永远和它们并排在目录里——一台机器只有一种启动
    形状，两边都够得到——所以问网关的时候得把话说明白。"""
    return {
        item["id"]
        for item in model_choices(project_settings)
        if item["supply"] == "gateway"
    }


def _running(monkeypatch, name: str, **bits: bool) -> None:
    """这套部署跑的是哪个骨架。它是部署设置，不是谁的属性（结论 28）。

    骨架当场造一个注册上去：注册表里今天只有 Claude Code（结论 43，答不出四条硬性
    要求的骨架留着代码不注册），而下面这几条问的是「跑着一个指不到订阅、或者不说
    网关那套话的骨架时，列出来的是哪一批」——那件事跟谁答得出四条无关。
    """
    registered(monkeypatch, name, **bits)
    monkeypatch.setattr(settings, "agent_harness", name)


@pytest.mark.parametrize(
    ("chosen", "requested"),
    [
        ("sonnet", "claude-sonnet-5"),
        ("opus", "claude-opus-5"),
        ("opus-4.8", "claude-opus-4-8"),
        ("fable", "claude-fable-5"),
    ],
)
def test_model_is_explicit(chosen, requested):
    assert subscription_model_alias(chosen) == requested


@pytest.mark.parametrize("chosen", [None, "", "unknown"])
def test_unknown_model_is_not_a_default(chosen):
    with pytest.raises(ValidationError):
        subscription_model_alias(chosen)


@pytest.mark.parametrize(
    ("supply", "expected"),
    [
        (None, "gateway-model"),
        ("gateway", "gateway-model"),
        ("subscription", "sonnet"),
    ],
)
def test_choices_follow_project_supply(monkeypatch, supply, expected):
    monkeypatch.setattr(settings, "agent_model", "gateway-model")
    project = {"supply": supply}
    chosen = [item for item in model_choices(project) if item["default"]]
    assert [item["id"] for item in chosen] == [expected]
    assert "unknown" not in _offered(project)


@pytest.mark.parametrize("supply", ["subscription", "gateway"])
def test_every_gateway_model_is_offered_whatever_the_projects_supply_is(
    supply, deployed_pool
):
    project = {"supply": supply}
    assert {"glm-5.2", "deepseek-flash", "sonnet"} <= _offered(project)
    assert sum(item["default"] for item in model_choices(project)) == 1


def test_a_deployment_on_a_gateway_harness_offers_every_pool_model(monkeypatch):
    """它是通过网关够到模型的，和项目自己那个池是同一条路，所以在这里重述一遍
    「哪些能跑」只会多出一份会过期的副本。订阅那几个是例外，它们自己有一条：
    那份凭据只有一个骨架拿得出来。"""
    monkeypatch.setattr(settings, "agent_model", "glm-5.2")
    _gateway_reports(_routes("glm-5.2"), _routes("deepseek-flash"))
    pool = _pool_models()
    _running(monkeypatch, PI)
    assert _offered({}) == pool


def test_a_deployment_is_offered_only_what_its_harness_has_an_adapter_for(
    monkeypatch,
):
    """跑的骨架不说网关那套话时，它只指得到运维替它点名的那几个——别的列出来，
    就是让绑上它的那条活在派出去的那一刻才失败。"""
    monkeypatch.setattr(settings, "agent_model", "glm-5.2")
    monkeypatch.setattr(settings, "agent_harness_models", {"codex": ["codex-fixture"]})
    _gateway_reports(_routes("glm-5.2"))
    assert {"glm-5.2", "codex-fixture"} <= _offered({})

    _running(monkeypatch, CODEX, speaks_gateway=False)
    assert _offered({}) == {"codex-fixture"}


def test_a_subscription_model_stays_with_the_harness_its_credential_is_for(
    monkeypatch,
):
    """The credential, not the request shape: a subscription turn authenticates
    with something minted for one harness, so listing the alias among another
    harness's API models must not make it reachable on a deployment that runs
    that other harness."""
    monkeypatch.setattr(
        settings, "agent_harness_models", {"codex": ["sonnet", "codex-fixture"]}
    )
    assert "sonnet" in _offered({})

    _running(monkeypatch, CODEX, speaks_gateway=False)
    assert "sonnet" not in _offered({})
    assert "codex-fixture" in _offered({})


def test_a_project_that_switched_harness_is_filtered_by_that_one(monkeypatch):
    """项目设置盖过部署设置（结论 28），目录就得按项目那个骨架筛。

    轮次组装和克隆都已经按项目答「跑哪个骨架」，目录再按部署答一遍就是同一个问题
    的第二个答法：部署跑 claude-code、项目设成 codex 时，轮次真跑在 codex 上，而
    按 claude-code 筛出来的订阅别名 codex 指不到——``binding.resolve`` 把它挑出来
    绑到活上，在派出去的那一刻才失败。
    """
    monkeypatch.setattr(
        settings, "agent_harness_models", {"codex": ["sonnet", "codex-fixture"]}
    )
    # 部署跑的是 claude-code，没动它——动的只有一个项目的设置。
    assert {"sonnet", "codex-fixture"} <= _offered({})

    registered(monkeypatch, CODEX, speaks_gateway=False)
    switched = {HARNESS_SETTING: CODEX}
    assert "sonnet" not in _offered(switched)
    assert "codex-fixture" in _offered(switched)
    assert "sonnet" in _offered({})


@pytest.mark.parametrize(
    ("supply", "default_model"),
    [
        # 订阅部署下，项目显式把默认改成网关模型 —— #1365 之前这是 agent 上的配置
        # 反推出来的，主线会走 gateway；#1365 之后唯一能让主线读到这个意图的地方
        # 就是 project.settings["default_model"]。
        ("subscription", "deepseek-flash"),
        # 网关部署下，项目显式把默认改成另一个网关模型
        ("gateway", "glm-5.2"),
    ],
)
def test_project_default_model_overrides_deployment_default(
    monkeypatch, deployed_pool, supply, default_model
):
    """项目 settings 里显式写 default_model，就把它那条标 default=True，其余清掉。

    没写时按部署兜底算（订阅部署→sonnet；网关部署→agent_model）。这条是事故
    「agent 配了 deepseek、主线静默换到订阅 sonnet」的修复点。
    """
    monkeypatch.setattr(settings, "agent_model", "gateway-model")
    project = {"supply": supply, "default_model": default_model}
    choices = model_choices(project)
    defaults = [item for item in choices if item["default"]]
    assert len(defaults) == 1
    # 主线 resolve(None, catalog) 拿的就是 catalog 里 default=True 的那一条，
    # 所以「目录里只有它标了 default」就是「主线走它」。
    assert defaults[0]["id"] == default_model


def test_no_default_model_falls_back_to_deployment_default(monkeypatch, deployed_pool):
    """没写 default_model 时保留部署兜底算出来的 default —— 不破坏现状。"""
    # deployed_pool fixture 把 settings.agent_model 设成 "glm-5.2"
    # 订阅部署 + 项目 supply=subscription → 默认 sonnet
    choices = model_choices({"supply": "subscription"})
    defaults = [item for item in choices if item["default"]]
    assert len(defaults) == 1
    assert defaults[0]["id"] == "sonnet"
    # 网关部署 → 默认 settings.agent_model（= glm-5.2）
    choices = model_choices({"supply": "gateway"})
    defaults = [item for item in choices if item["default"]]
    assert len(defaults) == 1
    assert defaults[0]["id"] == "glm-5.2"


def test_unknown_default_model_is_silently_ignored(monkeypatch, deployed_pool):
    """历史数据可能写过目录里没有的名字，那种情况按没设处理，不让目录空掉。"""
    project = {"supply": "subscription", "default_model": "nothing-real"}
    choices = model_choices(project)
    # 仍然有一个 default（部署兜底算出来的 sonnet），不是零个
    defaults = [item for item in choices if item["default"]]
    assert len(defaults) == 1
    assert defaults[0]["id"] == "sonnet"


# --- What the platform pool offers ------------------------------------------
# The gateway decides: it needs a route and a price to serve a model at all, so
# these tests say what a project is offered given what a gateway reports, and
# never read a list out of this codebase to compare against.


def _gateway_answering(rows):
    return gw.LlmGateway(
        "http://gw",
        "mk",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"data": rows})
        ),
    )


def _routes(name, *, offered=True, priced=True, label=None):
    info = {}
    if offered:
        info["cheese_selectable"] = True
    if label:
        info["cheese_label"] = label
    if priced:
        info["input_cost_per_token"] = 0.000004
        info["output_cost_per_token"] = 0.000004
    return {
        "model_name": name,
        "litellm_params": {"model": f"anthropic/{name}"},
        "model_info": info,
    }


def _gateway_reports(*rows):
    asyncio.run(gateway_catalog.refresh(_gateway_answering(list(rows))))


@pytest.fixture(autouse=True)
def _forget_the_catalogue():
    gateway_catalog.reset()
    yield
    gateway_catalog.reset()


@pytest.fixture
def deployed_pool(monkeypatch):
    """What the deployed gateway routes today, as it would answer."""
    monkeypatch.setattr(settings, "agent_model", "glm-5.2")
    _gateway_reports(
        _routes("glm-5.2", label="GLM-5.2"),
        _routes("deepseek-flash", label="DeepSeek V4.1 Flash"),
        # Where the subagent alias points. Routed, never a menu item.
        _routes("glm-4.5", offered=False),
    )


def test_a_model_the_gateway_starts_routing_is_offered_without_a_release(
    monkeypatch, deployed_pool
):
    """The whole point: the pool's menu follows the gateway, so putting a model
    into service does not also mean editing and shipping this codebase."""
    assert "brand-new-model" not in {item["id"] for item in model_choices({})}

    _gateway_reports(
        _routes("glm-5.2", label="GLM-5.2"),
        _routes("brand-new-model", label="Something Just Released"),
    )
    offered = {item["id"]: item for item in model_choices({})}
    assert "brand-new-model" in offered
    assert offered["brand-new-model"]["label"] == "Something Just Released"

    # And one the gateway stops routing stops being offered, rather than sitting
    # in the picker until someone notices a turn failing on it.
    _gateway_reports(_routes("glm-5.2", label="GLM-5.2"))
    assert "brand-new-model" not in {item["id"] for item in model_choices({})}


def test_a_model_the_gateway_routes_for_us_is_not_a_menu_item(deployed_pool):
    """glm-4.5 is where the subagent alias points. Offering it would invite a
    person to pick a model the platform routes to on their behalf."""
    assert "glm-4.5" not in {item["id"] for item in model_choices({})}


def test_a_model_the_gateway_cannot_bill_is_not_offered(monkeypatch):
    """Its tokens meter at zero, so the project's budget never trips and the
    first sign of trouble is the invoice. A model missing from the picker gets
    noticed; a brake that stopped working does not."""
    monkeypatch.setattr(settings, "agent_model", "glm-5.2")
    _gateway_reports(
        _routes("glm-5.2"),
        _routes("free-of-charge-by-accident", priced=False),
    )
    assert "free-of-charge-by-accident" not in {
        item["id"] for item in model_choices({})
    }


def test_an_unreachable_gateway_keeps_offering_what_it_last_reported(monkeypatch):
    """A blip must not empty the picker: model_choices also runs when a turn
    starts, so an empty answer stops every agent on the deployment."""
    monkeypatch.setattr(settings, "agent_model", "glm-5.2")
    _gateway_reports(_routes("glm-5.2"), _routes("deepseek-flash"))

    dead = gw.LlmGateway(
        "http://gw",
        "mk",
        transport=httpx.MockTransport(lambda request: httpx.Response(502)),
    )
    assert asyncio.run(gateway_catalog.refresh(dead)) is False
    assert {"glm-5.2", "deepseek-flash"} <= {item["id"] for item in model_choices({})}


def test_a_deployment_with_no_gateway_admin_api_still_runs_its_own_model(monkeypatch):
    """The admin API is optional. Such a deployment cannot be asked what it
    routes, but it is still configured to run one model, and naming that is
    better than offering nothing or inventing a list."""
    monkeypatch.setattr(settings, "agent_model", "the-configured-one")
    assert asyncio.run(gateway_catalog.refresh(None)) is False
    assert _pool_models() == {"the-configured-one"}


@pytest.mark.anyio
async def test_a_gateway_that_is_not_up_yet_is_asked_again_soon(monkeypatch):
    """The gateway is a stack of its own, started and restarted independently of
    the app, so the app can easily boot first. Waiting a full refresh interval
    to ask again would leave the deployment serving fewer models than it has,
    for minutes, after an ordinary restart."""
    monkeypatch.setattr(settings, "agent_model", "the-configured-one")
    monkeypatch.setattr(gateway_catalog, "FIRST_ANSWER_RETRY_SECONDS", 0.01)

    answers = [
        httpx.Response(502),
        httpx.Response(200, json={"data": [_routes("arrived-late")]}),
    ]
    late = gw.LlmGateway(
        "http://gw",
        "mk",
        transport=httpx.MockTransport(lambda request: answers.pop(0)),
    )

    task = asyncio.get_running_loop().create_task(gateway_catalog.keep_fresh(late))
    try:
        # Until it answers, the deployment runs on the model it is configured for.
        assert _pool_models() == {"the-configured-one"}
        for _ in range(200):
            await asyncio.sleep(0.01)
            if "arrived-late" in _pool_models():
                break
        assert "arrived-late" in _pool_models()
    finally:
        task.cancel()
