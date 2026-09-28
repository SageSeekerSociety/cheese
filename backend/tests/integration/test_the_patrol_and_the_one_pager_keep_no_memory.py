"""巡检和一页纸总结这两轮，不注入记忆（`keeps_memory=False`）。

它们不是某一间房的回合：巡检是芝士自己在总览上的一次巡逻，一页纸总结描述的是项目
状态。两轮都没有「本轮说话的人」，注入谁的 private 都不对；更要紧的是这两轮**不落
记忆文件**——说给它们听，它们照着写下去的文件也没有下一轮读得到。

这里读的是会话真的拿到的那份 system prompt（`StubChannel.last_system_prompt`），而
不是某个函数的参数：注入这件事唯一能被看见的地方就是那里。
"""

import uuid

import pytest

from app.domain.agent.chat import ChatService
from app.domain.agent.compute import ComputePool
from app.domain.project.services import ProjectService
from app.domain.topic.services import TopicService
from tests.conftest import StubChannel
from tests.integration.conftest import registered

pytestmark = pytest.mark.anyio

#: 说明书和索引各自的开头（`harness/prompt.py` 里那两段）。
INSTRUCTIONS_HEAD = "## 记忆（memory）"
INDEX_HEAD = "## 你的记忆（索引"


class Screen(StubChannel):
    async def send_prompt(self, screen: uuid.UUID, prompt: str) -> bool:
        self.starts(screen, session_id="patrol")
        self.stops(screen, "Done.", session_id="patrol")
        return True


async def _a_project(factory, tmp_path) -> tuple[ChatService, Screen, uuid.UUID]:
    """一个项目、一个会话池，和这个项目的 id。"""
    screen = Screen()
    svc = ChatService(
        session_factory=factory,
        compute=ComputePool([screen.runtime], screen.name),
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path / "ws"),
    )
    async with factory() as session:
        await registered(session, "u")
        project = await ProjectService(session).create(name="P", owner_handle="u")
        # 一个话题：巡检要按话题状态说话，一页纸总结要按话题列状态。
        await TopicService(session).create(
            project_id=project.id, title="Work", created_by="u"
        )
        project_id = project.id
        await session.commit()
    return svc, screen, project_id


async def test_the_patrol_is_not_told_about_memory(client, tmp_path):
    svc, screen, project_id = await _a_project(client.test_request_factory, tmp_path)

    await svc.run_heartbeat(project_id=project_id)

    assert screen.last_system_prompt is not None
    assert INSTRUCTIONS_HEAD not in screen.last_system_prompt
    assert INDEX_HEAD not in screen.last_system_prompt


async def test_the_one_pager_is_not_told_about_memory(client, tmp_path):
    svc, screen, project_id = await _a_project(client.test_request_factory, tmp_path)

    await svc.summarize_project(project_id=project_id)

    assert screen.last_system_prompt is not None
    assert INSTRUCTIONS_HEAD not in screen.last_system_prompt
    assert INDEX_HEAD not in screen.last_system_prompt
