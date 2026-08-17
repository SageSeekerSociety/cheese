"""Roles API (spec §8.2): merged catalog, custom CRUD, built-in read-only,
custom-shadows-builtin resolution — all through real HTTP/WS behavior."""

import asyncio

from app.domain.agent.roles import builtin_roles, resolve_role_description
from tests.integration.conftest import chat_ws_url


def _list_roles(client) -> list[dict]:
    r = client.get("/roles")
    assert r.status_code == 200
    return r.json()["data"]["data"]


def _create_role(client, **overrides) -> dict:
    payload = {
        "name": "data-science",
        "title": "数据科学",
        "description": "数据分析与建模",
        "body": "你是一位数据科学导师，擅长统计与机器学习。",
    }
    payload.update(overrides)
    r = client.post("/roles", json=payload)
    assert r.status_code == 200, r.text
    return r.json()["data"]


def test_list_includes_builtins(client):
    items = _list_roles(client)
    by_name = {i["name"]: i for i in items}
    assert "fullstack-engineer" in by_name
    fullstack = by_name["fullstack-engineer"]
    assert fullstack["builtin"] is True
    assert fullstack["title"] == "全栈工程"
    assert "全栈工程专家" in fullstack["body"]


def test_custom_role_crud(client):
    created = _create_role(client)
    assert created["builtin"] is False
    assert created["created_at"] is not None

    names = [i["name"] for i in _list_roles(client)]
    assert "data-science" in names

    r = client.put("/roles/data-science", json={"body": "新的 persona 正文。"})
    assert r.status_code == 200
    assert r.json()["data"]["body"] == "新的 persona 正文。"
    # Untouched fields survive a partial update.
    assert r.json()["data"]["title"] == "数据科学"

    r = client.delete("/roles/data-science")
    assert r.status_code == 200
    assert "data-science" not in [i["name"] for i in _list_roles(client)]


def test_duplicate_custom_name_rejected(client):
    _create_role(client)
    r = client.post(
        "/roles",
        json={"name": "data-science", "title": "重复", "body": "x"},
    )
    assert r.status_code == 422
    assert r.json()["code"] == 422


def test_invalid_role_name_rejected(client):
    r = client.post("/roles", json={"name": "Bad Name!", "body": "x"})
    assert r.status_code == 422


def test_builtin_roles_are_read_only(client):
    r = client.put("/roles/fullstack-engineer", json={"body": "篡改"})
    assert r.status_code == 422
    r = client.delete("/roles/fullstack-engineer")
    assert r.status_code == 422
    # Unknown names 404 (distinct from the read-only case).
    r = client.delete("/roles/no-such-role")
    assert r.status_code == 404


def test_custom_shadows_builtin_in_catalog_and_resolution(client):
    custom_body = "你是隐藏关卡的自定义学术角色。"
    _create_role(
        client, name="academic-research", title="学术研究(自定义)", body=custom_body
    )

    # Catalog: exactly one entry for the name, and it's the custom one.
    entries = [i for i in _list_roles(client) if i["name"] == "academic-research"]
    assert len(entries) == 1
    assert entries[0]["builtin"] is False
    assert entries[0]["body"] == custom_body

    # Prompt resolution: custom (DB) > builtin file > None.
    async def _resolve(name: str | None) -> str | None:
        async with client.test_factory() as session:
            return await resolve_role_description(session, name)

    assert asyncio.run(_resolve("academic-research")) == custom_body
    builtin_body = builtin_roles()["fullstack-engineer"].body
    assert asyncio.run(_resolve("fullstack-engineer")) == builtin_body
    assert asyncio.run(_resolve("no-such-role")) is None
    assert asyncio.run(_resolve(None)) is None


def test_custom_role_injected_into_chat_system_prompt(client, stub_agent):
    """End-to-end: a project whose expert_role names a custom role gets that
    persona in 芝士's system prompt (and not the shadowed builtin's)."""
    custom_body = "你是自定义的评审专家人格。"
    _create_role(client, name="academic-research", title="评审", body=custom_body)

    pr = client.post("/projects", json={"name": "RoleDemo"})
    project_id = pr.json()["data"]["id"]
    r = client.put(
        f"/projects/{project_id}/expert-role", json={"role": "academic-research"}
    )
    assert r.json()["data"]["current"] == "academic-research"

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
    builtin_body = builtin_roles()["academic-research"].body
    assert builtin_body not in stub_agent.last_system_prompt


def test_set_project_expert_role_validation(client):
    pr = client.post("/projects", json={"name": "P"})
    project_id = pr.json()["data"]["id"]

    # A builtin name works.
    r = client.put(
        f"/projects/{project_id}/expert-role", json={"role": "product-design"}
    )
    assert r.json()["data"]["current"] == "product-design"
    # Unknown names are rejected.
    r = client.put(f"/projects/{project_id}/expert-role", json={"role": "ghost-role"})
    assert r.status_code == 422
    # Empty clears.
    r = client.put(f"/projects/{project_id}/expert-role", json={"role": ""})
    assert r.json()["data"]["current"] is None
