"""Permanent archives stay readable after database expiry, behind room access."""

import gzip
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, text

from app.domain.agent.harness import CLAUDE_CODE
from app.domain.session_record import models, retention, service
from app.domain.session_record.models import SessionRecord
from tests.integration.conftest import session_auth_headers
from tests.integration.test_a_turns_raw_record_keeps_what_it_did import (
    _Bucket,
    _claude_records,
    _room_for,
    _Session,
)

ASKED = "Fix the login page; it rejects every password."


@pytest.fixture
def bucket(monkeypatch) -> _Bucket:
    """A deployment with a private bucket, as dev and production are."""
    bucket = _Bucket()
    monkeypatch.setattr(service, "private_storage", lambda: bucket)
    monkeypatch.setattr(service, "bucket", lambda: bucket)
    return bucket


def _turn(work: str) -> list[dict]:
    asked = {
        "type": "user",
        "uuid": str(uuid.uuid4()),
        "isReplay": True,
        "message": {"role": "user", "content": ASKED},
        "parent_tool_use_id": None,
        "cheese": {"work_id": work},
    }
    return [asked, *_claude_records(work)]


def _call(client, work):
    async def run():
        return await work()

    return client.portal.call(run)


def _seal(client, bucket) -> None:
    later = datetime.now(UTC) + service.SEAL_AFTER * 2
    _call(client, lambda: service.seal(client.test_request_factory, bucket, now=later))


def _sweep(client, monkeypatch, *, days: float = 0) -> None:
    """The periodic retention job, run as if `days` had passed since now.

    The window is patched on `models`, its one home: the sweep and `service.read`
    both read `models.RETENTION` through the module, so moving it here is the
    same passage of time to each of them."""
    monkeypatch.setattr(models, "RETENTION", models.RETENTION - timedelta(days))
    _call(client, lambda: retention.sweep(client.test_request_factory))


def _start(client, tmp_path, bucket) -> tuple[_Session, str]:
    session = _Session(client, tmp_path, CLAUDE_CODE, _room_for(client))
    work = str(uuid.uuid4())
    session.write(_turn(work))
    session.drain()
    _seal(client, bucket)
    assert bucket.objects, "the turn was sealed into the bucket"
    return session, work


def test_past_thirty_days_the_transcript_stays_in_the_bucket(
    client, tmp_path, monkeypatch, bucket
):
    session, work = _start(client, tmp_path, bucket)
    sealed = dict(bucket.objects)

    _sweep(client, monkeypatch, days=31)

    # The transcript is still there, byte for byte...
    assert bucket.objects == sealed
    # Database expiry must not make a permanent transcript disappear.
    assert session.read(turn_id=work)
    assert _rows(client, session.ref.topic_id) == 0


def test_within_thirty_days_nothing_is_touched(client, tmp_path, monkeypatch, bucket):
    session, work = _start(client, tmp_path, bucket)
    before = session.read(turn_id=work)
    objects = dict(bucket.objects)

    _sweep(client, monkeypatch, days=29)

    assert session.read(turn_id=work) == before
    assert bucket.objects == objects


def test_a_deleted_conversations_transcript_stays_in_the_bucket(
    client, tmp_path, monkeypatch, bucket
):
    session, first = _start(client, tmp_path, bucket)
    # A newer turn still in its rows when the channel is deleted.
    last = str(uuid.uuid4())
    session.write(_turn(last))
    session.drain()
    sealed = dict(bucket.objects)
    room = session.ref.topic_id

    _delete_room(client, room)
    # The sweep and the sealer run on clocks of their own, in either order.
    _sweep(client, monkeypatch)
    _seal(client, bucket)
    _sweep(client, monkeypatch)

    # What was sealed before is still there, byte for byte, and the turn that
    # was not yet sealed reached the bucket too.
    assert sealed.items() <= bucket.objects.items()
    assert _works_in(bucket) == {first, last}
    assert _rows(client, room) == 0


