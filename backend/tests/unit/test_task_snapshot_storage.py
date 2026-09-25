import hashlib
import io

import pytest

from app.core.errors import ValidationError
from app.core.storage import LocalStorageBackend
from app.domain.project.models import Project
from app.domain.room_task import snapshots
from app.domain.room_task.models import Task
from app.domain.topic.models import Topic
from tests.integration.conftest import a_team

pytestmark = pytest.mark.anyio


async def test_snapshot_survives_new_session_and_detects_corruption(
    db_factory, tmp_path
):
    storage = LocalStorageBackend(str(tmp_path), "unused-private-url")
    content = b"# v2 git bundle\n" + b"example bundle bytes"
    digest = hashlib.sha256(content).hexdigest()
    async with db_factory() as session:
        project = Project(team_id=await a_team(session), name="Snapshot test")
        session.add(project)
        await session.flush()
        room = Topic(project_id=project.id, title="Room")
        session.add(room)
        await session.flush()
        task = Task(project_id=project.id, room_id=room.id, title="Report")
        session.add(task)
        await session.flush()
        first = await snapshots.save(
            session,
            task,
            file=io.BytesIO(content),
            head_sha="a" * 40,
            snapshot_sha="b" * 40,
            digest=digest,
            storage=storage,
        )
        await session.commit()
        task_id, snapshot_id = task.id, first.id
        again = await snapshots.save(
            session,
            task,
            file=io.BytesIO(content),
            head_sha="a" * 40,
            snapshot_sha="b" * 40,
            digest=digest,
            storage=storage,
        )
        assert again.id == snapshot_id
    async with db_factory() as session:
        latest = await snapshots.latest(session, task_id)
        assert latest.id == snapshot_id
        assert await snapshots.download(latest, storage=storage) == content
        (tmp_path / latest.storage_key).write_bytes(b"damaged")
        with pytest.raises(ValidationError, match="校验失败"):
            await snapshots.download(latest, storage=storage)


async def test_invalid_upload_is_rejected_before_storage(db_factory, tmp_path):
    storage = LocalStorageBackend(str(tmp_path), "unused-private-url")
    async with db_factory() as session:
        with pytest.raises(ValidationError, match="校验失败"):
            await snapshots.save(
                session,
                None,
                file=io.BytesIO(b"damaged"),
                head_sha="a" * 40,
                snapshot_sha="b" * 40,
                digest="0" * 64,
                storage=storage,
            )
    assert list(tmp_path.iterdir()) == []
