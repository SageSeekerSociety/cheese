"""Task metadata remains on the backend; native Git is served by the forge."""

import uuid

from app.core.sandbox_auth import mint_scoped_token
from tests.delivery import delivery_task
from tests.integration.conftest import post_project


def _project(client) -> str:
    return post_project(client, json={"name": "git 项目"}).json()["data"]["id"]


def test_backend_git_protocol_endpoint_is_retired(client):
    pid = _project(client)
    response = client.get(
        f"/projects/{pid}/git/info/refs?service=git-upload-pack",
        headers={"X-Cheese-Token": mint_scoped_token(project_id=pid)},
    )
    assert response.status_code == 404


def test_task_manifest_names_only_that_tasks_branch_and_target(client):
    project = post_project(client, json={"name": "Task manifest"}).json()["data"]
    task = delivery_task(client, project["root_topic_id"], commit=False)
    headers = {
        "X-Cheese-Token": mint_scoped_token(
            project_id=project["id"], topic_id=project["root_topic_id"]
        )
    }
    response = client.get(
        f"/projects/{project['id']}/git/tasks/{task.id}", headers=headers
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["task_id"] == str(task.id)
    assert data["room_id"] == project["root_topic_id"]
    assert data["branch"] == task.branch_name
    assert data["base"] == task.base_branch
    assert data["closed"] is False


def test_manifest_refuses_an_unknown_task(client):
    pid = _project(client)
    response = client.get(
        f"/projects/{pid}/git/tasks/{uuid.uuid4()}",
        headers={"X-Cheese-Token": mint_scoped_token(project_id=pid)},
    )
    assert response.status_code == 404


def test_room_branch_negotiation_endpoint_is_retired(client):
    pid = _project(client)
    response = client.post(
        f"/projects/{pid}/git/branch/{uuid.uuid4()}",
        headers={"X-Cheese-Token": mint_scoped_token(project_id=pid)},
    )
    assert response.status_code == 404


def test_task_author_is_the_agent_opening_work_not_the_dispatcher_or_room_default(
    client,
):
    from app.domain.project.models import Project
    from app.domain.repository import identity
    from app.domain.review.pr_text import pr_trailers
    from app.domain.topic.models import Topic
    from tests.integration.conftest import session_auth_headers

    project = post_project(
        client, json={"name": "Task authors", "owner_handle": "alice"}
    ).json()["data"]
    pid, room = project["id"], project["root_topic_id"]
    task = delivery_task(client, room, commit=False)
    assert task.created_by == "alice"
    agents = []
    for handle in ("writer", "reviewer"):
        response = client.post(f"/projects/{pid}/agents", json={"handle": handle})
        assert response.status_code == 200, response.text
        agent = response.json()["data"]
        added = client.post(
            f"/topics/{room}/members",
            json={"handle": agent["seat_handle"], "role": "member", "actor": "alice"},
            headers=session_auth_headers("alice"),
        )
        assert added.status_code == 200, added.text
        agents.append(agent)
    writer, reviewer = agents
    route = f"/projects/{pid}/git/tasks/{task.id}"

    def headers(agent):
        return {
            "X-Cheese-Token": mint_scoped_token(
                project_id=pid, topic_id=room, agent_handle=agent["seat_handle"]
            )
        }

    assert client.get(route, headers=headers(reviewer)).json()["data"]["author"] is None
    opened = client.post(route, headers=headers(writer))
    assert opened.status_code == 200, opened.text
    expected = str(identity.agent_identity(writer["seat_handle"]))
    assert opened.json()["data"]["author"] == expected

    async def change_default():
        async with client.test_factory() as session:
            current = await session.get(Project, uuid.UUID(pid))
            current.default_agent_instance_id = uuid.UUID(reviewer["id"])
            await session.commit()

    client.portal.call(change_default)
    reopened = client.post(route, headers=headers(reviewer))
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["data"]["author"] == expected

    async def trailers():
        async with client.test_factory() as session:
            topic = await session.get(Topic, uuid.UUID(room))
            who = await identity.attribution(session, topic, task_id=task.id)
            return pr_trailers(topic, "alice", who)

    assert f"Cheese-Agent: {writer['seat_handle']}" in client.portal.call(trailers)


def test_historical_task_does_not_invent_an_agent_author(client):
    from app.domain.repository import identity
    from app.domain.review.pr_text import pr_trailers
    from app.domain.topic.models import Topic

    project = post_project(client, json={"name": "Old task"}).json()["data"]
    task = delivery_task(client, project["root_topic_id"], commit=False)

    async def read():
        async with client.test_factory() as session:
            room = await session.get(Topic, task.room_id)
            who = await identity.attribution(session, room, task_id=task.id)
            assert who.author is None
            return pr_trailers(room, "alice", who)

    assert "Cheese-Agent:" not in client.portal.call(read)
