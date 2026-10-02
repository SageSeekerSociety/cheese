"""「让芝士改」: a person selects text and tells the room's agent how to change
it; the change is made at once, as the agent, recorded as requested by that
person, and undone like any other passage edit.

The model call is faked at its boundary (``doc_rewrite.rewrite``): what is
under test is what the platform does with the answer.
"""

import pytest

from app.api import doc_rewrite
from tests.integration.conftest import room_agent_headers, session_auth_headers
from tests.integration.test_doc_edits import (
    ALICE_PARAGRAPH,
    _doc,
    _doc_lines,
    _document,
    _history,
)


@pytest.fixture
def model(monkeypatch):
    calls: list[dict] = []

    async def rewrite(**kwargs):
        calls.append(kwargs)
        return "边界"

    monkeypatch.setattr(doc_rewrite, "rewrite", rewrite)
    return calls


def _rewrite(client, room: str, headers: dict, block: str, selected: str):
    start = block.index(selected)
    return client.post(
        f"/topics/{room}/doc/rewrite",
        json={
            "block": block,
            "start": start,
            "end": start + len(selected),
            "instruction": "说得更具体",
        },
        headers=headers,
    )


def test_the_selection_is_rewritten_at_once_for_the_person_who_asked(client, model):
    room, seat = _document(client)

    response = _rewrite(
        client, room, session_auth_headers("alice"), ALICE_PARAGRAPH, "范围"
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"] == {
        "old": ALICE_PARAGRAPH,
        "new": "李老师写的第二段，讲边界。",
        "replacement": "边界",
    }
    assert model[0]["block"][model[0]["start"] : model[0]["end"]] == "范围"
    assert "李老师写的第二段，讲边界。" in _doc(client, room)["content"]
    latest = _history(client, room)[-1]
    assert latest["actor"] == seat and latest["requested_by"] == "alice"
    assert _doc_lines(client, room)[-1]["meta"]["doc_requested_by"] == "alice"


def test_undoing_a_rewrite_puts_the_text_back(client, model):
    room, _ = _document(client)
    done = _rewrite(
        client, room, session_auth_headers("alice"), ALICE_PARAGRAPH, "范围"
    ).json()["data"]

    response = client.post(
        f"/topics/{room}/doc/edits",
        json={"edits": [{"old": done["new"], "new": done["old"]}]},
        headers=session_auth_headers("alice"),
    )

    assert response.status_code == 200, response.text
    assert ALICE_PARAGRAPH in _doc(client, room)["content"]


def test_a_passage_changed_since_it_was_selected_is_refused(client, model):
    room, _ = _document(client)

    response = _rewrite(
        client, room, session_auth_headers("alice"), "早就改掉的一段。", "改掉"
    )

    assert response.status_code == 409, response.text
    assert ALICE_PARAGRAPH in _doc(client, room)["content"]


def test_the_agent_cannot_ask_itself_to_rewrite(client, model):
    room, _ = _document(client)

    response = _rewrite(
        client, room, room_agent_headers(client, room), ALICE_PARAGRAPH, "范围"
    )

    assert response.status_code in (401, 403), response.text
    assert model == []
