"""Built-in starting configurations are read-only and copied into new agents."""

from fastapi.testclient import TestClient

from app.api.response import Envelope, Page
from app.domain.agent_type.schemas import AgentTypeOut
from tests.integration.conftest import post_project

#: What ``GET /agent-types`` declares it returns. One name, used both to read a
#: real response and to state what OpenAPI must publish — the two can only agree
#: if the route really serves this model.
_RESPONSE_MODEL = Envelope[Page[AgentTypeOut]]


def _presets(client: TestClient) -> list[dict]:
    """The listed presets, read through the model the route publishes: a response
    that stops fitting its own schema fails here, not in a caller."""
    response = client.get("/agent-types")
    assert response.status_code == 200
    return [
        item.model_dump(mode="json")
        for item in _RESPONSE_MODEL.model_validate(response.json()).data.data
    ]


def test_presets_are_read_only(client):
    presets = _presets(client)
    assert all(item["builtin"] for item in presets)
    assert any(item["name"] == "fullstack-engineer" for item in presets)
    assert client.post("/agent-types", json={"name": "custom"}).status_code == 405
    assert client.put(
        "/agent-types/fullstack-engineer", json={"body": "replace"}
    ).status_code in (404, 405)


def test_preset_initializes_a_project_agent(client):
    preset = next(
        item for item in _presets(client) if item["name"] == "fullstack-engineer"
    )
    project = post_project(
        client, json={"name": "Preset", "agent_type": preset["name"]}
    ).json()["data"]
    agent = client.get(f"/projects/{project['id']}/agents").json()["data"]["data"][0]
    assert agent["configuration"]["body"] == preset["body"]
    # 类型交出去的是角色，不含模型；实例带一栏模型但默认空着，继承项目主模型。
    assert "model" not in preset
    assert agent["configuration"]["model"] is None


def test_envelope_carries_no_warnings_field_when_there_is_nothing_to_warn_about(client):
    """``ok()`` leaves ``warnings`` out rather than sending null, and declaring
    the model must not put it back — the route passes
    ``response_model_exclude_unset`` for exactly this reason."""
    body = client.get("/agent-types").json()
    assert sorted(body) == ["code", "data", "message"]
    assert sorted(body["data"]) == ["data", "total"]


def test_openapi_publishes_the_envelope_model(client):
    """200's schema is a ``$ref`` chaining Envelope -> Page -> AgentTypeOut: a
    caller generates the response type from the document instead of hand-copying
    it, which is the whole point of declaring the model."""
    spec = client.get("/openapi.json").json()
    schemas = spec["components"]["schemas"]
    envelope_name = _ref(
        spec["paths"]["/agent-types"]["get"]["responses"]["200"]["content"][
            "application/json"
        ]["schema"]
    )
    assert envelope_name.startswith("Envelope")
    envelope = schemas[envelope_name]
    assert set(envelope["properties"]) == {"code", "message", "data", "warnings"}
    page_name = _ref(envelope["properties"]["data"])
    assert page_name.startswith("Page")
    page = schemas[page_name]
    assert set(page["properties"]) == {"data", "total"}
    assert _ref(page["properties"]["data"]["items"]).endswith("AgentTypeOut")


def _ref(node: dict) -> str:
    """The schema name a ``$ref`` node points at."""
    prefix = "#/components/schemas/"
    ref = node.get("$ref", "")
    assert ref.startswith(prefix), node
    return ref[len(prefix) :]
