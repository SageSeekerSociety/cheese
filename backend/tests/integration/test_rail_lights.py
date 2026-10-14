"""侧栏每一行那盏灯的事实：`GET /topics?project_id=` 的每一行带哪几个判据。

画哪一盏灯是前端那个纯函数的事（`frontend/src/lib/roomLight.ts`），灯要问的四件事
却都是后端算的，而且**都按人算**：换一个人看，同一间房、同一件活可以是别的颜色。
所以这个文件里每条都从两个人的眼里看同一份数据 —— 只有一个人看得见的说法，光看
一个人的响应是钉不住的。

五条事实：这里在不在往前走（`moving`，频道那一行还含它名下的活）、在跑的那一轮
上一次有输出是什么时候（`last_output_at`）、有没有事在等我（`awaits_me`，这里只
当对照）、做完了交付完了而我还没点开看过（`unseen_delivery`），以及有人回复了我、
我还没处理（`replied_to_me`）。

活的行由 `_planted` 直接种进库，不走「建任务 → 递卡 → 采纳」那一圈：每一圈自己的
规矩在别处钉着（`test_accept.py`），而这里要对下来的是「哪几列推出哪盏灯」。判据
本身在 `room_task/presentation.py` 和 `agent/liveness.py`，各有单测
（`tests/unit/test_presentation.py`）；这个文件问的是另一个问题 —— 接口把它算出来
了没有。
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from app.api.deps import get_work_runner
from app.core.sandbox_auth import mint_scoped_token
from app.domain.agent.models import AgentTurn
from app.domain.review.models import AcceptCard, AcceptCardReviewer, AcceptStatus
from app.domain.review.notes import NoteCode
from app.domain.room_task.models import Task
from app.domain.room_task.presentation import Building
from app.main import app
from tests.integration.conftest import (
    in_thread,
    join_project_team,
    post_message,
    post_project,
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
    `card` 是它此刻骑的那张卡、`turn` 说它自己的线上有一轮在跑。
    """

    async def _run() -> None:
        async with client.test_factory() as s:
            for spec in specs:
                spec = dict(spec)
                name = spec.pop("name")
                card = spec.pop("card", None)
                turn = spec.pop("turn", False)
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
                if turn:
                    now = datetime.now(UTC)
                    s.add(
                        AgentTurn(
                            id=uuid.uuid4(),
                            conversation_id=task.id,
                            continuation_id=uuid.uuid4(),
                            author="alice",
                            content="开始吧",
                            started_at=now,
                            delivered_at=now,
                        )
                    )
                ids[name] = str(task.id)
            await s.commit()

    ids: dict[str, str] = {}
    asyncio.run(_run())
    return ids


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
                "started_at": datetime.now(UTC) - timedelta(hours=2),
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
                "started_at": datetime.now(UTC) - timedelta(hours=2),
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
                "started_at": datetime.now(UTC) - timedelta(hours=2),
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
        headers={"X-Cheese-Token": token},
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
                "started_at": datetime.now(UTC),
            }
        ],
    )
    mine = _said(client, ids["task"], "alice", "这版先按这个来")
    _said(client, ids["task"], "bob", "收到，我看一遍。", reply_to=mine["id"])

    assert _room_row(client, project, room, "alice")["replied_to_me"] is False


# —— 呼吸绿：这里有没有活在往前走 ————————————————————————————————————


