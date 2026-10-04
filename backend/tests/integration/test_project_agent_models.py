"""Project defaults, teammate overrides, and native child requests stay distinct."""

import uuid

import pytest

from app.core.config import settings
from app.core.sandbox_auth import mint_scoped_token
from app.domain.agent import gateway_catalog
from app.domain.agent.chat import ChatService
from app.domain.agent.device_provider import DeviceChannel
from app.domain.agent.harness import CLAUDE_CODE
from app.domain.agent.room.sessions import RoomSessions
from app.domain.agent.session_host.host import SessionHost
from tests.conftest import stub_compute
from tests.integration.conftest import (
    post_project,
    put_on_plan,
    session_auth_headers,
)


@pytest.fixture(autouse=True)
def models(monkeypatch):
    monkeypatch.setattr(settings, "agent_model", "deepseek-flash")
    gateway_catalog.reset()
    yield
    gateway_catalog.reset()


def create(client):
    response = post_project(
        client,
        json={"name": "Research", "agent_name": "Moss"},
        headers=session_auth_headers("alice"),
    )
    assert response.status_code == 200, response.text
    project = response.json()["data"]
    client.headers.update(session_auth_headers("alice"))

    # These tests are about routing the Claude subscription models; Free
    # leaves those out, Reserve allows every model.
    async def reserve() -> None:
        async with client.test_request_factory() as session:  # type: ignore[attr-defined]
            await put_on_plan(session, project["team_id"], "reserve")
            await session.commit()

    client.portal.call(reserve)  # type: ignore[union-attr]
    return project


def agents(client, pid):
    return client.get(f"/projects/{pid}/agents").json()["data"]["data"]


