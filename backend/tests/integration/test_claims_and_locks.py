"""Independent task worktrees still share machine capacity within a room."""

import uuid

from app.domain.project.models import Project
from app.domain.room_task.models import LockKind
from app.domain.room_task.services import RoomLockService, TaskService
from app.domain.topic.models import Topic, TopicKind


def _room(client) -> tuple[uuid.UUID, uuid.UUID]:
    made: dict[str, uuid.UUID] = {}

    async def _seed() -> None:
        async with client.test_factory() as s:
            project = Project(name="P", owner_handle="alice")
            s.add(project)
            await s.flush()
            root = Topic(project_id=project.id, title="root", kind=TopicKind.root)
            s.add(root)
            await s.flush()
            room = Topic(
                project_id=project.id,
                title="房间",
                kind=TopicKind.topic,
                parent_id=root.id,
            )
            s.add(room)
            await s.flush()
            await s.commit()
            made["project"] = project.id
            made["room"] = room.id

    client.portal.call(_seed)
    return made["project"], made["room"]


async def _thread(session, *, project_id, room_id, title):
    return await TaskService(session).open_thread(
        project_id=project_id,
        room_id=room_id,
        title=title,
        owner_handle="alice",
        created_by="alice",
    )


def test_a_lock_whose_holder_never_came_back_is_taken_back(client):
    """占着锁的进程死了就不会来还。一把没人能解的锁，比它挡住的那次覆盖更糟。"""
    from app.domain.room_task import models as m

    project_id, room_id = _room(client)
    seen: dict = {}

    async def _run() -> None:
        async with client.test_factory() as s:
            locks = RoomLockService(s)
            dead = await _thread(
                s, project_id=project_id, room_id=room_id, title="死掉的"
            )
            alive = await _thread(
                s, project_id=project_id, room_id=room_id, title="活着的"
            )
            await s.commit()
            # Its TTL already elapsed — the holder is not coming back.
            import app.domain.room_task.services as svc_mod

            original = m.HEAVY_LOCK_TTL
            svc_mod.HEAVY_LOCK_TTL = m.timedelta(seconds=-1)
            try:
                await locks.acquire(
                    room_id=room_id,
                    kind=LockKind.heavy,
                    resource="",
                    holder_task_id=dead.id,
                )
            finally:
                svc_mod.HEAVY_LOCK_TTL = original
            seen["taken_over"] = await locks.acquire(
                room_id=room_id,
                kind=LockKind.heavy,
                resource="",
                holder_task_id=alive.id,
            )
            await s.commit()

    client.portal.call(_run)

    assert seen["taken_over"][0] is True


def test_the_heavy_lane_is_one_per_room(client):
    """测试、装依赖、起 dev server 抢的是机器（端口、数据库、同一个 venv），
    不是树——所以再小心地划分文件也没用。"""
    project_id, room_id = _room(client)
    seen: dict = {}

    async def _run() -> None:
        async with client.test_factory() as s:
            locks = RoomLockService(s)
            a = await _thread(
                s, project_id=project_id, room_id=room_id, title="跑测试的"
            )
            b = await _thread(
                s, project_id=project_id, room_id=room_id, title="装依赖的"
            )
            await s.commit()
            seen["a"] = await locks.acquire(
                room_id=room_id, kind=LockKind.heavy, holder_task_id=a.id
            )
            seen["b"] = await locks.acquire(
                room_id=room_id, kind=LockKind.heavy, holder_task_id=b.id
            )
            await locks.release(
                room_id=room_id, kind=LockKind.heavy, holder_task_id=a.id
            )
            seen["b_after"] = await locks.acquire(
                room_id=room_id, kind=LockKind.heavy, holder_task_id=b.id
            )
            await s.commit()

    client.portal.call(_run)

    assert seen["a"][0] is True
    assert seen["b"][0] is False
    assert seen["b_after"][0] is True, "还回去之后，排在后面的就能跑了"
