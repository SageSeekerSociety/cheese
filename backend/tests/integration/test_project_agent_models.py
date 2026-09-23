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
            ClaudeCodeRuntime(DeviceChannel()),
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
        ClaudeCodeRuntime(DeviceChannel()),
        uuid.UUID(project["root_topic_id"]),
    )
    assert "sonnet" in kwargs["model"]
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


def test_explicit_native_child_models_are_validated_against_catalog_and_policy(client):
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
    for model in ("claude-opus-5", "deepseek-flash", "not-offered"):
        result = client.post(
            "/llm/admission", headers={**headers, "X-Cheese-Child-Model": model}
        ).json()["data"]
        if model == "not-offered":
            assert not result["allow"], result
            assert result["reason_kind"] == "binding"
        else:
            assert result["allow"], result
            assert result["supply"]["model"] == model
    # A main request cannot smuggle a different model through the child hint.
    result = client.post(
        "/llm/admission",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Cheese-Child-Model": "claude-opus-5",
        },
    ).json()["data"]
    assert result["supply"]["model"] == "deepseek-flash"
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
