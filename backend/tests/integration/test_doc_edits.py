"""The room's agent changes passages of the living document, and whether a
change is made or only proposed depends on who asked for it.

- Someone asked (the agent's turn was started by a person): the change is made
  and recorded as made for them — 「<@alice> 让芝士改了文档」.
- Nobody asked (a platform-started turn, such as the document-upkeep nudge):
  rewriting what a person wrote becomes a suggestion; adding new text, or
  changing what the agent itself wrote, is still made directly.
- A person editing a passage (restoring one change) edits as themselves.

The collaboration service is the stand-in in tests/support/collab.py.
"""

import asyncio
import uuid
from datetime import UTC, datetime

from app.domain.agent.repositories import AgentTurnRepository
from tests.integration.conftest import (
    room_agent_headers,
    room_agent_seat,
    session_auth_headers,
)
from tests.integration.test_message_edit import _room
from tests.support.living_doc import document_of

ALICE_PARAGRAPH = "李老师写的第二段，讲范围。"


def _path(client, room) -> str:
    """The room's document, as its routes address it."""
    return f"/documents/{document_of(client, room)}"


def _document(client) -> tuple[str, str]:
    """A room whose document has a paragraph the agent wrote, then one alice
    wrote. Returns (room, the agent's seat)."""
    room, _ = _room(client)
    seat = room_agent_seat(client, room)
    store = client.collab.type_in
    client.portal.call(store, uuid.UUID(room), "芝士写的第一段，讲目标。", seat)
    client.portal.call(
        store,
        uuid.UUID(room),
        f"芝士写的第一段，讲目标。\n\n{ALICE_PARAGRAPH}",
        "alice",
    )
    return room, seat


def _turn_started_by(client, room: str, seat: str, author: str) -> None:
    async def go():
        async with client.test_factory() as session:
            await AgentTurnRepository(session).open(
                turn_id=uuid.uuid4(),
                conversation_id=uuid.UUID(room),
                continuation_id=uuid.uuid4(),
                author=author,
                content="work",
                is_resume=False,
                resendable=True,
                started_at=datetime.now(UTC),
                agent_handle=seat,
            )
            await session.commit()

    asyncio.run(go())


def _edit(client, room: str, headers: dict, *edits: tuple[str, str], **extra):
    return client.post(
        f"{_path(client, room)}/edits",
        json={"edits": [{"old": o, "new": n} for o, n in edits], **extra},
        headers=headers,
    )


def _doc(client, room: str) -> dict:
    return client.get(
        f"{_path(client, room)}", headers=session_auth_headers("alice")
    ).json()["data"]


def _doc_lines(client, room: str) -> list[dict]:
    blocks = client.get(
        f"/topics/{room}/blocks", headers=session_auth_headers("alice")
    ).json()["data"]["data"]
    return [b for b in blocks if (b.get("meta") or {}).get("action") == "doc"]


def _history(client, room: str) -> list[dict]:
    return client.get(
        f"{_path(client, room)}/history",
        headers=session_auth_headers("alice"),
    ).json()["data"]["versions"]


def test_an_edit_someone_asked_for_is_made_and_says_who_asked(client):
    room, seat = _document(client)
    _turn_started_by(client, room, seat, "alice")

    response = _edit(
        client,
        room,
        room_agent_headers(client, room),
        (ALICE_PARAGRAPH, "李老师写的第二段，讲范围和不做的事。"),
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["mode"] == "direct"
    assert "不做的事" in _doc(client, room)["content"]
    line = _doc_lines(client, room)[-1]
    assert "<@alice>" in line["content"]
    assert line["meta"]["doc_requested_by"] == "alice"
    assert line["meta"]["doc_edits"] == [
        {"old": ALICE_PARAGRAPH, "new": "李老师写的第二段，讲范围和不做的事。"}
    ]
    latest = _history(client, room)[-1]
    assert latest["actor"] == seat
    assert latest["requested_by"] == "alice"


def test_rewriting_a_persons_text_unasked_is_only_suggested(client):
    room, seat = _document(client)
    _turn_started_by(client, room, seat, "system")
    before = _doc(client, room)

    response = _edit(
        client,
        room,
        room_agent_headers(client, room),
        ("讲范围", "讲边界"),
        reason="范围这个词太宽",
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["mode"] == "suggest"
    doc = _doc(client, room)
    assert doc["content"] == before["content"]
    assert doc["doc_version"] == before["doc_version"]
    assert doc["pending_suggestions"] == [
        {
            "id": response.json()["data"]["edits"][0]["suggestion_id"],
            "author": seat,
            "old": "讲范围",
            "new": "讲边界",
            "reason": "范围这个词太宽",
        }
    ]
    line = _doc_lines(client, room)[-1]
    assert line["meta"]["doc_suggested"] is True
    assert line["meta"]["doc_suggestions"] == [doc["pending_suggestions"][0]["id"]]


def test_an_unasked_turn_changes_the_agents_own_text_directly(client):
    """The document-upkeep nudge is the commonest unasked turn: keeping its
    own paragraph current must not turn into a suggestion."""
    room, seat = _document(client)
    _turn_started_by(client, room, seat, "system")

    response = _edit(
        client,
        room,
        room_agent_headers(client, room),
        ("讲目标。", "讲目标和进度。"),
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["mode"] == "direct"
    doc = _doc(client, room)
    assert "讲目标和进度。" in doc["content"]
    assert doc["pending_suggestions"] == []


def test_an_unasked_turn_adds_new_text_directly(client):
    room, seat = _document(client)
    _turn_started_by(client, room, seat, "system")

    response = _edit(
        client,
        room,
        room_agent_headers(client, room),
        (ALICE_PARAGRAPH, f"{ALICE_PARAGRAPH}\n\n芝士补的第三段。"),
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["mode"] == "direct"
    assert "芝士补的第三段" in _doc(client, room)["content"]


def test_a_person_restoring_a_change_edits_as_themselves(client):
    room, _ = _document(client)

    response = _edit(
        client,
        room,
        session_auth_headers("bob"),
        ("讲目标", "讲目的"),
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["mode"] == "direct" and data["requested_by"] is None
    latest = _history(client, room)[-1]
    assert latest["actor"] == "bob" and latest["requested_by"] is None
    assert "讲目的" in _doc(client, room)["content"]


def test_a_passage_that_is_not_there_changes_nothing_and_says_which(client):
    room, _ = _document(client)
    before = _doc(client, room)

    response = _edit(
        client,
        room,
        session_auth_headers("alice"),
        ("讲目标", "讲目的"),
        ("没有这句", "随便"),
    )

    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["data"]["index"] == 1
    assert "第 2 处" in error["message"]
    assert _doc(client, room)["content"] == before["content"]
