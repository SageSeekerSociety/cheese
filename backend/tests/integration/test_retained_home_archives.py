"""Home archives written while idle sandboxes were archived are kept for a
while, then deleted.

Each conversation that has one is told once until when they are kept, an
operator can list and fetch one until then, and past that date the object
leaves the private bucket and nothing is left of it.
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

import scripts.retained_files as retained_files
from app.domain.block.models import Block
from app.domain.machine import retained_archives
from app.domain.machine.models import RetainedHomeArchive
from tests.integration.conftest import post_project, session_auth_headers
from tests.integration.test_kept_room_files import bucket as bucket


def _room(client) -> tuple[uuid.UUID, uuid.UUID]:
    project = post_project(client, json={"name": "Kept"}, owner="alice").json()["data"]
    room = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "Old work"},
        headers=session_auth_headers("alice"),
    ).json()["data"]
    return uuid.UUID(project["id"]), uuid.UUID(room["id"])


def _sweep(client) -> dict:
    async def go():
        async with client.test_request_factory() as db:
            return await retained_archives.sweep(db)

    return client.portal.call(go)


def _said(client, room) -> list[str]:
    async def read():
        async with client.test_request_factory() as db:
            return list(
                await db.scalars(
                    select(Block.content).where(
                        Block.conversation_id == room,
                        Block.meta["event_type"].as_string() == "sandbox_archives_kept",
                    )
                )
            )

    return client.portal.call(read)


def test_a_kept_archive_is_announced_once_fetched_until_its_date_then_deleted(
    client, monkeypatch, bucket, tmp_path, capsys
):
    project, room = _room(client)
    keys = [f"sandbox-archives/{project}/{uuid.uuid4()}/a.tar.gz" for _ in range(2)]
    for key in keys:
        bucket.objects[key] = b"archived home " + key.encode()
    monkeypatch.setattr(retained_archives, "private_storage", lambda: bucket.storage)
    monkeypatch.setattr(
        retained_files, "async_session_factory", client.test_request_factory
    )
    until = datetime(2026, 11, 7, 3, tzinfo=UTC)

    async def seed():
        async with client.test_request_factory() as db:
            for key in keys:
                db.add(
                    RetainedHomeArchive(
                        key=key,
                        project_id=project,
                        conversation_id=room,
                        session_id=uuid.uuid4(),
                        delete_after=until,
                    )
                )
            await db.commit()

    client.portal.call(seed)

    assert _sweep(client) == {"told": 1, "expired": 0}
    [line] = _said(client, room)
    assert "2026-11-07" in line
    assert "机器" not in line
    # Told once, however many archives and sweeps.
    assert _sweep(client) == {"told": 0, "expired": 0}
    assert len(_said(client, room)) == 1
    assert set(bucket.objects) == set(keys)

    # An operator lists the room's archives and fetches one by its key.
    assert client.portal.call(retained_files.list_homes, room, None) == 0
    listed = capsys.readouterr().out
    assert all(key in listed for key in keys) and "2026-11-07" in listed
    out = tmp_path / "home.tar.gz"
    assert client.portal.call(retained_files.download_home, keys[0], out) == 0
    assert out.read_bytes() == bucket.objects[keys[0]]
    assert client.portal.call(retained_files.download_home, "nope", out) == 1

    async def date_passes():
        async with client.test_request_factory() as db:
            for archive in await db.scalars(select(RetainedHomeArchive)):
                archive.delete_after = datetime.now(UTC) - timedelta(minutes=1)
            await db.commit()

    client.portal.call(date_passes)
    assert _sweep(client) == {"told": 0, "expired": 2}
    assert bucket.objects == {}

    async def left():
        async with client.test_request_factory() as db:
            return list(await db.scalars(select(RetainedHomeArchive.key)))

    assert client.portal.call(left) == []
