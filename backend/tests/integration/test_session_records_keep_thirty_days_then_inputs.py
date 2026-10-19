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
from sqlalchemy import func, select, text

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
    _delete_room(client, room)
    _forget(client, bucket)

    assert not [key for key in bucket.objects if str(room) in key]
    assert _rows(client, room) == 0


def test_a_conversation_whose_row_is_gone_keeps_nothing(client, tmp_path, bucket):
    """Once a deleted channel's cleanup removes its row, nothing of it stays,
    whatever was not yet swept."""
    session, _work = _start(client, tmp_path, bucket)
    _expire(client, bucket, days=31)
    room = session.ref.topic_id

    async def remove() -> None:
        async with client.test_request_factory() as db:
            await db.execute(text("DELETE FROM topics WHERE id = :id"), {"id": room})
            await db.commit()

    client.portal.call(remove)
    _forget(client, bucket)

    assert not [key for key in bucket.objects if str(room) in key]
    assert _rows(client, room) == 0


def test_an_unreadable_run_holds_up_neither_expiry_nor_deletion(
    client, tmp_path, bucket
):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    first, _ = _start(client, tmp_path / "a", bucket)
    # Its object is gone, with its row still naming it.
    bucket.objects.clear()
    second, work = _start(client, tmp_path / "b", bucket)
    _delete_room(client, first.ref.topic_id)

    later = datetime.now(UTC) + timedelta(days=31)
    _call(
        client,
        lambda: retention.expire(client.test_request_factory, bucket, now=later),
    )
    _forget(client, bucket)

    assert _rows(client, first.ref.topic_id) == 0
    kept = second.read(turn_id=work)
    assert [item["record"]["message"]["content"] for item in kept] == [ASKED]


def test_a_run_whose_object_cannot_be_read_holds_up_no_other(
    client, tmp_path, monkeypatch, bucket
):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    _first, _ = _start(client, tmp_path / "a", bucket)
    broken = next(iter(bucket.objects))
    second, work = _start(client, tmp_path / "b", bucket)
    read = bucket.download

    async def flaky(key: str) -> bytes | None:
        if key == broken:
            raise ConnectionError("the bucket is away")
        return await read(key)

    monkeypatch.setattr(bucket, "download", flaky)
    _expire(client, bucket, days=31)

    kept = second.read(turn_id=work)
    assert [item["record"]["message"]["content"] for item in kept] == [ASKED]


def test_inputs_are_kept_when_the_old_object_will_not_go(
    client, tmp_path, monkeypatch, bucket
):
    session, work = _start(client, tmp_path, bucket)

    async def refuse(key: str) -> bool:
        raise ConnectionError("the bucket is away")

    monkeypatch.setattr(bucket, "delete", refuse)
    _expire(client, bucket, days=31)

    kept = session.read(turn_id=work)
    assert [item["record"]["message"]["content"] for item in kept] == [ASKED]
    assert [
        e["record"]["message"]["content"]
        for e in _archived(bucket, session.ref.topic_id)
    ] == [ASKED]


def test_an_object_expiry_could_not_delete_goes_with_its_conversation(
    client, tmp_path, monkeypatch, bucket
):
    session, _work = _start(client, tmp_path, bucket)
    delete = bucket.delete

    async def refuse(key: str) -> bool:
        raise ConnectionError("the bucket is away")

    monkeypatch.setattr(bucket, "delete", refuse)
    _expire(client, bucket, days=31)
    monkeypatch.setattr(bucket, "delete", delete)
    room = session.ref.topic_id
    assert [key for key in bucket.objects if key.startswith(f"{service.PREFIX}/")]

    _delete_room(client, room)
    _forget(client, bucket)

    assert not [key for key in bucket.objects if str(room) in key]


