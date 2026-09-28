"""AI 建议的会话属于提问的那个人 —— 读、删、列表，三条路问同一句话。

``ai_conversation`` 一直有 ``owner_id``（``AIConversationRepository.create`` 写的
就是提问者），而且 ``ask`` / ``stream`` 从第一天起就核它：``convo.owner_id !=
user_id`` → ``Conversation not found``。偏偏读与删这两条抄了同一段判据，只抄了弱的
一半（``convo.context_id != task_id``）—— 而 ``get_conversation`` 的 docstring 还把
「问的那条路要求 ``context_id == task_id``」写成了它存在的理由。分组列表更宽：repo
只按 ``context_id`` 与 ``deleted_at`` 过滤，于是整道题**所有人**的
``conversationId`` 与 ``title``（提问的前 60 字）一起交了出来。

这三条路都过 ``_ensure_task_visible_for_advice``，而那扇门对没开访问控制的题对任何
登录用户都开着（``TaskVisibilityService.can_view_task`` 的 ``not
access_control_enabled`` 分支）。于是链子是通的：① 列表拿到别人的 id 与标题 →
② 按 id 读出别人完整的提问与 AI 回答 → ③ 把别人的会话软删掉。

这里的题**故意不开** ``access_control_enabled``：两个人都看得见这道题，所以能拦住
第二个人的只可能是「这条会话不是他的」。每条测试都先证明前一道门是开的 —— 否则
断言只证明了「看不见这道题」，那件事别处已经测过（``test_who_may_read_a_project``）。

前端侧栏那一条链读的是「我的对话」：新建 / 搜索 / 删除对话都在自己那一列上，标题
是提问的前 60 字，没有任何署名或共享入口，空状态写的是「你的第一个问题将开启智慧
对话」。所以列表返回整题所有人这一件事，本身就是同一个缺陷的另一半。
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from tests.conftest import seed_space, seed_user
from tests.integration.test_team_member_enters_team_project import _bearer

QUESTION = "这道题的第三步怎么做？"
ANSWER = "先看材料里的第三条，再把它套到你的数据上。"


def _a_task_anyone_can_see(client, *, creator: str = "teacher") -> int:
    """一道任何登录用户都看得见的题（``access_control_enabled`` 保持默认的假）。"""
    from app.domain.space.models import SpaceCategory
    from app.domain.task.models import Task
    from app.domain.user.repositories import UserRepository

    seed_user(client, creator)
    space_id = seed_space(client, name=f"信院-{uuid.uuid4().hex[:8]}")
    holder: dict[str, int] = {}

    async def _seed() -> None:
        now = datetime.now(UTC)
        async with client.test_factory() as session:  # type: ignore[attr-defined]
            author = await UserRepository(session).get_by_username(creator)
            assert author is not None
            category = SpaceCategory(
                space_id=space_id,
                name="创研课",
                description="",
                display_order=0,
                resource_pack={},
                conditions=[],
                default_role=None,
                created_at=now,
                updated_at=now,
            )
            session.add(category)
            await session.flush()
            task = Task(
                name="赛题",
                intro="",
                description="",
                creator_id=author.id,
                space_id=space_id,
                category_id=category.id,
                submitter_type=0,
                approved=1,
                default_deadline=0,
                # 不设 access_control_enabled：这才是这个测试的意思 —— 题对谁都
                # 可见，拦住第二个人不是可见性，而是会话的主人。
                created_at=now,
                updated_at=now,
            )
            session.add(task)
            await session.flush()
            holder["id"] = task.id
            await session.commit()

    asyncio.run(_seed())
    return holder["id"]


def _a_conversation(
    client,
    *,
    task_id: int,
    owner: str,
    title: str,
    question: str = QUESTION,
    answer: str = ANSWER,
) -> str:
    """给 ``task_id`` 真建一条 ``owner`` 的对话（一条问、一条答），返回它的 id。"""
    from app.domain.llm.models import AIConversation, AIMessage
    from app.domain.user.repositories import UserRepository

    seed_user(client, owner)
    cid = f"conv-{task_id}-{owner}-{uuid.uuid4().hex[:8]}"
    holder: dict[str, str] = {}

    async def _seed() -> None:
        now = datetime.now(UTC)
        async with client.test_factory() as session:  # type: ignore[attr-defined]
            asker = await UserRepository(session).get_by_username(owner)
            assert asker is not None
            convo = AIConversation(
                owner_id=asker.id,
                context_id=task_id,
                conversation_id=cid,
                title=title,
                model_type="standard",
                module_type="task_ai_advice",
                created_at=now,
                updated_at=now,
            )
            session.add(convo)
            await session.flush()
            # 一问一答要能配成对，顺序得是真的：读路径按 created_at 升序配。
            session.add(
                AIMessage(
                    conversation_id=convo.id,
                    role="user",
                    content=question,
                    model_type="standard",
                    created_at=now,
                    updated_at=now,
                )
            )
            session.add(
                AIMessage(
                    conversation_id=convo.id,
                    role="assistant",
                    content=answer,
                    model_type="standard",
                    created_at=now + timedelta(seconds=1),
                    updated_at=now + timedelta(seconds=1),
                )
            )
            await session.commit()
        holder["cid"] = cid

    asyncio.run(_seed())
    return holder["cid"]


def _the_doors_open_for(client, task_id: int, who: str) -> None:
    """证明 ``who`` 过得了「看得见这道题」那一关 —— 下面拦住的不是它。

    ``/ai-advice/status`` 走的正是被改的三条路共用的那一扇门
    （``_ensure_task_visible_for_advice``）。它答 200，就说明这个人在那道题上
    「看得见」，于是 404 只可能来自别的判断。
    """
    headers = _bearer(seed_user(client, who))
    resp = client.get(f"/tasks/{task_id}/ai-advice/status", headers=headers)
    assert resp.status_code == 200, resp.text


def _conversation_url(task_id: int, conversation_id: str) -> str:
    return f"/tasks/{task_id}/ai-advice/conversations/{conversation_id}"


def test_a_stranger_cannot_read_my_conversation_by_id(client):
    """知道 id 不等于有权限：题对、id 对、主人不对，读到的只能是「不存在」。

    从前读的那条路只核 ``context_id``，于是同题任何登录用户把 id 放进地址里就能
    拿到别人完整的提问与 AI 回答 —— 三行判据抄错了一行。
    """
    task_id = _a_task_anyone_can_see(client)
    cid = _a_conversation(client, task_id=task_id, owner="asker", title="我的问题")
    asker = _bearer(seed_user(client, "asker"))
    stranger = _bearer(seed_user(client, "mallory"))
    url = _conversation_url(task_id, cid)

    _the_doors_open_for(client, task_id, "mallory")

    # 主人读得到，而且读到的是真的内容（下面 404 才有意义：这条路不是坏的）。
    mine = client.get(url, headers=asker)
    assert mine.status_code == 200, mine.text
    assert mine.json()["data"]["conversations"][0]["question"] == QUESTION
    assert mine.json()["data"]["conversations"][0]["response"] == ANSWER

    theirs = client.get(url, headers=stranger)
    assert theirs.status_code == 404, theirs.text
    assert QUESTION not in theirs.text
    assert ANSWER not in theirs.text


def test_a_stranger_cannot_delete_my_conversation(client):
    """删的是别人的会话：删不掉，而且那条会话还在。"""
    task_id = _a_task_anyone_can_see(client)
    cid = _a_conversation(client, task_id=task_id, owner="asker", title="我的问题")
    asker = _bearer(seed_user(client, "asker"))
    stranger = _bearer(seed_user(client, "mallory"))
    url = _conversation_url(task_id, cid)

    _the_doors_open_for(client, task_id, "mallory")

    refused = client.delete(url, headers=stranger)
    assert refused.status_code == 404, refused.text

    # 「删不掉」要看到会话本身，而不是只看那一次拒绝的响应码。
    still_there = client.get(url, headers=asker)
    assert still_there.status_code == 200, still_there.text
    assert still_there.json()["data"]["conversations"][0]["question"] == QUESTION


def test_the_grouped_list_holds_only_my_own_conversations(client):
    """列表是「我问过什么」，不是「这道题有谁问过」。

    别人的 id 出现在这一列里，第一步就已经完成了 —— 剩下两步只差一个 id。
    标题（提问的前 60 字）也一起漏，所以这里连标题一起断言。
    """
    task_id = _a_task_anyone_can_see(client)
    mine = _a_conversation(client, task_id=task_id, owner="asker", title="我问的问题")
    _a_conversation(client, task_id=task_id, owner="mallory", title="别人问的问题")
    asker = _bearer(seed_user(client, "asker"))
    stranger = _bearer(seed_user(client, "mallory"))
    url = f"/tasks/{task_id}/ai-advice/conversations/grouped"

    _the_doors_open_for(client, task_id, "mallory")

    mine_seen = client.get(url, headers=asker)
    assert mine_seen.status_code == 200, mine_seen.text
    assert [c["conversationId"] for c in mine_seen.json()["data"]["conversations"]] == [
        mine
    ]
    assert "别人问的问题" not in mine_seen.text

    # 同一份列表在另一个人眼里也是这样 —— 不是「谁的列表都行，只是内容一样」。
    theirs_seen = client.get(url, headers=stranger)
    assert theirs_seen.status_code == 200, theirs_seen.text
    assert "别人问的问题" in theirs_seen.text
    assert mine not in theirs_seen.text
    assert "我问的问题" not in theirs_seen.text


def test_i_can_still_read_and_delete_my_own_conversation(client):
    """上面每一条都是「拦住别人」，这一条是「没把自己也拦掉」。"""
    task_id = _a_task_anyone_can_see(client)
    cid = _a_conversation(client, task_id=task_id, owner="asker", title="我的问题")
    other_cid = _a_conversation(
        client, task_id=task_id, owner="mallory", title="别人问的问题"
    )
    asker = _bearer(seed_user(client, "asker"))
    stranger = _bearer(seed_user(client, "mallory"))
    url = _conversation_url(task_id, cid)

    read = client.get(url, headers=asker)
    assert read.status_code == 200, read.text
    assert read.json()["data"]["conversations"][0]["question"] == QUESTION

    deleted = client.delete(url, headers=asker)
    assert deleted.status_code == 200, deleted.text
    assert client.get(url, headers=asker).status_code == 404, "删完就不该再读得到"

    # 删自己的那一次没有顺手波及别人。
    theirs = client.get(_conversation_url(task_id, other_cid), headers=stranger)
    assert theirs.status_code == 200, theirs.text
    assert theirs.json()["data"]["conversations"][0]["question"] == QUESTION
