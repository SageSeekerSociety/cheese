"""Built-in starting configurations are read-only and copied into new agents."""

from tests.integration.conftest import post_project


def test_presets_are_read_only(client):
    response = client.get("/agent-types")
    assert response.status_code == 200
    presets = response.json()["data"]["data"]
    assert all(item["builtin"] for item in presets)
    assert any(item["name"] == "fullstack-engineer" for item in presets)
    assert client.post("/agent-types", json={"name": "custom"}).status_code == 405
    assert client.put(
        "/agent-types/fullstack-engineer", json={"body": "replace"}
    ).status_code in (404, 405)


def test_preset_initializes_a_project_agent(client):
    preset = next(
        item
        for item in client.get("/agent-types").json()["data"]["data"]
        if item["name"] == "fullstack-engineer"
    )
    project = post_project(
        client, json={"name": "Preset", "agent_type": preset["name"]}
    ).json()["data"]
    agent = client.get(f"/projects/{project['id']}/agents").json()["data"]["data"][0]
    assert agent["configuration"]["body"] == preset["body"]
    # 类型交出去的是角色，不含模型；实例带一栏模型但默认空着，继承项目主模型。
    assert "model" not in preset
    assert agent["configuration"]["model"] is None