@pytest.mark.anyio
async def test_project_name_defaults_and_teammate_model_reach_execution(
    client, tmp_path
):
    project = create(client)
    pid = project["id"]
    teammate = agents(client, pid)[0]
    assert teammate["display_name"] == "Moss"
    response = client.put(
        f"/projects/{pid}/default-model",
        json={"model": "deepseek-flash", "subagent_model": "sonnet"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["subagent_model"] == "sonnet"
    # A partial main-model update cannot clear the native child default.
    response = client.put(
        f"/projects/{pid}/default-model", json={"model": "deepseek-flash"}
    )
    assert response.json()["data"]["subagent_model"] == "sonnet"
    route = f"/projects/{pid}/agents/{teammate['id']}"
    response = client.put(route, json={"configuration": {"model": "opus"}})
    assert response.status_code == 200, response.text
    chat = ChatService(
        session_factory=client.test_request_factory,
        compute=stub_compute(),
        base_system_prompt="Test",
        workspace_root=str(tmp_path / "ws"),
    )
    kwargs, _ = client.portal.call(
        lambda: chat._model_kwargs(
            uuid.UUID(pid),
            RoomSessions(DeviceChannel(), CLAUDE_CODE, SessionHost()),
            uuid.UUID(project["root_topic_id"]),
        )
    )
    assert "opus" in kwargs["model"]
    assert kwargs["env"]["CLAUDE_CODE_GATEWAY_HINT_HEADERS"] == "1"
    token = mint_scoped_token(
        project_id=pid,
        topic_id=project["root_topic_id"],
        agent_handle=teammate["seat_handle"],
    )
    headers = {"Authorization": f"Bearer {token}"}
    main = client.post("/llm/admission", headers=headers).json()["data"]
    child = client.post(
        "/llm/admission", headers={**headers, "X-Cheese-Subagent": "1"}
    ).json()["data"]
    assert "opus" in main["supply"]["model"]
    assert "sonnet" in child["supply"]["model"]
    client.put(route, json={"configuration": {"model": None}})
    main = client.post("/llm/admission", headers=headers).json()["data"]
    assert main["supply"]["model"] == "deepseek-flash"
    assert main["supply"]["pool"] == "gateway"
    assert child["supply"]["pool"] == "subscription"
    client.put(f"/projects/{pid}/default-model", json={"subagent_model": None})
    child = client.post(
        "/llm/admission", headers={**headers, "X-Cheese-Subagent": "1"}
    ).json()["data"]
    assert child["supply"]["model"] == "deepseek-flash"


def test_unavailable_models_are_rejected_at_every_write(client):
    pid = create(client)["id"]
    for field in ("model", "subagent_model"):
        response = client.put(
            f"/projects/{pid}/default-model", json={field: "not-offered"}
        )
        assert response.status_code == 422, response.text
    response = client.post(
        f"/projects/{pid}/agents",
        json={"display_name": "Spark", "configuration": {"model": "not-offered"}},
    )
    assert response.status_code == 422, response.text
    teammate = agents(client, pid)[0]
    response = client.put(
        f"/projects/{pid}/agents/{teammate['id']}",
        json={"configuration": {"model": "not-offered"}},
    )
    assert response.status_code == 422, response.text


@pytest.mark.anyio
async def test_a_removed_teammate_model_is_refused_without_switching_pool(
    client, monkeypatch
):
    project = create(client)
    pid = project["id"]
    teammate = agents(client, pid)[0]
    client.put(
        f"/projects/{pid}/agents/{teammate['id']}",
        json={"configuration": {"model": "opus"}},
    )
    from app.domain.agent_instance import configuration

    available = configuration.subscription_model_listings()
    monkeypatch.setattr(
        configuration,
        "subscription_model_listings",
        lambda: [item for item in available if item.id != "opus"],
    )
    token = mint_scoped_token(
        project_id=pid,
        topic_id=project["root_topic_id"],
        agent_handle=teammate["seat_handle"],
    )
    result = client.post(
        "/llm/admission", headers={"Authorization": f"Bearer {token}"}
    ).json()["data"]
    assert not result["allow"]
    assert result["reason_kind"] == "binding"


@pytest.mark.anyio
async def test_removed_main_is_refused_but_unused_defaults_do_not_block_overrides(
    client, monkeypatch, tmp_path
):
    project = create(client)
    pid = project["id"]
    teammate = agents(client, pid)[0]
    assert (
        client.put(f"/projects/{pid}/default-model", json={"model": "opus"}).status_code
        == 200
    )
    from app.core.errors import ValidationError
    from app.domain.agent_instance import configuration

    available = configuration.subscription_model_listings()
    monkeypatch.setattr(
        configuration,
        "subscription_model_listings",
        lambda: [item for item in available if item.id != "opus"],
    )
    token = mint_scoped_token(
        project_id=pid,
        topic_id=project["root_topic_id"],
        agent_handle=teammate["seat_handle"],
    )
    headers = {"Authorization": f"Bearer {token}"}
    for child in (False, True):
        result = client.post(
            "/llm/admission",
            headers={**headers, **({"X-Cheese-Subagent": "1"} if child else {})},
        ).json()["data"]
        assert not result["allow"], result
        assert result["reason_kind"] == "binding"
    chat = ChatService(
        session_factory=client.test_factory,
        compute=stub_compute(),
        base_system_prompt="Test",
        workspace_root=str(tmp_path / "ws"),
    )
    with pytest.raises(ValidationError, match="opus"):
        await chat._model_kwargs(
            uuid.UUID(pid),
            RoomSessions(DeviceChannel(), CLAUDE_CODE, SessionHost()),
            uuid.UUID(project["root_topic_id"]),
        )
    assert (
        client.put(
            f"/projects/{pid}/agents/{teammate['id']}",
            json={"configuration": {"model": "sonnet"}},
        ).status_code
        == 200
    )
    main = client.post("/llm/admission", headers=headers).json()["data"]
    assert main["allow"], main
    assert "sonnet" in main["supply"]["model"]
    kwargs, _ = await chat._model_kwargs(
        uuid.UUID(pid),
        RoomSessions(DeviceChannel(), CLAUDE_CODE, SessionHost()),
        uuid.UUID(project["root_topic_id"]),
    )
    assert "sonnet" in kwargs["model"]
    # Claude Code is launched on the model admission puts each request on, so
    # its system prompt and self-description are for that model.
    assert kwargs["env"]["ANTHROPIC_MODEL"] == main["supply"]["model"]
    assert kwargs["env"]["CLAUDE_CODE_SUBAGENT_MODEL"] == "opus"
    assert (
        client.put(
            f"/projects/{pid}/default-model", json={"subagent_model": "sonnet"}
        ).status_code
        == 200
    )
    child = client.post(
        "/llm/admission", headers={**headers, "X-Cheese-Subagent": "1"}
    ).json()["data"]
    assert child["allow"], child
    assert "sonnet" in child["supply"]["model"]


@pytest.mark.anyio
async def test_explicit_native_child_models_are_validated_against_catalog_and_policy(
    client,
):
    """child_model 显式指定走目录语义（与队友身份无关）：目录没有的名字拒绝
    并列出目录可选；目录内（含项目显式配置的两个默认）绑定；主对话不能借
    child 头走私模型；显式指定的模型照过 tier 策略闸。"""
    project = create(client)
    pid = project["id"]
    teammate = agents(client, pid)[0]
    assert (
        client.put(
            f"/projects/{pid}/default-model",
            json={"model": "deepseek-flash", "subagent_model": "sonnet"},
        ).status_code
        == 200
    )
    token = mint_scoped_token(
        project_id=pid,
        topic_id=project["root_topic_id"],
        agent_handle=teammate["seat_handle"],
    )
    headers = {"Authorization": f"Bearer {token}", "X-Cheese-Subagent": "1"}
    # 目录里没有的名字：拒绝并列出目录可选 —— 不再是「队友的范围」。
    unlisted = client.post(
        "/llm/admission",
        headers={**headers, "X-Cheese-Child-Model": "not-offered"},
    ).json()["data"]
    assert not unlisted["allow"], unlisted
    assert unlisted["reason_kind"] == "binding"
    assert "可指定" in unlisted["reason"]
    # 项目显式配置的两个默认跨池也合法（供给跟着绑定走），目录内本名照绑。
    for model in ("deepseek-flash", "sonnet"):
        result = client.post(
            "/llm/admission", headers={**headers, "X-Cheese-Child-Model": model}
        ).json()["data"]
        assert result["allow"], (model, result)
    # A main request cannot smuggle a different model through the child hint.
    result = client.post(
        "/llm/admission",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Cheese-Child-Model": "sonnet",
        },
    ).json()["data"]
    assert result["supply"]["model"] == "deepseek-flash"
    # 换到订阅池：claude-opus-5 在目录里（旧队友语义下没有队友绑它就吃拒
    # 绝），目录语义直接绑得上 —— 但档位是 premium，过 tier 策略闸时被拒。
    from app.domain.project.repositories import ProjectRepository

    async with client.test_factory() as session:
        orm_project = await ProjectRepository(session).get(uuid.UUID(pid))
        assert orm_project is not None
        orm_project.settings = {
            **(orm_project.settings or {}),
            "supply": "subscription",
        }
        await session.commit()
    premium = client.post(
        "/llm/admission", headers={**headers, "X-Cheese-Child-Model": "claude-opus-5"}
    ).json()["data"]
    assert premium["allow"], premium
    assert (
        client.put(
            f"/projects/{pid}/tier-policy",
            json={"allowed_tiers": ["included"], "over_tier": "deny"},
        ).status_code
        == 200
    )
    denied = client.post(
        "/llm/admission", headers={**headers, "X-Cheese-Child-Model": "claude-opus-5"}
    ).json()["data"]
    assert not denied["allow"], denied
    assert denied["reason_kind"] == "binding"


# --- 开分身时指定模型（范围 = 项目模型目录，与队友身份无关） ------------------
# CC 把主 agent 给分身指定的模型写进分身请求体的顶层 model 成员，计量代理解
# 析出来随 admission 带上来（X-Cheese-Requested-Model）。可指定的集合是项目模
# 型目录（本池 + 项目显式配置的两个默认），不是任何参与者的配置（2026-09-23
# 拍板）。指定了就要么绑它、要么明说为什么不行 —— 静默改写回分身默认正是
# 「指定了却不生效」那个旧行为。


def _subagent_headers(pid, topic_id, requested=None):
    token = mint_scoped_token(project_id=pid, topic_id=topic_id)
    headers = {"Authorization": f"Bearer {token}", "X-Cheese-Subagent": "1"}
    if requested is not None:
        headers["X-Cheese-Requested-Model"] = requested
    return headers


@pytest.mark.anyio
async def test_a_subagent_may_ask_for_a_catalog_model(client, monkeypatch):
    """指定目录内（本池）的模型：与任何队友都无关，目录有就能绑。"""
    from app.domain.agent.gateway_catalog import GatewayModel

    monkeypatch.setattr(
        gateway_catalog,
        "offerable",
        lambda: [
            GatewayModel(
                id="deepseek-flash",
                label="deepseek-flash",
                selectable=True,
                priced=True,
            ),
            GatewayModel(id="kimi-k3", label="kimi-k3", selectable=True, priced=True),
        ],
    )
    project = create(client)
    pid = project["id"]
    body = client.post(
        "/llm/admission",
        headers=_subagent_headers(pid, project["root_topic_id"], "kimi-k3"),
    ).json()["data"]
    assert body["allow"] is True
    assert body["supply"]["model"] == "kimi-k3"
    assert body["supply"]["pool"] == "gateway"


@pytest.mark.anyio
async def test_a_subagent_asking_for_the_main_model_is_allowed(client):
    """指定主模型（在本池里）：绑得上。"""
    project = create(client)
    pid = project["id"]
    body = client.post(
        "/llm/admission",
        headers=_subagent_headers(pid, project["root_topic_id"], "deepseek-flash"),
    ).json()["data"]
    assert body["allow"] is True
    assert body["supply"]["model"] == "deepseek-flash"


@pytest.mark.anyio
async def test_a_subagent_naming_the_parents_model_runs_on_it(client):
    """指定和父会话同一个模型就是指定了它，不退回分身默认。

    会话启动时 CLAUDE_CODE_SUBAGENT_MODEL 钉着分身默认，没指定的分身体里写
    的是分身默认；体里写父会话的模型，只能是主 agent 要它和自己同模型。"""
    project = create(client)
    pid = project["id"]
    response = client.put(
        f"/projects/{pid}/default-model",
        json={"model": "deepseek-flash", "subagent_model": "sonnet"},
    )
    assert response.status_code == 200, response.text
    body = client.post(
        "/llm/admission",
        headers=_subagent_headers(pid, project["root_topic_id"], "deepseek-flash"),
    ).json()["data"]
    assert body["allow"] is True
    assert body["supply"] == {
        "pool": "gateway",
        "model": "deepseek-flash",
        "key": body["supply"]["key"],
    }


@pytest.mark.anyio
async def test_a_subagent_naming_its_teammate_parents_model_runs_on_it_across_pools(
    client,
):
    """父会话是绑了订阅模型的队友，项目在网关池：分身指定同一个模型照样绑上。

    这个模型本来不在项目池的可指定范围里，但父会话此刻就在这个席位上跑着
    它 —— 「让分身和我同模型」不该被拒，更不该被悄悄换成分身默认。"""
    project = create(client)
    pid = project["id"]
    teammate = agents(client, pid)[0]
    response = client.put(
        f"/projects/{pid}/agents/{teammate['id']}",
        json={"configuration": {"model": "opus"}},
    )
    assert response.status_code == 200, response.text
    token = mint_scoped_token(
        project_id=pid,
        topic_id=project["root_topic_id"],
        agent_handle=teammate["seat_handle"],
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Cheese-Subagent": "1",
        "X-Cheese-Child-Model": "claude-opus-5",
    }
    body = client.post("/llm/admission", headers=headers).json()["data"]
    assert body["allow"] is True, body
    assert body["supply"]["pool"] == "subscription"
    assert body["supply"]["model"] == "claude-opus-5"


@pytest.mark.anyio
async def test_a_subagent_asking_outside_the_projects_pool_is_refused_by_name(
    client,
):
    """目录里有、但不在本项目池里的模型：可指定范围按池算，跨池吃拒绝。"""
    project = create(client)
    pid = project["id"]
    body = client.post(
        "/llm/admission",
        headers=_subagent_headers(pid, project["root_topic_id"], "opus"),
    ).json()["data"]
    assert body["allow"] is False
    assert body["reason_kind"] == "binding"
    assert "不在当前项目可用的模型范围内" in body["reason"]
    # 拒绝要给出可指定的范围,不然就是一句没法行动的「不行」。
    assert "deepseek-flash" in body["reason"]


@pytest.mark.anyio
async def test_a_subagent_asking_for_a_model_the_catalogue_lacks_is_refused(client):
    project = create(client)
    pid = project["id"]
    body = client.post(
        "/llm/admission",
        headers=_subagent_headers(pid, project["root_topic_id"], "glm-4.7"),
    ).json()["data"]
    assert body["allow"] is False
    assert body["reason_kind"] == "binding"
    assert "模型目录里没有" in body["reason"]


@pytest.mark.anyio
async def test_an_unspecified_subagent_keeps_the_project_default(client):
    """没指定模型的分身走分身默认。"""
    project = create(client)
    pid = project["id"]
    response = client.put(
        f"/projects/{pid}/default-model",
        json={"model": "deepseek-flash", "subagent_model": "sonnet"},
    )
    assert response.status_code == 200, response.text
    body = client.post(
        "/llm/admission",
        headers=_subagent_headers(pid, project["root_topic_id"]),
    ).json()["data"]
    assert body["allow"] is True
    assert "sonnet" in body["supply"]["model"]
    # 而「指定的恰好就是分身默认」是合法的 —— 复述默认值不该吃到一个拒绝。
    body = client.post(
        "/llm/admission",
        headers=_subagent_headers(pid, project["root_topic_id"], "sonnet"),
    ).json()["data"]
    assert body["allow"] is True
    assert "sonnet" in body["supply"]["model"]


@pytest.mark.anyio
async def test_the_requested_model_header_is_ignored_off_the_subagent_path(client):
    """主对话的模型从来由绑定决定：同一个头在主对话请求上不是输入。"""
    project = create(client)
    pid = project["id"]
    token = mint_scoped_token(project_id=pid, topic_id=project["root_topic_id"])
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Cheese-Requested-Model": "sonnet",
    }
    body = client.post("/llm/admission", headers=headers).json()["data"]
    assert body["allow"] is True
    assert body["supply"]["model"] == "deepseek-flash"


def test_the_picker_reports_a_main_model_that_left_the_catalog_as_saved(
    client, monkeypatch
):
    """A removed main model refuses every turn, so the settings page must say
    that model is what is saved, not show the deployment default in its place."""
    pid = create(client)["id"]
    route = f"/projects/{pid}/default-model"
    assert client.put(route, json={"model": "opus"}).status_code == 200
    from app.domain.agent_instance import configuration

    available = configuration.subscription_model_listings()
    monkeypatch.setattr(
        configuration,
        "subscription_model_listings",
        lambda: [item for item in available if item.id != "opus"],
    )
    state = client.get(route).json()["data"]
    assert "opus" not in {c["id"] for c in state["choices"]}
    assert state["model"] == "opus"
    assert client.put(route, json={"model": "deepseek-flash"}).status_code == 200
    assert client.get(route).json()["data"]["model"] == "deepseek-flash"
