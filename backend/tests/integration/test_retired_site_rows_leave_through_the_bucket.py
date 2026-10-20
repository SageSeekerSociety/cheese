"""The old 现场 rows leave `blocks` only once the private bucket holds them.

Agent traces are kept for good, so a retired row is deleted only after the
bucket has returned exactly the bytes sent for it; any failure leaves the rows
where they were. Nothing deletes an archive object. Hidden notices that are
still written stay. A failure is alerted once, not on every retry. Someone who
had read a conversation sees what is said there next as unread, whatever the
archive took out of it.
"""

import gzip
import json
import logging
import uuid

import pytest
from sqlalchemy import select, text

from app.domain.agent.mentions import announce_mentions
from app.domain.block import editing
from app.domain.block.models import BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.event_author import AuthorType
from app.domain.session_record import retired_site
from app.domain.session_record.models import SiteEventArchive
from app.domain.topic.repositories import TopicRepository
from app.domain.topic.services import TopicService
from tests.integration.conftest import project_service, registered
from tests.integration.test_a_turns_raw_record_keeps_what_it_did import _Bucket
from tests.integration.test_retired_site_rows_are_only_the_removed_kinds import (
    RETIRED,
)

pytestmark = pytest.mark.anyio


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class _KeepsBucket(_Bucket):
    """A bucket whose objects nothing may delete."""

    async def delete(self, key: str) -> bool:
        raise AssertionError(f"an archive object was deleted: {key}")


async def _room(session, title: str):
    project = await project_service(session).create(name=title, owner_handle="alice")
    room = await TopicService(session).create(
        project_id=project.id, title=title, created_by="alice"
    )
    return project, room


async def _seed(factory, *, rooms: int = 2, copies: int = 2) -> dict:
    """Retired rows in several rooms, and the rows that must stay beside them."""
    retired: dict[uuid.UUID, dict] = {}
    kept: set[uuid.UUID] = set()
    async with factory() as session:
        await registered(session, "alice")
        for n in range(rooms):
            project, room = await _room(session, f"Retired {n}")
            blocks = BlockRepository(session)
            for _ in range(copies):
                for meta in RETIRED:
                    row = await blocks.add(
                        project_id=project.id,
                        conversation_id=room.id,
                        author="cheese",
                        author_type=AuthorType.participant,
                        content="retired",
                        kind=BlockKind.event,
                        meta=meta,
                    )
                    retired[row.id] = {"conversation": room.id, "meta": meta}
            said = await blocks.add(
                project_id=project.id,
                conversation_id=room.id,
                author="alice",
                author_type=AuthorType.participant,
                content="first words",
            )
            kept.add(said.id)
            notice = await editing._tell_the_room(blocks, said, "alice")
            kept.add(notice.id)
            agent_said = await blocks.add(
                project_id=project.id,
                conversation_id=room.id,
                author="cheese",
                author_type=AuthorType.participant,
                content="ask <@nobody-here>",
                turn_id=uuid.uuid4(),
            )
            kept.add(agent_said.id)
            await announce_mentions(
                session,
                room,
                agent_said,
                "cheese",
                [{"handle": "alice"}],
                flag_unresolved=True,
            )
        await session.commit()
        kept |= set(
            (
                await session.execute(
                    text("SELECT id FROM blocks WHERE id <> ALL(:retired)"),
                    {"retired": list(retired)},
                )
            ).scalars()
        )
    return {"retired": retired, "kept": kept}


async def _ids(factory) -> set[uuid.UUID]:
    async with factory() as session:
        return set((await session.execute(text("SELECT id FROM blocks"))).scalars())


def _archived(bucket: _Bucket) -> dict[uuid.UUID, dict]:
    rows = {}
    for key, body in bucket.objects.items():
        assert key.startswith(f"{retired_site.ARCHIVE_PREFIX}/")
        conversation = key.split("/")[2]
        for line in gzip.decompress(body).decode().splitlines():
            row = json.loads(line)
            assert row["conversation_id"] == conversation, key
            rows[uuid.UUID(row["id"])] = row
    return rows


def _job(factory, bucket, clock=None):
    return retired_site.RetiredSiteArchive(
        factory, bucket=lambda: bucket, clock=clock or _Clock()
    )


async def test_retired_rows_move_to_the_bucket_and_nothing_else_moves(
    business_db_factory, monkeypatch
):
    factory = business_db_factory
    seeded = await _seed(factory)
    monkeypatch.setattr(retired_site, "BATCH_ROWS", 4)
    bucket = _KeepsBucket()
    job = _job(factory, bucket)

    while (await job.run()).get("rows"):
        pass

    remaining = await _ids(factory)
    assert remaining == seeded["kept"]
    archived = _archived(bucket)
    assert set(archived) == set(seeded["retired"])
    for block_id, row in archived.items():
        written = seeded["retired"][block_id]
        assert row["conversation_id"] == str(written["conversation"])
        assert row["meta"] == written["meta"]
        assert row["kind"] == "event"
        assert row["content"] == "retired"
    async with factory() as session:
        ledger = (await session.execute(select(SiteEventArchive))).scalars().all()
    assert {entry.object_key for entry in ledger} == set(bucket.objects)
    assert sum(entry.rows for entry in ledger) == len(seeded["retired"])
    for entry in ledger:
        assert entry.bytes == len(bucket.objects[entry.object_key])
        assert str(entry.conversation_id) in entry.object_key

    # Nothing matches any more: the job does nothing at all.
    before = dict(bucket.objects)
    assert await job.run() == {}
    assert bucket.objects == before
    assert await _ids(factory) == seeded["kept"]


