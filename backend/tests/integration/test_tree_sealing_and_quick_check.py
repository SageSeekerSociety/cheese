"""Each task owns its code, checks and delivery; rooms keep coordinating."""

import asyncio
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

from app.domain.agent.announce import announce
from app.domain.agent_instance.adapters import project_team
from app.domain.delivery.agent import record_task_instruction
from app.domain.project import forge
from app.domain.project.models import Project
from app.domain.repository.forge_files import ProjectFiles
from app.domain.review import pr_publish
from app.domain.review.pr_publish import retarget_completed_dependencies
from app.domain.review.services import AcceptService
from app.domain.room_task.models import Task, TaskStatus
from app.domain.room_task.services import TaskService
from app.domain.topic.models import Topic, TopicKind
from tests.integration.conftest import a_team
from tests.integration.test_accept_pr import app_world as app_world
from tests.machine_work import machine_commits
from tests.support import git_store
from tests.support.review_ports import (
    settle_card,
    task_teammate,
)


def _room(client) -> tuple[uuid.UUID, uuid.UUID]:
    made: dict[str, uuid.UUID] = {}

    async def _seed() -> None:
        async with client.test_factory() as s:
            project = Project(team_id=await a_team(s), name="P", owner_handle="alice")
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


async def _admits_anyone(topic: Topic, handle: str) -> bool:
    """递给 ``AcceptService`` 的那道门：放行。

    真实的那一份由路由注入（``api/routes/accept.py`` 里拿 ``ActorResolver``
    拼出来的 ``ReviewerAdmission``），它自己的用例在
    ``tests/integration/test_accept_reviewer_membership.py``。
    """
    return True


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
            service = AcceptService(
                session,
                announce=announce,
                settle_card=settle_card,
                record_instruction=record_task_instruction,
                task_teammate=task_teammate,
                roster_team=project_team,
            )
            card = await service.create_card(
                topic_id=room,
                task_id=first.id,
                reviewer_handles=["alice"],
                focus="ready",
                change_subject="feat: add first result",
                # 这道门（「审阅人在不在房间里」）有它自己的用例：
                # `test_accept_reviewer_membership.py`。这个文件的房间是直接写库
                # 铺的、没有名册，而它量的是「收下一道题不会动到别的题与房间」，
                # 所以让门放行 —— 别把名册的事混进这条断言。
                admits_reviewer=_admits_anyone,
            )
            fake = app_world["fake"]
            # 假 PR 的标题就是卡片 subject：平台递卡时按它写 PR 标题，而两条
            # 合并路径都比对这两行，不一致就停下（`pr_text.pr_title_matches_
            # subject`）—— 少了它，这里会以「标题不一致」被拒。
            fake.seed_pr(11, head=first.branch_name, title="feat: add first result")
            fake.prs[11]["head_sha"] = delivered_head
            fake.check_state_by_sha[delivered_head] = ("success", "")
            card.pr_number, card.pr_head_sha = 11, delivered_head
            card.pr_url = "https://github.com/acme/widgets/pull/11"
            await service.accept(
                card_id=card.id, decided_by="alice", head_sha=delivered_head
            )
            await session.commit()
            # The accepted task is being written up before it closes; the
            # other one is untouched.
            assert first.closing_since is not None
            assert second.status == TaskStatus.open
            assert second.closing_since is None
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
    monkeypatch.setattr(pr_publish, "proposal_client", AsyncMock(return_value=github))

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
    client.portal.call(
        lambda: retarget_completed_dependencies(
            client.test_request_factory,
            announce=announce,
            record_instruction=record_task_instruction,
        )
    )
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
    client.portal.call(
        lambda: retarget_completed_dependencies(
            client.test_request_factory,
            announce=announce,
            record_instruction=record_task_instruction,
        )
    )
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
    monkeypatch.setattr(pr_publish, "proposal_client", AsyncMock(return_value=github))

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
            retarget_completed_dependencies(
                client.test_request_factory,
                announce=announce,
                record_instruction=record_task_instruction,
            )
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
            service = AcceptService(session, roster_team=project_team)
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


def test_ready_puts_the_commits_title_on_a_placeholder_pr(client, monkeypatch):
    """The `CI required` run the ready flag starts reads the PR's title, and
    this repository checks it. A draft PR still carrying the platform's
    placeholder is marked ready under the head commit's title, and the title
    goes on before the flag — later is a round of CI failed for `WIP: 任务名`.
    """
    project, room = _room(client)
    github = AsyncMock()
    github.pr_view.return_value = {
        "draft": True,
        "node_id": "PR_node",
        "title": "WIP: 领用额度",
        "head": {"sha": "abc123"},
    }
    github.commit_subject.return_value = "fix(usage): a claim is answered once"
    github.commit_parents.return_value = []
    monkeypatch.setattr(AcceptService, "_app_pr_client", AsyncMock(return_value=github))

    async def run():
        async with client.test_factory() as session:
            task = await _task(session, project, room, "ready")
            service = AcceptService(session, roster_team=project_team)
            task.pr_number, task.pr_url = 123, "https://example/pull/123"
            assert (await service.mark_ready(room, task.id))["ready"] is True
            github.commit_subject.assert_awaited_once_with("abc123")
            github.update_pr.assert_awaited_once_with(
                123, title="fix(usage): a claim is answered once"
            )
            named = [
                call[0]
                for call in github.mock_calls
                if call[0] in ("update_pr", "mark_ready_for_review")
            ]
            assert named == ["update_pr", "mark_ready_for_review"]

    client.portal.call(run)


