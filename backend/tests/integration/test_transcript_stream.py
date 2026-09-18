"""Raw transcript delivery survives retries and refuses gaps or changed bytes."""

import asyncio
import hashlib
import uuid

import pytest

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.storage import LocalStorageBackend
from app.domain.topic import transcript_stream as stream

pytestmark = pytest.mark.anyio


async def test_raw_files_reassemble_exactly_and_retries_do_not_duplicate(
    client, tmp_path
):
    storage = LocalStorageBackend(str(tmp_path), "/unused")
    project, topic, file_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    parts = ['{"text":"你好"}\n'.encode(), b'{"tool_result":"ok"}\n']
    args = dict(
        project_id=project,
        topic_id=topic,
        file_id=file_id,
        source=".claude/projects/p/session.jsonl",
        storage=storage,
    )
    async with client.test_factory() as db:
        first = await stream.append(db, **args, offset=0, content=parts[0])
        assert await stream.append(db, **args, offset=0, content=parts[0]) == first
        await stream.append(db, **args, offset=len(parts[0]), content=parts[1])
        assert await stream.read(
            db, project_id=project, topic_id=topic, file_id=file_id, storage=storage
        ) == b"".join(parts)
        assert len([path for path in tmp_path.rglob("*") if path.is_file()]) == 3


async def test_failed_storage_is_not_acknowledged_and_can_retry(client, tmp_path):
    class FailingStorage(LocalStorageBackend):
        async def upload(self, file, key, content_type):
            raise OSError("offline")

    ids = dict(
        project_id=uuid.uuid4(),
        topic_id=uuid.uuid4(),
        file_id=uuid.uuid4(),
        source=".claude/projects/p/subagents/agent-a.jsonl",
        offset=0,
        content=b"original\n",
    )
    async with client.test_factory() as db:
        with pytest.raises(OSError, match="offline"):
            await stream.append(
                db, **ids, storage=FailingStorage(str(tmp_path), "/unused")
            )
        await db.rollback()
        assert await stream.files(db, ids["project_id"], ids["topic_id"]) == []
        receipt = await stream.append(
            db, **ids, storage=LocalStorageBackend(str(tmp_path), "/unused")
        )
        assert receipt["size"] == len(ids["content"])


async def test_gaps_changed_retries_and_path_escape_are_rejected(client, tmp_path):
    storage = LocalStorageBackend(str(tmp_path), "/unused")
    args = dict(
        project_id=uuid.uuid4(),
        topic_id=uuid.uuid4(),
        file_id=uuid.uuid4(),
        source=".claude/projects/p/s.jsonl",
        storage=storage,
    )
    async with client.test_factory() as db:
        await stream.append(db, **args, offset=0, content=b"abc\n")
        for offset, content in [(0, b"xyz\n"), (10, b"gap\n")]:
            with pytest.raises(ConflictError):
                await stream.append(db, **args, offset=offset, content=content)
            await db.rollback()
        args["source"] = ".claude/projects/../../secret.jsonl"
        with pytest.raises(ValidationError):
            await stream.append(db, **args, offset=4, content=b"bad\n")


async def test_final_confirmation_checks_objects_and_empty_files(client, tmp_path):
    storage = LocalStorageBackend(str(tmp_path), "/unused")
    args = dict(
        project_id=uuid.uuid4(),
        topic_id=uuid.uuid4(),
        file_id=uuid.uuid4(),
        source=".claude/projects/p/s.jsonl",
        storage=storage,
    )
    async with client.test_factory() as db:
        with pytest.raises(NotFoundError):
            await stream.confirm(
                db, **args, size=3, sha256=hashlib.sha256(b"abc").hexdigest()
            )
        await stream.append(db, **args, offset=0, content=b"abc")
        await stream.confirm(
            db, **args, size=3, sha256=hashlib.sha256(b"abc").hexdigest()
        )
        with pytest.raises(NotFoundError):
            await stream.confirm(
                db,
                **{**args, "source": ".claude/projects/p/other.jsonl"},
                size=0,
                sha256=hashlib.sha256().hexdigest(),
            )
        await db.rollback()
        await stream.confirm(
            db, **args, size=3, sha256=hashlib.sha256(b"abc").hexdigest()
        )
        object_path = next(
            path
            for path in tmp_path.rglob("*")
            if path.is_file() and path.name != "source.json"
        )
        object_path.write_bytes(b"xyz")
        with pytest.raises(RuntimeError, match="corrupt"):
            await stream.confirm(
                db, **args, size=3, sha256=hashlib.sha256(b"abc").hexdigest()
            )
        args["file_id"] = uuid.uuid4()
        await stream.confirm(db, **args, size=0, sha256=hashlib.sha256().hexdigest())
        assert len(await stream.files(db, args["project_id"], args["topic_id"])) == 2


