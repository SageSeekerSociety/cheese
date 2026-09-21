"""Edits belong to one project agent and survive moves between rooms."""

from app.domain.agent_instance.configuration import AgentConfiguration
from app.domain.agent_instance.services import AgentInstanceService
from app.domain.agent_type.library import preset_types
from app.domain.project.services import ProjectService
from tests.integration.conftest import session_auth_headers


def test_agents_own_independent_configuration(db_session, _portal, monkeypatch):
    async def run():
        project = await ProjectService(db_session).create(
            name="Agents", forge_kind="github_app"
        )
        service = AgentInstanceService(db_session)
        first = await service.create(
            project_id=project.id,
            handle="first",
            type_name="product-design",
            display_name="First",
        )
        second = await service.create(
            project_id=project.id,
            handle="second",
            type_name="product-design",
            display_name="Second",
        )
        original = dict(second.configuration)
        await service.configure(
            first,
            AgentConfiguration(
                **{**first.configuration, "body": "Only review security"}
            ),
        )
        assert second.configuration == original
        assert (
            await service.system_prompt(service.resolved(first))
        ) == "Only review security"
        # Replacing the creation preset cannot change an existing agent. A
        # preset is where a teammate STARTED, not what it is: editing one is a
        # change to what gets created next, and an agent somebody has been
        # working with must not silently acquire a different role because the
        # catalogue moved under it.
        monkeypatch.setitem(
            preset_types(), "product-design", preset_types()["fullstack-engineer"]
        )
        assert second.configuration == original
        assert await service.system_prompt(service.resolved(second)) == original["body"]

    _portal.call(run)


def test_a_saved_agent_carries_no_model_and_no_harness(client):
    """一个队友存的是角色。模型和运行方式连一个键都不留下——留着就等于在活的
    绑定之外开了第二个住处，而两处都能设的东西，没人答得上来哪一处说了算。
    """
    retired = {"model", "harness", "effort"}
    pid = client.post("/projects", json={"name": "Models"}).json()["data"]["id"]
    default = client.get(f"/projects/{pid}/agents").json()["data"]["data"][0]
    assert not retired & set(default["configuration"])

    # 客户端硬塞进来的那三个键同样落不进库：这一版之后没有一条写路径再写它们。
    client.put(
        f"/projects/{pid}/agents/{default['id']}",
        json={"configuration": {"body": "Review", "model": "opus", "harness": "codex"}},
    )
    saved = client.get(f"/projects/{pid}/agents").json()["data"]["data"][0]
    assert saved["configuration"]["body"] == "Review"
    assert not retired & set(saved["configuration"])


def test_room_switch_preserves_the_selected_agents_role(client):
    pid = client.post("/projects", json={"name": "Rooms"}).json()["data"]["id"]
    agent = client.post(
        f"/projects/{pid}/agents",
        json={"display_name": "Reviewer", "configuration": {"body": "Review"}},
    ).json()["data"]
    for title in ["First room", "Second room"]:
        room = client.post(
            "/topics",
            json={"project_id": pid, "title": title, "created_by": "alice"},
        ).json()["data"]
        r = client.post(
            f"/topics/{room['id']}/members",
            json={"handle": agent["seat_handle"], "role": "member", "actor": "alice"},
            headers=session_auth_headers("alice"),
        )
        assert r.status_code == 200, r.text
    updated = client.put(
        f"/projects/{pid}/agents/{agent['id']}",
        json={"configuration": {"body": "Review security only"}},
    ).json()["data"]
    assert updated["id"] == agent["id"]
    assert updated["handle"] == agent["handle"]
    assert updated["configuration"]["body"] == "Review security only"
