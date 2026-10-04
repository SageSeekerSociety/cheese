"""Retained legacy journals prove execution ownership, never addressed ownership."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.agent.harness import CLAUDE_CODE, SessionRef
from app.domain.agent.harness.claude_code.journal import Journal
from app.domain.agent.harness.claude_code.subscription import Subscription


@pytest.mark.anyio
@pytest.mark.parametrize("format", ["unstamped", "draft-stamp"])
@pytest.mark.parametrize("broken", [None, "gap", "missing-receipt", "wrong-session"])
async def test_retained_completion_requires_complete_exact_evidence(
    tmp_path, format, broken
):
    session = SessionRef(uuid.uuid4(), uuid.uuid4(), "cheese-a", harness=CLAUDE_CODE)
    addressed, executed, input_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    owner = {"work_id": str(executed), "agent_handle": "cheese-a", "unsolicited": True}
    echo = {
        "type": "user",
        "uuid": str(input_id),
        "isReplay": True,
        "session_id": "native-session",
        "cheese": {
            **owner,
            "turn_start": True,
            "receipt": True,
            "receipt_work_id": str(addressed),
            "receipt_session_id": "native-session",
        },
    }
    if broken == "missing-receipt":
        echo["cheese"].pop("receipt_session_id")
    result = {
        "type": "result",
        "session_id": "native-session",
        "is_error": False,
        "cheese": dict(owner),
    }
    if format == "draft-stamp":
        result["cheese"].update(
            work_completed=True, completion_session_id="native-session"
        )
    if broken == "wrong-session":
        result["session_id"] = "someone-else"
    entries = [
        {"sequence": 1, "record": echo},
        {"sequence": 3 if broken == "gap" else 2, "record": result},
    ]
    at = (datetime.now(UTC) - timedelta(days=3)).isoformat()
    for entry in entries:
        entry["at"] = at
    path = tmp_path / "records.sqlite"
    journal = Journal(path)
    journal.import_records(entries, {})
    journal.close()
    receipts, completions = [], []

    async def unused(*args, **kwargs):
        pass

    async def receipt(value):
        receipts.append(value)

    async def completion(value):
        completions.append(value)

    reading = Subscription(
        session,
        path,
        unused,
        unused,
        unused,
        session_id="native-session",
        recipient_handle="cheese-a",
        announce=unused,
        receipts=receipt,
        completions=completion,
        input_protocol=None,
    )
    try:
        if broken:
            # Unprovable is not retried: the journal holds no more on the next
            # read, and a raise here stops the room reading the session at
            # this result for good. Nothing is settled.
            assert await reading.settle_completion(result) is None
            assert completions == []
            assert receipts == []
        else:
            await reading.settle_completion(result)
            assert completions[0].work_id == executed
            assert completions[0].input_ids == (input_id,)
            assert receipts[0].identity.work_id == addressed
            assert receipts[0].execution_work_id == executed
        journal = Journal(path)
        try:
            assert journal.recall("landed") is None
            assert journal.older_run(0, datetime.now(UTC).isoformat()) == (0, 0)
        finally:
            journal.close()
    finally:
        await reading.release()
