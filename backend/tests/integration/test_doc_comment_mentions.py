"""@-mentioning a person in a document comment, as a message in a channel does.

- A person the comment names is told, once for that comment, in their inbox.
- The comment says whom it told, so a name there is drawn as a mention only
  where it reached someone.
- Someone who cannot open the document (not seated in the private channel the
  task is in) is not told; the writer, and only the writer, is told whom the
  comment named that cannot read it and whether they may add them.
- A friendly @名字 is stored as the person's token.
"""

import uuid

from tests.conftest import seed_user
from tests.integration.conftest import (
    inbox,
    join_project_team,
    open_task,
    post_project,
    session_auth_headers,
)
from tests.integration.test_private_channels import _add, _channel, _project
from tests.support.living_doc import document_of


def _comments(client, task: str) -> str:
    doc = document_of(client, task, headers=session_auth_headers("alice"))
    return f"/documents/{doc}/comments"


def _comment(client, task: str, content: str, *, by: str = "alice") -> dict:
    response = client.post(
        _comments(client, task),
        json={"content": content},
        headers=session_auth_headers(by),
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _threads(client, task: str) -> list[dict]:
    response = client.get(
        f"{_comments(client, task)}/threads", headers=session_auth_headers("alice")
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["data"]


def _reply(client, task: str, root: str, content: str, *, by: str = "alice") -> dict:
    thread = client.get(
        f"{_comments(client, task)}/{root}/thread",
        headers=session_auth_headers(by),
    ).json()["data"]
    response = client.post(
        f"{_comments(client, task)}/{root}/replies",
        json={
            "operation_id": str(uuid.uuid4()),
            "expected_revision": thread["revision"],
            "content": content,
        },
        headers=session_auth_headers(by),
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _mentions(client, handle: str) -> list[dict]:
    return inbox(client, handle, "MENTION")


def _private_task(client) -> tuple[str, str]:
    """A task of alice's in a private channel bob sits in and carol does not."""
    p = _project(client)
    tid = _channel(client, p["id"])
    assert _add(client, tid, "bob", by="alice").status_code == 200
    return open_task(client, tid)["id"], tid


def test_a_person_named_in_a_comment_is_told_once(client):
    task, _ = _private_task(client)

    posted = _comment(client, task, "<@bob> 这里的数字再核一下，<@bob> 看完回我")

    assert len(_mentions(client, "bob")) == 1
    assert posted["notified"] == ["bob"]
    assert _threads(client, task)[0]["comment"]["notified"] == ["bob"]
    # The writer is not told of their own comment.
    assert _mentions(client, "alice") == []


def test_a_reply_that_names_someone_tells_them(client):
    task, _ = _private_task(client)
    root = _comment(client, task, "范围要再定一下")["id"]

    _reply(client, task, root, "<@bob> 你来定")

    assert len(_mentions(client, "bob")) == 1
    reply = _threads(client, task)[0]["replies"][0]["comment"]
    assert reply["notified"] == ["bob"]


def test_someone_who_cannot_open_the_document_is_not_told_and_the_writer_is(client):
    task, tid = _private_task(client)

    posted = _comment(client, task, "<@bob> <@carol> 看一下")

    assert _mentions(client, "carol") == []
    assert posted["notified"] == ["bob"]
    assert posted["mentioned_outside"] == {
        "room_id": tid,
        "handles": ["carol"],
        "may_add": True,
        "members_only": True,
    }
    # Said to the writer, never kept on the comment.
    assert "mentioned_outside" not in _threads(client, task)[0]["comment"]


def test_a_reply_naming_someone_outside_tells_the_writer(client):
    task, tid = _private_task(client)
    root = _comment(client, task, "范围要再定一下")["id"]

    replied = _reply(client, task, root, "<@carol> 你怎么看")

    assert _mentions(client, "carol") == []
    assert replied["mentioned_outside"]["handles"] == ["carol"]


def test_a_comment_naming_nobody_says_it_told_nobody(client):
    task, _ = _private_task(client)

    posted = _comment(client, task, "范围要再定一下")

    assert posted["notified"] == []
    assert "mentioned_outside" not in posted


def test_a_friendly_name_is_stored_as_the_persons_token(client):
    seed_user(client, "bob")
    seed_user(client, "alice")
    project = post_project(client, json={"name": "P"}, owner="alice").json()["data"]
    join_project_team(client, project["id"], "bob")
    room = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "话题"},
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]
    task = open_task(client, room)["id"]

    posted = _comment(client, task, "@bob 看一下")

    assert posted["content"] == "<@bob> 看一下"
    assert len(_mentions(client, "bob")) == 1


def test_in_a_public_channel_someone_not_in_it_is_still_told(client):
    p = _project(client)
    tid = _channel(client, p["id"], private=False)
    task = open_task(client, tid)["id"]

    posted = _comment(client, task, "<@carol> 看一下")

    assert len(_mentions(client, "carol")) == 1
    assert posted["notified"] == ["carol"]
    assert "mentioned_outside" not in posted
