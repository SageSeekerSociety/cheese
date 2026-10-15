"""The task's agent changes passages of its living document, and whether a
change is made or only proposed depends on who asked for it.

- Someone asked (the agent's turn was started by a person): the change is made
  and recorded as made for them — 「<@alice> 让芝士改了文档」.
- Nobody asked (a platform-started turn, such as the document-upkeep nudge):
  rewriting what a person wrote becomes a suggestion; adding new text, or
  changing what the agent itself wrote, is still made directly.
- A person editing a passage (restoring one change) edits as themselves.
- A change that lands on a pending suggestion: one an agent seat left is
  dropped and the change goes in, one a person left is refused. The same holds
  for a whole-document write (cheese_doc_set), which drops every suggestion.

The collaboration service is the stand-in in tests/support/collab.py.
"""

import asyncio
import uuid
from datetime import UTC, datetime

from app.domain.agent.announce import instance_of_seat
from app.domain.agent.repositories import AgentTurnRepository
from app.domain.living_doc import collab
from tests.integration.conftest import (
    open_task,
    read_task,
    room_agent_seat,
    session_auth_headers,
    task_agent_headers,
)
from tests.integration.test_message_edit import _room
from tests.support.living_doc import document_of

ALICE_PARAGRAPH = "李老师写的第二段，讲范围。"


def _path(client, task) -> str:
    """The task's document, as its routes address it."""
    return f"/documents/{document_of(client, task)}"


def _document(client) -> tuple[str, str, dict]:
    """A task of alice's, worked with bob, whose document has a paragraph the
    agent wrote, then one alice wrote. Returns (task, the agent's seat, its
    credential in the task)."""
    room, project = _room(client)
    seat = room_agent_seat(client, room)
    task = open_task(client, room, contributors=["bob"])["id"]
    store = client.collab.type_in
    client.portal.call(store, uuid.UUID(task), "芝士写的第一段，讲目标。", seat)
    client.portal.call(
        store,
        uuid.UUID(task),
        f"芝士写的第一段，讲目标。\n\n{ALICE_PARAGRAPH}",
        "alice",
    )
    return task, seat, task_agent_headers(project, task, seat)


