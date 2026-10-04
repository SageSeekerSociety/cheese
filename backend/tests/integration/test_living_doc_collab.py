"""The backend's side of the live document: who may open it and how, what the
collaboration service may load and store, and that every other writer goes
through the service instead of around it."""

import base64
import uuid

import jwt

from app.domain.living_doc import collab
from tests.integration.conftest import session_auth_headers
from tests.integration.test_docs import _topic
from tests.support.living_doc import document_of


def _doc(client, room) -> str:
    """The room's document, as its routes address it."""
    return f"/documents/{document_of(client, room)}"


def _ticket(client, room, headers):
    response = client.get(f"{_doc(client, room)}/ticket", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_a_member_gets_a_ticket_for_this_room_document_only(client):
    room = _topic(client)
    owner = session_auth_headers("owner")
    data = _ticket(client, room, owner)
    # Every ticket for the room opens the same document, and no other room's.
    assert _ticket(client, room, owner)["document"] == data["document"]
    assert _ticket(client, _topic(client), owner)["document"] != data["document"]
    assert data["read_only"] is False
    claims = jwt.decode(data["ticket"], collab._key("ticket"), algorithms=["HS256"])
    assert claims["doc"] == data["document"]
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
    owner = session_auth_headers("owner")
    name = _ticket(client, room, owner)["document"]
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
    assert client.get(_doc(client, room)).json()["data"] is None


def _service():
    return {"Authorization": f"Bearer {collab._key('internal')}"}


def _doc_events(client, room, owner):
    blocks = client.get(f"/topics/{room}/blocks", headers=owner).json()["data"]["data"]
    return [b for b in blocks if (b.get("meta") or {}).get("action") == "doc"]


def test_converting_a_markdown_document_is_recorded_quietly(client):
    room = _topic(client)
    owner = session_auth_headers("owner")
    original = "# 原稿\r\n\r\n* 一\r\n* 二\r\n"
    client.portal.call(client.collab.type_in, uuid.UUID(room), original, "owner")
    name = _ticket(client, room, owner)["document"]
    events = len(_doc_events(client, room, owner))
    # The service converts it on first open and stores the text it exports.
    exported = "# 原稿\n\n- 一\n- 二"
    client.put(
        f"/internal/collab/documents/{name}",
        json={
            "state": base64.b64encode(b"converted").decode(),
            "content": exported,
            "actors": ["system"],
            "converted": True,
        },
        headers=_service(),
    ).raise_for_status()
    loaded = client.get(f"/internal/collab/documents/{name}", headers=_service()).json()
    assert loaded["state"] == base64.b64encode(b"converted").decode()
    assert client.get(_doc(client, room)).json()["data"]["content"] == exported
    history = client.get(f"{_doc(client, room)}/history", headers=owner).json()["data"]
    assert [row["actor"] for row in history["versions"]] == ["owner", "system"]
    # The original stays readable, and nobody in the room is told about a
    # respelling as if somebody had edited.
    assert history["versions"][0]["content"] == original
    assert len(_doc_events(client, room, owner)) == events


def test_converting_a_markdown_document_keeps_its_author(client):
    room = _topic(client)
    original = "# 原稿\r\n\r\n* 一\r\n* 二\r\n"
    client.portal.call(client.collab.type_in, uuid.UUID(room), original, "owner")
    name = _ticket(client, room, session_auth_headers("owner"))["document"]
    client.put(
        f"/internal/collab/documents/{name}",
        json={
            "state": base64.b64encode(b"converted").decode(),
            "content": "# 原稿\n\n- 一\n- 二",
            "actors": ["system"],
            "converted": True,
        },
        headers=_service(),
    ).raise_for_status()
    # A respelling is nobody's edit: the document is still the owner's.
    assert client.get(_doc(client, room)).json()["data"]["author"] == "owner"
    nodes = client.get(f"{_doc(client, room)}/nodes").json()["data"]["data"]
    assert nodes
    assert {node["author"] for node in nodes} == {"owner"}


def test_people_typing_record_a_version_under_their_own_names(client):
    room = _topic(client)
    owner = session_auth_headers("owner")
    client.portal.call(
        client.collab.type_in, uuid.UUID(room), "两个人一起写的", "owner", "bob"
    )
    doc = client.get(_doc(client, room)).json()["data"]
    assert doc["content"] == "两个人一起写的"
    history = client.get(f"{_doc(client, room)}/history", headers=owner).json()["data"][
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
        _doc(client, room),
        json={"content": "按旧版写的", "expected_version": 1},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["data"]["doc_version"] == 2
    assert client.get(_doc(client, room)).json()["data"]["content"] == "人又改了"
    current = client.put(
        _doc(client, room),
        json={"content": "按新版写的", "expected_version": 2},
    )
    assert current.status_code == 200
    history = client.get(f"{_doc(client, room)}/history", headers=owner).json()["data"][
        "versions"
    ]
    assert [row["content"] for row in history] == ["人先写的", "人又改了", "按新版写的"]


def test_a_writer_that_reads_again_after_a_conflict_gets_its_retry_in(client):
    room = _topic(client)
    client.portal.call(client.collab.type_in, uuid.UUID(room), "人先写的", "owner")
    read = client.get(_doc(client, room)).json()["data"]
    # Somebody keeps typing: one store lands, and more is typed after it that
    # the service holds but has not stored yet.
    client.portal.call(client.collab.type_in, uuid.UUID(room), "人又改了", "owner")
    client.portal.call(
        client.collab.type_unsaved, uuid.UUID(room), "人又改了，还在写", "owner"
    )
    stale = client.put(
        _doc(client, room),
        json={"content": "按旧版写的", "expected_version": read["doc_version"]},
    )
    assert stale.status_code == 409
    # Nobody types after the refusal: what the writer reads now is the document.
    again = client.get(_doc(client, room)).json()["data"]
    assert again["content"] == "人又改了，还在写"
    retried = client.put(
        _doc(client, room),
        json={"content": "按新版写的", "expected_version": again["doc_version"]},
    )
    assert retried.status_code == 200, retried.text
    assert client.get(_doc(client, room)).json()["data"]["content"] == "按新版写的"


def test_writes_fail_plainly_when_the_service_is_unreachable(client, monkeypatch):
    import httpx

    def down(request):
        raise httpx.ConnectError("refused", request=request)

    monkeypatch.setattr(collab, "transport", httpx.MockTransport(down))
    room = _topic(client)
    response = client.put(
        _doc(client, room),
        json={"content": "写不进去", "expected_version": 0},
    )
    assert response.status_code == 503
    assert client.get(_doc(client, room)).json()["data"] is None


def test_a_markdown_write_that_would_lose_text_is_refused_with_the_reason(client):
    room = _topic(client)
    owner = session_auth_headers("owner")
    client.portal.call(client.collab.type_in, uuid.UUID(room), "原文", "owner")
    reason = (
        "第 3 行是脚注定义（[^1]: 注），实况文档不支持脚注。"
        "请把脚注内容改成正文里的括注。"
    )
    client.collab.refuse_writes = (reason, 3)
    refused = client.put(
        _doc(client, room),
        json={"content": "原文[^1]\n\n[^1]: 注\n", "expected_version": 1},
    )
    assert refused.status_code == 422
    assert refused.json()["error"]["message"] == reason
    assert refused.json()["error"]["data"]["line"] == 3
    assert client.get(_doc(client, room)).json()["data"]["content"] == "原文"
    # Restoring a stored version is not a Markdown write and is not checked.
    restored = client.post(
        f"{_doc(client, room)}/restore",
        json={"version": 1, "expected_version": 1, "operation_id": str(uuid.uuid4())},
        headers=owner,
    )
    assert restored.status_code == 200, restored.text


def test_a_project_document_in_no_room_is_its_members_and_nobody_elses(client):
    """A document of the project's own, in no room, is read and written by the
    project's members through the same routes, and refused to anyone else."""
    from app.domain.living_doc.models import Document

    room = _topic(client)
    project_id = uuid.UUID(client.get(f"/topics/{room}").json()["data"]["project_id"])

    async def make() -> uuid.UUID:
        async with client.test_factory() as session:
            doc = Document(project_id=project_id, room_id=None)
            session.add(doc)
            await session.commit()
            return doc.id

    path = f"/documents/{client.portal.call(make)}"
    owner = session_auth_headers("owner")
    outsider = session_auth_headers("outsider")
    client.headers.pop("X-Cheese-Token", None)

    assert client.get(path, headers=owner).json()["data"] is None
    written = client.put(
        path, json={"content": "项目自己的文档", "expected_version": 0}, headers=owner
    )
    assert written.status_code == 200, written.text
    read = client.get(path, headers=owner).json()["data"]
    assert read["content"] == "项目自己的文档"
    assert _ticket_for(client, path, owner)["read_only"] is False

    assert client.get(path, headers=outsider).status_code == 403
    assert client.get(f"{path}/ticket", headers=outsider).status_code == 403
    refused = client.put(
        path, json={"content": "外人写的", "expected_version": 1}, headers=outsider
    )
    assert refused.status_code == 403
    assert client.get(path, headers=owner).json()["data"]["content"] == "项目自己的文档"


def _ticket_for(client, path, headers):
    response = client.get(f"{path}/ticket", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["data"]
