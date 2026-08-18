"""Agent types: merged catalog, custom CRUD, presets read-only, and a custom
type shadowing a preset — all through real HTTP/WS behaviour."""

from app.domain.agent_type.library import preset_types
from tests.integration.conftest import chat_ws_url


def _list_types(client) -> list[dict]:
    r = client.get("/agent-types")
    assert r.status_code == 200
    return r.json()["data"]["data"]


def _create_type(client, **overrides) -> dict:
    payload = {
        "name": "data-science",
        "title": "数据科学",
        "description": "数据分析与建模",
        "body": "你是一位数据科学导师，擅长统计与机器学习。",
    }
    payload.update(overrides)
    r = client.post("/agent-types", json=payload)
    assert r.status_code == 200, r.text
    return r.json()["data"]


def test_list_includes_presets(client):
    items = _list_types(client)
    by_name = {i["name"]: i for i in items}
    assert "fullstack-engineer" in by_name
    fullstack = by_name["fullstack-engineer"]
    assert fullstack["builtin"] is True
    assert fullstack["title"] == "全栈工程"
    assert "全栈工程专家" in fullstack["body"]


def test_custom_type_crud(client):
    created = _create_type(client)
    assert created["builtin"] is False
    assert created["created_at"] is not None

    names = [i["name"] for i in _list_types(client)]
    assert "data-science" in names

    r = client.put("/agent-types/data-science", json={"body": "新的 persona 正文。"})
    assert r.status_code == 200
    assert r.json()["data"]["body"] == "新的 persona 正文。"
    # Untouched fields survive a partial update.
    assert r.json()["data"]["title"] == "数据科学"

    r = client.delete("/agent-types/data-science")
    assert r.status_code == 200
    assert "data-science" not in [i["name"] for i in _list_types(client)]


def test_a_type_carries_how_it_runs_not_just_who_it_is(client):
    """The half a persona never covered: tool surface and runtime knobs."""
    created = _create_type(
        client,
        name="ops-agent",
        skills=["deploy", "oncall"],
        mcp_servers=["grafana"],
        model="claude-opus-5",
        effort="high",
        harness="claude-code",
    )
    assert created["skills"] == ["deploy", "oncall"]
    assert created["mcp_servers"] == ["grafana"]
    assert (created["model"], created["effort"], created["harness"]) == (
        "claude-opus-5",
        "high",
        "claude-code",
    )

    fetched = {i["name"]: i for i in _list_types(client)}["ops-agent"]
    assert fetched["skills"] == ["deploy", "oncall"]
    assert fetched["model"] == "claude-opus-5"


def test_duplicate_custom_name_rejected(client):
    _create_type(client)
    r = client.post(
        "/agent-types",
        json={"name": "data-science", "title": "重复", "body": "x"},
    )
    assert r.status_code == 422
    assert r.json()["code"] == 422


def test_invalid_type_name_rejected(client):
    r = client.post("/agent-types", json={"name": "Bad Name!", "body": "x"})
    assert r.status_code == 422


def test_preset_types_are_read_only(client):
    r = client.put("/agent-types/fullstack-engineer", json={"body": "篡改"})
    assert r.status_code == 422
    r = client.delete("/agent-types/fullstack-engineer")
    assert r.status_code == 422
    # Unknown names 404 (distinct from the read-only case).
    r = client.delete("/agent-types/no-such-type")
    assert r.status_code == 404


def test_custom_shadows_preset_in_the_catalog(client):
    custom_body = "你是隐藏关卡的自定义学术角色。"
    _create_type(
        client, name="academic-research", title="学术研究(自定义)", body=custom_body
    )

    entries = [i for i in _list_types(client) if i["name"] == "academic-research"]
    assert len(entries) == 1
    assert entries[0]["builtin"] is False
    assert entries[0]["body"] == custom_body


def test_the_agents_type_is_what_reaches_the_system_prompt(client, stub_agent):
    """End-to-end: the persona 芝士 speaks with comes from the type its agent
    wears — and a custom type shadowing a preset wins."""
    custom_body = "你是自定义的评审专家人格。"
    _create_type(client, name="academic-research", title="评审", body=custom_body)

    pr = client.post("/projects", json={"name": "TypeDemo"})
    project_id = pr.json()["data"]["id"]
    r = client.put(
        f"/projects/{project_id}/default-agent",
        json={"type_name": "academic-research"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["type_name"] == "academic-research"

    tr = client.post(
        "/topics",
        json={"project_id": project_id, "title": "t", "created_by": "u"},
    )
    topic_id = tr.json()["data"]["id"]

    with client.websocket_connect(chat_ws_url(topic_id, "u")) as ws:
        ws.send_json({"type": "message", "content": "你好", "summon": True})
        while True:
            frame = ws.receive_json()
            if frame["type"] in ("done", "error"):
                break

    assert stub_agent.last_system_prompt is not None
    assert custom_body in stub_agent.last_system_prompt
    preset_body = preset_types()["academic-research"].body
    assert preset_body not in stub_agent.last_system_prompt


def test_a_project_can_be_created_wearing_a_type(client):
    r = client.post("/projects", json={"name": "P", "agent_type": "product-design"})
    project_id = r.json()["data"]["id"]

    agents = client.get(f"/projects/{project_id}/agents").json()["data"]["data"]
    default = next(a for a in agents if a["is_default"])
    assert default["type_name"] == "product-design"


def test_setting_an_unknown_type_is_rejected(client):
    pr = client.post("/projects", json={"name": "P"})
    project_id = pr.json()["data"]["id"]

    r = client.put(
        f"/projects/{project_id}/default-agent", json={"type_name": "product-design"}
    )
    assert r.json()["data"]["type_name"] == "product-design"

    r = client.put(
        f"/projects/{project_id}/default-agent", json={"type_name": "ghost-type"}
    )
    assert r.status_code == 422

    # Clearing it leaves the agent in place — it is 芝士 without a specialty,
    # not the absence of an agent.
    r = client.put(f"/projects/{project_id}/default-agent", json={"type_name": ""})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["type_name"] is None
    assert r.json()["data"]["handle"] == "cheese"