def _turn_started_by(client, task: str, seat: str, author: str) -> str:
    """Open a turn of the agent named ``seat`` in the task; returns its id."""
    turn_id = uuid.uuid4()

    async def go():
        async with client.test_factory() as session:
            await AgentTurnRepository(session).open(
                turn_id=turn_id,
                conversation_id=uuid.UUID(task),
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
    return str(turn_id)


def _instance_handle(client, task: str, seat: str) -> str:
    """The handle a running turn names the seat's agent by: its instance's."""
    project_id = uuid.UUID(read_task(client, task)["project_id"])

    async def go():
        async with client.test_factory() as session:
            return (await instance_of_seat(session, project_id, seat)).handle

    return asyncio.run(go())


def _edit(client, task: str, headers: dict, *edits: tuple[str, str], **extra):
    return client.post(
        f"{_path(client, task)}/edits",
        json={"edits": [{"old": o, "new": n} for o, n in edits], **extra},
        headers=headers,
    )


def _doc(client, task: str) -> dict:
    return client.get(
        f"{_path(client, task)}", headers=session_auth_headers("alice")
    ).json()["data"]


def _suggested_by(client, task: str, who: str) -> None:
    """Leave a suggestion the way the backend can actually see who left it.

    The backend judges a suggestion's author from the document journal, and the
    service is the only thing that writes it there. Putting the suggestion in
    the service's list alone leaves the journal empty, so every suggestion then
    reads as a person's and a test of the refusal passes against code that
    refused every suggestion alike — which is what this one used to do.
    """
    name = collab.document_name(uuid.UUID(document_of(client, task)))
    client.collab.pending[name] = [
        {"id": f"{who}:1", "author": who, "old": "讲范围", "new": "讲边界"}
    ]
    client.portal.call(
        client.collab.type_in, uuid.UUID(task), _doc(client, task)["content"], who
    )


def _doc_lines(client, task: str) -> list[dict]:
    blocks = client.get(
        f"/topics/{task}/blocks", headers=session_auth_headers("alice")
    ).json()["data"]["data"]
    return [b for b in blocks if (b.get("meta") or {}).get("action") == "doc"]


def _history(client, task: str) -> list[dict]:
    return client.get(
        f"{_path(client, task)}/history",
        headers=session_auth_headers("alice"),
    ).json()["data"]["versions"]


def test_an_edit_someone_asked_for_is_made_and_says_who_asked(client):
    task, seat, agent = _document(client)
    _turn_started_by(client, task, seat, "alice")

    response = _edit(
        client,
        task,
        agent,
        (ALICE_PARAGRAPH, "李老师写的第二段，讲范围和不做的事。"),
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["mode"] == "direct"
    assert "不做的事" in _doc(client, task)["content"]
    line = _doc_lines(client, task)[-1]
    assert "<@alice>" in line["content"]
    assert line["meta"]["doc_requested_by"] == "alice"
    assert line["meta"]["doc_edits"] == [
        {"old": ALICE_PARAGRAPH, "new": "李老师写的第二段，讲范围和不做的事。"}
    ]
    latest = _history(client, task)[-1]
    assert latest["actor"] == seat
    assert latest["requested_by"] == "alice"


def test_a_name_written_into_an_edit_is_stored_as_the_persons_token(client):
    """A passage edit stores a friendly @name as the same ``<@handle>`` token a
    whole-document write does, so the person reads as a reference to them and
    not as the letters someone typed."""
    task, seat, agent = _document(client)
    _turn_started_by(client, task, seat, "alice")

    response = _edit(
        client,
        task,
        agent,
        (ALICE_PARAGRAPH, "李老师写的第二段，讲范围，@bob 跟进。"),
    )

    assert response.status_code == 200, response.text
    content = _doc(client, task)["content"]
    assert "<@bob> 跟进" in content
    assert "@bob 跟进" not in content.replace("<@bob>", "")


def test_rewriting_a_persons_text_unasked_is_only_suggested(client):
    task, seat, agent = _document(client)
    _turn_started_by(client, task, seat, "system")
    before = _doc(client, task)

    response = _edit(
        client,
        task,
        agent,
        ("讲范围", "讲边界"),
        reason="范围这个词太宽",
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["mode"] == "suggest"
    doc = _doc(client, task)
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
    line = _doc_lines(client, task)[-1]
    assert line["meta"]["doc_suggested"] is True
    assert line["meta"]["doc_suggestions"] == [doc["pending_suggestions"][0]["id"]]


def test_an_unasked_turn_changes_the_agents_own_text_directly(client):
    """The document-upkeep nudge is the commonest unasked turn: keeping its
    own paragraph current must not turn into a suggestion."""
    task, seat, agent = _document(client)
    _turn_started_by(client, task, seat, "system")

    response = _edit(
        client,
        task,
        agent,
        ("讲目标。", "讲目标和进度。"),
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["mode"] == "direct"
    doc = _doc(client, task)
    assert "讲目标和进度。" in doc["content"]
    assert doc["pending_suggestions"] == []


def test_an_unasked_turn_adds_new_text_directly(client):
    task, seat, agent = _document(client)
    _turn_started_by(client, task, seat, "system")

    response = _edit(
        client,
        task,
        agent,
        (ALICE_PARAGRAPH, f"{ALICE_PARAGRAPH}\n\n芝士补的第三段。"),
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["mode"] == "direct"
    assert "芝士补的第三段" in _doc(client, task)["content"]


def test_a_person_restoring_a_change_edits_as_themselves(client):
    task, _, _ = _document(client)

    response = _edit(
        client,
        task,
        session_auth_headers("bob"),
        ("讲目标", "讲目的"),
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["mode"] == "direct" and data["requested_by"] is None
    latest = _history(client, task)[-1]
    assert latest["actor"] == "bob" and latest["requested_by"] is None
    assert "讲目的" in _doc(client, task)["content"]


def test_a_passage_that_is_not_there_changes_nothing_and_says_which(client):
    task, _, _ = _document(client)
    before = _doc(client, task)

    response = _edit(
        client,
        task,
        session_auth_headers("alice"),
        ("讲目标", "讲目的"),
        ("没有这句", "随便"),
    )

    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["data"]["index"] == 1
    assert "第 2 处" in error["message"]
    assert _doc(client, task)["content"] == before["content"]


def test_an_agent_edit_over_its_own_pending_suggestion_drops_it(client):
    """A seat left a suggestion and is gone; the next turn changes that passage.
    The change goes in and the stale suggestion goes with it — refusing would
    leave the passage uneditable until a person decided."""
    task, seat, agent = _document(client)
    _turn_started_by(client, task, seat, "system")
    proposed = _edit(client, task, agent, ("讲范围", "讲边界"), reason="范围这个词太宽")
    assert proposed.status_code == 200, proposed.text
    assert proposed.json()["data"]["mode"] == "suggest"
    assert _doc(client, task)["pending_suggestions"]

    _turn_started_by(client, task, seat, "alice")
    response = _edit(client, task, agent, ("讲范围", "讲边界和不做的事"))

    assert response.status_code == 200, response.text
    assert response.json()["data"]["mode"] == "direct"
    doc = _doc(client, task)
    assert "讲边界和不做的事" in doc["content"]
    assert doc["pending_suggestions"] == []


def test_an_agent_edit_over_a_persons_pending_suggestion_is_refused(client):
    """A person's suggestion waits for their decision: an agent's change to
    that passage is refused, and the suggestion stays."""
    task, seat, agent = _document(client)
    _suggested_by(client, task, "alice")
    before = _doc(client, task)
    _turn_started_by(client, task, seat, "alice")

    response = _edit(client, task, agent, ("讲范围", "讲边界和不做的事"))

    name = collab.document_name(uuid.UUID(document_of(client, task)))
    assert response.status_code == 409, response.text
    assert response.json()["error"]["data"]["reason"] == "suggested"
    assert _doc(client, task)["content"] == before["content"]
    assert client.collab.pending[name] == [
        {"id": "alice:1", "author": "alice", "old": "讲范围", "new": "讲边界"}
    ]


def test_a_whole_document_write_goes_through_when_only_a_seats_suggestion_pends(client):
    """An agent's own suggestion is not a person's to decide: cheese_doc_set
    still replaces the whole document, dropping it."""
    task, seat, agent = _document(client)
    _turn_started_by(client, task, seat, "system")
    assert _edit(client, task, agent, ("讲范围", "讲边界")).status_code == 200
    now = _doc(client, task)
    assert now["pending_suggestions"]

    written = client.put(
        _path(client, task),
        json={"content": "整份重写。", "expected_version": now["doc_version"]},
        headers=session_auth_headers("alice"),
    )

    assert written.status_code == 200, written.text
    doc = _doc(client, task)
    assert doc["content"] == "整份重写。"
    assert doc["pending_suggestions"] == []


def test_a_whole_document_write_is_refused_while_a_persons_suggestion_pends(client):
    """cheese_doc_set would drop a suggestion a person is still deciding on:
    it is refused, and the document is left alone."""
    task, _, _ = _document(client)
    _suggested_by(client, task, "alice")
    before = _doc(client, task)

    refused = client.put(
        _path(client, task),
        json={"content": "整份重写。", "expected_version": before["doc_version"]},
        headers=session_auth_headers("alice"),
    )

    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["data"]["reason"] == "suggestions"
    assert _doc(client, task)["content"] == before["content"]


def test_an_agents_edit_is_a_step_of_its_running_turn(client):
    """The line an edit leaves belongs to the turn whose tool call made it, so
    the conversation can draw it inside that turn. A running turn names its
    agent by the instance, the line by the seat: the two are the same agent."""
    task, seat, agent = _document(client)
    turn = _turn_started_by(client, task, _instance_handle(client, task, seat), "bob")

    response = _edit(client, task, agent, ("讲目标。", "讲目标和进度。"))

    assert response.status_code == 200, response.text
    line = _doc_lines(client, task)[-1]
    assert line["author"] == seat
    assert line["turn_id"] == turn


def test_an_edit_outside_a_turn_of_its_editor_belongs_to_no_turn(client):
    task, seat, agent = _document(client)
    # Another agent's turn running in the task is not this edit's.
    _turn_started_by(client, task, "cheese-0000000000ff", "alice")

    _edit(client, task, agent, ("讲目标。", "讲目标和进度。"))
    assert _doc_lines(client, task)[-1]["turn_id"] is None

    _edit(client, task, session_auth_headers("bob"), ("讲目标", "讲目的"))
    line = _doc_lines(client, task)[-1]
    assert line["author"] == "bob"
    assert line["turn_id"] is None


# A running turn names its agent by the instance, as the platform records every
# turn; the edit arrives under the seat. The rule must hold across the two.


def test_a_person_started_turn_records_who_asked(client):
    task, seat, agent = _document(client)
    _turn_started_by(client, task, _instance_handle(client, task, seat), "alice")

    response = _edit(
        client, task, agent, (ALICE_PARAGRAPH, "李老师写的第二段，讲范围和不做的事。")
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["requested_by"] == "alice"
    line = _doc_lines(client, task)[-1]
    assert line["meta"]["doc_requested_by"] == "alice"
    assert "<@alice>" in line["content"]
    assert _history(client, task)[-1]["requested_by"] == "alice"


def test_a_person_started_turn_edits_a_persons_text_directly(client):
    task, seat, agent = _document(client)
    _turn_started_by(client, task, _instance_handle(client, task, seat), "alice")
    before = _doc(client, task)

    response = _edit(client, task, agent, ("讲范围", "讲边界"))

    assert response.status_code == 200, response.text
    assert response.json()["data"]["mode"] == "direct"
    doc = _doc(client, task)
    assert "讲边界" in doc["content"]
    assert doc["doc_version"] > before["doc_version"]
    assert doc["pending_suggestions"] == []


def test_a_platform_started_turn_still_only_suggests_a_persons_text(client):
    task, seat, agent = _document(client)
    _turn_started_by(client, task, _instance_handle(client, task, seat), "system")
    before = _doc(client, task)

    response = _edit(client, task, agent, ("讲范围", "讲边界"))

    assert response.status_code == 200, response.text
    assert response.json()["data"]["mode"] == "suggest"
    assert response.json()["data"]["requested_by"] is None
    doc = _doc(client, task)
    assert doc["content"] == before["content"]
    assert [s["new"] for s in doc["pending_suggestions"]] == ["讲边界"]
