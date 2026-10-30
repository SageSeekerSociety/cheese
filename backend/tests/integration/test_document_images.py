"""The images a document references: whoever may read the document sees them,
and nobody else does.

An image is drawn from where the document's owner keeps files: a task's
document from the files of the task's channel (where `cheese show` puts what
芝士 made), any document from the project's library (`library/<名字>`).
"""

import base64

from app.core.sandbox_auth import mint_scoped_token
from tests.conftest import seed_user
from tests.integration.conftest import (
    join_project_team,
    open_task,
    post_project,
    session_auth_headers,
)
from tests.support import library_uploads
from tests.support.living_doc import document_of

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)


def _project(client) -> str:
    """dave's project; alice and bob are on its team, mallory is not."""
    for who in ("alice", "bob", "dave", "mallory"):
        seed_user(client, who)
    pid = post_project(client, json={"name": "P"}, owner="dave").json()["data"]["id"]
    for handle in ("alice", "bob"):
        join_project_team(client, pid, handle)
    return pid


def _channel(client, pid: str, *, private: bool) -> str:
    made = client.post(
        "/topics",
        json={"project_id": pid, "title": "频道", "members_only": private},
        headers=session_auth_headers("alice"),
    )
    assert made.status_code == 200, made.text
    return made.json()["data"]["id"]


def _show(client, conversation: str, path: str, headers: dict) -> None:
    shown = client.post(
        f"/topics/{conversation}/shown",
        json={"path": path, "content_b64": base64.b64encode(PNG).decode()},
        headers=headers,
    )
    assert shown.status_code == 200, shown.text


def _task_session(client, pid: str, room: str, task: str) -> dict:
    """The credential the task's own 芝士 session runs `cheese show` with."""
    rows = client.get(
        f"/topics/{room}/members", headers=session_auth_headers("alice")
    ).json()["data"]["data"]
    [seat] = [m["member_handle"] for m in rows if m["agent"]]
    token = mint_scoped_token(project_id=pid, topic_id=task, agent_handle=seat)
    return {"Authorization": f"Bearer {token}"}


def _image(client, doc: str, path: str, who: str):
    return client.get(
        f"/documents/{doc}/image",
        params={"path": path},
        headers=session_auth_headers(who),
    )


def test_an_image_cheese_shows_in_a_task_is_seen_in_the_tasks_document(client):
    pid = _project(client)
    room = _channel(client, pid, private=False)
    task = open_task(client, room, owner="alice")["id"]
    doc = document_of(client, task, headers=session_auth_headers("alice"))

    # What `cheese show figures/耗时.png` sends from the task's own session.
    _show(client, task, "figures/耗时.png", _task_session(client, pid, room, task))

    for reader in ("alice", "bob"):
        seen = _image(client, doc, "figures/耗时.png", reader)
        assert seen.status_code == 200, seen.text
        assert seen.content == PNG
        assert seen.headers["content-type"] == "image/png"
    assert _image(client, doc, "figures/耗时.png", "mallory").content != PNG


def test_a_private_channels_task_document_shows_its_images_to_its_people_only(
    client,
):
    pid = _project(client)
    room = _channel(client, pid, private=True)
    task = open_task(client, room, owner="alice")["id"]
    doc = document_of(client, task, headers=session_auth_headers("alice"))
    _show(client, task, "chart.png", session_auth_headers("alice"))

    assert _image(client, doc, "chart.png", "alice").content == PNG
    # bob is in the project but not in the channel: he cannot read the
    # document, and he does not get its image either.
    assert client.get(
        f"/documents/{doc}", headers=session_auth_headers("bob")
    ).status_code in (403, 404)
    refused = _image(client, doc, "chart.png", "bob")
    assert refused.status_code in (403, 404)
    assert refused.content != PNG

    brought_in = client.post(
        f"/topics/{room}/members",
        json={"handle": "bob"},
        headers=session_auth_headers("alice"),
    )
    assert brought_in.status_code == 200, brought_in.text
    assert _image(client, doc, "chart.png", "bob").content == PNG


def test_a_project_document_shows_library_images_to_the_projects_members(client):
    pid = _project(client)
    uploaded = library_uploads.put_in(
        client, pid, "架构.png", PNG, headers=session_auth_headers("dave")
    )
    assert uploaded.status_code == 200, uploaded.text
    made = client.post(
        f"/projects/{pid}/documents",
        json={"title": "方案", "content": "![](library/架构.png)\n"},
        headers=session_auth_headers("alice"),
    )
    assert made.status_code == 200, made.text
    doc = made.json()["data"]["id"]

    seen = _image(client, doc, "library/架构.png", "bob")
    assert seen.status_code == 200, seen.text
    assert seen.content == PNG
    refused = _image(client, doc, "library/架构.png", "mallory")
    assert refused.status_code in (403, 404)
    assert refused.content != PNG


def test_a_project_document_does_not_reach_into_a_channels_files(client):
    """A document of the project's own is read by every member; a channel's
    files are read by the channel's people. A path in such a document must not
    turn the one into the other."""
    pid = _project(client)
    room = _channel(client, pid, private=True)
    _show(client, room, "secret.png", session_auth_headers("alice"))
    made = client.post(
        f"/projects/{pid}/documents",
        json={"title": "方案"},
        headers=session_auth_headers("bob"),
    )
    doc = made.json()["data"]["id"]

    for reader in ("alice", "bob"):
        assert _image(client, doc, "secret.png", reader).content != PNG


def test_a_document_serves_only_images(client):
    pid = _project(client)
    room = _channel(client, pid, private=False)
    task = open_task(client, room, owner="alice")["id"]
    doc = document_of(client, task, headers=session_auth_headers("alice"))
    shown = client.post(
        f"/topics/{task}/shown",
        json={"path": "page.html", "content": "<script>alert(1)</script>"},
        headers=session_auth_headers("alice"),
    )
    assert shown.status_code == 200, shown.text

    refused = _image(client, doc, "page.html", "alice")
    assert refused.status_code == 422
    assert b"<script>" not in refused.content