def test_an_object_expiry_could_not_delete_does_not_outlive_the_retention(
    client, tmp_path, monkeypatch, bucket
):
    """The conversation goes on; the transcript past thirty days must still go."""
    session, work = _start(client, tmp_path, bucket)
    sealed = set(bucket.objects)
    delete = bucket.delete

    async def refuse(key: str) -> bool:
        raise ConnectionError("the bucket is away")

    monkeypatch.setattr(bucket, "delete", refuse)
    _expire(client, bucket, days=31)
    monkeypatch.setattr(bucket, "delete", delete)
    assert sealed & set(bucket.objects)

    later = datetime.now(UTC) + timedelta(days=62)
    _call(
        client,
        lambda: retention.forget_orphans(
            client.test_request_factory, bucket, now=later
        ),
    )

    assert not sealed & set(bucket.objects)
    kept = session.read(turn_id=work)
    assert [item["record"]["message"]["content"] for item in kept] == [ASKED]


def test_forgetting_one_conversation_leaves_every_other_alone(client, tmp_path, bucket):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    gone, _ = _start(client, tmp_path / "a", bucket)
    kept, work = _start(client, tmp_path / "b", bucket)
    _expire(client, bucket, days=31)
    # The kept conversation's newer turn, still whole, beside its archive.
    kept.write(_turn(str(uuid.uuid4())))
    kept.drain()
    _seal(client, bucket)
    theirs = {key for key in bucket.objects if str(kept.ref.topic_id) in key}
    assert len(theirs) == 2

    _delete_room(client, gone.ref.topic_id)
    _forget(client, bucket)

    assert theirs <= set(bucket.objects)
    assert [
        item["record"]["message"]["content"] for item in kept.read(turn_id=work)
    ] == [ASKED]


def test_the_orphan_sweep_takes_only_old_objects_nothing_names(
    client, tmp_path, monkeypatch, bucket
):
    session, _work = _start(client, tmp_path, bucket)
    named = set(bucket.objects)
    room = session.ref.topic_id
    young = f"{service.PREFIX}/{room}/young-and-uncommitted.jsonl.gz"
    old = f"{service.PREFIX}/{room}/old-and-unnamed.jsonl.gz"
    archive = f"{retention.ARCHIVE_PREFIX}/{room}/kept-by-design.jsonl.gz"
    for key in (young, old, archive):
        bucket.objects[key] = b""
    now = datetime.now(UTC)
    ages = {
        young: now,
        old: now - timedelta(days=40),
        archive: now - timedelta(days=40),
    }

    async def listed(prefix: str):
        yield [
            (key, ages.get(key, now - timedelta(days=40)))
            for key in bucket.objects
            if key.startswith(prefix)
        ]

    monkeypatch.setattr(bucket, "listed", listed)
    _call(
        client,
        lambda: retention.forget_orphans(client.test_request_factory, bucket, now=now),
    )

    # The sealed run's own object is old too, but its row still names it.
    assert named | {young, archive} == set(bucket.objects)


def test_a_channel_deleted_while_its_last_records_are_sealed_leaves_no_object(
    client, tmp_path, monkeypatch
):
    """The sealer runs on its own clock; the deletion must not lose an object
    it writes in the middle."""
    bucket = _SealsOnFirstDelete(client)
    monkeypatch.setattr(service, "private_storage", lambda: bucket)
    session, _work = _start(client, tmp_path, bucket)
    # The channel's last turn, not yet sealed when it is deleted.
    session.write(_turn(str(uuid.uuid4())))
    session.drain()
    room = session.ref.topic_id
    _delete_room(client, room)
    _forget(client, bucket)
    _forget(client, bucket)

    assert not [key for key in bucket.objects if str(room) in key]
    assert _rows(client, room) == 0


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
    _call(client, lambda: retention.sweep(client.test_request_factory))
    assert _rows(client, room) == 0


class _SealsOnFirstDelete(_Bucket):
    """A bucket in the middle of whose first delete the sealer runs."""

    def __init__(self, client) -> None:
        super().__init__()
        self.client = client
        self.sealed = False

    async def delete(self, key: str) -> bool:
        if not self.sealed:
            self.sealed = True
            later = datetime.now(UTC) + service.SEAL_AFTER * 2
            await service.seal(self.client.test_request_factory, self, now=later)
        return await super().delete(key)


def _delete_room(client, room: uuid.UUID) -> None:
    owner = {"X-Cheese-Token": "", **session_auth_headers("user-1")}
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
