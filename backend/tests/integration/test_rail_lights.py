"""侧栏每一行那盏灯的事实：`GET /topics?project_id=` 的每一行带哪几个判据。

画哪一盏灯是前端那个纯函数的事（`frontend/src/lib/roomLight.ts`），灯要问的五件事
却都是后端算的，而且**都按人算**：换一个人看，同一间房、同一件活可以是别的颜色。
所以这个文件里每条都从两个人的眼里看同一份数据 —— 只有一个人看得见的说法，光看
一个人的响应是钉不住的。

五条事实：有没有事在等我（`awaits_me`，这里只当对照）、有活在往前走（`moving`，
呼吸绿）、做完了交付完了而我还没点开看过（`unseen_delivery`）、有人回复了我、我还
没处理（`replied_to_me`），以及这间房里有没有一轮停在报错上、从什么时候起还没恢复
（`incomplete_since`，红灯）。

活的行由 `_planted` 直接种进库，不走「建任务 → 递卡 → 采纳」那一圈：每一圈自己的
规矩在别处钉着（`test_accept.py`），而这里要对下来的是「哪几列推出哪盏灯」。判据
本身在 `room_task/presentation.py` 和 `block/waits.py`，各有单测
（`tests/unit/test_presentation.py`）；这个文件问的是另一个问题 —— 接口把它算出来
了没有。
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from app.core.sandbox_auth import mint_scoped_token
from app.domain.agent.models import AgentTurn
from app.domain.block.models import AuthorType, Block, BlockKind
from app.domain.delivery.models import NativeInput
from app.domain.review.models import AcceptCard, AcceptCardReviewer
from app.domain.room_task.models import Task, TaskProgress, TaskStatus
from tests.integration.conftest import (
    in_thread,
    join_project_team,
    post_message,
    post_project,
    room_agent_seat,
    session_auth_headers,
)

#: 交付落地的时刻。种在半小时前：这一行的灯问的是「交付之后他看过没有」，「刚刚」
#: 和「半小时前」在这一问上没有区别。
DELIVERED_AT = timedelta(minutes=30)


def _project(client) -> str:
    """alice 的项目；bob 和 carol 在它的团队里（但不在同一条活上）。"""
    r = post_project(client, json={"name": "P"}, owner="alice")
    assert r.status_code == 200, r.text
    pid = r.json()["data"]["id"]
    for handle in ("bob", "carol"):
        join_project_team(client, pid, handle)
    return pid


def _room(client, project_id: str, title: str = "房间") -> str:
    r = client.post(
        "/topics",
        json={"project_id": project_id, "title": title},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _planted(
    client, project_id: str, room_id: str, specs: list[dict]
) -> dict[str, str]:
    """往库里种几条活，返回 {名字: 活的 id}。

    每条 `spec` 是 `Task` 的那几列，另外两项不是列：`name` 是这条活在断言里的叫法，
    `card` 是它此刻骑的那张卡。
    """

    async def _run() -> None:
        async with client.test_factory() as s:
            for spec in specs:
                spec = dict(spec)
                name = spec.pop("name")
                card = spec.pop("card", None)
                task = Task(
                    project_id=uuid.UUID(project_id),
                    room_id=uuid.UUID(room_id),
                    **spec,
                )
                s.add(task)
                await s.flush()
                if card is not None:
                    # 评审人在 `accept_card_reviewer` 表里（一卡一行），卡上那一列只
                    # 留影子；读的一侧（`awaits_me`）看的是表，所以直接写库的卡要
                    # 把那一行也写出来。
                    card_row = AcceptCard(
                        topic_id=uuid.UUID(room_id),
                        task_id=task.id,
                        reviewer_handle="alice",
                        **card,
                    )
                    s.add(card_row)
                    await s.flush()
                    s.add(AcceptCardReviewer(card_id=card_row.id, handle="alice"))
                ids[name] = str(task.id)
            await s.commit()

    ids: dict[str, str] = {}
    asyncio.run(_run())
    return ids


def _converted_thread(
    client, project_id: str, room_id: str, *, status=TaskStatus.open, closing=False
) -> str:
    """一条支线原地转成了活：`threads` 行留着（它挂住那条消息），活拿了它的 id，
    登记表里这条对话是 `task`。

    `closing` 是「最后一步交付已经落地、芝士正在收尾」——仍然 `open`，但已经不在
    「还有事的活」里（`tasks_of_rooms` 同一份判据）。
    """
    thread = in_thread(client, room_id, "alice")
    now = datetime.now(UTC)

    async def _make() -> None:
        async with client.test_factory() as s:
            s.add(
                Task(
                    id=uuid.UUID(thread),
                    project_id=uuid.UUID(project_id),
                    room_id=uuid.UUID(room_id),
                    title="从支线转出来的活",
                    owner_handle="alice",
                    created_by="alice",
                    status=status,
                    closing_since=now if closing else None,
                    closed_at=now if status == TaskStatus.closed else None,
                )
            )
            await s.commit()

    asyncio.run(_make())
    return thread


def _rows(client, project_id: str, handle: str) -> list[dict]:
    """这个人看到的侧栏：`GET /topics?project_id=`（侧栏读的就是这一条）。"""
    r = client.get(
        f"/topics?project_id={project_id}", headers=session_auth_headers(handle)
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["data"]


def _room_row(client, project_id: str, room_id: str, handle: str) -> dict:
    return next(t for t in _rows(client, project_id, handle) if t["id"] == room_id)


def _task_row(row: dict, task_id: str) -> dict | None:
    """频道那一行下面挂着的活行（`my_tasks.shown`），没有就是 None。"""
    return next((t for t in row["my_tasks"]["shown"] if t["id"] == task_id), None)


def _open(client, handle: str, topic_id: str) -> None:
    r = client.post(
        f"/topics/{topic_id}/read",
        json={"handle": handle},
        headers=session_auth_headers(handle),
    )
    assert r.status_code == 200, r.text


def _join(client, room_id: str, handle: str) -> None:
    """这个人加入这个频道（浏览器里点「加入」那一步）。主线发言要先加入；
    答在支线里不用（`channelJoinToPost`）。"""
    r = client.post(f"/topics/{room_id}/join", headers=session_auth_headers(handle))
    assert r.status_code == 200, r.text


# —— 长绿：做完了、交付完了，而我还没点开看过 ——————————————————————————


def test_a_delivery_lights_the_row_of_everyone_on_the_task_and_of_nobody_else(client):
    """同一件交付，三个人三种颜色 —— 这条判据是按人算的，一个人看到的不算数。"""
    project = _project(client)
    room = _room(client, project)
    ids = _planted(
        client,
        project,
        room,
        [
            {
                "name": "delivered",
                "title": "做完的活",
                "owner_handle": "alice",
                "contributor_handles": ["carol"],
                "accepted_at": datetime.now(UTC) - DELIVERED_AT,
            }
        ],
    )

    # 做这件活的两个人：负责的 alice 和一起做的 carol，都还没点开看过。
    for handle in ("alice", "carol"):
        row = _room_row(client, project, room, handle)
        assert row["unseen_delivery"] is True, handle
        assert _task_row(row, ids["delivered"])["unseen_delivery"] is True, handle

    # bob 在这个项目里，但没碰过这件活：交付了也不该在他侧栏上亮灯，那件活也不算
    # 挂在他名下 —— 他那一行下面纵有那一条，也是侧栏给这个频道补上来的（`filling_in`）。
    bob = _room_row(client, project, room, "bob")
    assert bob["unseen_delivery"] is False
    assert [t for t in bob["my_tasks"]["shown"] if not t.get("filling_in")] == []


def test_opening_the_room_puts_out_the_delivery_for_that_person_only(client):
    """点开频道就灭，别的还没点开的人不受影响。"""
    project = _project(client)
    room = _room(client, project)
    ids = _planted(
        client,
        project,
        room,
        [
            {
                "name": "delivered",
                "title": "做完的活",
                "owner_handle": "alice",
                "contributor_handles": ["carol"],
                "accepted_at": datetime.now(UTC) - DELIVERED_AT,
            }
        ],
    )
    assert _room_row(client, project, room, "alice")["unseen_delivery"] is True

    _open(client, "alice", room)

    alice = _room_row(client, project, room, "alice")
    assert alice["unseen_delivery"] is False
    assert _task_row(alice, ids["delivered"])["unseen_delivery"] is False
    assert _room_row(client, project, room, "carol")["unseen_delivery"] is True


def test_opening_the_task_itself_counts_as_having_seen_the_delivery(client):
    """点开那件活，频道那一行也跟着灭。

    频道要问的是「我在这间房里看过没有」，不只看「我看过那件活没有」：点开活落在活
    自己的已读位上，点开频道落在房间的已读位上，两个都算看过 —— 少认一个，点开活
    回来会看到一个已经看完的交付还在频道行上亮着。
    """
    project = _project(client)
    room = _room(client, project)
    ids = _planted(
        client,
        project,
        room,
        [
            {
                "name": "delivered",
                "title": "做完的活",
                "owner_handle": "alice",
                "accepted_at": datetime.now(UTC) - DELIVERED_AT,
            }
        ],
    )
    assert _room_row(client, project, room, "alice")["unseen_delivery"] is True

    _open(client, "alice", ids["delivered"])

    row = _room_row(client, project, room, "alice")
    assert row["unseen_delivery"] is False
    assert _task_row(row, ids["delivered"])["unseen_delivery"] is False


# —— 黄灯：有人回复了我、我还没处理 ——————————————————————————————————


def _said(client, topic_id: str, handle: str, content: str, **extra) -> dict:
    """某个人在某个地方说了一句（可以带 `reply_to`）。"""
    return post_message(client, topic_id, handle, {"content": content, **extra})


def _answered_in_a_thread(
    client, project: str, *, opened_first: bool = False
) -> tuple[str, str]:
    """alice 在频道里开了条支线、在里面问了一句，芝士在支线里答了她。

    返回 (频道 id, 支线 id)。这是真实的那一幕：芝士答一条主线上的消息，答在**那条
    消息的支线里**，不在主线。`opened_first` 让 alice 在这一切之前先打开一次频道。
    """
    room = _room(client, project)
    if opened_first:
        _open(client, "alice", room)
    thread = in_thread(client, room, "alice")
    asked = _said(client, thread, "alice", "这条要多久？")
    token = mint_scoped_token(project_id=project, topic_id=thread)
    r = client.post(
        f"/topics/{thread}/messages",
        json={
            "content": "大约半小时。",
            "request_id": str(uuid.uuid4()),
            "reply_to": asked["id"],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    return room, thread


def test_a_reply_to_my_message_lights_my_row_and_nobody_elses(client):
    """被回复的那个人那一行亮，同一份数据在别人眼里不亮。"""
    project = _project(client)
    room = _room(client, project)
    _join(client, room, "bob")
    mine = _said(client, room, "alice", "这件事怎么办？")
    _said(client, room, "bob", "我看一下。", reply_to=mine["id"])

    alice = _room_row(client, project, room, "alice")
    assert alice["replied_to_me"] is True
    # 这一位是**新**的一条判据，不是老的那一条：这间房并没有在等我拍板。
    assert alice["awaits_me"] is False
    # bob 说的是别人的话，carol 更没被谁答过。
    assert _room_row(client, project, room, "bob")["replied_to_me"] is False
    assert _room_row(client, project, room, "carol")["replied_to_me"] is False


def test_a_mention_lights_it_too_and_a_plain_message_does_not(client):
    """@ 我算「回复了我」；一句没答我、没点名的话不算。"""
    project = _project(client)
    room = _room(client, project)
    _join(client, room, "bob")

    _said(client, room, "bob", "大家都看下吧")
    assert _room_row(client, project, room, "alice")["replied_to_me"] is False

    _said(client, room, "bob", "<@alice> 你定")
    assert _room_row(client, project, room, "alice")["replied_to_me"] is True


def test_opening_the_channel_puts_it_out_for_that_person_only(client):
    """打开这个频道就算处理了 —— 只灭点开的那个人那一盏。"""
    project = _project(client)
    room = _room(client, project)
    for who in ("bob", "carol"):
        _join(client, room, who)
    first = _said(client, room, "alice", "先做 A")
    _said(client, room, "bob", "A 这版行吗？", reply_to=first["id"])
    second = _said(client, room, "bob", "B 的方案在这儿")
    _said(client, room, "carol", "B 我想想。", reply_to=second["id"])

    assert _room_row(client, project, room, "alice")["replied_to_me"] is True
    assert _room_row(client, project, room, "bob")["replied_to_me"] is True

    _open(client, "alice", room)

    assert _room_row(client, project, room, "alice")["replied_to_me"] is False
    assert _room_row(client, project, room, "bob")["replied_to_me"] is True


def test_a_teammates_answer_in_a_thread_lights_the_channel_row(client):
    """芝士在我那条消息的支线里答我：亮的是那间频道那一行。

    侧栏上那一行是频道，而芝士答一条主线上的消息从来答在支线里（`_answering_in`）：
    只认主线的话，这件事在侧栏上无处可亮。
    """
    project = _project(client)
    room, _thread = _answered_in_a_thread(client, project)

    assert _room_row(client, project, room, "alice")["replied_to_me"] is True


def test_opening_the_channel_puts_out_an_answer_that_landed_in_a_thread(client):
    """打开频道就灭，答在支线里的也一样。"""
    project = _project(client)
    room, _thread = _answered_in_a_thread(client, project)
    assert _room_row(client, project, room, "alice")["replied_to_me"] is True

    _open(client, "alice", room)

    assert _room_row(client, project, room, "alice")["replied_to_me"] is False


def test_opening_the_channel_before_an_answer_lands_does_not_count(client):
    """先看过频道、之后才有人答我 —— 打开的那一下在前，这盏灯还得亮。

    「打开频道就算处理」问的是**哪一下在前**：打开在先、答复在后，就是还没处理。
    """
    project = _project(client)
    room, _thread = _answered_in_a_thread(client, project, opened_first=True)

    assert _room_row(client, project, room, "alice")["replied_to_me"] is True


def test_reading_that_thread_itself_also_counts_as_having_handled_it(client):
    """在那条支线里读过了也算处理了，不必再回频道点一次。

    支线和频道各有一条已读位（`thread.reads` 的规矩），读的是哪一条都算看过这条回复
    —— 只有两处都还没读过，它才继续亮着。
    """
    project = _project(client)
    room, thread = _answered_in_a_thread(client, project)
    assert _room_row(client, project, room, "alice")["replied_to_me"] is True

    _open(client, "alice", thread)

    assert _room_row(client, project, room, "alice")["replied_to_me"] is False


def test_an_answer_inside_a_task_under_the_channel_does_not_light_that_row(client):
    """名下的活里有人回我，不是频道这一行的事 —— 活有它自己那一行。"""
    project = _project(client)
    room = _room(client, project)
    ids = _planted(
        client,
        project,
        room,
        [
            {
                "name": "task",
                "title": "一件活",
                "owner_handle": "alice",
                "contributor_handles": ["bob"],
            }
        ],
    )
    mine = _said(client, ids["task"], "alice", "这版先按这个来")
    _said(client, ids["task"], "bob", "收到，我看一遍。", reply_to=mine["id"])

    assert _room_row(client, project, room, "alice")["replied_to_me"] is False


# —— 红灯的另一半：一轮停在报错上、还没恢复 ————————————————————————————————
#
# 判据在 `block/waits.MemberWaits.unresolved_failures`：一条平台事件说这一轮坏了，
# 直到「那一轮的队友又说话」或者「这里又开了一轮」，它就算还没恢复。接口把**最早那条
# 没恢复的**从什么时候起交给前端，前端按自己的钟判五分钟（`roomLight.ts`）—— 后端不
# 判阈值，只给时刻。

#: 这两个名字的形状就是侧栏认的「芝士说的话」（`indexed_rows.LAST_SAID_ROWS`：署名
#: `cheese` 或 `cheese-…` 的 participant 消息）。一个当「坏了的那一轮是谁的」，另一个
#: 当「别人」。
AGENT = "cheese-0123456789ab"
OTHER_AGENT = "cheese-ba9876543210"


def _platform_event(
    client,
    project_id: str,
    conversation_id: str,
    *,
    ago,
    event_type="turn_failed",
    severity="error",
    turn_id=None,
):
    """平台在某个对话里记下一条事件，返回那一刻。"""
    at = datetime.now(UTC) - ago

    async def _add() -> None:
        async with client.test_factory() as s:
            s.add(
                Block(
                    project_id=uuid.UUID(project_id),
                    conversation_id=uuid.UUID(conversation_id),
                    turn_id=turn_id,
                    kind=BlockKind.message,
                    author_type=AuthorType.platform,
                    author="cheese",
                    content="…",
                    created_at=at,
                    meta={"event_type": event_type, "severity": severity},
                )
            )
            await s.commit()

    asyncio.run(_add())
    return at


def _agent_wrote(
    client,
    project_id: str,
    conversation_id: str,
    *,
    ago,
    handle=AGENT,
    turn_id=None,
):
    """芝士在某个对话里留下一条自己的话 —— 恢复的那一下，也是「哪一轮是谁的」的依据。"""
    at = datetime.now(UTC) - ago

    async def _add() -> None:
        async with client.test_factory() as s:
            s.add(
                Block(
                    project_id=uuid.UUID(project_id),
                    conversation_id=uuid.UUID(conversation_id),
                    turn_id=turn_id,
                    kind=BlockKind.message,
                    author_type=AuthorType.participant,
                    author=handle,
                    content="…",
                    created_at=at,
                )
            )
            await s.commit()

    asyncio.run(_add())
    return at


def _input(client, project_id: str, conversation_id: str, *, ago, read: bool):
    """一份输入写给了这里的会话；``read`` 说会话有没有把它读进去（`echoed_at`）。

    没读的那一态：传输接住了、会话还没读到，或者进程带着它走了 —— 谁也没听见它。
    """
    at = datetime.now(UTC) - ago

    async def _add() -> None:
        async with client.test_factory() as s:
            s.add(
                NativeInput(
                    project_id=uuid.UUID(project_id),
                    conversation_id=uuid.UUID(conversation_id),
                    recipient_handle=AGENT,
                    harness="claude-code",
                    native_session_id="sess-rail",
                    input_id=uuid.uuid4(),
                    work_id=uuid.uuid4(),
                    registered_at=at,
                    accepted_at=at,
                    echoed_at=at if read else None,
                )
            )
            await s.commit()

    asyncio.run(_add())
    return at


def _turn_opened(client, conversation_id: str, *, ago):
    """平台开了一轮：一行 `agent_turns`，连「送到了」都记上 —— 却没有任何会话
    读进过什么。"""
    at = datetime.now(UTC) - ago

    async def _add() -> None:
        async with client.test_factory() as s:
            s.add(
                AgentTurn(
                    id=uuid.uuid4(),
                    conversation_id=uuid.UUID(conversation_id),
                    continuation_id=uuid.uuid4(),
                    author="alice",
                    content="接着干",
                    started_at=at,
                    delivered_at=at,
                )
            )
            await s.commit()

    asyncio.run(_add())
    return at


def _broke(
    client,
    project_id: str,
    conversation_id: str,
    *,
    ago,
    agent=AGENT,
    turn=None,
):
    """一轮停在报错上：芝士在那一轮里写过话（把这一轮认到它头上），平台随即记下坏了。

    返回失败的那一刻。`agent` 换成 `OTHER_AGENT` 或 `None` 就换了这一轮是谁的。
    """
    turn = turn or uuid.uuid4()
    if agent is not None:
        _agent_wrote(
            client,
            project_id,
            conversation_id,
            ago=ago + timedelta(seconds=5),
            handle=agent,
            turn_id=turn,
        )
    return _platform_event(client, project_id, conversation_id, ago=ago, turn_id=turn)


def _incomplete(row: dict) -> datetime | None:
    at = row["incomplete_since"]
    return None if at is None else datetime.fromisoformat(at)


def test_a_failed_turn_lights_the_task_row_and_the_room_above_it(client):
    """一件活停下来报错：它自己那一行亮，上面那间房也跟着亮。

    频道那一格认的是它名下的活（`_unresolved_failures` 把频道自己那条线、支线和名下
    的活合起来取最早的一条）—— 名下一件活报错停下，人得在侧栏上看得见。
    """
    project = _project(client)
    room = _room(client, project)
    ids = _planted(
        client,
        project,
        room,
        [
            {
                "name": "broken",
                "title": "停下来报错的活",
                "owner_handle": "alice",
            },
            {
                "name": "fine",
                "title": "没事的活",
                "owner_handle": "alice",
            },
        ],
    )
    at = _broke(client, project, ids["broken"], ago=timedelta(minutes=9))

    row = _room_row(client, project, room, "alice")
    assert _incomplete(row) is not None
    assert abs(_incomplete(row) - at) < timedelta(seconds=1)
    assert abs(_incomplete(_task_row(row, ids["broken"])) - at) < timedelta(seconds=1)
    # 同一间房下的另一件活没坏：它的行不亮。
    assert _task_row(row, ids["fine"])["incomplete_since"] is None


def test_the_room_own_line_breaking_lights_the_room_row_alone(client):
    """频道自己那条线报错 —— 亮的是频道那一行；它名下没有活，不为谁点灯。"""
    project = _project(client)
    room = _room(client, project)
    at = _broke(client, project, room, ago=timedelta(minutes=9))

    row = _room_row(client, project, room, "alice")
    assert abs(_incomplete(row) - at) < timedelta(seconds=1)


def test_a_failure_in_a_thread_belongs_to_the_room(client):
    """答在支线里的一条坏掉的轮次，亮的还是那间频道 —— 支线是房间自己在说话。"""
    project = _project(client)
    room = _room(client, project)
    thread = in_thread(client, room, "alice")
    at = _broke(client, project, thread, ago=timedelta(minutes=9))

    row = _room_row(client, project, room, "alice")
    assert abs(_incomplete(row) - at) < timedelta(seconds=1)


def test_a_failure_in_another_room_lights_that_room_only(client):
    """别处的失败不落到这间房头上。"""
    project = _project(client)
    broken = _room(client, project, "坏了的房")
    quiet = _room(client, project, "没事的房")
    _broke(client, project, broken, ago=timedelta(minutes=9))

    assert _room_row(client, project, broken, "alice")["incomplete_since"] is not None
    assert _room_row(client, project, quiet, "alice")["incomplete_since"] is None


def test_the_member_speaking_again_clears_it(client):
    """那一轮的队友又说话了：一次成功的重试，或下一轮照常答上 —— 红灯灭。"""
    project = _project(client)
    room = _room(client, project)
    ids = _planted(
        client,
        project,
        room,
        [
            {
                "name": "task",
                "title": "坏过又好了的活",
                "owner_handle": "alice",
            }
        ],
    )
    _broke(client, project, ids["task"], ago=timedelta(minutes=9))
    assert _incomplete(_room_row(client, project, room, "alice")) is not None

    _agent_wrote(client, project, ids["task"], ago=timedelta(minutes=1))

    row = _room_row(client, project, room, "alice")
    assert row["incomplete_since"] is None
    assert _task_row(row, ids["task"])["incomplete_since"] is None


def test_a_session_reading_an_input_there_clears_it_even_without_a_word(client):
    """这里的会话又读进了一份输入 —— 一句话、一条指令、平台替它续上的那一下 ——
    就算它还没说话，坏掉的那一次也不再是当下的事。

    读进去就是「被接手了」；接手的那段工作自己再坏，是**另一条**失败、另一个钟。
    """
    project = _project(client)
    room = _room(client, project)
    ids = _planted(
        client,
        project,
        room,
        [
            {
                "name": "task",
                "title": "重来过的活",
                "owner_handle": "alice",
            }
        ],
    )
    _broke(client, project, ids["task"], ago=timedelta(minutes=9))
    assert _room_row(client, project, room, "alice")["incomplete_since"] is not None

    _input(client, project, ids["task"], ago=timedelta(minutes=1), read=True)

    row = _room_row(client, project, room, "alice")
    assert row["incomplete_since"] is None
    assert _task_row(row, ids["task"])["incomplete_since"] is None


def test_an_input_nobody_read_does_not_clear_the_old_failure(client):
    """写给了会话、会话却没读到 —— 没人接手，旧失败继续亮着。
    两个列表（侧栏与「全部任务」）读的是同一份事实。
    """
    project = _project(client)
    room = _room(client, project)
    ids = _planted(
        client,
        project,
        room,
        [
            {
                "name": "task",
                "title": "没读到所以还没人接手的活",
                "owner_handle": "alice",
            }
        ],
    )
    at = _broke(client, project, ids["task"], ago=timedelta(minutes=9))

    _input(client, project, ids["task"], ago=timedelta(minutes=1), read=False)

    row = _room_row(client, project, room, "alice")
    task = _task_row(row, ids["task"])
    assert abs(_incomplete(row) - at) < timedelta(seconds=1)
    assert abs(_incomplete(task) - at) < timedelta(seconds=1)

    listed = client.get(
        f"/projects/{project}/tasks", headers=session_auth_headers("alice")
    )
    assert listed.status_code == 200, listed.text
    assert abs(_incomplete(_project_task_row(listed, ids["task"])) - at) < timedelta(
        seconds=1
    )


def test_a_turn_the_platform_opened_does_not_clear_it(client):
    """平台开了一轮、连送到都记上了，可哪个会话都没读进什么 —— 开一轮只说明平台
    想让它接着干，不说明有谁接手了：旧失败继续亮着。"""
    project = _project(client)
    room = _room(client, project)
    ids = _planted(
        client,
        project,
        room,
        [
            {
                "name": "task",
                "title": "平台开了一轮的活",
                "owner_handle": "alice",
            }
        ],
    )
    at = _broke(client, project, ids["task"], ago=timedelta(minutes=9))

    _turn_opened(client, ids["task"], ago=timedelta(minutes=1))

    row = _room_row(client, project, room, "alice")
    assert abs(_incomplete(_task_row(row, ids["task"])) - at) < timedelta(seconds=1)


def test_only_an_input_the_session_read_clears_it(client):
    """同一件活：先有一份没读到（红灯不动），读到了才算接手（红灯清掉）。"""
    project = _project(client)
    room = _room(client, project)
    ids = _planted(
        client,
        project,
        room,
        [
            {
                "name": "task",
                "title": "先没读到、后读到了的活",
                "owner_handle": "alice",
            }
        ],
    )
    at = _broke(client, project, ids["task"], ago=timedelta(minutes=9))

    _input(client, project, ids["task"], ago=timedelta(minutes=2), read=False)
    row = _room_row(client, project, room, "alice")
    assert abs(_incomplete(_task_row(row, ids["task"])) - at) < timedelta(seconds=1)

    _input(client, project, ids["task"], ago=timedelta(seconds=30), read=True)
    row = _room_row(client, project, room, "alice")
    assert row["incomplete_since"] is None
    assert _task_row(row, ids["task"])["incomplete_since"] is None


def test_a_closed_task_that_was_a_thread_does_not_light_the_channel(client):
    """支线原地转成的那件活已经关了：`threads` 行还留着，但关了的活不点亮任何一行。

    它没在「还有事的活」里，也不能因为那行 `threads` 又折回频道 —— 那会把一件
    已经关掉的工作的旧错误重新挂到频道上。
    """
    project = _project(client)
    room = _room(client, project)
    task = _converted_thread(client, project, room, status=TaskStatus.closed)
    _broke(client, project, task, ago=timedelta(minutes=9))

    assert _room_row(client, project, room, "alice")["incomplete_since"] is None
    listed = client.get(
        f"/projects/{project}/tasks", headers=session_auth_headers("alice")
    )
    assert listed.status_code == 200, listed.text
    assert _project_task_row(listed, task)["incomplete_since"] is None


def test_a_closing_task_that_was_a_thread_does_not_light_the_channel(client):
    """收尾窗口里的活（最后一步已落地、正在写总结）也不是频道自己的支线。"""
    project = _project(client)
    room = _room(client, project)
    task = _converted_thread(client, project, room, closing=True)
    _broke(client, project, task, ago=timedelta(minutes=9))

    assert _room_row(client, project, room, "alice")["incomplete_since"] is None


def test_an_open_task_that_was_a_thread_still_lights_the_channel(client):
    """转成的那件活还开着：它自己的失败照旧折到频道那一行 —— 名下有一件活出事了。"""
    project = _project(client)
    room = _room(client, project)
    task = _converted_thread(client, project, room)
    at = _broke(client, project, task, ago=timedelta(minutes=9))

    row = _room_row(client, project, room, "alice")
    assert abs(_incomplete(row) - at) < timedelta(seconds=1)
    assert abs(_incomplete(_task_row(row, task)) - at) < timedelta(seconds=1)


def test_what_a_task_that_was_a_thread_says_does_not_clear_the_channel(client):
    """活里说的话不是频道自己的话：它盖不掉频道主线上的旧失败。"""
    project = _project(client)
    room = _room(client, project)
    task = _converted_thread(client, project, room)
    at = _broke(client, project, room, ago=timedelta(minutes=9))
    assert abs(_incomplete(_room_row(client, project, room, "alice")) - at) < (
        timedelta(seconds=1)
    )

    _agent_wrote(client, project, task, ago=timedelta(minutes=1))

    row = _room_row(client, project, room, "alice")
    assert abs(_incomplete(row) - at) < timedelta(seconds=1)
    assert _incomplete(_task_row(row, task)) is None


def test_another_members_output_does_not_clear_it(client):
    """坏了的那一轮是谁的，就等谁：别的队友说话不算接手。"""
    project = _project(client)
    room = _room(client, project)
    ids = _planted(
        client,
        project,
        room,
        [
            {
                "name": "task",
                "title": "还坏着的活",
                "owner_handle": "alice",
            }
        ],
    )
    at = _broke(client, project, ids["task"], ago=timedelta(minutes=9))
    _agent_wrote(
        client, project, ids["task"], ago=timedelta(minutes=1), handle=OTHER_AGENT
    )

    row = _room_row(client, project, room, "alice")
    assert abs(_incomplete(row) - at) < timedelta(seconds=1)
    assert abs(_incomplete(_task_row(row, ids["task"])) - at) < timedelta(seconds=1)


def test_one_task_recovering_does_not_clear_the_room_while_another_stands(client):
    """两件活都坏过：一件好了，另一件没有 —— 房间那一行还亮着，取还在坏的那条的钟。

    合并只发生在画那盏灯的时候，不在「哪几件活还没恢复」上：一件活的恢复只清它自己
    那一行，不能顺手把整间房的线擦掉。
    """
    project = _project(client)
    room = _room(client, project)
    ids = _planted(
        client,
        project,
        room,
        [
            {
                "name": "healed",
                "title": "修好的活",
                "owner_handle": "alice",
            },
            {
                "name": "broken",
                "title": "还坏着的活",
                "owner_handle": "alice",
            },
        ],
    )
    _broke(client, project, ids["healed"], ago=timedelta(minutes=12))
    standing = _broke(client, project, ids["broken"], ago=timedelta(minutes=6))

    _agent_wrote(client, project, ids["healed"], ago=timedelta(minutes=1))

    row = _room_row(client, project, room, "alice")
    assert _task_row(row, ids["healed"])["incomplete_since"] is None
    assert abs(_incomplete(_task_row(row, ids["broken"])) - standing) < timedelta(
        seconds=1
    )
    # 频道那一行取的是还在坏的那条（最早的没恢复的那条就是它）。
    assert abs(_incomplete(row) - standing) < timedelta(seconds=1)


def test_the_room_row_counts_from_the_earliest_of_its_own_line_and_its_tasks(client):
    """频道那一行取它自己那条线和名下各件活里最早的一条 —— 名下的活更晚坏，那算它的；
    自己那条线更早坏，频道就从自己那条线算起。"""
    project = _project(client)
    room = _room(client, project)
    ids = _planted(
        client,
        project,
        room,
        [
            {
                "name": "task",
                "title": "更晚坏掉的活",
                "owner_handle": "alice",
            }
        ],
    )
    own_line = _broke(client, project, room, ago=timedelta(minutes=8))
    task_broke = _broke(client, project, ids["task"], ago=timedelta(minutes=3))

    row = _room_row(client, project, room, "alice")
    assert abs(_incomplete(row) - own_line) < timedelta(seconds=1)
    assert abs(_incomplete(_task_row(row, ids["task"])) - task_broke) < timedelta(
        seconds=1
    )


def test_the_earliest_unrecovered_failure_is_the_one_the_row_counts_from(client):
    """坏过两次、只恢复了前一次：钟从后一次起，不是从第一次。

    「最早一条**还没恢复**的」—— 前一次被队友的话盖过之后就不算了，接着往后看。
    """
    project = _project(client)
    room = _room(client, project)
    _broke(client, project, room, ago=timedelta(minutes=12))
    _agent_wrote(client, project, room, ago=timedelta(minutes=8))
    second = _broke(client, project, room, ago=timedelta(minutes=4))

    row = _room_row(client, project, room, "alice")
    assert abs(_incomplete(row) - second) < timedelta(seconds=1)


def test_a_warning_is_not_an_unfinished_row(client):
    """超时、部署打断是 warn：平台会自己接着跑，不算这一轮还没完成。"""
    project = _project(client)
    room = _room(client, project)
    _platform_event(
        client,
        project,
        room,
        ago=timedelta(minutes=9),
        event_type="turn_timeout",
        severity="warn",
    )
    _platform_event(
        client,
        project,
        room,
        ago=timedelta(minutes=6),
        event_type="platform_error",
        severity="warn",
    )

    assert _room_row(client, project, room, "alice")["incomplete_since"] is None


def test_a_closed_tasks_failure_lights_nothing(client):
    """关了的活不是「没完成的活」：它自己那个错误不该再点亮任何一行。"""
    project = _project(client)
    room = _room(client, project)
    ids = _planted(
        client,
        project,
        room,
        [
            {
                "name": "closed",
                "title": "已经关掉的活",
                "owner_handle": "alice",
                "status": TaskStatus.closed,
                "closed_at": datetime.now(UTC) - timedelta(hours=1),
            }
        ],
    )
    _broke(client, project, ids["closed"], ago=timedelta(minutes=9))

    row = _room_row(client, project, room, "alice")
    assert row["incomplete_since"] is None
    # 关掉的活也不在「我的活」那一栏里，所以它那一行根本不存在。
    assert _task_row(row, ids["closed"]) is None

    for params in ({}, {"status": "closed"}, {"status": "closed", "limit": 5}):
        response = client.get(
            f"/projects/{project}/tasks",
            params=params,
            headers=session_auth_headers("alice"),
        )
        assert response.status_code == 200, response.text
        assert _project_task_row(response, ids["closed"])["incomplete_since"] is None


def test_the_project_task_list_carries_the_same_clock(client):
    """`/projects/{id}/tasks`（「展开剩余任务」那条路）带的是同一份事实。"""
    project = _project(client)
    room = _room(client, project)
    ids = _planted(
        client,
        project,
        room,
        [
            {
                "name": "task",
                "title": "丢掉全部任务时也看得到",
                "owner_handle": "alice",
            }
        ],
    )
    at = _broke(client, project, ids["task"], ago=timedelta(minutes=9))

    headers = session_auth_headers("alice")
    whole = client.get(f"/projects/{project}/tasks", headers=headers)
    assert whole.status_code == 200, whole.text
    row = _project_task_row(whole, ids["task"])
    assert abs(_incomplete(row) - at) < timedelta(seconds=1)

    paged = client.get(
        f"/projects/{project}/tasks",
        params={"status": "open", "limit": 5},
        headers=headers,
    )
    assert paged.status_code == 200, paged.text
    assert abs(_incomplete(_project_task_row(paged, ids["task"])) - at) < timedelta(
        seconds=1
    )


def _project_task_row(response, task_id: str) -> dict:
    return next(t for t in response.json()["data"]["data"] if t["id"] == task_id)


# —— 呼吸绿：有活在往前走 ————————————————————————————————————


def test_a_running_session_lights_the_task_and_its_room(client):
    """任务自己的会话在跑：那一行闪绿，上面那间房也闪绿（房间看它名下的全部对话）。"""
    from app.domain.agent.harness.driven.lifecycle import IDLE, RUNNING
    from app.domain.agent.session_state import SeatKey, declare, register

    project = _project(client)
    room = _room(client, project)
    ids = _planted(
        client,
        project,
        room,
        [{"name": "working", "title": "在跑的活", "owner_handle": "alice"}],
    )
    seat = room_agent_seat(client, room)
    key = SeatKey(uuid.UUID(ids["working"]), seat, "claude-code")

    async def _register() -> None:
        await register(client.test_factory, key)

    def declare_as(status: str) -> None:
        async def _run() -> None:
            async with client.test_factory() as s:
                await declare(s, key, status)
                await s.commit()

        asyncio.run(_run())

    asyncio.run(_register())

    # 还没有会话：两行都不亮。
    row = _room_row(client, project, room, "alice")
    assert row["moving"] is False
    assert _task_row(row, ids["working"])["moving"] is False

    declare_as(RUNNING)
    row = _room_row(client, project, room, "alice")
    assert row["moving"] is True
    assert _task_row(row, ids["working"])["moving"] is True

    # 会话停了就灭：这里没有「五分钟没动静」的等待，running 就是 running。
    declare_as(IDLE)
    row = _room_row(client, project, room, "alice")
    assert row["moving"] is False
    assert _task_row(row, ids["working"])["moving"] is False


def test_a_teammates_reported_working_state_alone_does_not_light_the_row(client):
    """队友自己报「在做」不够：那一位会话已经停了，纸面状态不算在跑。"""
    project = _project(client)
    room = _room(client, project)
    ids = _planted(
        client,
        project,
        room,
        [
            {
                "name": "claimed",
                "title": "说自己在做",
                "owner_handle": "alice",
                "progress_state": TaskProgress.working,
            }
        ],
    )

    row = _room_row(client, project, room, "alice")
    assert row["moving"] is False
    assert _task_row(row, ids["claimed"])["moving"] is False


def test_a_delivery_on_the_chain_lights_the_row_even_with_no_session(client):
    """活已经递出去、走在交付链路上（CI 在跑）：没有会话在跑，也画呼吸绿。"""
    project = _project(client)
    room = _room(client, project)
    ids = _planted(
        client,
        project,
        room,
        [
            {
                "name": "delivering",
                "title": "等 CI 的活",
                "owner_handle": "alice",
                "card": {
                    "status": "pending",
                    "merge_state": {"state": "pending", "who": "ci"},
                },
            }
        ],
    )

    row = _room_row(client, project, room, "alice")
    assert row["moving"] is True
    assert _task_row(row, ids["delivering"])["moving"] is True
