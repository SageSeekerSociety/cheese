"""芝士提出待回答的问题后本轮停止等待 —— 看板要显示它，等回答的人要收到通知。

这是「下一步在人手上」里唯一**会中断运行**的一种：其余几种都是一轮结束之后的状态
（验收卡已提交、检查未通过、已退回），而一个待回答的问题把这一轮停在中途。中断
本身在界面上没有任何痕迹 —— 房间只是安静下来，而安静与正在运行无法区分。

所以两件事一起做：房间与任务进「待处理 · 待回答」，同时通知发起这一轮的人。芝士
是代他执行这件事的，这个问题也只有他能回答。

判据分两套，看题是哪种形状存的（`_awaiting_an_answer`）：组题看这一组还有没有未答
成员；非组题是 #1084 定的那一条，不新增存储 —— **最近一条提问消息没有作答记录**
（`answer_log` 或上游点选的 `answered`，两种形状并存），且此后没人给过回应。回应有
三条出路：被问的人点了选项（追加作答记录）、他直接打字回了一句、或者**芝士自己又
接着说了一句**（#2046：芝士问完没等人答就自己把活做完又发了几条进展，房间却一直
停在「待回答」）。后两条只管非组题，也只管芝士自己问出口的题 —— 人问的那道题，
芝士在不在房间里说话都与它无关。
"""

import uuid

from app.domain.agent.announce import notify_question
from app.domain.block.models import AuthorType, Block
from app.domain.block.repositories import BlockRepository
from app.domain.room_task.presentation import NeedsYou
from app.domain.topic.models import Topic
from app.domain.topic.services import TopicService
from tests.ask_fixtures import active_ask, legacy_question, wait_turn_idle
from tests.conftest import seed_user
from tests.integration.conftest import (
    in_thread,
    join_project_team,
    post_project,
    room_agent_headers,
    room_agent_seat,
    session_auth_headers,
)


def _room(client) -> tuple[str, str]:
    """alice 创建的房间 —— 创建者即名册上的第一个人，而提问要求调用者在名册内。"""
    auth = session_auth_headers("alice")
    pid = post_project(client, json={"name": "P"}, headers=auth).json()["data"]["id"]
    tid = client.post(
        "/topics", json={"project_id": pid, "title": "预算复核"}, headers=auth
    ).json()["data"]["id"]
    return pid, tid


