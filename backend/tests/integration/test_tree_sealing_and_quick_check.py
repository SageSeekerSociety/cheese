"""Each task owns its code, checks and delivery; rooms keep coordinating."""

import asyncio
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

from app.domain.project import forge
from app.domain.project.models import Project
from app.domain.repository.forge_files import ProjectFiles
from app.domain.review.pr_publish import retarget_completed_dependencies
from app.domain.review.services import AcceptService
from app.domain.room_task.models import Task, TaskStatus
from app.domain.room_task.services import TaskService
from app.domain.topic.models import Topic, TopicKind
from tests.integration.test_accept_pr import app_world as app_world
from tests.machine_work import machine_commits
from tests.support import git_store


def _room(client) -> tuple[uuid.UUID, uuid.UUID]:
    made: dict[str, uuid.UUID] = {}

    async def _seed() -> None:
        async with client.test_factory() as s:
            project = Project(name="P", owner_handle="alice")
            s.add(project)
            await s.flush()
            await forge.provision_repository(project.id, s)
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


async def _task(session, project, room, title, **extra):
    task = await TaskService(session).open_thread(
        project_id=project,
        room_id=room,
        title=title,
        owner_handle="alice",
        created_by="alice",
        reviewer_handle="alice",
        **extra,
    )
    git_store.bind_task(
        task.id,
        branch=task.branch_name,
        directory=task.workspace_name,
        base=task.base_branch,
    )
    return task


def test_two_tasks_edit_the_same_path_without_sharing_commits(client):
    project, room = _room(client)

    async def run():
        async with client.test_factory() as session:
            first = await _task(session, project, room, "first")
            second = await _task(session, project, room, "second")
            await session.commit()
            return first, second

    first, second = client.portal.call(run)
    head_a = machine_commits(project, first.id, {"result.txt": "first\n"})
    head_b = machine_commits(project, second.id, {"result.txt": "second\n"})
    assert first.branch_name != second.branch_name
    assert head_a != head_b
    assert first.subagent_id is None and second.subagent_id is None

    async def check_files():
        async with client.test_factory() as session:
            a = ProjectFiles(session, project, first.id)
            b = ProjectFiles(session, project, second.id)
            assert (await a.text("result.txt", "committed"))["content"] == "first\n"
            assert (await b.text("result.txt", "committed"))["content"] == "second\n"
            assert not any(ref["hash"] == head_a for ref in await b.history())

    client.portal.call(check_files)


def test_accepting_one_task_leaves_other_tasks_and_room_active(client, app_world):
    project, room = _room(client)

    async def run():
        async with client.test_factory() as session:
            first = await _task(session, project, room, "first")
            second = await _task(session, project, room, "second")
            delivered_head = machine_commits(project, first.id, {"a.txt": "first"})
            service = AcceptService(session)
            card = await service.create_card(
                topic_id=room,
                task_id=first.id,
                reviewer_handle="alice",
                routing_reason="ready",
                change_subject="feat: add first result",
            )
            fake = app_world["fake"]
            fake.seed_pr(11, head=first.branch_name)
            fake.prs[11]["head_sha"] = delivered_head
            fake.check_state_by_sha[delivered_head] = ("success", "")
            card.pr_number, card.pr_head_sha = 11, delivered_head
            card.pr_url = "https://github.com/acme/widgets/pull/11"
            await service.accept(
                card_id=card.id, decided_by="alice", head_sha=delivered_head
            )
            await session.commit()
            assert first.status == TaskStatus.closed
            assert second.status == TaskStatus.open
            assert (await session.get(Topic, room)).status == "active"
            assert card.task_id == first.id
            assert first.delivered_head == delivered_head
            assert fake.merge_calls[0]["sha"] == delivered_head
            assert (
                git_store.git(
                    git_store.path(project), "show", f"{delivered_head}:a.txt"
                )
                == "first"
            )

    client.portal.call(run)


def test_a_dependency_uses_the_parent_branch_and_reports_only_its_own_diff(client):
    project, room = _room(client)

    async def run():
        async with client.test_factory() as session:
            parent = await _task(session, project, room, "parent")
            machine_commits(project, parent.id, {"parent.txt": "parent"})
            child = await _task(session, project, room, "child", base_task_id=parent.id)
            await session.commit()
            return parent, child

    parent, child = client.portal.call(run)
    machine_commits(project, child.id, {"child.txt": "child"})
    assert child.base_branch == parent.branch_name

    async def check_files():
        async with client.test_factory() as session:
            files = ProjectFiles(session, project, child.id)
            assert (await files.text("parent.txt", "committed"))["content"] == "parent"
            assert await files.changed_files() == ["child.txt"]

    client.portal.call(check_files)


