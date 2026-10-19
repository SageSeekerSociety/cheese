"""A session's raw records are kept for thirty days; after that, only what the
session was sent is kept, and a deleted conversation keeps nothing.

The rules, stated without the code: a record older than thirty days is gone,
from the database and from the bucket; a younger one is untouched; the inputs
a session was sent (a person's message, the platform's prompt) outlive the
thirty days, compressed in the bucket; and deleting a conversation removes its
records, their objects and that archive.
"""

import gzip
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.domain.agent.harness import CLAUDE_CODE
from app.domain.session_record import retention, service
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
    bucket = _Bucket()
    monkeypatch.setattr(service, "private_storage", lambda: bucket)
    return bucket


def _turn(work: str) -> list[dict]:
    """A turn whose first record is the input the session was sent."""
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


def _seal(client, bucket, *, days: float = 0) -> None:
    later = datetime.now(UTC) + timedelta(days=days) + service.SEAL_AFTER * 2
    _call(client, lambda: service.seal(client.test_request_factory, bucket, now=later))


def _expire(client, bucket, *, days: float) -> None:
    later = datetime.now(UTC) + timedelta(days=days)
    _call(
        client,
        lambda: retention.expire(client.test_request_factory, bucket, now=later),
    )


def _forget(client, bucket) -> None:
    _call(client, lambda: retention.forget_gone(client.test_request_factory, bucket))


def _archived(bucket: _Bucket, conversation: uuid.UUID) -> list[dict]:
    prefix = f"{retention.ARCHIVE_PREFIX}/{conversation}/"
    return [
        json.loads(line)
        for key, body in bucket.objects.items()
        if key.startswith(prefix)
        for line in gzip.decompress(body).decode().splitlines()
    ]


def _start(client, tmp_path, bucket) -> tuple[_Session, str]:
    session = _Session(client, tmp_path, CLAUDE_CODE, _room_for(client))
    work = str(uuid.uuid4())
    session.write(_turn(work))
    session.drain()
    _seal(client, bucket)
    assert bucket.objects, "the turn was sealed into the bucket"
    return session, work


def test_past_thirty_days_only_the_inputs_are_kept(client, tmp_path, bucket):
    session, work = _start(client, tmp_path, bucket)
    sealed = set(bucket.objects)

    _expire(client, bucket, days=31)

    # The bulk is gone from the bucket and from the record...
    assert not sealed & set(bucket.objects)
    kept = session.read(turn_id=work)
    assert [item["record"]["message"]["content"] for item in kept] == [ASKED]
    # ...and what the session was sent is in the archive, compressed.
    archived = _archived(bucket, session.ref.topic_id)
    assert [entry["record"]["message"]["content"] for entry in archived] == [ASKED]


def test_within_thirty_days_nothing_is_touched(client, tmp_path, bucket):
    session, work = _start(client, tmp_path, bucket)
    before = session.read(turn_id=work)
    objects = dict(bucket.objects)

    _expire(client, bucket, days=29)

    assert session.read(turn_id=work) == before
    assert bucket.objects == objects


def test_a_deleted_conversation_keeps_no_records_and_no_archive(
    client, tmp_path, bucket
):
    session, work = _start(client, tmp_path, bucket)
    _expire(client, bucket, days=31)
    assert _archived(bucket, session.ref.topic_id)
    # A newer turn, still whole, beside the archive.
    session.write(_turn(str(uuid.uuid4())))
    session.drain()
    _seal(client, bucket)

    room = session.ref.topic_id
    owner = {"X-Cheese-Token": "", **session_auth_headers("user-1")}
    gone = client.delete(f"/topics/{room}", headers=owner)
    assert gone.status_code == 200, gone.text
    _forget(client, bucket)

    assert not [key for key in bucket.objects if str(room) in key]

    async def rows() -> int:
        async with client.test_request_factory() as db:
            return await db.scalar(
                select(func.count())
                .select_from(SessionRecord)
                .where(SessionRecord.conversation_id == room)
            )

    assert client.portal.call(rows) == 0
