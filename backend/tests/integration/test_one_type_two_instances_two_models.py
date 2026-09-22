"""一个类型建出来的两个实例，被绑不同模型的两条活分别使用（结论 3、28）。

这是「模型搬出参与者」之后必须还成立的那件事。从前换模型的办法是**再建一个
agent**——两个 role 一模一样的队友，区别只有背后那个型号。模型搬到活上以后，
同一个类型建出来的两个实例是**同一份出厂设置**（模型这一栏默认空着，继承项目
主模型），而两条活各绑各的模型，互不影响。

「一个 agent 一个模型」如果偷偷活着，这条会红在第一个断言上：两份 configuration
不再相等。
"""

import uuid

import pytest

from app.domain.agent_instance.services import AgentInstanceService
from app.domain.project.services import ProjectService
from app.domain.room_task.models import Task
from app.domain.topic.services import TopicService

RETIRED = {"harness", "effort"}


@pytest.mark.anyio
async def test_one_type_two_instances_and_two_works_on_two_models(client):
    bound = {"one": "sonnet", "two": "opus"}
    ids: dict[str, uuid.UUID] = {}
    async with client.test_factory() as session:
        project = await ProjectService(session).create(name="P", owner_handle="alice")
        room = await TopicService(session).create(
            project_id=project.id, title="房间", created_by="alice"
        )
        agents = AgentInstanceService(session)
        instances = [
            await agents.create(
                project_id=project.id,
                handle=handle,
                type_name="fullstack-engineer",
                display_name=handle,
            )
            for handle in bound
        ]
        first, second = (row.configuration for row in instances)
        assert first == second, "同一个类型建出来的两个实例是同一份出厂设置"
        assert not RETIRED & set(first)
        assert first["model"] is None, "实例默认不预设模型，继承项目主模型"

        for handle, model in bound.items():
            work = Task(
                project_id=project.id,
                room_id=room.id,
                title=f"{handle} 的活",
                owner_handle=handle,
                model=model,
            )
            session.add(work)
            await session.flush()
            ids[handle] = work.id
        ids["room"] = room.id
        await session.commit()

    for handle, model in bound.items():
        card = client.get(f"/topics/{ids['room']}/tasks/{ids[handle]}")
        assert card.status_code == 200, card.text
        assert card.json()["data"]["model"] == model
