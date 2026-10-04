"""Asking the room's AI teammate from the document's selection box.

- What the teammate changed comes back to the box, and is recorded as asked by
  the person who asked.
- A shortcut that only asks, and any question about a read-only document,
  cannot change the document, whatever the session tries.
- The box's follow-ups go to the same session; nobody else can go on with it.
- A shortcut is sent by its id and is refused when it does not fit.
- The box's answer can be put into a comment thread the person started, as the
  teammate's reply; not into anyone else's.

The session is faked at its boundary, as in test_doc_agent.
"""

import json
import threading
import uuid

from app.domain.topic.models import Topic, TopicStatus
from tests.integration.conftest import session_auth_headers
from tests.integration.test_doc_agent import _tool
from tests.integration.test_doc_agent import sessions as sessions  # noqa: F401
from tests.integration.test_doc_edits import ALICE_PARAGRAPH, _doc, _document
from tests.support.living_doc import document_of


def _selection(selected: str) -> dict:
    start = ALICE_PARAGRAPH.index(selected)
    return {"block": ALICE_PARAGRAPH, "start": start, "end": start + len(selected)}


def _agent(client, room: str) -> str:
    """Where the room's document is asked (``/documents/{id}/agent``)."""
    return f"/documents/{document_of(client, room)}/agent"


def _ask(client, room: str, by: str = "alice", **body) -> tuple[int, list]:
    """The box's events, in order: (name, data)."""
    response = client.post(
        _agent(client, room), json=body, headers=session_auth_headers(by)
    )
    if response.status_code != 200:
        return response.status_code, []
    events = []
    for raw in response.text.split("\n\n"):
        lines = dict(line.split(": ", 1) for line in raw.splitlines() if ": " in line)
        if "event" in lines:
            events.append((lines["event"], json.loads(lines["data"])))
    return 200, events


def _done(events: list) -> dict:
    [done] = [data for name, data in events if name == "done"]
    return done


def _edit_to(old: str, new: str):
    async def edit(credential, question):
        await _tool(
            credential, "cheese_doc_edit", {"edits": [{"old": old, "new": new}]}
        )
        return "改好了。", None

    return edit


def test_a_shortcut_changes_the_selection_and_the_box_gets_the_change(client, sessions):
    room, seat = _document(client)
    sessions.script = _edit_to("讲范围", "讲边界")

    status, events = _ask(
        client, room, by="bob", preset="polish", selection=_selection("范围")
    )

    assert status == 200
    assert _done(events)["edits"] == [{"old": "讲范围", "new": "讲边界"}]
    assert "李老师写的第二段，讲边界。" in _doc(client, room)["content"]
    latest = client.get(
        f"/documents/{document_of(client, room)}/history",
        headers=session_auth_headers("alice"),
    ).json()["data"]["versions"][-1]
    assert latest["actor"] == seat and latest["requested_by"] == "bob"
    [(_, question)] = sessions.asked
    assert "范围" in question


def test_a_shortcut_that_only_asks_cannot_change_the_document(client, sessions):
    room, _ = _document(client)
    sessions.script = _edit_to("讲范围", "讲边界")

    status, events = _ask(client, room, preset="check", selection=_selection("范围"))

    assert status == 200
    assert _done(events)["edits"] == []
    assert ALICE_PARAGRAPH in _doc(client, room)["content"]


def test_nothing_asked_about_a_read_only_document_changes_it(client, sessions):
    room, _ = _document(client)

    async def archive():
        async with client.test_factory() as db:
            topic = await db.get(Topic, uuid.UUID(room))
            assert topic is not None
            topic.status = TopicStatus.archived
            await db.commit()

    client.portal.call(archive)
    sessions.script = _edit_to("讲范围", "讲边界")

    status, events = _ask(
        client, room, text="把范围改成边界", selection=_selection("范围")
    )

    assert status == 200
    assert _done(events)["edits"] == []
    assert ALICE_PARAGRAPH in _doc(client, room)["content"]


