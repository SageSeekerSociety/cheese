"""Users see their session's sandbox, never the cloud host under it, and no
team counts cloud machines any more."""

import uuid

from app.core.config import settings
from app.domain.agent.compute_configs import standard_choice
from app.domain.agent_session.models import AgentSession
from app.domain.project.repositories import ProjectRepository
from tests.integration.conftest import new_project, session_auth_headers

HOST_DEVICE = "host-device-7f3a"


def test_a_cloud_sessions_sandbox_is_shown_without_its_host(client, monkeypatch):
    monkeypatch.setattr(settings, "microcloud_base_url", "https://example.invalid")
    monkeypatch.setattr(settings, "microcloud_tenant_secret", "test-only")
    project = new_project(client, "Sandboxes", owner="alice")
    room = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "Room"},
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]

    async def seed():
        async with client.test_request_factory() as db:
            generation = str(uuid.uuid4())
            db.add(
                AgentSession(
                    topic_id=uuid.UUID(room),
                    agent_handle="agent",
                    harness="claude-code",
                    execution_request={
                        "generation": generation,
                        "choice": standard_choice("cloud").model_dump(),
                        "authorized_by": None,
                    },
                    work_lease={
                        "kind": "device",
                        "generation": generation,
                        "resource_id": generation,
                        "device_id": HOST_DEVICE,
                        "status": "ready",
                        "home": f"/home/cheese/.cheese/{generation}",
                        "workspace": f"/home/cheese/.cheese/{generation}/work",
                        "state": "/state",
                        "mcp_servers": [],
                    },
                )
            )
            await db.commit()

    client.portal.call(seed)

    answer = client.get(
        f"/topics/{room}/compute-profile", headers=session_auth_headers("alice")
    )

    assert answer.status_code == 200, answer.text
    [session] = answer.json()["data"]["sessions"]
    assert session["lease"] == {"status": "ready", "online": False}
    assert HOST_DEVICE not in answer.text
    assert "/home/cheese" not in answer.text


def test_no_route_lists_counts_or_powers_cloud_machines(client):
    project = new_project(client, "No machines", owner="alice")
    headers = session_auth_headers("alice")

    async def team_of_project():
        async with client.test_request_factory() as db:
            return await ProjectRepository(db).team_for_project(
                uuid.UUID(project["id"])
            )

    team_id = client.portal.call(team_of_project)
    for method, path in (
        ("get", f"/projects/{project['id']}/machines"),
        ("delete", f"/projects/{project['id']}/machines/{uuid.uuid4()}"),
        ("post", f"/projects/{project['id']}/machines/{uuid.uuid4()}/suspend"),
        ("get", f"/projects/{project['id']}/cloud-supply"),
        ("get", f"/teams/{team_id}/resource-quotas"),
    ):
        response = getattr(client, method)(path, headers=headers)
        assert response.status_code in (404, 405), (path, response.status_code)

    limits = client.get("/projects/resource-limits", headers=headers)
    assert limits.status_code == 200, limits.text
    assert limits.json()["data"] == {
        "max_concurrent_turns": settings.max_concurrent_turns
    }


def test_a_cloud_choice_carries_no_machine_size(client, monkeypatch):
    monkeypatch.setattr(settings, "microcloud_base_url", "https://example.invalid")
    monkeypatch.setattr(settings, "microcloud_tenant_secret", "test-only")
    project = new_project(client, "No sizes", owner="alice")
    room = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "Room"},
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]

    sized = client.put(
        f"/topics/{room}/compute-profile",
        json={"choice": {"profile": "cloud", "cores": 8}},
        headers=session_auth_headers("alice"),
    )

    assert sized.status_code in (400, 422), sized.text