def test_deleted_channel_records_are_hidden_before_sealing(client, tmp_path, bucket):
    session = _Session(client, tmp_path, CLAUDE_CODE, _room_for(client))
    work = str(uuid.uuid4())
    session.write(_turn(work))
    session.drain()
    assert session.read(turn_id=work)
    _delete_room(client, session.ref.topic_id)

    response = client.get(f"/topics/{session.ref.topic_id}/session-records")
    assert response.status_code == 404
    assert _rows(client, session.ref.topic_id) > 0
    _seal(client, bucket)
    assert work in _works_in(bucket)


def test_sealing_keeps_database_copy_when_bucket_readback_differs(
    client, tmp_path, bucket, monkeypatch
):
    session = _Session(client, tmp_path, CLAUDE_CODE, _room_for(client))
    work = str(uuid.uuid4())
    session.write(_turn(work))
    session.drain()
    before = session.read(turn_id=work)
    download = bucket.download

    async def corrupt_readback(key):
        return b"truncated upload"

    monkeypatch.setattr(bucket, "download", corrupt_readback)
    with pytest.raises(RuntimeError, match="verification failed"):
        _seal(client, bucket)
    assert session.read(turn_id=work) == before
    monkeypatch.setattr(bucket, "download", download)
    _seal(client, bucket)
    assert session.read(turn_id=work) == before


def test_a_conversation_whose_row_is_gone_keeps_its_transcript(
    client, tmp_path, monkeypatch, bucket
):
    session, _work = _start(client, tmp_path, bucket)
    sealed = dict(bucket.objects)
    room = session.ref.topic_id

    async def remove() -> None:
        async with client.test_request_factory() as db:
            await db.execute(text("DELETE FROM topics WHERE id = :id"), {"id": room})
            await db.commit()

    client.portal.call(remove)
    _sweep(client, monkeypatch)

    assert bucket.objects == sealed
    assert _rows(client, room) == 0


def test_forgetting_one_conversation_leaves_every_other_readable(
    client, tmp_path, monkeypatch, bucket
):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    gone, _ = _start(client, tmp_path / "a", bucket)
    kept, work = _start(client, tmp_path / "b", bucket)
    before = kept.read(turn_id=work)

    _delete_room(client, gone.ref.topic_id)
    _sweep(client, monkeypatch)

    assert kept.read(turn_id=work) == before


def test_without_a_bucket_age_does_not_take_the_only_copy(
    client, tmp_path, monkeypatch
):
    monkeypatch.setattr(service, "bucket", lambda: None)
    session = _Session(client, tmp_path, CLAUDE_CODE, _room_for(client))
    work = str(uuid.uuid4())
    session.write(_turn(work))
    session.drain()
    before = session.read(turn_id=work)

    _sweep(client, monkeypatch, days=31)

    assert session.read(turn_id=work) == before


def test_without_a_bucket_a_deleted_conversation_still_loses_its_records(
    client, tmp_path, monkeypatch
):
    monkeypatch.setattr(service, "bucket", lambda: None)
    session = _Session(client, tmp_path, CLAUDE_CODE, _room_for(client))
    session.write(_turn(str(uuid.uuid4())))
    session.drain()
    room = session.ref.topic_id
    assert _rows(client, room) > 0
    _delete_room(client, room)
    _sweep(client, monkeypatch)
    assert _rows(client, room) == 0


def _works_in(bucket: _Bucket) -> set[str]:
    """The turns whose records the bucket's objects hold."""
    return {
        json.loads(line)["record"]["cheese"]["work_id"]
        for key, body in bucket.objects.items()
        if key.startswith("session-records/")
        for line in gzip.decompress(body).decode().splitlines()
    }


def _delete_room(client, room: uuid.UUID) -> None:
    owner = {"Authorization": "", **session_auth_headers("user-1")}
    gone = client.delete(f"/topics/{room}", headers=owner)
    assert gone.status_code == 200, gone.text


def _rows(client, room: uuid.UUID) -> int:
    async def count() -> int:
        async with client.test_request_factory() as db:
            return await db.scalar(
                select(func.count())
                .select_from(SessionRecord)
                .where(SessionRecord.conversation_id == room)
            )

    return client.portal.call(count)
