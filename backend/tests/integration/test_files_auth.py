"""Who can read a project's files.

Same shape as the terminal routes: the handler checks that the project EXISTS,
which is not a check on the caller. Worth pinning explicitly because what these
routes return is the source itself, not a rendering of it.
"""

import pytest

from tests.delivery import delivery_task_id
from tests.integration.conftest import post_project
from tests.integration.test_file_panel_safety import (
    _mkproject,
    _mktopic,
    _owner,
    _put,
    _worktree,
    task_machine,  # noqa: F401
)


def test_listing_a_projects_files_needs_a_credential(client):
    project = post_project(
        client, json={"name": "Secret", "owner_handle": "alice"}
    ).json()["data"]

    resp = client.get(f"/projects/{project['id']}/files")

    assert resp.status_code in (401, 403, 404), (
        f"an unauthenticated caller got {resp.status_code}: {resp.text[:200]}"
    )


def test_reading_a_file_needs_a_credential(client):
    project = post_project(
        client, json={"name": "Secret2", "owner_handle": "alice"}
    ).json()["data"]

    resp = client.get(f"/projects/{project['id']}/file", params={"path": "README.md"})

    assert resp.status_code in (401, 403, 404), (
        f"an unauthenticated caller got {resp.status_code}: {resp.text[:200]}"
    )


@pytest.mark.usefixtures("task_machine")
def test_live_listing_tracks_machine_writes_without_discarding_edits(client):
    project = _mkproject(client)
    room = _mktopic(client, project)
    _put(client, project, room, "being_edited.txt", b"a human is typing here\n")
    params = {"task": str(delivery_task_id(client, room)), "source": "live"}
    headers = _owner(client)

    def listed():
        response = client.get(
            f"/projects/{project}/files", params=params, headers=headers
        )
        assert response.status_code == 200, response.text
        return {item["path"] for item in response.json()["data"]["data"]}

    assert "being_edited.txt" in listed()
    _put(client, project, room, "from_machine.txt", b"new work\n")
    assert {"being_edited.txt", "from_machine.txt"} <= listed()
    assert (_worktree(client, room) / "being_edited.txt").read_bytes() == (
        b"a human is typing here\n"
    )
