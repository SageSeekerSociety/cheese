"""一个记忆池属于「某个项目里的某个实例」，关于人的那一份也是（结论 8、54）。

从前「关于某个人的记忆」是一个跨项目的池，而且只有私聊读得到：`chat.py` 那条
`if private_owner` 的分支把整轮的池换成那个人的池——别的房间一个字也读不到，私聊
里芝士自己那个池反而不见了。结论 54 把回忆规则改成「关于人的池随时可读」，结论 8
把池收进实例：关于他的判断是**这个项目里这位芝士**形成的，跨项目读不到。

这里比的是 system prompt 里到底有没有那条事实——注入是这套机制唯一能被看见的地
方，读侧的池清单怎么拼是实现（`pools_for_turn`），不是要守的东西。
"""

import uuid

import pytest

from app.domain.agent.chat import ChatService
from app.domain.agent.compute import ComputePool
from app.domain.agent_instance.services import AgentInstanceService, memory_pool
from app.domain.memory.models import MemoryLayer, MemoryScope, user_scope_id
from app.domain.memory.store import memory_store
from app.domain.project.services import ProjectService
from app.domain.topic.services import TopicService
from tests.conftest import StubChannel, settle_turn

pytestmark = pytest.mark.anyio

ABOUT_THE_PERSON = "他要结论在最前面，别铺垫"
WHAT_IT_LEARNED = "这个项目的前端构建用 pnpm"


class Screen(StubChannel):
    async def send_prompt(self, screen: uuid.UUID, prompt: str) -> bool:
        self.starts(screen, session_id="pools")
        self.stops(screen, "Done.", session_id="pools")
        return True


async def _turn_in(client, tmp_path, *, private: bool, facts: dict[str, str]) -> str:
    """开一个项目、往指定的池里写几条核心记忆、跑一轮，交回这一轮的 system prompt。

    ``facts``：``{"about_person" | "own": 内容}``。写的是 core 层，因为注入只带
    core（`recall_pools`）——普通记忆要 `cheese recall` 才拿得到，注入里本来就不该有。
    """
    factory = client.test_factory
    screen = Screen()
    svc = ChatService(
        session_factory=factory,
        compute=ComputePool([screen.runtime], screen.name),
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path / "ws"),
    )
    async with factory() as session:
        project = await ProjectService(session).create(name="P", owner_handle="u")
        if private:
            topic = await TopicService(session).get_or_create_private(
                project_id=project.id, user_handle="u"
            )
        else:
            topic = await TopicService(session).create(
                project_id=project.id, title="Work", created_by="u"
            )
        topic_id = topic.id
        agent = await AgentInstanceService(session).for_project(project)
        store = memory_store(session)
        if "about_person" in facts:
            await store.remember(
                MemoryScope.user,
                user_scope_id(project.id, agent.handle, "u"),
                facts["about_person"],
                layer=MemoryLayer.core,
            )
        if "own" in facts:
            await store.remember(
                *memory_pool(project.id, agent),
                facts["own"],
                layer=MemoryLayer.core,
            )
        await session.commit()
    async for _ in svc.converse(
        topic_id=topic_id, author="u", content="说说进展", summon=True
    ):
        pass
    await settle_turn(svc, topic_id)
    assert screen.last_system_prompt is not None
    return screen.last_system_prompt


async def test_an_ordinary_room_carries_what_is_known_about_the_people_in_it(
    client, tmp_path
):
    """普通房间也读得到关于在场的人的记忆——这正是结论 54 改掉的那个缺口。

    从前这条事实只有在私聊里才进得了提示词，所以同一位芝士在项目总览里跟同一个人
    说话时，表现得像从没认识过他。
    """
    prompt = await _turn_in(
        client, tmp_path, private=False, facts={"about_person": ABOUT_THE_PERSON}
    )
    assert ABOUT_THE_PERSON in prompt


async def test_a_private_chat_still_carries_the_agents_own_pool(client, tmp_path):
    """私聊不再是特例：那个人的池**加上**芝士自己那个池，不是二选一。

    旧分支把整轮的池换成了 `[(user, owner)]`，于是芝士在私聊里连自己在这个项目学
    到的东西都不记得——它只是不再是同一位芝士了。
    """
    prompt = await _turn_in(
        client,
        tmp_path,
        private=True,
        facts={"about_person": ABOUT_THE_PERSON, "own": WHAT_IT_LEARNED},
    )
    assert ABOUT_THE_PERSON in prompt
    assert WHAT_IT_LEARNED in prompt


async def test_another_projects_cheese_does_not_read_it(client, tmp_path):
    """同一个人，另一个项目的芝士读不到——池的键里就有项目 id（结论 8）。

    跨项目那一份的去向是「关于他自己的资料」（结论 10），由他自己写、任何项目的芝
    士都读得到；不是这个池。
    """
    # A 项目：关于 u 的这条事实写在这里，而且这一轮读得到（前提，否则下面的
    # 「读不到」证明不了任何事）。
    here = await _turn_in(
        client, tmp_path, private=False, facts={"about_person": ABOUT_THE_PERSON}
    )
    assert ABOUT_THE_PERSON in here, "前提：写它的那个项目里本来就读得到"

    # B 项目：同一个人 u 在场，同一个库，只是芝士是另一个实例。键是全局的那一天，
    # 这一句会命中。
    elsewhere = await _turn_in(
        client, tmp_path, private=False, facts={"own": WHAT_IT_LEARNED}
    )
    assert ABOUT_THE_PERSON not in elsewhere
