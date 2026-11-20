"""一轮带项目共看的那个索引；私聊里再带那个人的那一份在哪（结论 8、54）。

从前进提示词的是「池」：关于某个人的那份是跨项目的，只有私聊读得到，而私聊里芝士
自己那个池反而不见了。条目池撤下来之后，记忆是两个文件——`team/MEMORY.md` 和
`private/<那个人>/MEMORY.md`，换一个项目就是另一份。多人的房间只带前一个：座位上
的会话一轮接着一轮替不同的人回答，它为一个人读过的东西下一轮还拿得出来。

这里比的是 system prompt 里有什么——注入是这套机制唯一能被看见的地方，索引怎么读
出来是实现，不是要守的东西。私聊里那个人的那一份只注入它在哪（`MINE`），不注入正
文：注入记进会话记录，正文由他的会话自己读。
"""

import uuid
from contextlib import ExitStack
from dataclasses import replace
from unittest.mock import patch

import pytest

from app.api import deps as session_turn_deps
from app.domain.agent.chat import ChatService
from app.domain.agent.compute import ComputePool
from app.domain.agent.harness import HARNESSES
from app.domain.agent_instance.adapters import named_teammate
from app.domain.block.models import BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.event_author import AuthorType
from app.domain.memory.files import INDEX_NAME, MemoryFileScope, prompt_path
from app.domain.memory.files_store import MemoryFileStore
from app.domain.memory.writers import MemoryWriter
from app.domain.topic.services import TopicService
from tests.conftest import StubChannel, settle_turn
from tests.integration.conftest import project_service, registered

pytestmark = pytest.mark.anyio

# 索引行。注入带的就是它们，指向的正文不进来。
TEAM_HOOK = "- [回答先给结论](answer-first.md) — 项目那一份的钩子"
MINE_HOOK = "- [他要结论在最前面](conclusion-first.md) — 说话这个人那一份的钩子"
SOMEONE_ELSE_HOOK = "- [别人](elsewhere.md) — 没说话那个人那一份的钩子"
#: 这一轮说话那个人（`u`）的 private 索引在哪：注入里有它，没有它的正文。
MINE = prompt_path(f"private/u/{INDEX_NAME}")
SOMEONE_ELSES = prompt_path(f"private/bob/{INDEX_NAME}")
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
    spoke_before: str | None = None,
) -> "Screen":
    """开一个项目、铺下索引、跑一轮，交回跑它的那个替身（`told` 是这一轮读到的）。

    ``team`` 是项目共看那个索引的内容；``private`` 是 {人: 内容}——这一轮说话的
    人是 ``u``，所以真正该读到的是 ``private["u"]``。``spoke_before`` 是在 ``u``
    之前、同一段还没回答的窗口里说过话的另一个人。``dm`` 说的是这一轮落在私聊
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
        work_runner=session_turn_deps.get_work_runner(),
        session_factory=factory,
        compute=ComputePool([screen.runtime]),
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path / "ws"),
    )
    async with factory() as session:
        await registered(session, "u")
        project = await project_service(session).create(name="P", owner_handle="u")
        if dm:
            topic = await TopicService(session).get_or_create_private(
                project_id=project.id,
                user_handle="u",
                named_teammate=named_teammate,
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
                writer=MemoryWriter.panel,
            )

        if spoke_before is not None:
            await registered(session, spoke_before)
            await BlockRepository(session).add(
                project_id=project.id,
                conversation_id=topic_id,
                author=spoke_before,
                author_type=AuthorType.participant,
                content="我先说一句",
                kind=BlockKind.message,
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
    return screen


@pytest.mark.parametrize("spoke_before", [None, "bob"], ids=["alone", "after-bob"])
async def test_a_shared_room_carries_nobodys_private_memory(
    client, tmp_path, spoke_before
):
    """一间多人的房间：项目那一份在，任何人的 private 都不在，说话这个人的也不在。

    座位上的会话一轮一轮接着用，替谁回答跟着谁换。它替 u 读过的 u 的 private 会留
    在它自己的对话里，也留在平台交给这间房执行机的那份会话记录里；下一轮替 bob
    干活时一条命令就拿得出来，而命令的输出标不上主人。所以多人房间里既不告诉它
    u 的索引在哪，也不往它的座位上铺 u 的那一棵。
    """
    screen = client.portal.call(
        lambda: _turn_in(
            client.test_request_factory,
            tmp_path,
            dm=False,
            team=TEAM_HOOK,
            private={"u": MINE_HOOK, "bob": SOMEONE_ELSE_HOOK},
            spoke_before=spoke_before,
        )
    )
    prompt = screen.told
    assert TEAM_HOOK in prompt
    for absent in (MINE, MINE_HOOK, SOMEONE_ELSE_HOOK, SOMEONE_ELSES):
        assert absent not in prompt
    trees = [runner.memory_root() for runner in screen.sessions.values()]
    assert trees
    assert all(not (tree / "private").exists() for tree in trees), trees


async def test_a_dm_carries_the_project_index_and_where_the_persons_is(
    client, tmp_path
):
    """私聊：项目那一份**加上**这个人那一份在哪，不是二选一，而且只有私聊带后者。

    旧分支把整轮的池换成了那个人的池，于是芝士在私聊里连自己在这个项目学到的东
    西都不记得。私聊里每一轮都是这个人的，所以他的 private 在这里交给会话自己读。
    """
    screen = client.portal.call(
        lambda: _turn_in(
            client.test_request_factory,
            tmp_path,
            dm=True,
            team=TEAM_HOOK,
            private={"u": MINE_HOOK},
        )
    )
    prompt = screen.told
    assert TEAM_HOOK in prompt
    assert MINE in prompt
    assert MINE_HOOK not in prompt


async def test_a_harness_that_keeps_no_memory_is_not_given_the_index(client, tmp_path):
    """带不回去的骨架（codex、pi）读到的是做不到的说明。

    索引也一样：一条读得到、改不回去的索引，只会让 agent 去改一个它写不回去的地
    方。这一轮的索引在库里、也在磁盘上，只是不注入。
    """
    screen = client.portal.call(
        lambda: _turn_in(
            client.test_request_factory,
            tmp_path,
            dm=False,
            team=TEAM_HOOK,
            keeps_memory=False,
        )
    )
    prompt = screen.told
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
    assert TEAM_HOOK in here.told

    elsewhere = client.portal.call(
        lambda: _turn_in(
            client.test_request_factory,
            tmp_path,
            dm=False,
            team=OTHER_TEAM_HOOK,
        )
    )
    assert OTHER_TEAM_HOOK in elsewhere.told
    assert TEAM_HOOK not in elsewhere.told
