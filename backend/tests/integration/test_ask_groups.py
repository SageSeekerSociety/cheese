"""Real HTTP authorization and PostgreSQL group transactions.

Only native origin discovery and post-commit dispatch are fixture providers.
These tests do not prove native session provenance, I/O or completion.
"""

import uuid

import pytest

from app.core.sandbox_auth import mint_scoped_token
from tests.integration.conftest import (
    post_project,
    room_agent_seat,
    session_auth_headers,
)


@pytest.fixture
def group(client, monkeypatch):
    project = post_project(
        client, json={"name": "Ask groups"}, headers=session_auth_headers("user-1")
    ).json()["data"]
    topic = client.post(
        "/topics", json={"project_id": project["id"], "title": "Groups"}
    ).json()["data"]["id"]
    seat = room_agent_seat(client, topic)
    client.headers["X-Cheese-Token"] = mint_scoped_token(
        project_id=project["id"], topic_id=topic, agent_handle=seat
    )

    async def origin(self, project_id, topic_id, author):
        return {
            "harness": "fixture",
            "native_session_id": "fixture-native-session",
            "work_id": str(uuid.uuid4()),
            "recipient_handle": seat,
            "asked_by": author,
            "asked": "user-1",
        }

    async def no_dispatch(*args, **kwargs):
        return 0

    monkeypatch.setattr("app.api.routes.topics_asks.ask_origin", origin)
    monkeypatch.setattr("app.api.routes.topics_asks.dispatch_pending", no_dispatch)
    body = {
        "ask_group": "fixed-group",
        "questions": [
            {"question": "First?", "options": [{"text": "A"}, {"text": "B"}]},
            {"question": "Second?", "options": [{"text": "A"}, {"text": "B"}]},
        ],
    }
    response = client.post(f"/topics/{topic}/asks", json=body)
    assert response.status_code == 200, response.text
    return response.json()["data"], body


def submission(data, *, operation="op-1", version=0, choice="A"):
    members = data["group"]["members"]
    return {
        "topic_id": data["group"]["topic_id"],
        "asked_by": data["group"]["asked_by"],
        "client_op_id": operation,
        "expect_version": version,
        "answered": [
            {
                "block_id": members[0],
                "kind": "option",
                "option": choice,
                "client_op_id": operation + "-answer",
                "expect_version": version,
            }
        ],
        "later": [{"block_id": members[1], "client_op_id": operation + "-later"}],
        "unanswered": [],
    }


def settle(client, data, payload):
    return client.post(
        f"/topics/asks/{data['group']['id']}/settle",
        json=payload,
        headers=session_auth_headers("user-1"),
    )


def answer_all(data, *, operation, group_version=0, choice="A"):
    """Settle a group with every member answered and nothing deferred."""
    return {
        "topic_id": data["group"]["topic_id"],
        "asked_by": data["group"]["asked_by"],
        "client_op_id": operation,
        "expect_version": group_version,
        "answered": [
            {
                "block_id": block_id,
                "kind": "option",
                "option": choice,
                "client_op_id": f"{operation}-{position}",
                "expect_version": 0,
            }
            for position, block_id in enumerate(data["group"]["members"])
        ],
        "later": [],
        "unanswered": [],
    }


def read(client, data):
    return client.get(
        f"/topics/asks/{data['group']['id']}",
        params={key: data["group"][key] for key in ("topic_id", "asked_by")},
        headers=session_auth_headers("user-1"),
    ).json()["data"]


def test_replay_confirms_old_operation_without_rolling_back_blocks(client, group):
    data, _ = group
    first = submission(data)
    response = settle(client, data, first)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["settlement"]["v"] == 1
    second = submission(data, operation="op-2", version=1, choice="B")
    response = settle(client, data, second)
    assert response.status_code == 200, response.text
    replay = settle(client, data, first)
    assert replay.status_code == 200, replay.text
    result = replay.json()["data"]
    assert result["settlement"]["client_op_id"] == "op-1"
    assert result["settlement"]["v"] == 1
    assert result["blocks"][0]["meta"]["answer_log"][-1]["option"] == "B"
    assert read(client, data)["settlement"]["client_op_id"] == "op-2"
    changed = {**first, "answered": [{**first["answered"][0], "option": "B"}]}
    assert settle(client, data, changed).status_code == 409
    blocks = client.get(f"/topics/{data['group']['topic_id']}/blocks").json()["data"][
        "data"
    ]
    wakes = [block for block in blocks if (block.get("meta") or {}).get("answer_group")]
    assert len(wakes) == 2