def test_ready_leaves_the_placeholder_alone_when_the_commit_is_one_too(
    client, monkeypatch
):
    """Writing the commit's placeholder onto the PR would put back the title
    this is here to remove — the PR still fails the title check, and now the
    platform wrote the bad title itself. The commit is what has to change."""
    project, room = _room(client)
    github = AsyncMock()
    github.pr_view.return_value = {
        "draft": True,
        "node_id": "PR_node",
        "title": "WIP: 领用额度",
        "head": {"sha": "abc123"},
    }
    github.commit_subject.return_value = "WIP: still sketching this one"
    github.commit_parents.return_value = []
    monkeypatch.setattr(AcceptService, "_app_pr_client", AsyncMock(return_value=github))

    async def run():
        async with client.test_factory() as session:
            task = await _task(session, project, room, "ready")
            service = AcceptService(session, roster_team=project_team)
            task.pr_number, task.pr_url = 123, "https://example/pull/123"
            assert (await service.mark_ready(room, task.id))["ready"] is True
            github.update_pr.assert_not_awaited()
            github.mark_ready_for_review.assert_awaited_once_with("PR_node")

    client.portal.call(run)


def test_ready_leaves_a_title_that_is_not_the_placeholder(client, monkeypatch):
    """Only the platform's own placeholder is the platform's to replace: a
    title the card or a person already wrote stays, and this asserts it with a
    commit subject standing by — without one the test would pass whether the
    placeholder was recognised or not."""
    project, room = _room(client)
    github = AsyncMock()
    github.pr_view.return_value = {
        "draft": True,
        "node_id": "PR_node",
        "title": "fix(usage): a claim is answered once",
        "head": {"sha": "abc123"},
    }
    github.commit_subject.return_value = "fix(usage): some other title entirely"
    github.commit_parents.return_value = []
    monkeypatch.setattr(AcceptService, "_app_pr_client", AsyncMock(return_value=github))

    async def run():
        async with client.test_factory() as session:
            task = await _task(session, project, room, "ready")
            service = AcceptService(session, roster_team=project_team)
            task.pr_number, task.pr_url = 123, "https://example/pull/123"
            assert (await service.mark_ready(room, task.id))["ready"] is True
            github.update_pr.assert_not_awaited()
            github.mark_ready_for_review.assert_awaited_once_with("PR_node")

    client.portal.call(run)


def test_ready_answers_an_exhausted_quota_wherever_it_hits(client, monkeypatch):
    """The quota is per installation: whichever call in the hand-over spends
    the last request, the caller gets the sentence that says so and can retry,
    not a transport error. The steps are idempotent, so nothing is half done.
    """
    from app.domain.review.github_pr import GitHubPRRateLimited

    project, room = _room(client)
    github = AsyncMock()
    github.pr_view.return_value = {
        "draft": True,
        "node_id": "PR_node",
        "title": "WIP: 领用额度",
        "head": {"sha": "abc123"},
    }
    monkeypatch.setattr(AcceptService, "_app_pr_client", AsyncMock(return_value=github))

    async def run():
        async with client.test_factory() as session:
            task = await _task(session, project, room, "ready")
            service = AcceptService(session, roster_team=project_team)
            task.pr_number, task.pr_url = 123, "https://example/pull/123"
            github.commit_subject.return_value = "fix(usage): a claim is answered once"
            github.commit_parents.return_value = []

            # The read that opens the hand-over is always the first call, so
            # each leg spends the quota one call later than the one above it.
            github.commit_parents.side_effect = GitHubPRRateLimited("limited")
            parents = await service.mark_ready(room, task.id)
            assert parents["ready"] is False and "额度暂时用尽" in parents["reason"]
            github.commit_parents.side_effect = None
            github.update_pr.assert_not_awaited()
            github.mark_ready_for_review.assert_not_awaited()

            github.commit_subject.side_effect = GitHubPRRateLimited("limited")
            read = await service.mark_ready(room, task.id)
            assert read["ready"] is False and "额度暂时用尽" in read["reason"]
            github.update_pr.assert_not_awaited()
            github.mark_ready_for_review.assert_not_awaited()

            github.commit_subject.side_effect = None
            github.update_pr.side_effect = GitHubPRRateLimited("limited")
            write = await service.mark_ready(room, task.id)
            assert write["ready"] is False and "额度暂时用尽" in write["reason"]
            github.mark_ready_for_review.assert_not_awaited()

            github.update_pr.side_effect = None
            github.mark_ready_for_review.side_effect = GitHubPRRateLimited("limited")
            flip = await service.mark_ready(room, task.id)
            assert flip["ready"] is False and "额度暂时用尽" in flip["reason"]

    client.portal.call(run)
