"""Committed file endpoints read changes pushed by task machines."""

import uuid

from tests.delivery import delivery_task_id
from tests.integration.conftest import post_project
from tests.machine_work import declare_task, machine_commits


def _mkproject(client) -> uuid.UUID:
    resp = post_project(client, json={"name": "P", "owner_handle": "alice"}).json()
    return uuid.UUID(resp["data"]["id"])


def _mktopic(client, pid: uuid.UUID) -> uuid.UUID:
    response = client.post("/topics", json={"project_id": str(pid), "title": "Files"})
    assert response.status_code == 200
    return uuid.UUID(response.json()["data"]["id"])


def _owner(client) -> dict[str, str]:
    """These routes return the source, so they need a caller with a claim on the
    project; the tests used to reach them with no credential at all."""
    from tests.integration.test_connector_viewer import _login

    return {"Authorization": f"Bearer {_login(client, 'alice')}"}


def _native_edit(pid: uuid.UUID, topic_id: uuid.UUID, path: str, content: str) -> None:
    """One turn: the machine writes a file, commits it, and pushes the branch."""
    declare_task(pid, topic_id)
    machine_commits(pid, topic_id, {path: content})


def test_file_endpoints_survive_the_agent_committing(client):
    """A task push remains readable without affecting another task."""
    pid = _mkproject(client)
    tid = _mktopic(client, pid)
    task_id = delivery_task_id(client, tid)
    _native_edit(pid, task_id, "note.md", "hello\n")

    machine_commits(pid, task_id, {"from_the_agent.md": "written by the agent\n"})

    listed = client.get(
        f"/projects/{pid}/files",
        params={"topic": str(tid), "task": str(task_id), "source": "committed"},
        headers=_owner(client),
    )
    assert listed.status_code == 200
    paths = {f["path"] for f in listed.json()["data"]["data"]}
    assert {"note.md", "from_the_agent.md"} <= paths

    body = client.get(
        f"/projects/{pid}/file",
        params={
            "topic": str(tid),
            "task": str(task_id),
            "path": "note.md",
            "source": "committed",
        },
        headers=_owner(client),
    )
    assert body.status_code == 200

    # A second topic in the same project — the blast radius that made this a P0.
    other = _mktopic(client, pid)
    other_task = delivery_task_id(client, other)
    _native_edit(pid, other_task, "other.md", "still fine\n")
    assert (
        client.get(
            f"/projects/{pid}/files",
            params={
                "topic": str(other),
                "task": str(other_task),
                "source": "committed",
            },
            headers=_owner(client),
        ).status_code
        == 200
    )


def test_git_diff_rejects_option_injection(client):
    pid = _mkproject(client)
    r = client.get(
        f"/projects/{pid}/git/diff",
        params={"ref": "--help"},
        headers=_owner(client),
    )
    assert r.status_code == 422
