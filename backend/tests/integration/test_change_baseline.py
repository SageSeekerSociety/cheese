"""The commit baseline a room turn's change summary is measured against.

A channel holds every task its project ever ran; a closed task takes no more
commits and its branch is often gone. The baseline reads the branches work can
still land on, so one deleted branch does not cost every turn its summary."""

import pytest

from app.domain.agent.room_events import _known_commits
from app.domain.project.services import ProjectService
from app.domain.repository import forge_files
from app.domain.room_task.services import TaskService
from app.domain.topic.services import TopicService
from tests.integration.conftest import registered


@pytest.mark.anyio
async def test_a_closed_task_with_a_gone_branch_leaves_the_baseline_readable(
    business_db_factory, monkeypatch
):
    async with business_db_factory() as session:
        await registered(session, "u")
        project = await ProjectService(session).create(name="P", owner_handle="u")
        room = await TopicService(session).create(
            project_id=project.id, title="R", created_by="u"
        )
        tasks = TaskService(session)
        live = await tasks.open_thread(
            project_id=project.id,
            room_id=room.id,
            title="live",
            owner_handle="u",
            created_by="u",
        )
        done = await tasks.open_thread(
            project_id=project.id,
            room_id=room.id,
            title="done",
            owner_handle="u",
            created_by="u",
        )
        live.branch_name, done.branch_name = "topic/live", "topic/done"
        await tasks.close_thread(done)
        await session.commit()
        pid, rid, live_id = project.id, room.id, live.id

    async def history(self):
        if self.task_id != live_id:
            raise RuntimeError("branch topic/done does not exist")
        return [{"sha": "a1"}, {"sha": "b2"}]

    monkeypatch.setattr(forge_files.ProjectFiles, "history", history)
    assert await _known_commits(business_db_factory, pid, rid) == {"a1", "b2"}