def _ask(
    client,
    room: str,
    headers: dict[str, str],
    question: str = "预算按哪个口径统计",
) -> dict:
    """芝士在这一轮里问出口的一组题 —— 凭据是这轮自己的那位队友。"""
    r = client.post(
        f"/topics/{room}/asks",
        json={
            "questions": [
                {
                    "question": question,
                    "options": [{"text": "按部门"}, {"text": "按项目"}],
                }
            ]
        },
        headers=headers,
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _settle(client, data: dict, *, by: str = "alice") -> None:
    """整组交一次：一题一题交不进去，组题必须整组结算。"""
    (member,) = data["group"]["members"]
    r = client.post(
        f"/topics/asks/{data['group']['id']}/settle",
        json={
            "topic_id": data["group"]["topic_id"],
            "asked_by": data["group"]["asked_by"],
            "client_op_id": f"settle-{uuid.uuid4()}",
            "expect_version": 0,
            "answered": [
                {
                    "block_id": member,
                    "kind": "option",
                    "option": "按部门",
                    "client_op_id": f"answer-{uuid.uuid4()}",
                    "expect_version": 0,
                }
            ],
            "later": [],
            "unanswered": [],
        },
        headers=session_auth_headers(by),
    )
    assert r.status_code == 200, r.text


def _agent_says(client, room: str, text: str) -> None:
    """芝士在房间自己的线上又发了一句 —— 落一条真消息，不走轮次。"""
    agent = room_agent_seat(client, room)

    async def go() -> None:
        async with client.test_factory() as session:
            topic = await session.get(Topic, uuid.UUID(room))
            await BlockRepository(session).add(
                project_id=topic.project_id,
                conversation_id=topic.id,
                author=agent,
                author_type=AuthorType.participant,
                content=text,
            )
            await session.commit()

    client.portal.call(go)


def _shown(client, project: str, room: str) -> dict:
    rows = client.get("/topics", params={"project_id": project}).json()["data"]["data"]
    (row,) = [r for r in rows if r["id"] == room]
    return row["presentation"]


def _questions(client, token: str) -> list[dict]:
    r = client.get(
        "/notifications",
        params={"type": "CHEESE_QUESTION"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["notifications"]


def test_an_unanswered_question_puts_the_room_in_the_waiting_column(
    client, stub_hooks, monkeypatch
):
    seed_user(client, "alice")
    pid, room = _room(client)
    # 芝士在支线里回答，题也在那里问。
    thread = in_thread(client, room, "alice")

    with active_ask(client, stub_hooks, monkeypatch, thread, actor="alice") as headers:
        _ask(client, thread, headers)

    shown = _shown(client, pid, room)
    assert shown["column"] == "needs_you"
    assert shown["phrase"] == NeedsYou.awaiting_answer


def test_answering_it_takes_the_room_back_out(client, stub_hooks, monkeypatch):
    """已回答的问题不应让房间长期停留在待回答 —— 判据是**最近一条**。"""
    seed_user(client, "alice")
    pid, room = _room(client)
    # 芝士在支线里回答，题也在那里问。
    thread = in_thread(client, room, "alice")
    with active_ask(client, stub_hooks, monkeypatch, thread, actor="alice") as headers:
        data = _ask(client, thread, headers)
    assert _shown(client, pid, room)["column"] == "needs_you"

    _settle(client, data)
    # 作答会把芝士叫起来；等那一轮收尾再看板，免得量到的是「正在跑」。
    wait_turn_idle(client, thread)

    assert _shown(client, pid, room)["column"] != "needs_you"


def test_a_second_question_after_an_answered_one_still_counts(
    client, stub_hooks, monkeypatch
):
    """一组答完不等于房间没题在等 —— 后面新问的那组还没答，仍然停在待回答。"""
    seed_user(client, "alice")
    pid, room = _room(client)
    # 芝士在支线里回答，题也在那里问。
    thread = in_thread(client, room, "alice")
    with active_ask(client, stub_hooks, monkeypatch, thread, actor="alice") as headers:
        data = _ask(client, thread, headers)
    _settle(client, data)
    wait_turn_idle(client, thread)

    with active_ask(client, stub_hooks, monkeypatch, thread, actor="alice") as headers:
        _ask(client, thread, headers, question="那按项目的口径要不要含外包")

    assert _shown(client, pid, room)["phrase"] == NeedsYou.awaiting_answer


def test_an_agent_that_speaks_again_takes_its_own_question_off_the_desk(client):
    """芝士问完没等回答，自己又接着说了几句 —— 那道题不再挂在人身上 (#2046)。

    实况：芝士在房间里问「截图里那个灰底圆角块是哪一处」，没等人答就自己找到根因、
    把活做完、又发了几条进展，而房间从 01:36 一直停在「待回答」，直到人真去点一下
    才灭。提问的人自己往前走了，球就不在他手上了。

    这条规则只管非组题（`legacy_question` 是那一种形状）：组的答案要整组明确提交，
    一句进展不作数。
    """
    seed_user(client, "alice")
    pid, room = _room(client)
    legacy_question(
        client, room, question="截图里那个灰底圆角块是哪一处？", asked="alice"
    )
    assert _shown(client, pid, room)["phrase"] == NeedsYou.awaiting_answer

    _agent_says(client, room, "找到根因了，改完推上去了")

    assert _shown(client, pid, room)["phrase"] != NeedsYou.awaiting_answer


def test_a_question_a_person_asked_still_waits_while_the_agent_works(client):
    """这条只管芝士自己问的题：人问的题不会因为芝士在房间里说话而消失。

    人在房间里发起的选项问的是房间里的人；芝士在旁边干活不是他们的回答。人问的题
    是迁移留给历史的那一种形状（`e5a1c7d3b284` 不动人类历史），所以读侧还得认它。
    """
    seed_user(client, "alice")
    pid, room = _room(client)
    legacy_question(
        client,
        room,
        question="周会挪到周四行吗",
        author="alice",
        asked="alice",
        options=[{"text": "行"}, {"text": "不行"}],
    )
    _agent_says(client, room, "我先把能查的查了")

    assert _shown(client, pid, room)["phrase"] == NeedsYou.awaiting_answer


def test_the_person_who_started_the_turn_hears_the_question(
    client, stub_hooks, monkeypatch
):
    """收件人是发起这一轮的人，不是房间名册。

    芝士是代他执行这件事的，房间里其他人并不在等这个回答。
    """
    alice = seed_user(client, "alice")
    bob = seed_user(client, "bob")
    pid, room = _room(client)
    # 芝士在支线里回答，题也在那里问。
    thread = in_thread(client, room, "alice")
    join_project_team(client, pid, "bob")

    with active_ask(client, stub_hooks, monkeypatch, thread, actor="bob") as headers:
        _ask(client, thread, headers)

    (row,) = _questions(client, bob)
    assert row["contextMetadata"]["question"] == "预算按哪个口径统计"
    assert row["contextMetadata"]["topicId"] == room
    assert row["contextMetadata"]["topicTitle"] == "预算复核"
    assert row["read"] is False
    assert _questions(client, alice) == []  # 未发起这一轮的人不接收


def test_a_question_in_a_turn_the_platform_started_reaches_nobody(
    client, stub_hooks, monkeypatch
):
    """平台发起的轮次（resume、各类提醒）作者是 system。

    这种轮次里的提问指不到具体的人，因此不通知任何人：把一条多数人不需要处理的
    通知发给全部成员，代价是他们此后关闭这个渠道。题照样挂在房间里等回答 ——
    等的只是没有名字。
    """
    alice = seed_user(client, "alice")
    bob = seed_user(client, "bob")
    pid, room = _room(client)
    # 芝士在支线里回答，题也在那里问。
    thread = in_thread(client, room, "alice")
    join_project_team(client, pid, "bob")

    with active_ask(
        client, stub_hooks, monkeypatch, thread, actor="alice", platform_turn=True
    ) as headers:
        _ask(client, thread, headers)

    assert _questions(client, alice) == []
    assert _questions(client, bob) == []


def test_a_question_with_no_turn_at_all_reaches_nobody(client):
    """没有进行中的轮次 —— 这个问题问不出去，因此不通知任何人。

    「这道题在等谁」要从在跑的那一轮读。读不到就不猜人，提问直接被拒（403）：
    以前那条「退到最近点了芝士名的人」的退路随旧的单题路由一起拆了。
    """
    alice = seed_user(client, "alice")
    _pid, room = _room(client)

    r = client.post(
        f"/topics/{room}/asks",
        json={
            "questions": [
                {
                    "question": "预算按哪个口径统计",
                    "options": [{"text": "按部门"}, {"text": "按项目"}],
                }
            ]
        },
        headers=room_agent_headers(client, room),
    )
    assert r.status_code == 403, r.text
    assert "无法确认原生提问会话和执行区间" in r.text

    assert _questions(client, alice) == []


def test_answering_the_question_settles_its_notification(
    client, stub_hooks, monkeypatch
):
    """点了选项之后，通知里那一条不再是未读，也不再说「待你回答」。

    实况：人在房间里点完了选项，首页「动态」里那条还是未读，还写着「已暂停，待你
    回答」——一句已经不成立的话，还要他手动标记已读。
    """
    alice = seed_user(client, "alice")
    _pid, room = _room(client)
    # 芝士在支线里回答，题也在那里问。
    thread = in_thread(client, room, "alice")
    with active_ask(client, stub_hooks, monkeypatch, thread, actor="alice") as headers:
        data = _ask(client, thread, headers)
    (before,) = _questions(client, alice)
    assert before["read"] is False

    _settle(client, data, by="alice")

    (row,) = _questions(client, alice)
    assert row["read"] is True
    assert row["contextMetadata"]["answered"] == "按部门"
    unread = client.get(
        "/notifications/unread-count", headers={"Authorization": f"Bearer {alice}"}
    )
    assert unread.json()["data"]["count"] == 0


def _say(client, room: str, text: str, handle: str = "alice") -> None:
    """人在房间输入框里打了一句话发出去 —— 没点选项，走的是消息那条路。"""
    r = client.post(
        f"/topics/{room}/messages",
        json={"content": text, "request_id": str(uuid.uuid4())},
        headers=session_auth_headers(handle),
    )
    assert r.status_code == 200, r.text


def _ask_an_old_question(
    client, room: str, *, asked: str, question: str = "预算按哪个口径统计"
) -> dict:
    """迁移留下的非组题，连它那条通知一起 —— 旧代码问出口时通知就落下了。

    合并后的提问入口一律建组，非组形状只剩数据库里已有的行，所以这道题和它的通知
    都由测试摆出来；通知仍走生产那条路（`notify_question`），不是手写一条记录。
    """
    block = legacy_question(client, room, question=question, asked=asked)

    async def notify() -> None:
        async with client.test_factory() as session:
            row = await session.get(Block, uuid.UUID(str(block["id"])))
            assert row is not None
            await notify_question(
                session,
                place=await TopicService(session).place_or_404(row.conversation_id),
                block=row,
                question=row.content,
                asker=row.author,
                asked=asked,
            )
            await session.commit()

    client.portal.call(notify)
    return block


def test_typing_a_reply_settles_a_legacy_question(client):
    """没点选项、直接打字回了一句，也是回答：通知不再是未读，也不再说「待你回答」。

    实况：被问的人在房间里打字答了，首页「动态」里那条还是未读，还写着「已暂停，
    待你回答」。组题不走这条路（整组明确提交才算），所以这里用迁移留下的非组形状。
    """
    alice = seed_user(client, "alice")
    pid, room = _room(client)
    _ask_an_old_question(client, room, asked="alice")
    assert _shown(client, pid, room)["phrase"] == NeedsYou.awaiting_answer
    (before,) = _questions(client, alice)
    assert before["read"] is False

    _say(client, room, "按项目，外包单列")

    assert _shown(client, pid, room)["phrase"] != NeedsYou.awaiting_answer
    (row,) = _questions(client, alice)
    assert row["read"] is True
    assert row["contextMetadata"]["answered"] == "按项目，外包单列"
    unread = client.get(
        "/notifications/unread-count", headers={"Authorization": f"Bearer {alice}"}
    )
    assert unread.json()["data"]["count"] == 0


def test_the_mention_in_a_typed_reply_is_not_part_of_the_answer(client):
    """回话时点了队友的名，通知里记的回答只有他说的话，不带那个 @。"""
    alice = seed_user(client, "alice")
    _pid, room = _room(client)
    _ask_an_old_question(client, room, asked="alice")

    _say(client, room, f"<@{room_agent_seat(client, room)}> 按部门")

    (row,) = _questions(client, alice)
    assert row["contextMetadata"]["answered"] == "按部门"


def test_a_long_typed_answer_is_shortened_in_the_notification(client):
    """通知里的回答是一行说明，不是聊天记录 —— 太长就截到 `ANSWER_EXCERPT_CHARS`。"""
    alice = seed_user(client, "alice")
    _pid, room = _room(client)
    _ask_an_old_question(client, room, asked="alice")
    reply = "按项目统计，" + "外包和临时人员的费用都单独列一栏，" * 10

    _say(client, room, reply)

    (row,) = _questions(client, alice)
    answered = row["contextMetadata"]["answered"]
    assert answered.startswith("按项目统计，外包和临时人员")
    assert answered.endswith("…")
    assert len(answered) < len(reply)


def test_only_the_next_message_is_the_answer(client):
    """答完又接着说了几句：通知里记的还是回答那一句，不被后面的话改写。"""
    alice = seed_user(client, "alice")
    _pid, room = _room(client)
    _ask_an_old_question(client, room, asked="alice")

    _say(client, room, "按项目")
    _say(client, room, "顺便把上个月的也重算一下")

    (row,) = _questions(client, alice)
    assert row["contextMetadata"]["answered"] == "按项目"


def test_someone_else_typing_does_not_answer_for_the_person_asked(client):
    """题问的是 bob；房间里 alice 说话不是他的回答。"""
    seed_user(client, "alice")
    bob = seed_user(client, "bob")
    pid, room = _room(client)
    _ask_an_old_question(client, room, asked="bob")

    _say(client, room, "我觉得按部门")

    assert _shown(client, pid, room)["phrase"] == NeedsYou.awaiting_answer
    (row,) = _questions(client, bob)
    assert row["read"] is False
    assert "answered" not in row["contextMetadata"]
