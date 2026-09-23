"""Project defaults, teammate overrides, and native child requests stay distinct."""

import uuid

import pytest

from app.core.config import settings
from app.core.sandbox_auth import mint_scoped_token
from app.domain.agent import gateway_catalog
from app.domain.agent.chat import ChatService
from app.domain.agent.device_provider import DeviceChannel
from app.domain.agent.harness.claude_code import ClaudeCodeRuntime
from tests.conftest import stub_compute
from tests.integration.conftest import session_auth_headers


@pytest.fixture(autouse=True)
def models(monkeypatch):
    monkeypatch.setattr(settings, "agent_model", "deepseek-flash")
    gateway_catalog.reset()
    yield
    gateway_catalog.reset()


def create(client):
    response = client.post(
        "/projects",
        json={"name": "Research", "agent_name": "Moss"},
        headers=session_auth_headers("alice"),
    )
    assert response.status_code == 200, response.text
    project = response.json()["data"]
    client.headers.update(session_auth_headers("alice"))
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
        session_factory=client.test_factory,
        compute=stub_compute(),
        base_system_prompt="Test",
        workspace_root=str(tmp_path / "ws"),
    )
    kwargs, _ = await chat._model_kwargs(
        uuid.UUID(pid),
        ClaudeCodeRuntime(DeviceChannel()),
        uuid.UUID(project["root_topic_id"]),
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
async def test_a_subagent_asking_for_the_inherited_main_model_is_allowed(client):
    """fork 分身继承父模型：体里的名字翻译回来等于父会话绑定的,按「未指
    定」退回分身默认 —— 继承名不会被推进目录校验。"""
    project = create(client)
    pid = project["id"]
    body = client.post(
        "/llm/admission",
        headers=_subagent_headers(pid, project["root_topic_id"], "deepseek-flash"),
    ).json()["data"]
    assert body["allow"] is True
    assert body["supply"]["model"] == "deepseek-flash"


@pytest.mark.anyio
async def test_an_inherited_subagent_keeps_the_projects_subagent_default(client):
    """继承不算指定（这是 I27 那一侧最容易写错的一条）: CC 对 fork 和未带
    model 的定义都在体里写父模型 —— 项目显式设的分身默认必须照走,池都不换。"""
    project = create(client)
    pid = project["id"]
    response = client.put(
        f"/projects/{pid}/default-model",
        json={"model": "deepseek-flash", "subagent_model": "sonnet"},
    )
    assert response.status_code == 200, response.text
    # 主芝士没绑模型(继承项目默认 deepseek-flash),体里写着 deepseek-flash
    # 的 fork 不是「指定了 deepseek-flash」,是「没指定」。
    body = client.post(
        "/llm/admission",
        headers=_subagent_headers(pid, project["root_topic_id"], "deepseek-flash"),
    ).json()["data"]
    assert body["allow"] is True
    assert "sonnet" in body["supply"]["model"]
    assert body["supply"]["pool"] == "subscription"


@pytest.mark.anyio
async def test_a_fork_of_a_teammate_session_also_reads_as_inherit(client):
    """父会话是队友也一样:队友会话 fork 出来的分身,体里写的是队友绑的那
    个模型 —— 同样按继承退回分身默认,不吃目录校验。"""
    project = create(client)
    pid = project["id"]
    teammate = agents(client, pid)[0]
    response = client.put(
        f"/projects/{pid}/agents/{teammate['id']}",
        json={"configuration": {"model": "opus"}},
    )
    assert response.status_code == 200, response.text
    response = client.put(
        f"/projects/{pid}/default-model",
        json={"model": "deepseek-flash", "subagent_model": "sonnet"},
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
        "X-Cheese-Requested-Model": "claude-opus-5",
    }
    body = client.post("/llm/admission", headers=headers).json()["data"]
    assert body["allow"] is True
    assert "sonnet" in body["supply"]["model"]


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
    """没指定的分身（fork、定义里不带 model 的）维持现状:走分身默认。"""
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
