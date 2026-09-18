"""Edits belong to one project agent and survive moves between rooms."""

from app.core.config import settings
from app.domain.agent_instance.configuration import AgentConfiguration
from app.domain.agent_instance.services import AgentInstanceService
from app.domain.agent_type.library import preset_types
from app.domain.project.services import ProjectService
from tests.integration.conftest import session_auth_headers


def test_agents_own_independent_configuration(db_session, _portal, monkeypatch):
    monkeypatch.setattr(settings, "subscription_enabled", True)

    async def run():
        project = await ProjectService(db_session).create(name="Agents")
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
                **{
                    **first.configuration,
                    "body": "Only review security",
                    "model": "opus",
                }
            ),
        )
        assert second.configuration == original
        assert (await service.model(service.resolved(first))) == "opus"
        assert (
            await service.system_prompt(service.resolved(first))
        ) == "Only review security"
        # Replacing the creation preset cannot change an existing agent. A
        # preset is where a teammate STARTED, not what it is: editing one is a
        # change to what gets created next, and an agent somebody has been
        # working with must not silently acquire a different role, model or
        # harness because the catalogue moved under it.
        monkeypatch.setitem(
            preset_types(), "product-design", preset_types()["fullstack-engineer"]
        )
        assert second.configuration == original
        assert await service.system_prompt(service.resolved(second)) == original["body"]
        project.settings = {"subscription_model": "fable"}
        assert await service.model(service.resolved(second)) == original["model"]

    _portal.call(run)


def test_agent_configuration_api_rejects_unavailable_model(client, monkeypatch):
    monkeypatch.setattr(settings, "subscription_enabled", True)
    project = client.post("/projects", json={"name": "Models"}).json()["data"]
    pid = project["id"]
    agents = client.get(f"/projects/{pid}/agents").json()["data"]["data"]
    default = agents[0]
    assert default["id"] and default["configuration"]["model"] == "sonnet"
    result = client.put(
        f"/projects/{pid}/agents/{default['id']}",
        json={"configuration": {**default["configuration"], "model": "unknown"}},
    )
    assert result.status_code == 422
    assert (
        client.get(f"/projects/{pid}/agents").json()["data"]["data"][0]["configuration"]
        == default["configuration"]
    )


def test_room_switch_preserves_the_selected_agents_model(client, monkeypatch):
    monkeypatch.setattr(settings, "subscription_enabled", True)
    pid = client.post("/projects", json={"name": "Rooms"}).json()["data"]["id"]
    agent = client.post(
        f"/projects/{pid}/agents",
        json={
            "display_name": "Reviewer",
            "configuration": {"model": "opus", "body": "Review"},
        },
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
        json={"configuration": {**agent["configuration"], "model": "fable"}},
    ).json()["data"]
    assert updated["id"] == agent["id"]
    assert updated["handle"] == agent["handle"]
    assert updated["configuration"]["model"] == "fable"
