"""A 题目's skills (`Teaching.skills`) reach every AI teammate of a project
built from it, on every turn — a teammate added to the project after it was
built included, and one carrying skills of its own too.

They used to be copied into the default teammate once, when the project was
built, so a teammate added later never had them (#4767).
"""

from datetime import UTC, datetime

import pytest

from app.api import deps as session_turn_deps
from app.domain.agent.chat import ChatService
from app.domain.agent_instance.services import AgentInstanceService
from app.domain.identity.handles import agent_instance_handle
from app.domain.project.models import Project
from app.domain.skill_library import native_skill_doc
from app.domain.space.models import Space, SpaceCategory
from app.domain.task.models import Task
from app.domain.topic.services import TopicService
from app.domain.topic_membership.services import TopicMemberService
from tests.conftest import stub_compute
from tests.integration.conftest import project_service, registered
from tests.integration.test_chat_realtime import InstantScreen


async def _task_with_skills(session, skills: list[str]) -> int:
    now = datetime.now(UTC)
    space = Space(
        name=f"空间-{now.timestamp()}",
        intro="",
        description="",
        task_templates=[],
        created_at=now,
        updated_at=now,
    )
    session.add(space)
    await session.flush()
    category = SpaceCategory(
        space_id=space.id,
        name="分组",
        description="",
        display_order=0,
        created_at=now,
        updated_at=now,
    )
    session.add(category)
    await session.flush()
    task = Task(
        name="题目",
        intro="",
        description="",
        protocol_override={"teaching": {"skills": skills}},
        creator_id=1,
        space_id=space.id,
        category_id=category.id,
        submitter_type=0,
        approved=0,
        default_deadline=0,
        created_at=now,
        updated_at=now,
    )
    session.add(task)
    await session.flush()
    return task.id


@pytest.mark.anyio
async def test_a_teammate_added_after_the_project_was_built_carries_the_tasks_skills(
    business_db_factory, tmp_path
):
    factory = business_db_factory
    svc = ChatService(
        work_runner=session_turn_deps.get_work_runner(),
        session_factory=factory,
        compute=stub_compute(InstantScreen()),
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path / "ws"),
    )
    async with factory() as session:
        await registered(session, "u")
        task_id = await _task_with_skills(session, ["wolfram"])
        project = await project_service(session).create(name="P", owner_handle="u")
        (await session.get(Project, project.id)).external_task_id = task_id
        topic = await TopicService(session).create(
            project_id=project.id, title="T", created_by="u"
        )
        # Added after the project exists, carrying a skill of its own.
        later = await AgentInstanceService(session).create(
            project_id=project.id,
            handle="later",
            type_name=None,
            display_name="Later",
        )
        later.configuration = {**(later.configuration or {}), "skills": ["documents"]}
        await TopicMemberService(session).ensure_agent_seat(
            topic.id, agent_instance_handle(later.id)
        )
        topic_id = topic.id
        await session.commit()

    _, message, _, _ = await svc.human_messages.post_user_message(
        topic_id, author="u", content="@Later 算一下", turn_id=None, reply_to=None
    )
    prepared = await svc.turn_preparation.prepare(
        topic_id=topic_id,
        content="@Later 算一下",
        turn_id=message,
        user_block_id=message,
    )

    assert prepared.agent.handle == "later"
    wolfram = native_skill_doc("wolfram")
    documents = native_skill_doc("documents")
    assert wolfram is not None and documents is not None
    assert wolfram[1] in prepared.skill_bodies
    assert documents[1] in prepared.skill_bodies
