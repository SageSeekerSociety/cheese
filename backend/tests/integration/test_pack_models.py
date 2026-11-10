"""A 题目's 资源包 can give its projects a model kept off the platform's menu.

The case it exists for: an institution pays for its own model supply for one
course (moark tokens for the eTrip board). Those models must reach the
projects opened from that course's 题目 and no other project on the platform,
or other people spend the course's tokens.
"""

import pytest

from app.core.config import settings
from app.domain.agent import gateway_catalog
from app.domain.agent.gateway import GatewayModel
from tests.conftest import seed_task_with_protocol, seed_user, start_task_project
from tests.integration.conftest import post_project

COURSE_MODEL = "course-model"


@pytest.fixture
def catalog(monkeypatch, client):
    monkeypatch.setattr(settings, "agent_model", "menu-model")
    gateway_catalog.reset()

    class Gateway:
        async def models(self):
            return [
                GatewayModel("menu-model", "Menu Model", True, True),
                # Routed and priced, kept off the menu.
                GatewayModel(COURSE_MODEL, "Course Model", False, True),
                GatewayModel("paused-course-model", "Paused", False, True, live=False),
            ]

    client.portal.call(gateway_catalog.refresh, Gateway())
    yield
    gateway_catalog.reset()


def _picker(client, project_id: str) -> dict:
    resp = client.get(f"/projects/{project_id}/default-model")
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def test_a_course_model_reaches_only_the_projects_its_pack_gave_it_to(client, catalog):
    task_id = seed_task_with_protocol(
        client,
        resource_pack={
            "model": COURSE_MODEL,
            "models": [COURSE_MODEL, "paused-course-model"],
        },
    )
    client.headers["Authorization"] = f"Bearer {seed_user(client, 'u1')}"
    from_course = start_task_project(client, task_id, handle="u1")
    elsewhere = post_project(client, json={"name": "Own"}).json()["data"]["id"]

    course = _picker(client, from_course)
    assert course["model"] == COURSE_MODEL
    assert COURSE_MODEL in {c["id"] for c in course["choices"]}
    # A paused model is not offered, whatever a pack says.
    assert "paused-course-model" not in {c["id"] for c in course["choices"]}

    own = _picker(client, elsewhere)
    assert COURSE_MODEL not in {c["id"] for c in own["choices"]}
    # The same person cannot point another project at it either.
    resp = client.put(
        f"/projects/{elsewhere}/default-model", json={"model": COURSE_MODEL}
    )
    assert resp.status_code >= 400, resp.text


def test_a_default_alone_grants_nothing(client, catalog):
    """``model`` is only a starting value: a pack that names an off-menu model
    there without granting it leaves the project on a model it cannot run,
    and the picker shows it as unavailable rather than offering it."""
    task_id = seed_task_with_protocol(client, resource_pack={"model": COURSE_MODEL})
    client.headers["Authorization"] = f"Bearer {seed_user(client, 'u1')}"
    project_id = start_task_project(client, task_id, handle="u1")

    assert COURSE_MODEL not in {c["id"] for c in _picker(client, project_id)["choices"]}