async def test_a_readback_that_differs_deletes_nothing_and_is_retried(
    business_db_factory, monkeypatch
):
    factory = business_db_factory
    seeded = await _seed(factory, rooms=1, copies=1)
    bucket = _KeepsBucket()
    clock = _Clock()
    job = _job(factory, bucket, clock)
    download = bucket.download

    async def truncated(key):
        return (await download(key))[:-1]

    monkeypatch.setattr(bucket, "download", truncated)
    await job.run()
    assert await _ids(factory) == seeded["kept"] | set(seeded["retired"])
    uploaded = dict(bucket.objects)
    assert uploaded, "the attempt reached the bucket"

    # Before the retry is due, the job does not touch the bucket or the rows.
    monkeypatch.setattr(bucket, "download", download)
    clock.now += retired_site.RETRY_FIRST_S - 1
    await job.run()
    assert bucket.objects == uploaded
    assert await _ids(factory) == seeded["kept"] | set(seeded["retired"])

    clock.now += 1
    await job.run()
    assert await _ids(factory) == seeded["kept"]
    # The failed attempt's object is kept too; its rows are archived again.
    assert uploaded.items() <= bucket.objects.items()
    assert set(_archived(bucket)) == set(seeded["retired"])


async def test_an_upload_that_fails_leaves_the_rows(business_db_factory, monkeypatch):
    factory = business_db_factory
    seeded = await _seed(factory, rooms=1, copies=1)
    bucket = _KeepsBucket()

    async def refused(file, key, content_type):
        raise ConnectionError("bucket unreachable")

    monkeypatch.setattr(bucket, "upload", refused)
    await _job(factory, bucket).run()

    assert await _ids(factory) == seeded["kept"] | set(seeded["retired"])
    async with factory() as session:
        assert (await session.execute(select(SiteEventArchive))).first() is None


async def test_a_failure_alerts_once_until_it_changes(
    business_db_factory, monkeypatch, caplog
):
    factory = business_db_factory
    await _seed(factory, rooms=1, copies=1)
    bucket = _KeepsBucket()
    clock = _Clock()
    job = _job(factory, bucket, clock)

    async def refused(file, key, content_type):
        raise ConnectionError("bucket unreachable")

    monkeypatch.setattr(bucket, "upload", refused)
    caplog.set_level(logging.WARNING, logger=retired_site.__name__)

    def errors() -> int:
        return sum(
            1
            for r in caplog.records
            if r.name == retired_site.__name__ and r.levelno >= logging.ERROR
        )

    for _ in range(4):
        await job.run()
        clock.now += retired_site.RETRY_MAX_S
    assert errors() == 1

    # A different failure is news again.
    async def unreadable(key):
        raise TimeoutError("bucket slow")

    upload = _Bucket.upload.__get__(bucket)
    monkeypatch.setattr(bucket, "upload", upload)
    monkeypatch.setattr(bucket, "download", unreadable)
    await job.run()
    assert errors() == 2


async def test_without_a_bucket_every_row_stays(business_db_factory):
    factory = business_db_factory
    seeded = await _seed(factory, rooms=1, copies=1)
    job = retired_site.RetiredSiteArchive(factory, bucket=lambda: None)

    assert await job.run() == {}
    assert await _ids(factory) == seeded["kept"] | set(seeded["retired"])


async def test_what_is_said_after_the_archive_is_unread_for_someone_who_had_read(
    business_db_factory,
):
    factory = business_db_factory
    async with factory() as session:
        await registered(session, "alice")
        await registered(session, "bob")
        project, room = await _room(session, "Last words were the site's")
        blocks = BlockRepository(session)
        await blocks.add(
            project_id=project.id,
            conversation_id=room.id,
            author="bob",
            author_type=AuthorType.participant,
            content="hello",
        )
        # The conversation's newest blocks are the retired site's.
        for meta in RETIRED:
            await blocks.add(
                project_id=project.id,
                conversation_id=room.id,
                author="cheese",
                author_type=AuthorType.participant,
                content="retired",
                kind=BlockKind.event,
                meta=meta,
            )
        await TopicRepository(session).mark_read(room.id, "alice")
        await session.commit()

    job = _job(factory, _KeepsBucket())
    while (await job.run()).get("rows"):
        pass

    async with factory() as session:
        await BlockRepository(session).add(
            project_id=project.id,
            conversation_id=room.id,
            author="bob",
            author_type=AuthorType.participant,
            content="are you there, <@alice>?",
        )
        await session.commit()
        unread = await TopicRepository(session).unread_counts(project.id, "alice")
    assert room.id in unread, unread