def test_last_member_error_leaves_every_answer_unchanged(client, group):
    data, _ = group
    body = submission(data)
    body["answered"].append(
        {
            "block_id": data["group"]["members"][1],
            "kind": "option",
            "option": "missing",
            "client_op_id": "invalid-last",
            "expect_version": 0,
        }
    )
    body["later"] = []
    response = settle(client, data, body)
    assert response.status_code == 422, response.text
    current = read(client, data)
    assert current["settlement"] is None
    assert all(block["meta"]["answer_log"] == [] for block in current["blocks"])
    # Fixed groups cannot be submitted as a loop over the single-answer route.
    response = client.post(
        f"/topics/blocks/{data['group']['members'][0]}/answers",
        json=body["answered"][0],
        headers=session_auth_headers("user-1"),
    )
    assert response.status_code == 422, response.text


def test_second_submission_confirms_delta_and_preserves_effective_answers(
    client, group
):
    data, _ = group
    first = submission(data)
    response = settle(client, data, first)
    assert response.status_code == 200, response.text
    first_operation = response.json()["data"]["settlement"]["operation"]
    q1, q2 = data["group"]["members"]
    second = {
        **submission(data, operation="op-2", version=1),
        "answered": [
            {
                "block_id": q2,
                "kind": "option",
                "option": "B",
                "note": "",
                "expect_version": 0,
                "client_op_id": "op-2-answer",
            }
        ],
        "later": [],
        "unanswered": [{"block_id": q1, "client_op_id": "op-2-kept"}],
    }
    response = settle(client, data, second)
    assert response.status_code == 200, response.text
    result = response.json()["data"]
    settlement = result["settlement"]
    assert settlement["operation"] == {**second, "group_id": data["group"]["id"]}
    assert settlement["answered"] == [q1, q2]
    assert settlement["unanswered"] == []
    assert [b["meta"]["answer_log"][-1]["option"] for b in result["blocks"]] == [
        "A",
        "B",
    ]
    replay = settle(client, data, first)
    assert replay.status_code == 200, replay.text
    assert replay.json()["data"]["settlement"]["operation"] == first_operation
    assert read(client, data)["settlement"]["operation"] == settlement["operation"]


def test_deferred_member_remains_in_waiting_list_with_exact_block(client, group):
    data, _ = group
    response = settle(client, data, submission(data))
    assert response.status_code == 200, response.text
    waiting = client.get(
        "/awaiting-me",
        headers={**session_auth_headers("user-1"), "X-Cheese-Token": ""},
    )
    assert waiting.status_code == 200, waiting.text
    item = next(
        row
        for row in waiting.json()["data"]["data"]
        if row["topicId"] == data["group"]["topic_id"]
    )
    assert item["reason"] == "asked"
    assert item["blockId"] == data["group"]["members"][1]


def test_human_cannot_create_group_or_append_members(client, group):
    data, body = group
    path = f"/topics/{data['group']['topic_id']}/asks"
    response = client.post(path, json=body, headers=session_auth_headers("user-1"))
    assert response.status_code == 403, response.text
    response = client.post(path, json=body)
    assert response.status_code == 409, response.text
    assert read(client, data)["group"]["total"] == 2


def test_answering_a_later_group_leaves_the_earlier_one_pending(client, group):
    """答完后发的那组，不会把先发那组的待答冲掉。

    待答读的是「哪一组还有没答的题」，不是「最近那组答完没有」。两组同在一个房间
    里，后发那组整组答完就退场，先发那组一道没答，仍旧挂在它第一道没答的题上。
    """
    earlier, _ = group
    topic_id = earlier["group"]["topic_id"]
    created = client.post(
        f"/topics/{topic_id}/asks",
        json={
            "ask_group": "later-group",
            "questions": [
                {"question": "Third?", "options": [{"text": "A"}, {"text": "B"}]},
                {"question": "Fourth?", "options": [{"text": "A"}, {"text": "B"}]},
            ],
        },
    )
    assert created.status_code == 200, created.text
    later = created.json()["data"]

    response = settle(client, later, answer_all(later, operation="later-op"))
    assert response.status_code == 200, response.text
    assert [
        block["meta"]["answer_log"][-1]["option"]
        for block in response.json()["data"]["blocks"]
    ] == ["A", "A"]

    waiting = client.get(
        "/awaiting-me",
        headers={**session_auth_headers("user-1"), "X-Cheese-Token": ""},
    )
    assert waiting.status_code == 200, waiting.text
    pending = {
        row["blockId"]
        for row in waiting.json()["data"]["data"]
        if row["topicId"] == topic_id and row["reason"] == "asked"
    }
    assert earlier["group"]["members"][0] in pending, "先发那组该还挂着第一道没答的题"
    assert not set(later["group"]["members"]) & pending, "后发那组答完就不该再挂"
    assert read(client, earlier)["settlement"] is None
