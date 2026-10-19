"""`cheese status` says a turn died when its session stopped part-way through a
tool call, read from the session's own raw records.

A turn that is cut off between starting a tool and hearing back leaves a call
with no result in its journal, and nothing after it. When the platform still
holds that turn open and nothing is running it, the room's status must say it
stalled, and whose seat it was. A call that came back, a turn that ended, and a
turn the platform already closed are not stalls.
"""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.domain.agent.models import AgentTurn
from app.domain.session_record import service as session_records
from app.domain.topic.services import TopicService
from tests.integration.conftest import project_service, registered

SEAT = "cheese"
AN_HOUR_AGO = datetime.now(UTC) - timedelta(hours=1)


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
    "message": {"role": "assistant", "content": [{"type": "text", "text": "Done."}]},
}
_ENDED = {"type": "result", "subtype": "success", "result": "Done."}
_PROMPT = {"type": "user", "message": {"role": "user", "content": "run the tests"}}


async def _room_with_turn(factory, *, closed: bool) -> tuple[uuid.UUID, uuid.UUID]:
    async with factory() as session:
        await registered(session, "alice")
        project = await project_service(session).create(
            name="Mid-call", owner_handle="alice"
        )
        room = await TopicService(session).create(
            project_id=project.id, title="Mid-call", created_by="alice"
        )
        work = uuid.uuid4()
        session.add(
            AgentTurn(
                id=work,
                conversation_id=room.id,
                continuation_id=work,
                author="alice",
                content="run the tests",
                started_at=AN_HOUR_AGO,
                delivered_at=AN_HOUR_AGO,
                stopped_at=AN_HOUR_AGO + timedelta(minutes=30) if closed else None,
                agent_handle=SEAT,
            )
        )
        await session.commit()
    return room.id, work


async def _journal(factory, room: uuid.UUID, work: uuid.UUID, records: list[dict]):
    stream = SimpleNamespace(
        conversation_id=room, agent_handle=SEAT, harness="claude-code"
    )
    await session_records.append(
        factory,
        stream,
        [
            {
                "sequence": n,
                "at": (AN_HOUR_AGO + timedelta(seconds=n)).isoformat(),
                "record": {**record, "cheese": {"work_id": str(work)}},
            }
            for n, record in enumerate(records, start=1)
        ],
    )


async def _stall(factory, room: uuid.UUID) -> dict:
    async with factory() as session:
        return await TopicService(session).stall_signal(
            room, live_turn=None, threshold_s=60
        )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "records",
    [
        [_PROMPT, _call("toolu_1", "pytest -q")],
        # Two calls in flight; one came back, the other never did.
        [
            _PROMPT,
            _call("toolu_1", "pytest -q"),
            _call("toolu_2", "ruff check"),
            _result_of("toolu_1"),
        ],
    ],
    ids=["one-call", "one-of-two-calls"],
)
async def test_a_session_cut_off_mid_call_reads_as_stalled(
    business_db_factory, records
):
    room, work = await _room_with_turn(business_db_factory, closed=False)
    await _journal(business_db_factory, room, work, records)

    stall = await _stall(business_db_factory, room)

    assert stall["stalled"] is True, stall
    assert stall["member"] == SEAT
    assert stall["reason"] == "no_live_turn"
    assert stall["silent_for_s"] > 60


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("records", "closed"),
    [
        (
            [_PROMPT, _call("toolu_1", "pytest -q"), _result_of("toolu_1"), _SAID, _ENDED],
            False,
        ),
        ([_PROMPT, _call("toolu_1", "pytest -q")], True),
    ],
    ids=["turn-ended", "platform-closed-the-turn"],
)
async def test_a_turn_that_ended_is_not_a_stall(business_db_factory, records, closed):
    room, work = await _room_with_turn(business_db_factory, closed=closed)
    await _journal(business_db_factory, room, work, records)

    stall = await _stall(business_db_factory, room)

    assert stall["stalled"] is False, stall
    assert stall["member"] is None
