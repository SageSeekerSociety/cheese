"""GET /api/topics/{id}/status → `stall`: did a turn die on this topic?

The 8-hour incident (2026-08-11): three topics stopped mid-work — container
recreate, OOM-killed child, sandbox image swap — and every surface kept saying
the same thing it says about healthy work. Topic `status` was `active`, the
last thing the agent did was an ordinary tool call, and nothing anywhere could
be ASKED "is this one dead?". These tests are that question, and its answer
must hold up against the case that makes it hard: a turn legitimately grinding
away for hours must never be reported dead.

What a turn did is read from its session's raw records: a turn cut off between
starting a tool and hearing back has written the call and not its result.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import update

from app.api.deps import get_work_runner
from app.domain.agent.models import AgentTurn
from app.domain.session_record import service as session_records
from app.main import app
from tests.integration.conftest import post_project

SEAT = "cheese"


def _topic(client) -> str:
    pid = post_project(client, json={"name": "P"}).json()["data"]["id"]
    return client.post(
        "/topics", json={"project_id": pid, "title": "做一个东西"}
    ).json()["data"]["id"]


def _call(call_id: str, command: str) -> dict:
    return {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": call_id,
                    "name": "Bash",
                    "input": {"command": command},
                }
            ],
        },
    }


def _result_of(call_id: str) -> dict:
    return {
        "type": "user",
        "message": {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": call_id, "content": "ok"}
            ],
        },
    }


_SAID = {
    "type": "assistant",
    "message": {"role": "assistant", "content": [{"type": "text", "text": "跑完了"}]},
}
_ENDED = {"type": "result", "subtype": "success", "result": "跑完了"}
_PROMPT = {"type": "user", "message": {"role": "user", "content": "跑一下测试"}}


def _turn(client, topic_id: str, records: list[dict], *, age: timedelta) -> uuid.UUID:
    """A turn of the seat's session that wrote ``records``, the last of them
    ``age`` ago, which the platform still holds open."""
    room = uuid.UUID(topic_id)
    work = uuid.uuid4()
    last = datetime.now(UTC) - age
    first = last - timedelta(seconds=len(records))

    async def _run() -> None:
        async with client.test_factory() as session:
            session.add(
                AgentTurn(
                    id=work,
                    conversation_id=room,
                    continuation_id=work,
                    author="user-1",
                    content="跑一下测试",
                    started_at=first,
                    delivered_at=first,
                    agent_handle=SEAT,
                )
            )
            await session.commit()
        await session_records.append(
            client.test_factory,
            SimpleNamespace(
                conversation_id=room, agent_handle=SEAT, harness="claude-code"
            ),
            [
                {
                    "sequence": n,
                    "at": (first + timedelta(seconds=n)).isoformat(),
                    "record": {**record, "cheese": {"work_id": str(work)}},
                }
                for n, record in enumerate(records, start=1)
            ],
        )

    asyncio.run(_run())
    return work


def _close(client, work: uuid.UUID) -> None:
    """The platform ends the turn: it failed, timed out or the sweep took it."""

    async def _run() -> None:
        async with client.test_factory() as session:
            await session.execute(
                update(AgentTurn)
                .where(AgentTurn.id == work)
                .values(stopped_at=datetime.now(UTC))
            )
            await session.commit()

    asyncio.run(_run())


def _stall(client, topic_id: str) -> dict:
    r = client.get(f"/topics/{topic_id}/status")
    assert r.status_code == 200
    return r.json()["data"]["stall"]


def test_a_turn_that_died_mid_tool_is_reported_stalled(client):
    tid = _topic(client)
    _turn(
        client, tid, [_PROMPT, _call("toolu_1", "pytest -q")], age=timedelta(minutes=45)
    )

    stall = _stall(client, tid)
    assert stall["stalled"] is True
    # Nobody is executing this topic, which is why the silence counts.
    assert stall["reason"] == "no_live_turn"
    assert stall["live_turn"] is None
    assert 2600 < stall["silent_for_s"] < 2800
    # The verdict shows its work: the call a reader can go and look at.
    assert stall["last_record"]["open_call"]["tool"] == "Bash"
    assert stall["last_record"]["open_call"]["call_id"] == "toolu_1"
    # A turn is a member's: the seat whose session stopped mid-call.
    assert stall["member"] == SEAT


def test_a_turn_that_died_while_two_calls_were_out_is_reported_stalled(client):
    tid = _topic(client)
    _turn(
        client,
        tid,
        [
            _PROMPT,
            _call("toolu_1", "pytest -q"),
            _call("toolu_2", "ruff check"),
            _result_of("toolu_1"),
        ],
        age=timedelta(minutes=45),
    )

    stall = _stall(client, tid)
    assert stall["stalled"] is True
    assert stall["last_record"]["open_call"]["call_id"] == "toolu_2"


def test_a_turn_that_died_right_after_showing_something_is_reported_stalled(client):
    # `cheese show` is a call like any other: the room has the file, and the
    # turn never heard back from showing it.
    tid = _topic(client)
    _turn(
        client,
        tid,
        [_PROMPT, _call("toolu_1", "cheese show report.html")],
        age=timedelta(minutes=45),
    )

    stall = _stall(client, tid)
    assert stall["stalled"] is True
    assert stall["reason"] == "no_live_turn"


def test_a_topic_that_just_acted_is_not_stalled(client):
    """Same shape, seconds old — a turn inside a tool call is the normal state
    of a working topic, and calling that dead would make the signal useless
    within a day."""
    tid = _topic(client)
    _turn(
        client, tid, [_PROMPT, _call("toolu_1", "pytest -q")], age=timedelta(seconds=5)
    )

    stall = _stall(client, tid)
    assert stall["stalled"] is False
    assert stall["reason"] is None
    assert stall["member"] is None


def test_a_turn_that_finished_hours_ago_is_not_stalled(client):
    """An idle topic is not a dead one: a turn that ends says so."""
    tid = _topic(client)
    _turn(
        client,
        tid,
        [_PROMPT, _call("toolu_1", "pytest -q"), _result_of("toolu_1"), _SAID, _ENDED],
        age=timedelta(hours=5),
    )

    assert _stall(client, tid)["stalled"] is False


def test_a_call_that_came_back_is_not_a_stall(client):
    """The session heard back from its call; whatever it does next, it was
    not cut off between starting something and hearing back."""
    tid = _topic(client)
    _turn(
        client,
        tid,
        [_PROMPT, _call("toolu_1", "pytest -q"), _result_of("toolu_1")],
        age=timedelta(minutes=45),
    )

    assert _stall(client, tid)["stalled"] is False


def test_a_turn_that_ended_with_a_call_unanswered_is_not_a_stall(client):
    """An interrupted turn ends on its result record, its last call never
    answered: the turn is over, not cut off."""
    tid = _topic(client)
    _turn(
        client,
        tid,
        [_PROMPT, _call("toolu_1", "pytest -q"), _ENDED],
        age=timedelta(minutes=45),
    )

    assert _stall(client, tid)["stalled"] is False


def test_the_signal_goes_quiet_once_the_platform_has_ended_the_turn(client):
    """The orphan sweep tears a wedged turn down and the room is told. After
    that the topic SAYS what happened, so it is no longer an unanswered
    question — and a signal that keeps firing past its own fix is one people
    learn to ignore."""
    tid = _topic(client)
    work = _turn(
        client, tid, [_PROMPT, _call("toolu_1", "pytest -q")], age=timedelta(hours=6)
    )
    assert _stall(client, tid)["stalled"] is True

    _close(client, work)
    assert _stall(client, tid)["stalled"] is False


def test_an_empty_topic_is_not_stalled(client):
    tid = _topic(client)
    stall = _stall(client, tid)
    assert stall["stalled"] is False
    assert stall["last_record"] is None
    assert stall["silent_for_s"] is None


class _RunnerWithLiveTurn:
    """A runner that IS executing a turn on this topic, still publishing frames.

    Stands in for the real one because a live turn cannot be held across a
    blocking HTTP call from a test; that the real runner reports a genuinely
    streaming turn this way is pinned separately in
    tests/unit/test_runtime.py::test_live_turn_for_topic_tracks_a_running_turn.
    """

    def __init__(self, topic_id: str, *, silent_for_s: float) -> None:
        self._topic_id = topic_id
        self._silent_for_s = silent_for_s

    def live_work_for_topic(self, topic_id: uuid.UUID) -> dict | None:
        if str(topic_id) != self._topic_id:
            return None
        return {"turn_id": "t-1", "silent_for_s": self._silent_for_s}

    def topic_work(self, topic_id: uuid.UUID) -> dict | None:
        return None

    def active_work_count(self) -> int:
        return 1

    async def project_queue_depth(self, project_id: uuid.UUID | str) -> int:
        return 0


def test_a_turn_still_running_is_never_called_dead(client):
    """The load-bearing case. A turn deep in one long tool call writes nothing
    for as long as that call takes, so its records alone cannot tell it from a
    corpse. The heartbeat is what separates them, and it has to win."""
    tid = _topic(client)
    _turn(client, tid, [_PROMPT, _call("toolu_1", "pytest -q")], age=timedelta(hours=3))
    assert _stall(client, tid)["stalled"] is True  # ...with no turn running

    app.dependency_overrides[get_work_runner] = lambda: _RunnerWithLiveTurn(
        tid, silent_for_s=12.0
    )
    try:
        stall = _stall(client, tid)
        assert stall["stalled"] is False
        assert stall["live_turn"]["silent_for_s"] == 12.0
    finally:
        app.dependency_overrides.pop(get_work_runner, None)


def test_a_turn_that_is_live_but_long_silent_is_still_caught(client):
    """A task can outlive what it was driving: the container dies, the stream
    never ends, and the turn sits in the runner producing nothing. Being
    "running" is not proof of life — the heartbeat is."""
    tid = _topic(client)
    _turn(client, tid, [_PROMPT, _call("toolu_1", "pytest -q")], age=timedelta(hours=3))

    app.dependency_overrides[get_work_runner] = lambda: _RunnerWithLiveTurn(
        tid, silent_for_s=3 * 3600
    )
    try:
        stall = _stall(client, tid)
        assert stall["stalled"] is True
        # Named apart from the process-is-gone case: this one still holds a task.
        assert stall["reason"] == "silent_turn"
    finally:
        app.dependency_overrides.pop(get_work_runner, None)
