"""一个房间最多同时开 4 条后台子代理，多的排队。

The cap counts what is RUNNING, not what exists — a thread that finished its
turn is holding nothing and comes straight back if anyone speaks to it. That
distinction is the whole design: counting open threads instead would let four
finished-but-unread ones wedge a room forever, with nothing on screen saying
why.

Functional throughout: every assertion is on what a caller gets back, or on
what a later call then does. Nothing reads the bookkeeping.
"""

import uuid

from app.domain.project.models import Project
from app.domain.room_task.models import MAX_RESIDENT_TASKS_PER_ROOM, Residency
from app.domain.room_task.repositories import TaskRepository
from app.domain.room_task.services import ResidencyService, TaskService
from app.domain.topic.models import Topic, TopicKind


def _room(client) -> tuple[uuid.UUID, uuid.UUID]:
    """A project with one room in it, returned as (project_id, room_id)."""
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


async def _dispatch(session, *, project_id, room_id, title):
    return await TaskService(session).open_thread(
        project_id=project_id,
        room_id=room_id,
        title=title,
        owner_handle="alice",
        created_by="alice",
        agent_instance_id=None,
    )


def test_the_first_four_threads_start_and_the_fifth_queues(client):
    project_id, room_id = _room(client)
    seen: dict = {}

    async def _run() -> None:
        async with client.test_factory() as s:
            svc = ResidencyService(s)
            admitted = []
            for i in range(MAX_RESIDENT_TASKS_PER_ROOM + 1):
                task = await _dispatch(
                    s, project_id=project_id, room_id=room_id, title=f"活 {i}"
                )
                admitted.append(await svc.admit(task))
            seen["admitted"] = admitted
            seen["fifth_position"] = await svc.queue_position(task)
            await s.commit()

    client.portal.call(_run)

    assert seen["admitted"][:-1] == [True] * MAX_RESIDENT_TASKS_PER_ROOM
    assert seen["admitted"][-1] is False, "第五条应该排队，不是被拒绝"
    assert seen["fifth_position"] == 1


def test_a_queued_thread_keeps_everything_it_was_dispatched_with(client):
    """排队不是拒绝：它照样有标题、有主、在这个房间里。"""
    project_id, room_id = _room(client)
    seen: dict = {}

    async def _run() -> None:
        async with client.test_factory() as s:
            svc = ResidencyService(s)
            for i in range(MAX_RESIDENT_TASKS_PER_ROOM):
                await svc.admit(
                    await _dispatch(
                        s, project_id=project_id, room_id=room_id, title=f"活 {i}"
                    )
                )
            queued = await _dispatch(
                s, project_id=project_id, room_id=room_id, title="排队的那条"
            )
            seen["admitted"] = await svc.admit(queued)
            await s.commit()
            fetched = await TaskRepository(s).get(queued.id)
            seen["title"] = fetched.title
            seen["owner"] = fetched.owner_handle
            seen["room"] = fetched.room_id

    client.portal.call(_run)

    assert seen["admitted"] is False
    assert seen["title"] == "排队的那条"
    assert seen["owner"] == "alice"
    assert seen["room"] == room_id


def test_releasing_a_slot_starts_the_longest_waiter(client):
    project_id, room_id = _room(client)
    seen: dict = {}

    async def _run() -> None:
        async with client.test_factory() as s:
            svc = ResidencyService(s)
            running = []
            for i in range(MAX_RESIDENT_TASKS_PER_ROOM):
                t = await _dispatch(
                    s, project_id=project_id, room_id=room_id, title=f"活 {i}"
                )
                await svc.admit(t)
                running.append(t)
            first = await _dispatch(
                s, project_id=project_id, room_id=room_id, title="先排的"
            )
            await svc.admit(first)
            second = await _dispatch(
                s, project_id=project_id, room_id=room_id, title="后排的"
            )
            await svc.admit(second)

            promoted = await svc.release(running[0])
            await s.commit()
            seen["promoted"] = promoted.title if promoted else None
            seen["promoted_residency"] = promoted.residency if promoted else None
            seen["second_still_queued"] = (
                await TaskRepository(s).get(second.id)
            ).queued_at is not None

    client.portal.call(_run)

    assert seen["promoted"] == "先排的", "先排的先走"
    assert seen["promoted_residency"] == Residency.running
    assert seen["second_still_queued"] is True


def test_a_thread_that_went_quiet_does_not_hold_a_slot(client):
    """The point of counting residency rather than existence: four
    finished-but-open threads must not wedge the room."""
    project_id, room_id = _room(client)
    seen: dict = {}

    async def _run() -> None:
        async with client.test_factory() as s:
            svc = ResidencyService(s)
            for i in range(MAX_RESIDENT_TASKS_PER_ROOM):
                t = await _dispatch(
                    s, project_id=project_id, room_id=room_id, title=f"活 {i}"
                )
                await svc.admit(t)
                await svc.release(t)  # each finishes its turn and goes quiet
            fresh = await _dispatch(
                s, project_id=project_id, room_id=room_id, title="新的一条"
            )
            seen["admitted"] = await svc.admit(fresh)
            await s.commit()

    client.portal.call(_run)

    assert seen["admitted"] is True, "闲着的活不占额度，房间还能接新活"


def test_a_full_room_can_say_who_is_holding_it(client):
    project_id, room_id = _room(client)
    seen: dict = {}
    titles = [f"活 {i}" for i in range(MAX_RESIDENT_TASKS_PER_ROOM)]

    async def _run() -> None:
        async with client.test_factory() as s:
            svc = ResidencyService(s)
            for title in titles:
                await svc.admit(
                    await _dispatch(
                        s, project_id=project_id, room_id=room_id, title=title
                    )
                )
            await s.commit()
            holders = await svc.holders(room_id)
            seen["titles"] = sorted(t.title for t in holders)
            seen["all_have_a_time"] = all(t.last_turn_at is not None for t in holders)

    client.portal.call(_run)

    assert seen["titles"] == sorted(titles)
    assert seen["all_have_a_time"], "每条都要带最后活动时间——否则人不知道该去动哪一条"


def test_a_turn_that_died_with_its_backend_stops_holding_the_slot(client, monkeypatch):
    """Materialised residency is what lets a slot survive a restart; the cost is
    that a crash leaves a row claiming to run. The sweep bounds that cost."""
    from app.domain.room_task import services as svc_mod

    project_id, room_id = _room(client)
    seen: dict = {}
    monkeypatch.setattr(svc_mod, "GHOST_RESIDENCY_AFTER", svc_mod.timedelta(seconds=-1))

    async def _run() -> None:
        async with client.test_factory() as s:
            svc = ResidencyService(s)
            ghost = await _dispatch(
                s, project_id=project_id, room_id=room_id, title="被杀掉的那轮"
            )
            await svc.admit(ghost)
            await s.commit()
            freed = await svc.sweep_ghosts()
            await s.commit()
            seen["freed"] = [t.title for t in freed]
            seen["residency"] = (await TaskRepository(s).get(ghost.id)).residency

    client.portal.call(_run)

    assert seen["freed"] == ["被杀掉的那轮"]
    assert seen["residency"] == Residency.idle