def test_a_room_breathes_while_a_task_under_it_rides_a_delivery(client):
    """房间自己那条线闲着，名下的活走在交付链路上 —— 那也在这间房在往前走。

    这一半是侧栏专门为频道补的：一条活在等 CI 时，上面那间房通常是安静的，缺了这
    一半它就一直不亮。
    """
    project = _project(client)
    riding = _room(client, project, "递出去了")
    waiting = _room(client, project, "等着我")
    riding_ids = _planted(
        client,
        project,
        riding,
        [
            {
                "name": "riding",
                "title": "在等检查的活",
                "owner_handle": "alice",
                "started_at": datetime.now(UTC),
                "card": {
                    "status": AcceptStatus.pending,
                    "note_code": NoteCode.waiting_merge_queue,
                    "pr_number": 42,
                },
            }
        ],
    )
    waiting_ids = _planted(
        client,
        project,
        waiting,
        [
            {
                "name": "waiting",
                "title": "等我采纳的活",
                "owner_handle": "alice",
                "started_at": datetime.now(UTC),
                "card": {"status": AcceptStatus.pending},
            }
        ],
    )

    row = _room_row(client, project, riding, "alice")
    # 房间自己那条线不在往前走 —— 灯来自它名下的活。
    assert row["presentation"]["column"] == "building"
    assert row["moving"] is True
    assert _task_row(row, riding_ids["riding"])["moving"] is True

    # 反面：一张等我去点的卡是「有事等我」（黄灯），不是「在往前走」（呼吸绿）——
    # 两者要的下一步正好相反：去点，还是别催。
    blocked = _room_row(client, project, waiting, "alice")
    assert blocked["moving"] is False
    task = _task_row(blocked, waiting_ids["waiting"])
    assert task["moving"] is False
    assert task["awaits_me"] is True


def test_a_running_turn_breathes_and_the_room_says_nothing_about_its_clock(client):
    """有一轮在跑就是呼吸绿；多久没输出这一栏，说不出就是 null。

    这一请求跑的进程没有握着那一轮（`silent_of` 问的是内存里那份当下事实），所以
    `last_output_at` 是 null。这不是漏了：不知道不当成「很新」，也不当成卡住 ——
    红灯要有一个时刻才画得出来。
    """
    project = _project(client)
    room = _room(client, project, "在跑")
    ids = _planted(
        client,
        project,
        room,
        [
            {
                "name": "running",
                "title": "在跑的活",
                "owner_handle": "alice",
                "started_at": datetime.now(UTC),
                "turn": True,
            }
        ],
    )

    row = _room_row(client, project, room, "alice")
    # 看板那一格只说房间**自己那条线**（另一条测试钉着：名下的活在跑时它不变）；
    # 「名下的活在跑」只进那盏呼吸绿。
    assert row["presentation"]["phrase"] == Building.idle
    assert row["moving"] is True
    assert row["last_output_at"] is None
    task = _task_row(row, ids["running"])
    assert task["moving"] is True
    assert task["last_output_at"] is None


class _TurnStillPublishing:
    """一轮还在往外送帧：只有握着它的那个进程说得出「多久没输出了」。

    真的轮次跨不过一次阻塞的 HTTP 调用，所以这里替一个 —— 真 runner 会这样报，
    在 `tests/unit/test_runtime.py` 里另外钉着。
    """

    def __init__(self, *, silent_for_s: float) -> None:
        self._silent_for_s = silent_for_s

    def running_topic_ids(self) -> set[str]:
        return set()

    def live_work_for_topic(self, topic_id: uuid.UUID) -> dict | None:
        return {"turn_id": "t-1", "silent_for_s": self._silent_for_s}

    def topic_work(self, topic_id: uuid.UUID) -> dict | None:
        return None

    def active_work_count(self) -> int:
        return 1

    async def project_queue_depth(self, project_id: uuid.UUID | str) -> int:
        return 0


def test_the_process_holding_the_turn_says_when_it_last_output(client):
    """有人说得出那一刻时，行上就带着那个时刻 —— 取整到分钟。

    灯判的是五分钟，而这一栏要能进清单的条件请求（整份 body 的指纹）：一整帧一个
    时间戳会让「有任何一轮在跑」期间每一次轮询都命中不了 304。取整把它压到一分钟
    最多变一次。
    """
    project = _project(client)
    room = _room(client, project, "在跑")
    _planted(
        client,
        project,
        room,
        [
            {
                "name": "running",
                "title": "在跑的活",
                "owner_handle": "alice",
                "started_at": datetime.now(UTC),
                "turn": True,
            }
        ],
    )

    app.dependency_overrides[get_work_runner] = lambda: _TurnStillPublishing(
        silent_for_s=13 * 60
    )
    try:
        row = _room_row(client, project, room, "alice")
    finally:
        app.dependency_overrides.pop(get_work_runner, None)

    at = datetime.fromisoformat(row["last_output_at"])
    assert (at.second, at.microsecond) == (0, 0)
    quiet_for = datetime.now(UTC) - at
    assert timedelta(minutes=13) <= quiet_for < timedelta(minutes=14)
