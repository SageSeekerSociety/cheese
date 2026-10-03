"""Receipt evidence survives reader failure, age and runner output retention.

SQLite journals are real; callbacks simulate settlement availability, not a
PostgreSQL commit failure or a native model. Readers are reconstructed in-process.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.agent.harness import SessionRef
from app.domain.agent.harness.claude_code.journal import Journal
from app.domain.agent.harness.claude_code.subscription import Subscription
from app.domain.agent.harness.driven import backlog
from app.domain.delivery.input_identity import InputIdentity, InputReceipt


@pytest.mark.anyio
@pytest.mark.parametrize("old", [False, True])
async def test_unsettled_receipt_survives_reconstructed_reader(
    tmp_path, monkeypatch, old
):
    path = tmp_path / "records.sqlite"
    work, input_id = str(uuid.uuid4()), str(uuid.uuid4())
    now = datetime.now(UTC)
    at = now - timedelta(days=3) if old else now
    rows = [
        {
            "sequence": 1,
            "at": at.isoformat(),
            "record": {
                "type": "user",
                "uuid": input_id,
                "isReplay": True,
                "message": {"role": "user", "content": "answer received"},
                "cheese": {
                    "work_id": work,
                    "receipt": True,
                    "receipt_work_id": work,
                    "receipt_session_id": "native-session",
                },
            },
        }
    ]
    calls = []
    available = False
    session = SessionRef(uuid.uuid4(), uuid.uuid4(), "cheese-a", harness="claude-code")

    async def remote(method, params):
        return {"events": [row for row in rows if row["sequence"] > params["after"]]}

    async def consume(*args):
        pass

    async def activity(*args):
        pass

    async def announce():
        pass

    async def settle(receipt):
        calls.append(receipt)
        if not available:
            raise OSError("settlement unavailable")

    async def drain():
        reading = Subscription(
            session,
            path,
            remote,
            consume,
            activity,
            session_id="native-session",
            recipient_handle="cheese-a",
            announce=announce,
            receipts=settle,
        )
        try:
            await reading.drain()
        finally:
            await reading.release()

    class Clock(datetime):
        ahead = timedelta()

        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) + cls.ahead

    monkeypatch.setattr(backlog, "datetime", Clock)
    for _ in range(5):
        with pytest.raises(OSError, match="settlement unavailable"):
            await drain()
        Clock.ahead += timedelta(minutes=10)
        journal = Journal(path)
        try:
            assert int(journal.recall("landed") or 0) == 0
            journal.prune((now + timedelta(days=1)).isoformat())
            assert len(journal.read()) == 1
        finally:
            journal.close()
    available = True
    await drain()
    assert (
        calls
        == [
            InputReceipt(
                InputIdentity(
                    session.project_id,
                    session.topic_id,
                    "cheese-a",
                    session.harness,
                    "native-session",
                    uuid.UUID(input_id),
                    uuid.UUID(work),
                ),
                "native_echo",
            )
        ]
        * 6
    )
    await drain()
    assert len(calls) == 6
    journal = Journal(path)
    try:
        assert journal.recall("landed") == "1"
        journal.prune((now + timedelta(days=1)).isoformat())
        assert journal.read() == []
    finally:
        journal.close()


def test_runner_retention_preserves_echo_until_a_reader_can_settle(tmp_path):
    path = tmp_path / "runner.sqlite"
    journal = Journal(path)
    try:
        journal.append({"type": "assistant", "uuid": "output"})
        journal.append({"type": "user", "uuid": "echo", "cheese": {"receipt": True}})
        journal.expire((datetime.now(UTC) + timedelta(days=1)).isoformat())
    finally:
        journal.close()
    reopened = Journal(path)
    try:
        assert [row["record"]["uuid"] for row in reopened.read()] == ["echo"]
    finally:
        reopened.close()