async def test_http_ingress_and_readable_download_enforce_room_scope(
    client, monkeypatch, tmp_path
):
    from app.core.sandbox_auth import mint_scoped_token
    from app.domain.project.services import ProjectService
    from app.domain.topic.services import TopicService
    from tests.integration.conftest import session_auth_headers

    storage = LocalStorageBackend(str(tmp_path), "/unused")
    monkeypatch.setattr(stream, "transcript_storage", lambda: storage)
    async with client.test_factory() as db:
        project = await ProjectService(db).create(name="P", owner_handle="owner")
        first = await TopicService(db).create(
            project_id=project.id, title="First", created_by="owner"
        )
        second = await TopicService(db).create(
            project_id=project.id, title="Second", created_by="owner"
        )
        await db.commit()
        project_id, first_id, second_id = project.id, first.id, second.id
    file_id = uuid.uuid4()
    source = ".claude/projects/p/subagents/agent-a.jsonl"
    content = '{"tool_result":"原始结果"}\n'.encode()
    token = mint_scoped_token(project_id=str(project_id), topic_id=str(first_id))
    headers = {"X-Cheese-Token": token}
    for topic_id, status in ((second_id, 401), (first_id, 200)):
        result = client.put(
            f"/sandbox/transcripts/{topic_id}/{file_id}",
            params={"source": source, "offset": 0},
            content=content,
            headers=headers,
        )
        assert result.status_code == status, result.text
    endpoint = f"/topics/{first_id}/transcripts/{file_id}"
    assert (
        client.get(endpoint, headers=session_auth_headers("stranger")).status_code
        == 403
    )
    result = client.get(endpoint, headers=session_auth_headers("owner"))
    assert result.status_code == 200 and result.content == content
    assert result.headers["content-type"].startswith("text/plain")
    assert result.headers["cache-control"] == "no-store"
    assert (
        client.get(
            f"/topics/{second_id}/transcripts/{file_id}",
            headers=session_auth_headers("owner"),
        ).status_code
        == 404
    )


async def test_a_stalled_upload_holds_no_row_lock(client, tmp_path):
    """The dev outage of 2026-09-18: one upload that never returned held the
    file's row lock, and every later upload of that file queued behind it
    with a pool connection each. Now a second delivery of the same bytes
    completes while the first is still stuck in storage, and the stuck one
    lands as the same single chunk once it returns."""
    release = asyncio.Event()

    class StalledStorage(LocalStorageBackend):
        async def upload(self, file, key, content_type):
            await release.wait()
            return await super().upload(file, key, content_type)

    args = dict(
        project_id=uuid.uuid4(),
        topic_id=uuid.uuid4(),
        file_id=uuid.uuid4(),
        source=".claude/projects/p/s.jsonl",
        offset=0,
        content=b"abc\n",
    )
    async with client.test_factory() as stuck, client.test_factory() as db:
        first = asyncio.create_task(
            stream.append(stuck, **args, storage=StalledStorage(str(tmp_path), "/u"))
        )
        await asyncio.sleep(0.2)
        assert not first.done()
        receipt = await asyncio.wait_for(
            stream.append(db, **args, storage=LocalStorageBackend(str(tmp_path), "/u")),
            timeout=5,
        )
        release.set()
        assert await asyncio.wait_for(first, timeout=5) == receipt
        assert await stream.files(db, args["project_id"], args["topic_id"]) == [
            {"id": str(args["file_id"]), "source": args["source"], "size": 4}
        ]


async def test_a_transfer_that_never_returns_fails_in_bounded_time(
    client, monkeypatch, tmp_path
):
    class HangingStorage(LocalStorageBackend):
        async def upload(self, file, key, content_type):
            await asyncio.Event().wait()

    monkeypatch.setattr(stream, "TRANSFER_SECONDS", 0.2)
    async with client.test_factory() as db:
        with pytest.raises(TimeoutError):
            await stream.append(
                db,
                project_id=uuid.uuid4(),
                topic_id=uuid.uuid4(),
                file_id=uuid.uuid4(),
                source=".claude/projects/p/s.jsonl",
                offset=0,
                content=b"abc\n",
                storage=HangingStorage(str(tmp_path), "/u"),
            )
