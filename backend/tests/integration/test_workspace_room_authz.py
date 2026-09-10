"""A project member must not read or overwrite another person's private work."""

import uuid

import pytest

from app.core.sandbox_auth import mint_scoped_token
from app.domain.workspace import service as ws
from tests.delivery import delivery_task_id
from tests.integration.conftest import session_auth_headers
from tests.machine_work import machine_commits


@pytest.fixture
def private_workspace(client):
    alice = session_auth_headers("alice")
    project = client.post(
        "/projects", json={"name": "Room authorization", "owner_handle": "alice"}
    ).json()["data"]
    pid = project["id"]
    response = client.post(
        f"/projects/{pid}/members", json={"user_handle": "bob"}, headers=alice
    )
    assert response.status_code == 200
    private = client.get(
        f"/projects/{pid}/private-chat",
        params={"user_handle": "alice", "peer_handle": "carol"},
        headers=alice,
    )
    assert private.status_code == 200, private.text
    tid = private.json()["data"]["id"]
    machine_commits(
        uuid.UUID(pid),
        delivery_task_id(client, tid),
        {"private.txt": "private draft\n"},
    )
    return project, tid


def request_workspace(client, project_id, topic_id, operation, headers):
    params = {"topic": topic_id, "path": "private.txt"}
    task = next(
        (
            task
            for room, task in getattr(client, "delivery_tasks", {}).items()
            if str(room) == str(topic_id)
        ),
        None,
    )
    if task is not None:
        params["task"] = str(task.id)
    if operation == "write":
        return client.put(
            f"/projects/{project_id}/file",
            params=params,
            json={"path": "private.txt", "content": "updated draft\n"},
            headers=headers,
        )
    if operation == "work-summary":
        return client.get(
            f"/projects/{project_id}/topics/{topic_id}/work-summary", headers=headers
        )
    return client.get(
        f"/projects/{project_id}/{operation}", params=params, headers=headers
    )


@pytest.mark.parametrize(
    "operation",
    ["files", "file", "file/raw", "git/log", "git/diff", "work-summary", "write"],
)
def test_project_members_cannot_access_private_room_work(
    client, private_workspace, operation
):
    project, tid = private_workspace
    denied = request_workspace(
        client, project["id"], tid, operation, session_auth_headers("bob")
    )
    assert denied.status_code == 403, denied.text
    assert "private draft" not in denied.text
    assert (
        ws.read_file(
            uuid.UUID(project["id"]), "private.txt", delivery_task_id(client, tid)
        )
        == "private draft\n"
    )
    allowed = request_workspace(
        client, project["id"], tid, operation, session_auth_headers("alice")
    )
    assert allowed.status_code == 200, allowed.text


def test_room_from_another_project_is_rejected_before_workspace_lookup(
    client, private_workspace
):
    project, tid = private_workspace
    other = client.post(
        "/projects", json={"name": "Other", "owner_handle": "alice"}
    ).json()["data"]
    response = request_workspace(
        client, other["id"], tid, "files", session_auth_headers("alice")
    )
    assert response.status_code == 404
    response = request_workspace(
        client, other["id"], tid, "work-summary", session_auth_headers("alice")
    )
    assert response.status_code == 404


