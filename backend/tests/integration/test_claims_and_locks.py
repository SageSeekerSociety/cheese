"""谁在碰哪些路径，谁在整块覆盖什么。

Threads batched onto one tree write to one working directory, and two of them
overwriting the same file loses one of the writes with nothing reporting it.
DeepSeek Harness has the same shape (children inherit the parent's cwd — its
`SubagentStartRequest` has no cwd field at all) and offers nothing against it:
its answer is that the coordinator arranges non-overlapping work.

These tests are about the two things added on top of that answer — a claim that
can be checked, and a lock for the one write that cannot defend itself.
"""

import uuid

from app.domain.project.models import Project
from app.domain.room_task.models import LockKind, WorkTree
from app.domain.room_task.services import ClaimService, RoomLockService, TaskService
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
            s.add(WorkTree(id=room.id, project_id=project.id, room_id=room.id))
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
        agent_instance_id=None,
    )


def test_the_same_file_claimed_twice_is_refused_and_names_the_other_work(client):
    """两条活整块覆盖同一个文件，后写的会无声盖掉先写的——这是唯一没有别的防线
    的情况，所以是拒绝，不是警告。"""
    project_id, room_id = _room(client)
    seen: dict = {}

    async def _run() -> None:
        async with client.test_factory() as s:
            svc = ClaimService(s)
            first = await _thread(
                s, project_id=project_id, room_id=room_id, title="先来的活"
            )
            await svc.claim(first, ["backend/app/domain/review/services.py"])
            second = await _thread(
                s, project_id=project_id, room_id=room_id, title="后来的活"
            )
            refusals, warnings = await svc.claim(
                second, ["backend/app/domain/review/services.py"]
            )
            await s.commit()
            seen["refusals"] = refusals
            seen["warnings"] = warnings
            seen["second_claims"] = list(second.claimed_paths or [])

    client.portal.call(_run)

    assert len(seen["refusals"]) == 1
    assert "先来的活" in seen["refusals"][0], "拒绝要说清是跟谁撞了，否则没法处理"
    assert "services.py" in seen["refusals"][0]
    assert seen["second_claims"] == [], "被拒绝的声明不该被记下来"


def test_overlapping_directories_only_warn(client):
    """两条活都在 backend/app/domain/review/ 底下干活是常态。拒绝它，规则就会
    被绕过去。"""
    project_id, room_id = _room(client)
    seen: dict = {}

    async def _run() -> None:
        async with client.test_factory() as s:
            svc = ClaimService(s)
            first = await _thread(s, project_id=project_id, room_id=room_id, title="甲")
            await svc.claim(first, ["backend/app/domain/review/"])
            second = await _thread(
                s, project_id=project_id, room_id=room_id, title="乙"
            )
            refusals, warnings = await svc.claim(
                second, ["backend/app/domain/review/notes.py"]
            )
            await s.commit()
            seen["refusals"] = refusals
            seen["warnings"] = warnings
            seen["claims"] = list(second.claimed_paths or [])

    client.portal.call(_run)

    assert seen["refusals"] == []
    assert len(seen["warnings"]) == 1
    assert "甲" in seen["warnings"][0]
    assert seen["claims"] == ["backend/app/domain/review/notes.py"], "警告不阻止声明"


def test_a_claim_grows_rather_than_replacing(client):
    """活干着干着会碰到没预料到的文件——所以是追加。替换会把已经说好的地方
    悄悄让出去。"""
    project_id, room_id = _room(client)
    seen: dict = {}

    async def _run() -> None:
        async with client.test_factory() as s:
            svc = ClaimService(s)
            task = await _thread(s, project_id=project_id, room_id=room_id, title="活")
            await svc.claim(task, ["a.py"])
            await svc.claim(task, ["b.py", "a.py"])  # 重复的不重复记
            await s.commit()
            seen["claims"] = list(task.claimed_paths or [])

    client.portal.call(_run)

    assert seen["claims"] == ["a.py", "b.py"]


def test_what_was_touched_but_never_claimed_is_reported(client):
    """声明是意图，快照是事实。没有人比对过的声明就是装饰。"""
    project_id, room_id = _room(client)
    seen: dict = {}

    async def _run() -> None:
        async with client.test_factory() as s:
            svc = ClaimService(s)
            task = await _thread(s, project_id=project_id, room_id=room_id, title="活")
            await svc.claim(task, ["backend/app/domain/review/"])
            await s.commit()
            seen["surprises"] = await svc.unclaimed(
                task,
                [
                    "backend/app/domain/review/notes.py",  # 在声明范围内
                    "frontend/src/App.vue",  # 不在
                ],
            )

    client.portal.call(_run)

    assert seen["surprises"] == ["frontend/src/App.vue"]


def test_a_file_lock_says_who_has_it_instead_of_waiting(client):
    project_id, room_id = _room(client)
    seen: dict = {}

    async def _run() -> None:
        async with client.test_factory() as s:
            locks = RoomLockService(s)
            holder = await _thread(
                s, project_id=project_id, room_id=room_id, title="正在覆盖的活"
            )
            other = await _thread(
                s, project_id=project_id, room_id=room_id, title="也想写的活"
            )
            await s.commit()
            seen["first"] = await locks.acquire(
                room_id=room_id,
                kind=LockKind.file,
                resource="a.py",
                holder_task_id=holder.id,
            )
            seen["second"] = await locks.acquire(
                room_id=room_id,
                kind=LockKind.file,
                resource="a.py",
                holder_task_id=other.id,
            )
            seen["elsewhere"] = await locks.acquire(
                room_id=room_id,
                kind=LockKind.file,
                resource="b.py",
                holder_task_id=other.id,
            )
            await s.commit()

    client.portal.call(_run)

    assert seen["first"][0] is True
    assert seen["second"][0] is False
    assert "正在覆盖的活" in seen["second"][1], "占不到的时候要说是谁占着"
    assert seen["elsewhere"][0] is True, "锁的是那个文件，不是整个房间"


def test_the_room_itself_is_not_exempt_from_the_lock(client):
    """房间和它的支线写同一棵树，所以它一样会盖掉别人的文件。"""
    project_id, room_id = _room(client)
    seen: dict = {}

    async def _run() -> None:
        async with client.test_factory() as s:
            locks = RoomLockService(s)
            thread = await _thread(
                s, project_id=project_id, room_id=room_id, title="支线"
            )
            await s.commit()
            seen["room_takes_it"] = await locks.acquire(
                room_id=room_id,
                kind=LockKind.file,
                resource="a.py",
                holder_task_id=None,  # the room's own line
            )
            seen["thread_blocked"] = await locks.acquire(
                room_id=room_id,
                kind=LockKind.file,
                resource="a.py",
                holder_task_id=thread.id,
            )
            await s.commit()

    client.portal.call(_run)

    assert seen["room_takes_it"][0] is True
    assert seen["thread_blocked"][0] is False
    assert "房间自己" in seen["thread_blocked"][1]


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

            original = m.FILE_LOCK_TTL
            svc_mod.FILE_LOCK_TTL = m.timedelta(seconds=-1)
            try:
                await locks.acquire(
                    room_id=room_id,
                    kind=LockKind.file,
                    resource="a.py",
                    holder_task_id=dead.id,
                )
            finally:
                svc_mod.FILE_LOCK_TTL = original
            seen["taken_over"] = await locks.acquire(
                room_id=room_id,
                kind=LockKind.file,
                resource="a.py",
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
