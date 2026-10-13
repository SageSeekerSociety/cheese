"""Skipping a question: the person it waits on says he will not answer it.

A skip is a response to the question, so it does what an answer does to it —
the question stops waiting, leaves that person's 待你回答 list and settles its
notice — without anything being said in the room. The agent that asked has no
message to read, so it is told in its next turn that the question was skipped;
otherwise it waits for an answer that is not coming. Taking the skip back
(`DELETE /blocks/{id}/answer`, the same undo an answer has) makes the question
wait again.

Real HTTP, PostgreSQL and the room's runner; the session is the scripted stub.
"""

from tests.ask_fixtures import active_ask, wait_turn_idle
from tests.conftest import seed_user, wait_work_idle
from tests.integration.conftest import (
    in_thread,
    join_project_team,
    post_project,
    session_auth_headers,
)

QUESTION = {
    "questions": [
        {
            "question": "预算按哪个口径统计？",
            "options": [{"text": "按部门"}, {"text": "按项目"}],
        }
    ]
}


def _room(client) -> tuple[str, str, str]:
    """A project, a 支线 of its channel, and alice's token."""
    token = seed_user(client, "alice")
    data = post_project(client, {"name": "Ask"}, owner="alice").json()["data"]
    return data["id"], in_thread(client, data["root_topic_id"], "alice"), token


def _ask(client, stub_hooks, monkeypatch, room) -> dict:
    """The agent asks inside the turn alice's message started; the turn ends."""
    with active_ask(client, stub_hooks, monkeypatch, room, actor="alice") as headers:
        response = client.post(f"/topics/{room}/asks", json=QUESTION, headers=headers)
        assert response.status_code == 200, response.text
    wait_turn_idle(client, room)
    (question,) = response.json()["data"]["blocks"]
    return question


def _prompts(stub_hooks, monkeypatch) -> list[str]:
    """Every prompt the agent's session is handed from here on."""
    seen: list[str] = []
    emit = stub_hooks.emit_turn

    def recording(topic_id, prompt, reply, *, agent=None):
        seen.append(prompt)
        emit(topic_id, prompt, reply, agent=agent)

    monkeypatch.setattr(stub_hooks, "emit_turn", recording)
    return seen


def _skip(client, question_id, *, by="alice"):
    """Skip and wait out the turn it starts: the agent's turn is run from a
    delivery the request records, not by the request itself."""
    response = client.post(
        f"/blocks/{question_id}/skip", headers=session_auth_headers(by)
    )
    wait_work_idle()
    return response


def _undo(client, question_id, *, by="alice"):
    response = client.delete(
        f"/blocks/{question_id}/answer", headers=session_auth_headers(by)
    )
    wait_work_idle()
    return response


def _blocks(client, room) -> list[dict]:
    return client.get(f"/topics/{room}/blocks").json()["data"]["data"]


def _waiting_questions(client, handle) -> list[str]:
    """The questions on ``handle``'s 待你回答 list."""
    r = client.get("/awaiting-me", headers=session_auth_headers(handle))
    assert r.status_code == 200, r.text
    return [item["blockId"] for item in r.json()["data"]["data"] if item["blockId"]]


def test_the_person_asked_skips_and_it_leaves_his_list_without_a_word_in_the_room(
    client, stub_hooks, monkeypatch
):
    _, room, _ = _room(client)
    question = _ask(client, stub_hooks, monkeypatch, room)
    assert _waiting_questions(client, "alice") == [question["id"]]
    before = len(_blocks(client, room))
    prompts = _prompts(stub_hooks, monkeypatch)

    response = _skip(client, question["id"])

    assert response.status_code == 200, response.text
    assert _waiting_questions(client, "alice") == []
    # No message from alice: what the room shows is the question, now skipped.
    people_said = [b for b in _blocks(client, room)[before:] if b["author"] == "alice"]
    assert people_said == []
    # The agent is told, in the turn the skip starts, that no answer is coming.
    assert len(prompts) == 1
    assert "跳过" in prompts[0] and "没有回答" in prompts[0]
    assert "预算按哪个口径统计？" in prompts[0]


def test_only_the_person_the_question_waits_on_can_skip_it(
    client, stub_hooks, monkeypatch
):
    pid, room, _ = _room(client)
    seed_user(client, "bob")
    join_project_team(client, pid, "bob")
    question = _ask(client, stub_hooks, monkeypatch, room)
    prompts = _prompts(stub_hooks, monkeypatch)

    refused = _skip(client, question["id"], by="bob")

    assert refused.status_code == 403, refused.text
    assert _waiting_questions(client, "alice") == [question["id"]]
    assert prompts == []


def test_taking_the_skip_back_makes_the_question_wait_again(
    client, stub_hooks, monkeypatch
):
    _, room, _ = _room(client)
    question = _ask(client, stub_hooks, monkeypatch, room)
    _skip(client, question["id"])
    prompts = _prompts(stub_hooks, monkeypatch)

    response = _undo(client, question["id"])

    assert response.status_code == 200, response.text
    assert _waiting_questions(client, "alice") == [question["id"]]
    # The agent that had been told "skipped" is told it waits again.
    assert len(prompts) == 1 and "撤销" in prompts[0]


def test_an_answered_question_cannot_also_be_skipped(client, stub_hooks, monkeypatch):
    """One response closes a question, a skip as much as an answer."""
    _, room, _ = _room(client)
    question = _ask(client, stub_hooks, monkeypatch, room)
    _skip(client, question["id"])

    again = _skip(client, question["id"])

    assert again.status_code == 409, again.text


def test_skipping_settles_the_question_s_notice(client, stub_hooks, monkeypatch):
    """The inbox notice stops saying 待你回答 once the question is skipped."""
    _, room, token = _room(client)
    question = _ask(client, stub_hooks, monkeypatch, room)

    def notice() -> dict:
        r = client.get(
            "/notifications",
            params={"type": "CHEESE_QUESTION"},
            headers={"Authorization": f"Bearer {token}"},
        )
        (row,) = r.json()["data"]["notifications"]
        return row

    assert notice()["read"] is False
    _skip(client, question["id"])
    assert notice()["read"] is True