@pytest.mark.parametrize("end", ["close", "archive"])
def test_finished_work_remains_readable_but_cannot_be_overwritten(
    client, private_workspace, end
):
    project, tid = private_workspace
    task = delivery_task_id(client, tid)
    path = (
        f"/topics/{tid}/tasks/{task}/close"
        if end == "close"
        else f"/topics/{tid}/archive"
    )
    result = client.post(
        path, json={"by": "alice"}, headers=session_auth_headers("alice")
    )
    assert result.status_code == 200, result.text
    denied = request_workspace(
        client, project["id"], tid, "write", session_auth_headers("alice")
    )
    assert denied.status_code == 422, denied.text
    assert "只读" in denied.text
    allowed = request_workspace(
        client, project["id"], tid, "file", session_auth_headers("alice")
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["data"]["content"] == "private draft\n"


def test_topic_token_cannot_read_a_different_rooms_work(client, private_workspace):
    project, tid = private_workspace
    token = mint_scoped_token(
        project_id=project["id"], topic_id=project["root_topic_id"]
    )
    response = request_workspace(
        client, project["id"], tid, "file", {"X-Cheese-Token": token}
    )
    assert response.status_code == 403, response.text


def test_matching_room_agent_can_read_its_own_work(client):
    project = client.post(
        "/projects", json={"name": "Agent access", "owner_handle": "alice"}
    ).json()["data"]
    pid, tid = project["id"], project["root_topic_id"]
    machine_commits(
        uuid.UUID(pid),
        delivery_task_id(client, tid),
        {"private.txt": "private draft\n"},
    )
    token = mint_scoped_token(project_id=pid, topic_id=tid)
    response = request_workspace(client, pid, tid, "file", {"X-Cheese-Token": token})
    assert response.status_code == 200, response.text
    assert response.json()["data"]["content"] == "private draft\n"


def test_project_diff_cannot_name_a_private_branch(client, private_workspace):
    project, tid = private_workspace
    branch = ws.branch_for_task(delivery_task_id(client, tid))
    response = client.get(
        f"/projects/{project['id']}/git/diff",
        params={"ref": branch},
        headers=session_auth_headers("bob"),
    )
    assert response.status_code == 404, response.text
    assert "private draft" not in response.text


def test_path_room_cannot_be_overridden_by_a_public_topic_query(
    client, private_workspace
):
    project, tid = private_workspace
    response = client.get(
        f"/projects/{project['id']}/topics/{tid}/work-summary",
        params={"topic": project["root_topic_id"]},
        headers=session_auth_headers("bob"),
    )
    assert response.status_code == 403, response.text


def test_project_diff_still_reads_accepted_history(client, private_workspace):
    project, _ = private_workspace
    pid = uuid.UUID(project["id"])
    tid = uuid.UUID(project["root_topic_id"])
    machine_commits(
        pid, delivery_task_id(client, tid), {"public.txt": "accepted content\n"}
    )
    assert ws.merge_topic(
        pid,
        delivery_task_id(client, tid),
        message="feat: accept public work\n\nRequested-by: alice",
    )["merged"]
    accepted = ws.accepted_revision(pid)
    for ref in ("main", accepted):
        response = client.get(
            f"/projects/{pid}/git/diff",
            params={"ref": ref},
            headers=session_auth_headers("bob"),
        )
        assert response.status_code == 200, response.text
        assert "accepted content" in response.json()["data"]["diff"]
        assert "private draft" not in response.text


def test_malformed_work_summary_room_is_rejected(client, private_workspace):
    project, _ = private_workspace
    response = request_workspace(
        client,
        project["id"],
        "not-a-uuid",
        "work-summary",
        session_auth_headers("alice"),
    )
    assert response.status_code == 404


@pytest.mark.parametrize(
    "endpoint, body",
    [
        ("bind", {"agent_id": "outsider"}),
        ("title", {"title": "unauthorized edit"}),
        ("close", {"conclusion": "unauthorized close"}),
    ],
)
def test_task_management_keeps_room_authorization(
    client, private_workspace, endpoint, body
):
    _, room = private_workspace
    task = delivery_task_id(client, room)
    denied = client.post(
        f"/topics/{room}/tasks/{task}/{endpoint}",
        json=body,
        headers=session_auth_headers("bob"),
    )
    assert denied.status_code == 403, denied.text


def test_git_manifest_rejects_another_room_token(client, private_workspace):
    project, room = private_workspace
    task = delivery_task_id(client, room)
    token = mint_scoped_token(
        project_id=project["id"], topic_id=project["root_topic_id"]
    )
    response = client.get(
        f"/projects/{project['id']}/git/tasks/{task}", headers={"X-Cheese-Token": token}
    )
    assert response.status_code in (401, 403, 404), response.text
    assert "branch" not in response.json().get("data", {})
