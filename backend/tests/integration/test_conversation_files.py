"""A conversation's files and documents stay in it until someone saves them (#3828).

A file sent in a channel or a private chat, and a document 芝士 writes there,
belong to that conversation: whoever may read the conversation reads them, the
library does not list them, and nobody outside a private channel reaches them.
Saving one to the library is a person's act, and from then on the whole project
finds it there.
"""

from app.domain.agent.harness.channel import mint_session_token
from tests.conftest import seed_user
from tests.integration.conftest import (
    join_project_team,
    post_message,
    post_project,
    room_agent_seat,
    session_auth_headers,
)

ALICE = session_auth_headers("alice")
CAROL = session_auth_headers("carol")


def _project(client) -> str:
    """alice's project; carol is on its team but not in alice's private channel."""
    for who in ("alice", "carol"):
        seed_user(client, who)
    pid = post_project(client, json={"name": "P"}, owner="alice").json()["data"]["id"]
    join_project_team(client, pid, "carol")
    return pid


def _channel(client, pid: str, *, private: bool) -> str:
    r = client.post(
        "/topics",
        json={"project_id": pid, "title": "合作洽谈", "members_only": private},
        headers=ALICE,
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _send_file(client, room: str) -> dict:
    sent = client.post(
        f"/topics/{room}/attachments",
        files={"file": ("合作协议.docx", b"PK\x03\x04agreement", "application/zip")},
        headers=ALICE,
    )
    assert sent.status_code == 200, sent.text
    att = sent.json()["data"]
    post_message(client, room, "alice", {"content": "合同在这", "attachments": [att]})
    return att


def _library(client, pid: str, headers) -> list[str]:
    r = client.get(f"/projects/{pid}/library", headers=headers)
    assert r.status_code == 200, r.text
    return [row["path"] for row in r.json()["data"]["data"]]


def _library_documents(client, pid: str, headers) -> list[str]:
    r = client.get(f"/projects/{pid}/documents", headers=headers)
    assert r.status_code == 200, r.text
    return [row["id"] for row in r.json()["data"]["data"]]


def _agent(client, pid: str, room: str) -> dict:
    return {
        "Authorization": f"Bearer {mint_session_token(pid, room, room_agent_seat(client, room))}"
    }


def _agent_writes(client, pid: str, room: str) -> str:
    made = client.post(
        f"/projects/{pid}/documents",
        json={"title": "合作方案"},
        headers=_agent(client, pid, room),
    )
    assert made.status_code == 200, made.text
    return made.json()["data"]["id"]


def test_a_file_sent_in_a_private_channel_is_not_in_the_library_until_saved(client):
    pid = _project(client)
    room = _channel(client, pid, private=True)

    att = _send_file(client, room)

    assert _library(client, pid, CAROL) == []
    listed = client.get(f"/topics/{room}/attachments", headers=ALICE).json()["data"]
    assert [(r["path"], r["in_library"]) for r in listed["data"]] == [
        (att["path"], False)
    ]
    outside = client.get(
        f"/topics/{room}/attachments/raw",
        params={"path": att["path"], "download": "true"},
        headers=CAROL,
    )
    assert outside.status_code in (403, 404)

    saved = client.post(
        f"/topics/{room}/shown/save", json={"path": att["path"]}, headers=ALICE
    )
    assert saved.status_code == 200, saved.text

    assert _library(client, pid, CAROL) == ["合作协议.docx"]
    listed = client.get(f"/topics/{room}/attachments", headers=ALICE).json()["data"]
    assert [r["in_library"] for r in listed["data"]] == [True]


def test_a_document_cheese_writes_in_a_private_channel_stays_there_until_saved(client):
    pid = _project(client)
    room = _channel(client, pid, private=True)

    doc = _agent_writes(client, pid, room)

    assert doc not in _library_documents(client, pid, ALICE)
    assert client.get(f"/documents/{doc}", headers=ALICE).status_code == 200
    assert client.get(f"/documents/{doc}", headers=CAROL).status_code in (403, 404)
    here = client.get(f"/topics/{room}/documents", headers=ALICE).json()["data"]
    assert [(d["id"], d["in_library"]) for d in here["data"]] == [(doc, False)]

    kept = client.post(f"/documents/{doc}/keep", headers=ALICE)
    assert kept.status_code == 200, kept.text

    assert doc in _library_documents(client, pid, CAROL)
    assert client.get(f"/documents/{doc}", headers=CAROL).status_code == 200


def test_a_document_written_in_a_public_channel_is_read_there_but_not_listed(client):
    pid = _project(client)
    room = _channel(client, pid, private=False)

    doc = _agent_writes(client, pid, room)

    assert client.get(f"/documents/{doc}", headers=CAROL).status_code == 200
    assert doc not in _library_documents(client, pid, CAROL)


def test_cheese_finds_what_it_wrote_in_its_conversation_and_nothing_from_another(
    client,
):
    pid = _project(client)
    room = _channel(client, pid, private=False)
    elsewhere = _channel(client, pid, private=False)

    mine = _agent_writes(client, pid, room)
    theirs = _agent_writes(client, pid, elsewhere)

    listed = client.get(
        f"/projects/{pid}/documents", headers=_agent(client, pid, room)
    ).json()["data"]["data"]
    assert [d["id"] for d in listed] == [mine]
    assert theirs not in [d["id"] for d in listed]


def test_only_a_person_saves_a_document_to_the_library(client):
    pid = _project(client)
    room = _channel(client, pid, private=False)
    doc = _agent_writes(client, pid, room)

    refused = client.post(f"/documents/{doc}/keep", headers=_agent(client, pid, room))

    assert refused.status_code in (401, 403), refused.text
    assert doc not in _library_documents(client, pid, ALICE)


def test_cheese_in_another_channel_cannot_read_a_private_channels_document(client):
    pid = _project(client)
    private = _channel(client, pid, private=True)
    elsewhere = _channel(client, pid, private=False)
    doc = _agent_writes(client, pid, private)

    refused = client.get(f"/documents/{doc}", headers=_agent(client, pid, elsewhere))

    assert refused.status_code in (403, 404), refused.text
