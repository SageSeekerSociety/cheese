"""Who can read a project's files.

Same shape as the terminal routes: the handler checks that the project EXISTS,
which is not a check on the caller. Worth pinning explicitly because what these
routes return is the source itself, not a rendering of it.
"""

import pytest

from tests.delivery import delivery_task_id
from tests.integration.conftest import post_project, session_token
from tests.integration.test_file_panel_safety import (
    _mkproject,
    _mktopic,
    _owner,
    _put,
    _worktree,
    task_machine,  # noqa: F401
)


def test_listing_a_projects_files_needs_a_credential(client):
    project = post_project(client, json={"name": "Secret"}, owner="alice").json()[
        "data"
    ]

    resp = client.get(f"/projects/{project['id']}/files")

    assert resp.status_code in (401, 403, 404), (
        f"an unauthenticated caller got {resp.status_code}: {resp.text[:200]}"
    )


def test_reading_a_file_needs_a_credential(client):
    project = post_project(client, json={"name": "Secret2"}, owner="alice").json()[
        "data"
    ]

    resp = client.get(f"/projects/{project['id']}/file", params={"path": "README.md"})

    assert resp.status_code in (401, 403, 404), (
        f"an unauthenticated caller got {resp.status_code}: {resp.text[:200]}"
    )


def test_a_lapsed_credential_is_told_it_is_lapsed_not_that_the_project_is_gone(client):
    """A token the server cannot verify is a 401, never 「项目不存在」.

    The 「改动」 tab showed the English sentence ``project not found`` on its task
    card, next to a working PR link: the browser held an access token that had
    lapsed, the route folded 认不出 into 没有凭据, answered 404 with no
    ``error.i18n``, and the frontend — which refreshes once on a 401 and has
    nothing to do with a 404 — printed the server's own words. 404 also says
    something false about the project, which is there.
    """
    project = post_project(client, json={"name": "Lapsed"}, owner="alice").json()[
        "data"
    ]

    lapsed = client.get(
        f"/projects/{project['id']}/files",
        headers={"Authorization": f"Bearer {session_token('alice', ttl_s=-3600)}"},
    )

    assert lapsed.status_code == 401, lapsed.text
    assert lapsed.json()["error"]["i18n"]["key"] == "sessionExpired"


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
