"""GET /api/topics/{id}/status → `stall`: is a session here stopped mid-call?

The 8-hour incident (2026-08-11): three topics stopped mid-work — container
recreate, OOM-killed child, sandbox image swap — and every surface kept saying
the same thing it says about healthy work. Topic `status` was `active`, the
last thing the agent did was an ordinary tool call, and nothing anywhere could
be ASKED "is this one dead?". These tests are that question, and its answer
must hold up against the case that makes it hard: a turn legitimately grinding
away for hours must never be reported dead.

What a session did is read from its raw records: work cut off between starting
a tool and hearing back has written the call and not its result. Whether it is
still at work is what its runner declares (`session_states`), and whether the
runner is still there is whether it answered a read lately. No turn interval is
read: the work here is written straight into the records, with no turn row.
"""

import asyncio
import time
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.domain.agent import session_state
from app.domain.agent.realtime.broker import get_broker
from app.domain.session_record import service as session_records
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


def _declares(client, topic_id: str, status: str) -> None:
    """The runner of the seat's session in the room says it is ``status``."""
    key = session_state.SeatKey(uuid.UUID(topic_id), SEAT, "claude-code")

    async def _run() -> None:
        await session_state.register(client.test_factory, key)
        async with client.test_factory() as session:
            await session_state.declare(session, key, status, datetime.now(UTC))
            await session.commit()

    asyncio.run(_run())


def _turn(client, topic_id: str, records: list[dict], *, age: timedelta) -> uuid.UUID:
    """Work of the seat's session that wrote ``records``, the last of them
    ``age`` ago, and that its runner still declares running."""
    room = uuid.UUID(topic_id)
    work = uuid.uuid4()
    last = datetime.now(UTC) - age
    first = last - timedelta(seconds=len(records))

    async def _run() -> None:
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
    _declares(client, topic_id, "running")
    return work


def _close(client, topic_id: str) -> None:
    """The runner says the session is idle: the work ended, failed or was
    taken away."""
    _declares(client, topic_id, "idle")


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
    # No process here reads the session, which is why the silence counts.
    assert stall["reason"] == "not_read"
    assert stall["runner_answered_s"] is None
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
    assert stall["reason"] == "not_read"


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


def test_the_signal_goes_quiet_once_the_runner_says_the_session_is_idle(client):
    """The runner ends wedged work itself and says the session is idle. After
    that the room SAYS what happened, so it is no longer an unanswered
    question — and a signal that keeps firing past its own fix is one people
    learn to ignore."""
    tid = _topic(client)
    _turn(client, tid, [_PROMPT, _call("toolu_1", "pytest -q")], age=timedelta(hours=6))
    assert _stall(client, tid)["stalled"] is True

    _close(client, tid)
    assert _stall(client, tid)["stalled"] is False


def test_an_empty_topic_is_not_stalled(client):
    tid = _topic(client)
    stall = _stall(client, tid)
    assert stall["stalled"] is False
    assert stall["last_record"] is None
    assert stall["silent_for_s"] is None


def _runner_answered(topic_id: str, *, ago: float) -> None:
    """The runner holding the room's session last answered a read ``ago``
    seconds back (`note_alive`)."""
    broker = get_broker()
    broker.note_alive(topic_id)
    broker._last_alive_at[topic_id] = time.monotonic() - ago


def test_work_still_running_is_never_called_dead(client):
    """The load-bearing case. A session deep in one long tool call writes
    nothing for as long as that call takes, so its records alone cannot tell
    it from a corpse. Its runner answering is what separates them, and it has
    to win."""
    tid = _topic(client)
    _turn(client, tid, [_PROMPT, _call("toolu_1", "pytest -q")], age=timedelta(hours=3))
    assert _stall(client, tid)["stalled"] is True  # ...with nobody listening

    _runner_answered(tid, ago=12.0)
    stall = _stall(client, tid)
    assert stall["stalled"] is False
    assert stall["runner_answered_s"] == 12


def test_a_runner_long_silent_is_still_caught(client):
    """The machine can outlive what it was driving, or go away under it: the
    session still reads as running, and its runner has stopped answering.
    Having answered once is not proof of life — answering lately is."""
    tid = _topic(client)
    _turn(client, tid, [_PROMPT, _call("toolu_1", "pytest -q")], age=timedelta(hours=3))

    _runner_answered(tid, ago=3 * 3600)
    stall = _stall(client, tid)
    assert stall["stalled"] is True
    # Named apart from the nobody-is-reading case: this one was being read.
    assert stall["reason"] == "runner_silent"