def test_quick_checks_belong_to_the_checked_task_and_do_not_gate_work(client):
    project, room = _room(client)

    async def run():
        async with client.test_factory() as session:
            service = TaskService(session)
            first = await _task(session, project, room, "first")
            await service.record_check(first, ok=False, detail="test failure")
            second = await _task(session, project, room, "second")
            assert first.last_check_ok is False
            assert first.last_check_detail == "test failure"
            assert second.last_check_at is None
            assert first.status == second.status == TaskStatus.open
            await session.commit()

    client.portal.call(run)


def test_accepting_parent_retargets_child_pr_and_keeps_its_work(client, monkeypatch):
    project, room = _room(client)
    github = AsyncMock()
    monkeypatch.setattr(AcceptService, "_app_pr_client", AsyncMock(return_value=github))

    async def seed():
        async with client.test_factory() as session:
            parent = await _task(session, project, room, "parent")
            machine_commits(project, parent.id, {"parent.txt": "parent"})
            child = await _task(session, project, room, "child", base_task_id=parent.id)
            machine_commits(project, child.id, {"child.txt": "child"})
            child.pr_number = 123
            parent.status = TaskStatus.closed
            parent.accepted_at = datetime.now(UTC)
            await session.commit()
            return parent, child

    parent, child = client.portal.call(seed)
    original = git_store.head(project, child.branch_name)
    client.portal.call(retarget_completed_dependencies, client.test_request_factory)
    github.update_pr.assert_awaited_once_with(123, base=parent.base_branch)

    async def check():
        async with client.test_factory() as session:
            saved = await session.get(Task, child.id)
            assert saved.base_branch == parent.base_branch
            assert saved.base_task_id == parent.id
            assert saved.status == TaskStatus.open
            files = ProjectFiles(session, project, child.id)
            assert (await files.text("child.txt", "committed"))["content"] == "child"

    client.portal.call(check)
    assert git_store.head(project, child.branch_name) == original
    client.portal.call(retarget_completed_dependencies, client.test_request_factory)
    assert github.update_pr.await_count == 1


def test_retargeting_holds_no_task_lock_while_github_is_called(client, monkeypatch):
    """While GitHub is being asked to move the PR's base, the task row stays
    free for other writers, and a task that closes meanwhile is left alone."""
    from sqlalchemy import text

    project, room = _room(client)
    github = AsyncMock()
    in_flight = asyncio.Event()
    release = asyncio.Event()

    async def slow_update_pr(number, **kwargs):
        in_flight.set()
        await release.wait()

    github.update_pr = slow_update_pr
    monkeypatch.setattr(AcceptService, "_app_pr_client", AsyncMock(return_value=github))

    async def seed():
        async with client.test_factory() as session:
            parent = await _task(session, project, room, "parent")
            machine_commits(project, parent.id, {"parent.txt": "parent"})
            child = await _task(session, project, room, "child", base_task_id=parent.id)
            machine_commits(project, child.id, {"child.txt": "child"})
            child.pr_number = 123
            parent.status = TaskStatus.closed
            parent.accepted_at = datetime.now(UTC)
            await session.commit()
            return child.id, child.base_branch

    child_id, old_base = client.portal.call(seed)

    async def race():
        sweep = asyncio.create_task(
            retarget_completed_dependencies(client.test_request_factory)
        )
        await asyncio.wait_for(in_flight.wait(), timeout=5)
        async with client.test_factory() as session:
            # NOWAIT fails at once if the sweep were holding the row.
            child = await session.scalar(
                text("SELECT id FROM tasks WHERE id = :id FOR UPDATE NOWAIT"),
                {"id": child_id},
            )
            assert child == child_id
            task = await session.get(Task, child_id)
            task.status = TaskStatus.closed
            await session.commit()
        release.set()
        await asyncio.wait_for(sweep, timeout=5)
        async with client.test_factory() as session:
            saved = await session.get(Task, child_id)
            assert saved.status == TaskStatus.closed
            assert saved.base_branch == old_base

    client.portal.call(race)


def test_ready_only_changes_existing_pr_review_state(client, monkeypatch):
    project, room = _room(client)
    github = AsyncMock()
    github.pr_view.return_value = {"draft": True, "node_id": "PR_node"}
    monkeypatch.setattr(AcceptService, "_app_pr_client", AsyncMock(return_value=github))

    async def run():
        async with client.test_factory() as session:
            task = await _task(session, project, room, "ready")
            service = AcceptService(session)
            assert (await service.mark_ready(room, task.id))["ready"] is False
            github.open_pr.assert_not_awaited()
            task.pr_number, task.pr_url = 123, "https://example/pull/123"
            result = await service.mark_ready(room, task.id)
            assert result["ready"] is True
            github.mark_ready_for_review.assert_awaited_once_with("PR_node")
            github.open_pr.assert_not_awaited()
            github.merge_pr.assert_not_awaited()
            assert (await service.list_for_topic(room))[0] == []
            assert task.status == TaskStatus.open

    client.portal.call(run)