def test_a_follow_up_goes_to_the_same_session_and_is_the_askers_alone(client, sessions):
    room, _ = _document(client)
    _, first = _ask(client, room, preset="shorten", selection=_selection("范围"))
    [conversation] = [data["id"] for name, data in first if name == "conversation"]

    status, _ = _ask(client, room, conversation=conversation, text="再短一点")
    assert status == 200
    first, second = (session for session, _ in sessions.asked)
    assert first == second and conversation in first.home

    refused, _ = _ask(client, room, by="bob", conversation=conversation, text="我也来")
    assert refused == 403
    assert len(sessions.asked) == 2


def test_a_shortcut_that_does_not_fit_is_refused(client, sessions):
    room, _ = _document(client)

    unknown, _ = _ask(client, room, preset="rhyme", selection=_selection("范围"))
    no_selection, _ = _ask(client, room, preset="polish")
    needs_none, _ = _ask(client, room, preset="summarize", selection=_selection("范围"))

    assert (unknown, no_selection, needs_none) == (422, 422, 422)
    assert sessions.asked == []


def test_the_answer_goes_into_the_askers_own_thread_only(client, sessions):
    room, seat = _document(client)

    async def answer(credential, question):
        return "第二段的范围和第一段的目标对得上。", None

    sessions.script = answer
    _, events = _ask(client, room, preset="check", selection=_selection("范围"))
    [conversation] = [data["id"] for name, data in events if name == "conversation"]

    def thread(by: str) -> str:
        return client.post(
            f"/documents/{document_of(client, room)}/comments",
            json={"content": "检查", "quote": "范围"},
            headers=session_auth_headers(by),
        ).json()["data"]["id"]

    mine, theirs = thread("alice"), thread("bob")
    into = f"{_agent(client, room)}/{conversation}/reply"

    refused = client.post(f"{into}/{theirs}", headers=session_auth_headers("alice"))
    taken = client.post(f"{into}/{mine}", headers=session_auth_headers("alice"))

    assert refused.status_code == 403
    assert taken.status_code == 200, taken.text
    replies = client.get(
        f"/documents/{document_of(client, room)}/comments/{mine}/thread",
        headers=session_auth_headers("alice"),
    ).json()["data"]["replies"]
    assert [(r["comment"]["author"], r["comment"]["content"]) for r in replies] == [
        (seat, "第二段的范围和第一段的目标对得上。")
    ]


def test_a_stopped_answer_keeps_what_was_written(client, sessions):
    room, _ = _document(client)
    sessions.held = "这一段讲的是"
    answered: list = []
    asking = threading.Thread(
        target=lambda: answered.append(
            _ask(client, room, preset="explain", selection=_selection("范围"))
        )
    )
    asking.start()
    assert sessions.waiting.wait(20)
    # The box's conversation, as its session is named (`document/session.py`).
    [(ref, _)] = sessions.asked
    conversation = ref.home.rsplit("/", 1)[-1]

    stopped = client.post(
        f"{_agent(client, room)}/{conversation}/stop",
        headers=session_auth_headers("alice"),
    )

    assert stopped.status_code == 200, stopped.text
    asking.join(20)
    status, events = answered[0]
    assert status == 200
    assert _done(events) == {"answer": "这一段讲的是", "edits": [], "stopped": True}


def test_a_question_waits_for_a_full_host_and_is_then_answered(client, sessions):
    room, _ = _document(client)
    sessions.room.clear()
    answered: list = []
    asking = threading.Thread(
        target=lambda: answered.append(
            _ask(client, room, preset="explain", selection=_selection("范围"))
        )
    )
    asking.start()
    assert sessions.waiting.wait(20)
    assert sessions.asked == []

    sessions.room.set()

    asking.join(20)
    status, events = answered[0]
    assert status == 200
    names = [name for name, _ in events]
    assert names.index("queued") < names.index("done")
    assert _done(events)["answer"] == "好的。"
