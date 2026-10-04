"""Edits belong to one project agent and survive moves between rooms."""

from app.domain.agent_instance.configuration import AgentConfiguration
from app.domain.agent_instance.services import AgentInstanceService
from app.domain.agent_type.library import preset_types
from app.domain.project.services import ProjectService
from tests.integration.conftest import (
    add_external_member,
    post_project,
    put_on_plan,
    registered,
    session_auth_headers,
)


def test_agents_own_independent_configuration(db_session, _portal, monkeypatch):
    async def run():
        await registered(db_session, "owner")
        project = await ProjectService(db_session).create(
            owner_handle="owner", name="Agents", forge_kind="github_app"
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


def test_a_saved_agent_can_override_model_but_not_harness(client):
    """A teammate can select a model while the harness remains project-owned."""
    retired = {"harness"}
    project = post_project(client, json={"name": "Models"}).json()["data"]
    pid = project["id"]

    # Opus is a subscription model, which Free leaves out.
    async def reserve() -> None:
        async with client.test_request_factory() as session:
            await put_on_plan(session, project["team_id"], "reserve")
            await session.commit()

    client.portal.call(reserve)
    default = client.get(f"/projects/{pid}/agents").json()["data"]["data"][0]
    assert not retired & set(default["configuration"])
    assert default["configuration"]["model"] is None

    client.put(
        f"/projects/{pid}/agents/{default['id']}",
        json={
            "configuration": {
                "body": "Review",
                "model": "opus",
                "harness": "codex",
            }
        },
    )
    saved = client.get(f"/projects/{pid}/agents").json()["data"]["data"][0]
    assert saved["configuration"]["body"] == "Review"
    assert saved["configuration"]["model"] == "opus"
    assert not retired & set(saved["configuration"])


def test_room_switch_preserves_the_selected_agents_role(client):
    pid = post_project(client, json={"name": "Rooms"}, owner="alice").json()["data"][
        "id"
    ]
    agent = client.post(
        f"/projects/{pid}/agents",
        json={"display_name": "Reviewer", "configuration": {"body": "Review"}},
    ).json()["data"]
    for title in ["First room", "Second room"]:
        room = client.post(
            "/topics",
            json={"project_id": pid, "title": title},
            headers=session_auth_headers("alice"),
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


def test_a_teammate_saves_how_hard_it_thinks_and_when_it_compacts(client):
    """Effort and compaction share are a teammate's, saved with its role."""
    project = post_project(client, json={"name": "Effort"}, owner="alice").json()[
        "data"
    ]
    pid = project["id"]
    alice = session_auth_headers("alice")
    default = client.get(f"/projects/{pid}/agents", headers=alice).json()["data"][
        "data"
    ][0]

    saved = client.put(
        f"/projects/{pid}/agents/{default['id']}",
        json={"configuration": {"effort": "high", "compact_percent": 70}},
        headers=alice,
    )
    assert saved.status_code == 200, saved.text
    after = client.get(f"/projects/{pid}/agents", headers=alice).json()["data"]["data"][
        0
    ]
    assert after["configuration"]["effort"] == "high"
    assert after["configuration"]["compact_percent"] == 70

    for refused in ({"effort": "turbo"}, {"compact_percent": 95}):
        answer = client.put(
            f"/projects/{pid}/agents/{default['id']}",
            json={"configuration": refused},
            headers=alice,
        )
        assert answer.status_code == 400, answer.text


def test_only_a_manager_changes_a_teammates_advanced_settings(client):
    """The highest effort, compaction and skills are a manager's, as the project
    main model is; anything else on a teammate is any member's to change."""
    project = post_project(client, json={"name": "Advanced"}, owner="alice").json()[
        "data"
    ]
    pid = project["id"]
    add_external_member(client, pid, "bob", by="alice")
    bob = session_auth_headers("bob")
    default = client.get(f"/projects/{pid}/agents", headers=bob).json()["data"]["data"][
        0
    ]
    url = f"/projects/{pid}/agents/{default['id']}"

    for advanced in (
        {"effort": "max"},
        {"compact_percent": 60},
        {"skills": ["documents"]},
    ):
        refused = client.put(url, json={"configuration": advanced}, headers=bob)
        assert refused.status_code == 403, (advanced, refused.text)

    basic = client.put(
        url,
        json={"configuration": {"body": "Review", "effort": "high"}},
        headers=bob,
    )
    assert basic.status_code == 200, basic.text

    owner = client.put(
        url,
        json={"configuration": {"body": "Review", "compact_percent": 60}},
        headers=session_auth_headers("alice"),
    )
    assert owner.status_code == 200, owner.text
    # Saving it back as it is, with the role rewritten, is still bob's to do.
    kept = client.put(
        url,
        json={"configuration": {"body": "Review again", "compact_percent": 60}},
        headers=bob,
    )
    assert kept.status_code == 200, kept.text
