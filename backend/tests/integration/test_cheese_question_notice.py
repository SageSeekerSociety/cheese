"""芝士问了一个问题、这一轮就此结束 —— 看板要显示它，等回答的人要收到通知。

一个待回答的问题本身在界面上没有别的痕迹 —— 芝士问完就收工，对话只是安静下来，
而安静与正在运行无法区分。所以两件事一起做：房间与任务进「待处理 · 待回答」，同时
通知发起那一轮的人。芝士是代他执行这件事的，这个问题在等的是他。

判据是 #1084 定的那一条，不新增存储 —— **最近一条提问消息没有作答记录**
（`answer_log` 或上游点选的 `answered`，两种形状并存），且此后没人给过回应。回应有
三条出路：有人点了选项（就是回复那道题的一句话）、被问的人直接打字回了一句、或者
**芝士自己又接着说了一句**（#2046：芝士问完没等人答就自己把活做完又发了几条进展，
房间却一直停在「待回答」）。最后一条只管芝士自己问出口的题 —— 人问的那道题，芝士
在不在房间里说话都与它无关。
"""

import uuid

from app.domain.agent.announce import notify_question
from app.domain.block.models import AuthorType, Block
from app.domain.block.repositories import BlockRepository
from app.domain.room_task.presentation import NeedsYou
from app.domain.topic.models import Topic
from app.domain.topic.services import TopicService
from tests.ask_fixtures import active_ask, question_row, wait_turn_idle
from tests.conftest import seed_user
from tests.integration.conftest import (
    in_thread,
    join_project_team,
    post_message,
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


def _click(client, conversation: str, data: dict, *, by: str = "alice") -> None:
    """点「按部门」：浏览器把选项文字作为对那道题的回复发出去，和打字是同一扇门。"""
    (question,) = data["blocks"]
    post_message(
        client, conversation, by, {"content": "按部门", "reply_to": question["id"]}
    )


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

    _click(client, thread, data)
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
    _click(client, thread, data)
    wait_turn_idle(client, thread)

    with active_ask(client, stub_hooks, monkeypatch, thread, actor="alice") as headers:
        _ask(client, thread, headers, question="那按项目的口径要不要含外包")

    assert _shown(client, pid, room)["phrase"] == NeedsYou.awaiting_answer


def test_an_agent_that_speaks_again_takes_its_own_question_off_the_desk(client):
    """芝士问完没等回答，自己又接着说了几句 —— 那道题不再挂在人身上 (#2046)。

    实况：芝士在房间里问「截图里那个灰底圆角块是哪一处」，没等人答就自己找到根因、
    把活做完、又发了几条进展，而房间从 01:36 一直停在「待回答」，直到人真去点一下
    才灭。提问的人自己往前走了，球就不在他手上了。
    """
    seed_user(client, "alice")
    pid, room = _room(client)
    question_row(client, room, question="截图里那个灰底圆角块是哪一处？", asked="alice")
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
    question_row(
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


def test_a_question_with_no_turn_at_all_is_asked_and_reaches_nobody(client):
    """没有进行中的轮次也问得出去 —— 提问不靠任何一轮，只是没有名字可通知。

    「这道题在等谁」从那位队友开着的那一轮读；一轮都没开着，就不猜人：题照样落进
    对话，回复它的人都算，只是不通知任何人。
    """
    alice = seed_user(client, "alice")
    pid, room = _room(client)

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
    assert r.status_code == 200, r.text
    (question,) = r.json()["data"]["blocks"]
    assert question["meta"]["asked"] is None

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

    _click(client, thread, data, by="alice")
    wait_turn_idle(client, thread)

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
    # A reply that answers the question wakes the agent; let that turn finish.
    wait_turn_idle(client, room)


def _ask_an_old_question(
    client, room: str, *, asked: str, question: str = "预算按哪个口径统计"
) -> dict:
    """一道问 ``asked`` 的题，连它那条通知一起，不经过任何一轮。

    题由测试摆出来，通知仍走生产那条路（`notify_question`），不是手写一条记录。
    """
    block = question_row(client, room, question=question, asked=asked)

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


def test_typing_a_reply_settles_a_question_row(client):
    """没点选项、直接打字回了一句，也是回答：通知不再是未读，也不再说「待你回答」。

    实况：被问的人在房间里打字答了，首页「动态」里那条还是未读，还写着「已暂停，
    待你回答」。
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
