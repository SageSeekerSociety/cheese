"""项目设置: 各写各的键时，谁都不该把对方写的键写回去.

``Project.settings`` is one JSON column and a dozen call sites each own a key or
two in it. A writer that reads the blob, changes its own key and assigns the
whole column back puts its stale copy over whatever another writer committed in
between — a silent lost update, no error and no log. ``merge_settings`` locks the
project row and re-reads the blob under that lock, so what it merges into is what
the database has now rather than what this writer saw when it started.

What it writes is a partial change: the keys a caller names are the only ones it
moves, and the ones it does not name are left as the database has them.
"""

import asyncio
import uuid

import pytest
from sqlalchemy import select

from app.domain.project.models import Project
from app.domain.project.repositories import ProjectRepository
from app.domain.room_task import naming
from tests.integration.conftest import new_project, session_auth_headers

pytestmark = pytest.mark.anyio


async def _stored(client, pid: uuid.UUID) -> dict:
    async with client.test_factory() as session:  # type: ignore[attr-defined]
        return await session.scalar(select(Project.settings).where(Project.id == pid))


async def _merge(client, pid: uuid.UUID, patch: dict, **kwargs) -> None:
    async with client.test_factory() as session:  # type: ignore[attr-defined]
        repo = ProjectRepository(session)
        row = await repo.get(pid)
        assert row is not None
        await repo.merge_settings(row, patch, **kwargs)
        await session.commit()


async def _named(client, name: str = "并发设置") -> uuid.UUID:
    """A project and a client signed in as its owner, who may manage it."""
    project = new_project(client, name=name, owner="alice")
    client.headers.update(session_auth_headers("alice"))
    return uuid.UUID(project["id"])


async def test_a_writer_that_read_before_the_other_committed_keeps_both_keys(client):
    """The lost update itself: the second writer holds a copy read before the
    first landed, and means to change one key of its own.

    Without the row lock its flush writes that stale copy back and the first
    writer's key is gone. With it, the merge starts from the row as the first
    writer left it.
    """
    pid = await _named(client)

    async with (
        client.test_factory() as first,  # type: ignore[attr-defined]
        client.test_factory() as second,  # type: ignore[attr-defined]
    ):
        repo_a = ProjectRepository(first)
        repo_b = ProjectRepository(second)
        row_a = await repo_a.get(pid)
        row_b = await repo_b.get(pid)  # read before either write landed
        assert row_a is not None and row_b is not None

        await repo_a.merge_settings(row_a, {"alpha": 1})
        await first.commit()

        await repo_b.merge_settings(row_b, {"beta": 2})
        await second.commit()

    stored = await _stored(client, pid)

    assert stored["alpha"] == 1
    assert stored["beta"] == 2


async def test_two_writers_landing_together_keep_both_keys(client):
    """The same thing with no interleaving imposed by the test: whichever order
    the two take the lock in, each merges into what the other left behind."""
    pid = await _named(client)

    async def write(key: str, value: object) -> None:
        await _merge(client, pid, {key: value})

    await asyncio.gather(write("alpha", 1), write("beta", 2))

    stored = await _stored(client, pid)

    assert stored["alpha"] == 1
    assert stored["beta"] == 2


async def test_a_merge_leaves_the_keys_it_does_not_name(client):
    pid = await _named(client)

    await _merge(client, pid, {"alpha": 1, "beta": 2})
    await _merge(client, pid, {"alpha": 3})
    await _merge(client, pid, {}, remove=("beta",))

    stored = await _stored(client, pid)

    assert stored["alpha"] == 3
    assert "beta" not in stored


async def test_two_routes_each_write_their_own_key(client):
    """Route level: task naming and forge attribution are separate settings, and
    saving one of them is not a reason for the other to change."""
    pid = await _named(client)

    named = client.put(f"/projects/{pid}/task-naming", json={"mode": "manual"})
    assert named.status_code == 200, named.text
    attributed = client.put(
        f"/projects/{pid}/forge-attribution", json={"requester_coauthor": False}
    )
    assert attributed.status_code == 200, attributed.text

    stored = await _stored(client, pid)

    assert stored[naming.SETTINGS_KEY] == "manual"
    assert stored["forge_requester_coauthor"] is False


async def test_a_key_written_outside_the_routes_survives_a_save(client):
    """The shell a topic declared is not collateral damage of the next settings
    save — it is a key no route in this test names."""
    pid = await _named(client)

    await _merge(client, pid, {"shell": "course-teacher"})
    saved = client.put(f"/projects/{pid}/task-naming", json={"mode": "manual"})
    assert saved.status_code == 200, saved.text

    stored = await _stored(client, pid)

    assert stored["shell"] == "course-teacher"
    assert stored[naming.SETTINGS_KEY] == "manual"
