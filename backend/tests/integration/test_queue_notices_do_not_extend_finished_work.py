"""Queue notices stay readable without claiming an execution ever ran them."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.agent.harness import CLAUDE_CODE
from app.domain.agent.harness.driven.reporting import Whose
from app.domain.session_record import service
from app.domain.session_record.models import SessionRecord
from tests.integration.test_a_turns_raw_record_keeps_what_it_did import (
    _Bucket,
    _room_for,
    _Session,
)
from tests.integration.test_session_records_rows_expire_transcripts_stay import (
    _call,
    _seal,
    _sweep,
)


@pytest.fixture
def bucket(monkeypatch):
    storage = _Bucket()
    monkeypatch.setattr(service, "private_storage", lambda: storage)
    monkeypatch.setattr(service, "bucket", lambda: storage)
    return storage


def _write(client, tmp_path, *, historical=False):
    session = _Session(client, tmp_path, CLAUDE_CODE, _room_for(client))
    old, new = uuid.uuid4(), uuid.uuid4()
    start = datetime(2026, 10, 21, 17, 1, 37, tzinfo=UTC)
    stop = start + timedelta(minutes=4, seconds=7)
    queued_at = start + timedelta(hours=21, minutes=20, seconds=29)
    records = [
        (start, {"type": "command_lifecycle", "state": "started"}, old),
        (stop, {"type": "result", "result": "done"}, old),
        (
            queued_at,
            {
                "type": "command_lifecycle",
                "state": "queued",
                "command_uuid": str(uuid.uuid4()),
            },
            old,
        ),
        (queued_at + timedelta(seconds=1), {"type": "result"}, new),
    ]
    rows = [
        {
            "sequence": sequence,
            "at": at.isoformat(),
            "record": {**record, "cheese": {"work_id": str(work)}},
        }
        for sequence, (at, record, work) in enumerate(records, 1)
    ]
    if historical:
        # Seed the append-only rows as the old runner wrote them, without
        # asking today's ingestion to misclassify the queue notice for us.
        async def seed():
            async with client.test_request_factory() as db:
                for row, (at, _record, work) in zip(rows, records, strict=True):
                    db.add(
                        SessionRecord(
                            id=uuid.uuid4(),
                            conversation_id=session.ref.topic_id,
                            turn_id=work,
                            agent_handle="cheese",
                            harness=CLAUDE_CODE,
                            first_sequence=row["sequence"],
                            last_sequence=row["sequence"],
                            first_at=at,
                            last_at=at,
                            count=1,
                            bytes=service.size(row),
                            records=[row],
                        )
                    )
                await db.commit()

        _call(client, seed)
    else:
        stream = Whose(
            session.ref, "cheese", session.ref.topic_id, "cheese", CLAUDE_CODE, ""
        )
        _call(client, lambda: service.append(client.test_request_factory, stream, rows))
    return session, old, stop, rows[2]["record"]


def test_a_queue_notice_never_claims_the_work_in_an_old_runner_stamp(client, tmp_path):
    session, old, _stop, queued = _write(client, tmp_path)
    items = session.read()
    notice = next(item for item in items if item["record"] == queued)
    assert notice["turn_id"] is None
    assert [item["sequence"] for item in session.read(turn_id=str(old))] == [1, 2]


@pytest.mark.parametrize("stored", ["rows", "sealed", "expired"])
def test_old_queue_stamps_do_not_change_the_finished_turns_timeline(
    client, tmp_path, monkeypatch, bucket, stored
):
    session, old, stop, queued = _write(client, tmp_path, historical=True)
    room = session.ref.topic_id
    if stored != "rows":
        _seal(client, bucket)
    if stored == "expired":
        _sweep(client, monkeypatch, days=31)
    before = dict(bucket.objects)

    # Pagination still includes the queue notice in the conversation, with
    # its original evidence intact, but never as part of the old execution.
    items = session.read()
    assert [item["sequence"] for item in items] == [1, 2, 3, 4]
    notice = next(item for item in items if item["record"] == queued)
    assert notice["turn_id"] is None
    assert notice["record"]["cheese"]["work_id"] == str(old)
    assert [item["sequence"] for item in session.read(turn_id=str(old))] == [1, 2]

    # The site reads newest-first. A one-record page must scan past the
    # misindexed queue notice rather than stop on an empty page.
    response = client.get(
        f"/topics/{room}/session-records",
        params={"turn_id": str(old), "newest": True, "limit": 1},
    )
    assert response.status_code == 200, response.text
    page = response.json()["data"]
    assert [item["sequence"] for item in page["items"]] == [2]
    assert page["next"] is not None

    timeline = client.get(f"/topics/{room}/transcript").json()["data"]["data"]
    execution = [item for item in timeline if item["turn_id"] == str(old)]
    assert max(datetime.fromisoformat(item["created_at"]) for item in execution) == stop
    assert bucket.objects == before
