"""封口，和快检说了什么。

一棵树 = 一个分支 = 一个 PR = 一批活. Filing a card is the moment a tree's
content stops being work in progress and starts being what CI is checking, so
that is when it seals — and the next batch starts on a fresh tree, which is the
whole reason a room may hold more than one. Before it could, a PR in flight
froze the room for as long as CI took.

The quick check gates nothing (#296 settled that the PR's real CI decides). It
is here so a red one is in front of the person about to accept.
"""

import uuid

from app.domain.project.models import Project
from app.domain.room_task.models import TreeStatus, WorkTree
from app.domain.room_task.services import TaskService, WorkTreeService
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


def test_a_sealed_tree_does_not_take_new_work_and_the_next_one_does(client):
    """封口只封那一棵树，不封房间——房间照常接活，接到新的一棵上。"""
    project_id, room_id = _room(client)
    seen: dict = {}

    async def _run() -> None:
        async with client.test_factory() as s:
            trees = WorkTreeService(s)
            first = await trees.ensure_open(project_id=project_id, room_id=room_id)
            early = await TaskService(s).open_thread(
                project_id=project_id,
                room_id=room_id,
                title="第一批的活",
                owner_handle="alice",
                created_by="alice",
                agent_instance_id=None,
            )
            await trees.seal(first)
            await s.commit()

            later = await TaskService(s).open_thread(
                project_id=project_id,
                room_id=room_id,
                title="封口之后派的活",
                owner_handle="alice",
                created_by="alice",
                agent_instance_id=None,
            )
            await s.commit()
            seen["first_tree"] = first.id
            seen["first_status"] = first.status
            seen["early_tree"] = early.tree_id
            seen["later_tree"] = later.tree_id

    client.portal.call(_run)

    assert seen["first_status"] is TreeStatus.sealed
    assert seen["early_tree"] == seen["first_tree"]
    assert seen["later_tree"] != seen["first_tree"], (
        "封口之后派的活必须落在新的一棵树上，否则它会改到正在被 CI 检查的内容"
    )


def test_a_sealed_tree_keeps_the_batch_that_produced_it(client):
    """`merged` 之后树也不删——做出它的那些活还指着它，一条活的树没了就说不清
    自己的改动去哪了。"""
    project_id, room_id = _room(client)
    seen: dict = {}

    async def _run() -> None:
        async with client.test_factory() as s:
            trees = WorkTreeService(s)
            tree = await trees.ensure_open(project_id=project_id, room_id=room_id)
            task = await TaskService(s).open_thread(
                project_id=project_id,
                room_id=room_id,
                title="活",
                owner_handle="alice",
                created_by="alice",
                agent_instance_id=None,
            )
            await trees.seal(tree)
            await trees.mark_merged(tree)
            await s.commit()
            seen["status"] = tree.status
            seen["still_there"] = await trees.get(task.tree_id) is not None
            seen["batch"] = [t.title for t in await trees.tasks_on(tree.id)]

    client.portal.call(_run)

    assert seen["status"] is TreeStatus.merged
    assert seen["still_there"] is True
    assert seen["batch"] == ["活"]


def test_many_tasks_share_one_tree(client):
    """一个 PR 有多个 task 是常态，不是边界情况。"""
    project_id, room_id = _room(client)
    seen: dict = {}

    async def _run() -> None:
        async with client.test_factory() as s:
            svc = TaskService(s)
            made = []
            for i in range(3):
                made.append(
                    await svc.open_thread(
                        project_id=project_id,
                        room_id=room_id,
                        title=f"活 {i}",
                        owner_handle="alice",
                        created_by="alice",
                        agent_instance_id=None,
                    )
                )
            await s.commit()
            seen["trees"] = {t.tree_id for t in made}

    client.portal.call(_run)

    assert len(seen["trees"]) == 1


def test_the_quick_check_result_is_remembered_on_the_tree(client):
    project_id, room_id = _room(client)
    seen: dict = {}

    async def _run() -> None:
        async with client.test_factory() as s:
            trees = WorkTreeService(s)
            tree = await trees.ensure_open(project_id=project_id, room_id=room_id)
            await trees.record_check(tree, ok=False, detail="超时\n最后几行输出")
            await s.commit()
            fresh = await trees.get(tree.id)
            seen["ok"] = fresh.last_check_ok
            seen["detail"] = fresh.last_check_detail
            seen["at"] = fresh.last_check_at

    client.portal.call(_run)

    assert seen["ok"] is False
    assert "超时" in seen["detail"]
    assert seen["at"] is not None


def test_a_red_quick_check_does_not_stop_anything(client):
    """它不是闸门。红了照样能派活、照样能继续——决定权在 PR 上的真 CI。"""
    project_id, room_id = _room(client)
    seen: dict = {}

    async def _run() -> None:
        async with client.test_factory() as s:
            trees = WorkTreeService(s)
            tree = await trees.ensure_open(project_id=project_id, room_id=room_id)
            await trees.record_check(tree, ok=False, detail="失败(exit 1)")
            await s.commit()
            task = await TaskService(s).open_thread(
                project_id=project_id,
                room_id=room_id,
                title="红着也照样派的活",
                owner_handle="alice",
                created_by="alice",
                agent_instance_id=None,
            )
            await s.commit()
            seen["dispatched_onto"] = task.tree_id
            seen["tree"] = tree.id

    client.portal.call(_run)

    assert seen["dispatched_onto"] == seen["tree"]
