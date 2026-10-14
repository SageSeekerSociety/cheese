"""A person @-mentioned in a document's text is told so, once.

A person writing ``<@bob>`` into a document tells bob, the way a message does,
but a document is stored over and over while people type in it: only the save
that adds the mention tells him, and the document never tells him twice. An AI
teammate's ``<@bob>`` in a document tells nobody. Nor does a mention of someone
who cannot open the document; its writer is offered to add them instead.

The collaboration service is the stand-in in tests/support/collab.py:
``type_in`` stores the document as typed by the given people.
"""

import uuid

from app.domain.living_doc import collab
from tests.conftest import seed_user
from tests.integration.conftest import (
    inbox,
    join_project_team,
    open_task,
    post_project,
    room_agent_seat,
    session_auth_headers,
)
from tests.support.living_doc import document_of


def _project(client, *, members_only: bool = False) -> tuple[str, str]:
    """alice's project with bob and carol on it, a channel of alice's and a
    task in it; returns (channel, task). In a private channel only alice and
    bob are seated."""
    for handle in ("alice", "bob", "carol"):
        seed_user(client, handle)
    project = post_project(client, json={"name": "P"}, owner="alice").json()["data"]
    for handle in ("bob", "carol"):
        join_project_team(client, project["id"], handle)
    room = client.post(
        "/topics",
        json={
            "project_id": project["id"],
            "title": "前端",
            **({"members_only": True} if members_only else {}),
        },
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]
    if members_only:
        added = client.post(
            f"/topics/{room}/members",
            json={"handle": "bob"},
            headers=session_auth_headers("alice"),
        )
        assert added.status_code == 200, added.text
    task = open_task(client, room, owner="alice")["id"]
    return room, task


def _type(client, task: str, text: str, *actors: str) -> None:
    client.portal.call(client.collab.type_in, uuid.UUID(task), text, *actors)


def _mentions(client, handle: str) -> list[dict]:
    return inbox(client, handle, "MENTION")


def _document(client, task: str) -> dict:
    return client.get(
        f"/documents/{document_of(client, task)}",
        headers=session_auth_headers("alice"),
    ).json()["data"]


def test_a_person_who_writes_someone_into_a_document_tells_them(client):
    _, task = _project(client)

    _type(client, task, "目标\n\n<@bob> 跟进接口", "alice")

    (told,) = _mentions(client, "bob")
    assert "跟进接口" in str(told)
    assert _mentions(client, "carol") == []
    assert _document(client, task)["notified_mentions"] == ["bob"]


def test_saving_again_does_not_tell_them_again(client):
    _, task = _project(client)
    _type(client, task, "<@bob> 跟进接口", "alice")

    _type(client, task, "<@bob> 跟进接口，周五前", "alice")
    _type(client, task, "<@bob> 跟进接口，周五前。", "carol")

    assert len(_mentions(client, "bob")) == 1


def test_a_mention_removed_and_written_again_is_not_told_twice(client):
    _, task = _project(client)
    _type(client, task, "<@bob> 跟进接口", "alice")
    _type(client, task, "跟进接口", "alice")

    _type(client, task, "<@bob> 跟进接口", "alice")

    assert len(_mentions(client, "bob")) == 1


def test_only_the_person_the_save_adds_is_told(client):
    _, task = _project(client)
    _type(client, task, "<@bob> 跟进接口", "alice")

    _type(client, task, "<@bob> 跟进接口\n\n<@carol> 看样式", "alice")

    assert len(_mentions(client, "bob")) == 1
    assert len(_mentions(client, "carol")) == 1


def test_an_ai_teammates_mention_in_a_document_tells_nobody(client):
    room, task = _project(client)

    _type(client, task, "<@bob> 跟进接口", room_agent_seat(client, room))

    assert _mentions(client, "bob") == []
    assert _document(client, task)["notified_mentions"] == []


def test_someone_who_cannot_open_the_document_is_not_told(client):
    _, task = _project(client, members_only=True)
    name = collab.document_name(uuid.UUID(document_of(client, task)))

    _type(client, task, "<@bob> 跟进接口\n\n<@carol> 看样式", "alice")

    assert len(_mentions(client, "bob")) == 1
    assert _mentions(client, "carol") == []
    assert _document(client, task)["notified_mentions"] == ["bob"]
    offers = [
        f for f in client.collab.told.get(name, []) if f["type"] == "mention_outside"
    ]
    assert [(f["to"], f["handles"], f["may_add"]) for f in offers] == [
        ("alice", ["carol"], True)
    ]
