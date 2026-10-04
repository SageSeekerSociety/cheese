"""一轮只带两个索引：项目共看的那个，和这一轮说话那个人的（结论 8、54）。

从前进提示词的是「池」：关于某个人的那份是跨项目的，只有私聊读得到，而私聊里芝士
自己那个池反而不见了。条目池撤下来之后，注入的东西缩成两个文件——`team/MEMORY.md`
和 `private/<说话的那个人>/MEMORY.md`。所以「哪一间房」不再改变任何事：普通房间和
私聊拿到的是同样两个索引，换一个项目就是另一份。

这里比的是 system prompt 里有什么——注入是这套机制唯一能被看见的地方，索引怎么读
出来是实现，不是要守的东西。
"""

import uuid
from contextlib import ExitStack
from dataclasses import replace
from unittest.mock import patch

import pytest

from app.domain.agent.chat import ChatService
from app.domain.agent.compute import ComputePool
from app.domain.agent.harness import HARNESSES
from app.domain.memory.files import INDEX_NAME, MemoryFileScope
from app.domain.memory.files_store import MemoryFileStore
from app.domain.project.services import ProjectService
from app.domain.topic.services import TopicService
from tests.conftest import StubChannel, settle_turn
from tests.integration.conftest import registered

pytestmark = pytest.mark.anyio

# 索引行。注入带的就是它们，指向的正文不进来。
TEAM_HOOK = "- [回答先给结论](answer-first.md) — 项目那一份的钩子"
MINE_HOOK = "- [他要结论在最前面](conclusion-first.md) — 说话这个人那一份的钩子"
SOMEONE_ELSE_HOOK = "- [别人](elsewhere.md) — 没说话那个人那一份的钩子"
OTHER_TEAM_HOOK = "- [另一个项目的](other.md) — 另一个项目那一份的钩子"


class Screen(StubChannel):
    async def send_prompt(self, screen: uuid.UUID, prompt: str) -> bool:
        self.starts(screen, session_id="indexes")
        self.stops(screen, "Done.", session_id="indexes")
        return True


async def _turn_in(factory, tmp_path, **kwargs) -> str:
    with ExitStack() as stack:
        return await _turn_on(stack, factory, tmp_path, **kwargs)


async def _turn_on(
    stack: ExitStack,
    factory,
    tmp_path,
    *,
    dm: bool,
    team: str | None = None,
    private: dict[str, str] | None = None,
    keeps_memory: bool = True,
) -> str:
    """开一个项目、铺下索引、跑一轮，交回这一轮的 system prompt。

    ``team`` 是项目共看那个索引的内容；``private`` 是 {人: 内容}——这一轮说话的
    人是 ``u``，所以真正该读到的是 ``private["u"]``。``dm`` 说的是这一轮落在私聊
    里还是普通房间里。``keeps_memory`` 是这一轮跑的骨架会不会把记忆文件对账回平
    台——不会的时候，记忆那两段一个字都不该进去。
    """
    screen = Screen()
    if not keeps_memory:
        # The room's harness, as the registry says it: one that does not
        # reconcile the memory files back.
        entry = HARNESSES[screen.runtime.harness]
        stack.enter_context(
            patch.dict(
                HARNESSES,
                {screen.runtime.harness: replace(entry, keeps_memory=False)},
            )
        )
    svc = ChatService(
        session_factory=factory,
        compute=ComputePool([screen.runtime], screen.name),
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path / "ws"),
    )
    async with factory() as session:
        await registered(session, "u")
        project = await ProjectService(session).create(name="P", owner_handle="u")
        if dm:
            topic = await TopicService(session).get_or_create_private(
                project_id=project.id, user_handle="u"
            )
        else:
            topic = await TopicService(session).create(
                project_id=project.id, title="Work", created_by="u"
            )
        topic_id = topic.id
        store = MemoryFileStore(session)

        async def put(scope: MemoryFileScope, owner: str | None, content: str) -> None:
            await store.write(
                project_id=project.id,
                scope=scope,
                owner_handle=owner,
                path=INDEX_NAME,
                content=content,
                updated_by="cheese",
                expected_version=None,
            )

        if team is not None:
            await put(MemoryFileScope.team, None, team)
        for owner, content in (private or {}).items():
            await put(MemoryFileScope.private, owner, content)
        await session.commit()
    async for _ in svc.converse(
        topic_id=topic_id, author="u", content="说说进展", summon=True
    ):
        pass
    await settle_turn(svc, topic_id)
    assert screen.last_system_prompt is not None
    return screen.last_system_prompt


async def test_a_room_carries_the_projects_index_and_the_speakers(client, tmp_path):
    """一间普通房间：项目那一份和说话这个人那一份都在。

    别人的那一份不在——索引是每一轮都要付钱的东西，而一个项目比这一间房里的人宽。
    结论 54 改掉的是「只有私聊才读得到」：现在哪一间房都读得到，因为要读的不是房
    间，是这个人。
    """
    prompt = client.portal.call(
        lambda: _turn_in(
            client.test_request_factory,
            tmp_path,
            dm=False,
            team=TEAM_HOOK,
            private={"u": MINE_HOOK, "bob": SOMEONE_ELSE_HOOK},
        )
    )
    assert TEAM_HOOK in prompt
    assert MINE_HOOK in prompt
    assert SOMEONE_ELSE_HOOK not in prompt


async def test_a_dm_carries_the_same_two(client, tmp_path):
    """私聊不是特例：项目那一份**加上**说话这个人那一份，不是二选一。

    旧分支把整轮的池换成了那个人的池，于是芝士在私聊里连自己在这个项目学到的东
    西都不记得。现在两间房拿到的是同样两个索引，区别只在说话的人是谁。
    """
    prompt = client.portal.call(
        lambda: _turn_in(
            client.test_request_factory,
            tmp_path,
            dm=True,
            team=TEAM_HOOK,
            private={"u": MINE_HOOK},
        )
    )
    assert TEAM_HOOK in prompt
    assert MINE_HOOK in prompt


async def test_a_harness_that_keeps_no_memory_is_not_given_the_index(client, tmp_path):
    """带不回去的骨架（codex、pi）读到的是做不到的说明。

    索引也一样：一条读得到、改不回去的索引，只会让 agent 去改一个它写不回去的地
    方。这一轮的索引在库里、也在磁盘上，只是不注入。
    """
    prompt = client.portal.call(
        lambda: _turn_in(
            client.test_request_factory,
            tmp_path,
            dm=False,
            team=TEAM_HOOK,
            keeps_memory=False,
        )
    )
    assert TEAM_HOOK not in prompt
    assert "## 记忆（memory）" not in prompt
    assert "## 你的记忆（索引" not in prompt


async def test_another_projects_index_does_not_come_along(client, tmp_path):
    """另一个项目也铺了索引，它那一条不会跟过来——索引是按项目读的。"""
    # 前提：写它的那个项目里本来就读得到，否则下面那句证明不了任何事。
    here = client.portal.call(
        lambda: _turn_in(
            client.test_request_factory,
            tmp_path,
            dm=False,
            team=TEAM_HOOK,
        )
    )
    assert TEAM_HOOK in here

    elsewhere = client.portal.call(
        lambda: _turn_in(
            client.test_request_factory,
            tmp_path,
            dm=False,
            team=OTHER_TEAM_HOOK,
        )
    )
    assert OTHER_TEAM_HOOK in elsewhere
    assert TEAM_HOOK not in elsewhere
