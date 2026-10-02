"""The backend's side of the live document: who may open it and how, what the
collaboration service may load and store, and that every other writer goes
through the service instead of around it."""

import base64
import uuid

import jwt

from app.domain.living_doc import collab
from tests.integration.conftest import session_auth_headers
from tests.integration.test_docs import _topic


def _ticket(client, room, headers):
    response = client.get(f"/topics/{room}/doc/ticket", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_a_member_gets_a_ticket_for_this_room_document_only(client):
    room = _topic(client)
    data = _ticket(client, room, session_auth_headers("owner"))
    assert data["document"] == f"room:{room}"
    assert data["read_only"] is False
    claims = jwt.decode(data["ticket"], collab._key("ticket"), algorithms=["HS256"])
    assert claims["doc"] == f"room:{room}"
    assert claims["sub"] == "owner"
    assert claims["ro"] is False
    # Nothing else opens with it: not the service's own bearer.
    assert data["ticket"] != collab._key("internal")


def test_an_archived_room_document_opens_read_only(client):
    room = _topic(client)
    owner = session_auth_headers("owner")
    assert client.post(f"/topics/{room}/archive", headers=owner).status_code == 200
    data = _ticket(client, room, owner)
    assert data["read_only"] is True
    claims = jwt.decode(data["ticket"], collab._key("ticket"), algorithms=["HS256"])
    assert claims["ro"] is True


def test_a_caller_without_a_credential_can_only_read(client, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "authz_enforce_topic_access", False)
    room = _topic(client)
    client.headers.pop("X-Cheese-Token", None)
    assert _ticket(client, room, {})["read_only"] is True


def test_the_service_routes_refuse_anyone_but_the_service(client):
    room = _topic(client)
    name = f"room:{room}"
    owner = session_auth_headers("owner")
    assert (
        client.get(f"/internal/collab/documents/{name}", headers=owner).status_code
        == 403
    )
    stored = client.put(
        f"/internal/collab/documents/{name}",
        json={"state": "", "content": "伪造的一版", "actors": ["owner"]},
        headers={"Authorization": "Bearer not-the-key"},
    )
    assert stored.status_code == 403
    assert client.get(f"/topics/{room}/doc").json()["data"] is None


def _service():
    return {"Authorization": f"Bearer {collab._key('internal')}"}


def test_a_document_converted_on_first_open_keeps_its_text_and_version(client):
    room = _topic(client)
    owner = session_auth_headers("owner")
    original = "# 原稿\r\n\r\n- 一\r\n- 二\r\n"
    assert (
        client.put(
            f"/topics/{room}/doc",
            json={"content": original, "expected_version": 0},
            headers=owner,
        ).status_code
        == 200
    )
    name = f"room:{room}"
    loaded = client.get(f"/internal/collab/documents/{name}", headers=_service()).json()
    assert loaded["content"] == original
    # The stand-in stored a state with the write; a converting service stores
    # the state alone, which changes nothing a reader sees.
    state = base64.b64encode(b"converted").decode()
    client.put(
        f"/internal/collab/documents/{name}",
        json={"state": state, "content": None},
        headers=_service(),
    ).raise_for_status()
    loaded = client.get(f"/internal/collab/documents/{name}", headers=_service()).json()
    assert loaded["state"] == state
    assert loaded["content"] == original
    assert loaded["doc_version"] == 1
    assert (
        len(
            client.get(f"/topics/{room}/doc/history", headers=owner).json()["data"][
                "versions"
            ]
        )
        == 1
    )


def test_people_typing_record_a_version_under_their_own_names(client):
    room = _topic(client)
    owner = session_auth_headers("owner")
    client.portal.call(
        client.collab.type_in, uuid.UUID(room), "两个人一起写的", "owner", "bob"
    )
    doc = client.get(f"/topics/{room}/doc").json()["data"]
    assert doc["content"] == "两个人一起写的"
    history = client.get(f"/topics/{room}/doc/history", headers=owner).json()["data"][
        "versions"
    ]
    assert history[-1]["actor"] == "owner"


def test_a_backend_write_based_on_an_older_document_is_refused_not_applied(client):
    room = _topic(client)
    owner = session_auth_headers("owner")
    client.portal.call(client.collab.type_in, uuid.UUID(room), "人先写的", "owner")
    # Read at version 1, then somebody types again before the write lands.
    client.portal.call(client.collab.type_in, uuid.UUID(room), "人又改了", "owner")
    stale = client.put(
        f"/topics/{room}/doc", json={"content": "按旧版写的", "expected_version": 1}
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["data"]["doc_version"] == 2
    assert client.get(f"/topics/{room}/doc").json()["data"]["content"] == "人又改了"
    current = client.put(
        f"/topics/{room}/doc", json={"content": "按新版写的", "expected_version": 2}
    )
    assert current.status_code == 200
    history = client.get(f"/topics/{room}/doc/history", headers=owner).json()["data"][
        "versions"
    ]
    assert [row["content"] for row in history] == ["人先写的", "人又改了", "按新版写的"]


def test_writes_fail_plainly_when_the_service_is_unreachable(client, monkeypatch):
    import httpx

    def down(request):
        raise httpx.ConnectError("refused", request=request)

    monkeypatch.setattr(collab, "transport", httpx.MockTransport(down))
    room = _topic(client)
    response = client.put(
        f"/topics/{room}/doc", json={"content": "写不进去", "expected_version": 0}
    )
    assert response.status_code == 503
    assert client.get(f"/topics/{room}/doc").json()["data"] is None
