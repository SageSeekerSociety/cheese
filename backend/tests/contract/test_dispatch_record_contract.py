"""平台侧执行记录与重派路径之间的契约（结论 57，ARCH 6.5）。

一次工具调用的落点以前只有两个：结果，或者异常。中间那一个 —— 发出去了，而结果永远
不会回来了 —— 今天有了名字，重派路径按它分叉。三条：

1. 发出后崩，重启时这条记录是 ``unknown``；
2. ``unknown`` 的操作不被自动重发，而是产生一条送到人面前的事件；
3. ``failed``（确定没被执行）的可以直接重派。

第 1 条不经过路由：崩溃这件事就是「记录已经提交，写回它的那个进程没了」，而一个能在
这两步中间死掉的路由替身，除了模拟同一件事之外什么也不多说。第 2、3 条走真的扫底
路径 —— 要断言的正是它读没读这条记录。

重派路径问的是「**这几轮**里有什么悬着」，不是「这个房间有史以来」。一次超时留下的
空行会一直空着（那一轮照常跑完，没有谁会再碰它），而几天后它不该顶掉另一轮一次合法
的重发。

这里的时刻按真实时序摆：崩溃和属主重启发生在派发之后几秒到几分钟内，启动扫底紧跟着
就跑。所以这几条用例里的记录都比一次调用能在飞的 660 秒年轻得多 —— 把判据换回「够老
才算悬着」，第 ①②条当场全红。

哪一种落点被记成什么，则只有走真的路由才问得出来。那套分类整个长在
``api/routes/execution.py`` 的 except 上，而它最要命的一档 —— 帧已经写出去了，之后
链路才断 —— 从异常的类型上看和「链路一开始就不在」一模一样。所以下半篇打真的
``POST /topics/{id}/execution/{resource}``，中间放一个真的 ``DeviceHub``：发出前与
发出后的分界是它画的，替它的身就等于把答案自己写进测试里。
"""

import asyncio
import base64
import json
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.db import get_db
from app.core.sandbox_auth import mint_scoped_token
from app.domain.agent import dispatch_log, execution
from app.domain.agent.device_hub import DeviceHub
from app.domain.agent.harness import deployment_harness
from app.domain.agent.runtime import AgentWorkRunner, InProcessBroker
from app.domain.agent_session.models import AgentSession
from app.domain.project.services import ProjectService
from app.domain.topic.services import TopicService
from tests.turn_log import a_topic, open_turn

# 在任何测试把 `asyncio.sleep` 换掉之前拿住真的那个：重派是一个先睡 3 秒再发的任务，
# 而它要等的那次落库是真的数据库往返。
_REAL_SLEEP = asyncio.sleep

#: 被打断的那一轮多老。两分钟：一次部署打断一轮，启动扫底跟着就跑，这是它常见的样
#: 子。必须比它自己派出去的那些调用还老 —— 一次派发是那一轮做的，不可能早于那一轮开
#: 始，而重派路径正是按这个把「这几轮里悬着的」和上一轮遗留的空行分开的
#: （`dispatch_log.unsettled` 的 `since`）。
_TURN_AGE_S = 120

#: 卡死那一档里，那一轮多老：机器死在它手上，它于是一个字都不再输出，直到
#: `SILENT_TURN_S` 判它卡死。
_WEDGED_TURN_AGE_S = 4000


def _a_wide_window() -> datetime:
    """直接问这张表时给的窗口起点，宽到装得下这条用例摆的那些行。

    真的重派路径给的是这个话题里最早那个孤儿轮次的开始时刻（见 `runtime.py`）。
    """
    return datetime.now(UTC) - timedelta(days=1)


class _Chat:
    """ChatService 替身，只留扫底这条路径问到的那几件事。

    `tests/unit/test_orphan_sweep_attach.py` 有一个更全的同类（它还要验收养、spool
    收口）。这里刻意只留这几个方法：这一份要断言的是「派出去的东西有没有被再派一
    次」，多一个方法就多一处它可以从别的地方绿掉。
    """

    def __init__(self, factory):
        self.session_factory = factory
        self.events: list[tuple[uuid.UUID, str, dict]] = []
        self.converse_calls: list[dict] = []

    async def post_system_event(self, topic_id, text, turn_id=None, meta=None):
        self.events.append((topic_id, text, meta or {}))
        return {"id": "b1", "content": text}

    def has_live_screen(self, topic_id):
        # 屏幕还在，但它从没听到这条消息 —— 这正是平台今天会原样重发的那一档。
        return True

    async def turns_that_produced_something(self, turn_ids):
        return set()

    def schedule_spool_settle(self, topic_id, delay_s=2.0):
        pass

    async def converse(self, **kw):
        self.converse_calls.append(kw)
        yield {"type": "done"}


def _instant_sleep(monkeypatch) -> None:
    async def _instant(_delay, *a, **k):
        await _REAL_SLEEP(0)

    monkeypatch.setattr(asyncio, "sleep", _instant)


async def _let_a_resend_land(chat: _Chat, rounds: int = 300) -> None:
    for _ in range(rounds):
        await _REAL_SLEEP(0.01)
        if chat.converse_calls:
            return


async def _dispatched(
    factory,
    topic_id,
    *,
    key: str,
    tool: str = "Bash",
    ago_s: float = 60,
):
    """派出去一次，然后这个进程就没了 —— 记录提交了，结果没人写回来。

    ``ago_s`` 默认一分钟：属主是在派发之后一分钟没的，而不是十一分钟之后。这一档才
    是真实时序，也是这份记录唯一有机会拦住重发的那一档。
    """
    async with factory() as session:
        dispatch = dispatch_log.record(session, place_id=topic_id, key=key, tool=tool)
        await session.flush()
        row = await session.get(dispatch_log.DispatchRow, dispatch)
        row.dispatched_at = datetime.now(UTC) - timedelta(seconds=ago_s)
        await session.commit()
    return dispatch


async def _outcomes(factory, topic_id) -> list[tuple[str, str | None]]:
    """这个房间里每一行记录：键，和写回去的结果（``None`` = 没有人写回来）。"""
    async with factory() as session:
        rows = (
            (
                await session.execute(
                    select(dispatch_log.DispatchRow)
                    .where(dispatch_log.DispatchRow.place_id == topic_id)
                    .order_by(dispatch_log.DispatchRow.dispatched_at)
                )
            )
            .scalars()
            .all()
        )
        return [(row.key, row.outcome) for row in rows]


@pytest.mark.anyio
async def test_a_dispatch_whose_process_died_reads_unknown(db_factory):
    """①发出后崩，重启时这条记录是 `unknown`。

    `unknown` 是唯一一个写不出来的结果：该写它的那个进程，正是没了的那个。所以库里
    它是「没有人写回来」，读出来才是 `unknown` —— 而不是某处写下的一句猜测。
    """
    topic = await a_topic(db_factory)
    await _dispatched(db_factory, topic, key="tool-1", tool="Bash")

    async with db_factory() as session:
        window = _a_wide_window()
        pending = await dispatch_log.unsettled(session, topic, since=window)

    assert [(d.key, d.tool, d.outcome) for d in pending] == [
        ("tool-1", "Bash", dispatch_log.Outcome.unknown)
    ]


@pytest.mark.anyio
async def test_a_row_is_settled_once_and_a_late_answer_does_not_erase_it(db_factory):
    """结清是一次性的：迟到的结果不许抹掉已经交到人手上的那一行。

    属主的调用最终回来了，而平台早已判它未知、房间里也已经有人照着那条通知在查。
    让那个 `done` 盖上去，留下的是一行说「都办妥了」的记录和一个仍然被要求去确认的
    人，而没有任何地方还留着他为什么被叫来。
    """
    topic = await a_topic(db_factory)
    dispatch = await _dispatched(db_factory, topic, key="tool-1")
    async with db_factory() as session:
        await dispatch_log.settle(session, dispatch, dispatch_log.Outcome.unknown)
        await session.commit()

    async with db_factory() as session:
        await dispatch_log.settle(session, dispatch, dispatch_log.Outcome.done)
        await session.commit()

    assert await _outcomes(db_factory, topic) == [("tool-1", "unknown")]


@pytest.mark.anyio
async def test_an_unknown_dispatch_is_handed_to_a_person_instead_of_resent(
    db_factory, monkeypatch
):
    """②`unknown` 的操作不被自动重发，而是产生一条要人确认的事件。

    这个房间里的轮次没送到任何人 —— 换在这条记录之前，平台会把原话原样再发一次。发
    不得：那次工具调用可能已经落地了，再跑一轮就是把它再做一遍。
    """
    _instant_sleep(monkeypatch)
    topic = await a_topic(db_factory)
    await open_turn(db_factory, topic, content="把迁移跑上去", age_s=_TURN_AGE_S)
    await _dispatched(db_factory, topic, key="tool-1", tool="Bash")
    chat = _Chat(db_factory)
    runner = AgentWorkRunner(InProcessBroker())

    assert await runner.resume_orphans(chat) == 0
    await _let_a_resend_land(chat, rounds=50)

    assert chat.converse_calls == []
    assert [meta["event_type"] for _, _, meta in chat.events] == ["dispatch_unknown"]
    _, text, meta = chat.events[0]
    assert meta["who"] == "human"
    # 人要确认的是哪一次调用，这句话里得说得出来：哪个工具，哪一次。「invoke」不是
    # 一句话 —— 带 id 的调用只有它一种。
    assert "Bash（tool-1）" in meta["detail"]
    assert "重试" in text
    assert await _outcomes(db_factory, topic) == [("tool-1", "unknown")]

    # 问过一次就结清：同一个人不该在这个房间此后每一次扫底里被问同一件事。
    async with db_factory() as session:
        window = _a_wide_window()
        assert await dispatch_log.unsettled(session, topic, since=window) == []


@pytest.mark.anyio
async def test_a_wedged_room_is_told_which_calls_are_in_doubt(db_factory, monkeypatch):
    """机器整台死掉 —— 这张表的旗舰场景 —— 房间照样要听见这句话。

    那台机器死在这一轮手上，于是这一轮在 `SILENT_TURN_S` 之后被判卡死。换在这之前，
    卡死的话题走完自己那一档就不再往下走了：悬着的调用既不通知也不结清，房间里只剩
    一条「平台不会自动重试……@ 芝士，它会从断点接着做」，一个字没提有一次写可能已经
    落地一半 —— 恰好在结论 57 最该说话的那一档落空。
    """
    _instant_sleep(monkeypatch)
    topic = await a_topic(db_factory)
    turn = await open_turn(
        db_factory, topic, content="把迁移跑上去", age_s=_WEDGED_TURN_AGE_S
    )
    await _dispatched(db_factory, topic, key="tool-1", tool="Bash")
    chat = _Chat(db_factory)
    runner = AgentWorkRunner(InProcessBroker())

    async def _never():
        await asyncio.Event().wait()

    task = asyncio.create_task(_never())
    await _REAL_SLEEP(0)
    runner._live[str(turn)] = task
    runner._last_frame_at[str(turn)] = time.monotonic() - 4000

    async def _last_activity(_topics):
        return {topic: datetime.now(UTC) - timedelta(seconds=4000)}

    assert await runner.sweep_orphans(chat, last_activity=_last_activity) == 0
    await _let_a_resend_land(chat, rounds=50)

    assert chat.converse_calls == []
    assert [meta["event_type"] for _, _, meta in chat.events] == [
        "turn_timeout",
        "dispatch_unknown",
    ]
    assert "tool-1" in chat.events[1][2]["detail"]
    assert await _outcomes(db_factory, topic) == [("tool-1", "unknown")]
    task.cancel()


@pytest.mark.anyio
async def test_a_failed_dispatch_does_not_stand_in_the_way_of_a_resend(
    db_factory, monkeypatch
):
    """③`failed`（确定没被执行）的可以直接重派。

    链路不在，或者执行器回话说这个 id 上已经有别的输入 —— 两档都挡在执行之前。什么
    都没发生过的调用不该把这条活扣在人手上。
    """
    _instant_sleep(monkeypatch)
    topic = await a_topic(db_factory)
    await open_turn(db_factory, topic, content="把迁移跑上去", age_s=_TURN_AGE_S)
    dispatch = await _dispatched(db_factory, topic, key="tool-1", tool="Bash")
    async with db_factory() as session:
        await dispatch_log.settle(session, dispatch, dispatch_log.Outcome.failed)
        await session.commit()
    chat = _Chat(db_factory)
    runner = AgentWorkRunner(InProcessBroker())

    assert await runner.resume_orphans(chat) == 1
    await _let_a_resend_land(chat)

    assert [call["content"] for call in chat.converse_calls] == ["把迁移跑上去"]
    assert chat.events == []


@pytest.mark.anyio
async def test_a_row_left_over_from_an_earlier_turn_does_not_eat_a_resend(
    db_factory, monkeypatch
):
    """上一轮留下的空行不该顶掉这一轮一次合法的重发。

    一次超时在房间里留下一行永远不会有人写回来的记录：沙箱把 504 当一次工具报错交给
    模型，那一轮照常跑完，没有任何路径会再碰那一行。几天后这个房间的另一轮被一次部
    署打断，那一轮本该被原样重发。读整个房间的话，读到的是那行陈年的空记录：重发被
    掐掉，房间里换成一条指着几天前那次早就结束的调用的通知 —— 用户这一次的消息真丢
    了，而提示说的是别的事。
    """
    _instant_sleep(monkeypatch)
    topic = await a_topic(db_factory)
    await _dispatched(db_factory, topic, key="old-tool", ago_s=3 * 86400)
    await open_turn(db_factory, topic, content="把迁移跑上去", age_s=_TURN_AGE_S)
    chat = _Chat(db_factory)
    runner = AgentWorkRunner(InProcessBroker())

    assert await runner.resume_orphans(chat) == 1
    await _let_a_resend_land(chat)

    assert [call["content"] for call in chat.converse_calls] == ["把迁移跑上去"]
    assert chat.events == []


# --- 落点 → 记成什么：走真的路由 ------------------------------------------------

_MACHINE = "machine-7"


class _Link:
    """一台机器的链路替身：帧一写进来就算出去了，之后这台机器怎么样由 ``answer`` 说。

    只替链路的身，不替 hub 的身。发出前与发出后的分界整个长在 ``call_executor``
    里，拿一个直接抛异常的假 hub 来测，测的是测试自己写下的答案。
    """

    def __init__(self, hub: DeviceHub, answer=None) -> None:
        self.hub = hub
        self.answer = answer
        self.sent: list[dict] = []

    async def send_json(self, msg: dict) -> None:
        self.sent.append(msg)
        if msg.get("t") != "execution.call" or self.answer is None:
            return
        await self.answer(self, msg["id"])


async def _answers_with_a_result(link: _Link, call_id: str) -> None:
    """机器收下了，跑完了，把结果送回来。"""
    body = json.dumps({"result": {"value": "ok"}}).encode()
    await link.hub.on_device_message(
        _MACHINE,
        {
            "t": "execution.data",
            "id": call_id,
            "data": base64.b64encode(body).decode(),
        },
    )
    await link.hub.on_device_message(_MACHINE, {"t": "execution.result", "id": call_id})


async def _answers_with_a_failure(link: _Link, call_id: str) -> None:
    """机器收下了，然后回话说这次调用出了错 —— 执行器那句「已受理」就长这样。"""
    await link.hub.on_device_message(
        _MACHINE,
        {
            "t": "execution.result",
            "id": call_id,
            "error": "Request accepted; outcome is pending or unknown. "
            "Do not replay with a new ID",
        },
    )


async def _drops_the_link(link: _Link, _call_id: str) -> None:
    """帧已经写进 socket，然后这台机器没了 —— 6.5 的「突然损坏」。

    ``drop_transport`` 把在飞的 executor future 全置成 ``DeviceOffline``，这是这一
    档在代码里的真实走法，也是它和「链路一开始就不在」在类型上分不开的原因。
    """
    link.hub._devices[_MACHINE].drop_transport(link)


class _NeverAnswers:
    """只答应一件事：这次调用等到超时。

    一次真的超时要等 ``device_hub`` 的那个时限，测试等不起；而超时这一档里没有
    「发出前还是发出后」可分，所以这里替 hub 的身不会把答案写进测试。
    """

    async def call_executor(self, *a, **kw) -> dict:
        raise TimeoutError("the machine holds its link and does not answer")


async def _a_room_with_hands(factory) -> tuple[uuid.UUID, uuid.UUID, str]:
    """一个房间、一条坐在里面的会话、它租到的那台机器，和这个房间的凭据。"""
    async with factory() as session:
        project = await ProjectService(session).create(
            name="P", owner_handle="u", forge_kind="github_app"
        )
        topic = await TopicService(session).create(
            project_id=project.id, title="T", created_by="u"
        )
        session.add(
            AgentSession(
                topic_id=topic.id,
                agent_handle="cheese",
                harness=deployment_harness(),
                runtime_location={
                    "device_id": _MACHINE,
                    "channel": "device",
                    "resource_id": str(topic.id),
                },
                work_lease={
                    "kind": "device",
                    "device_id": _MACHINE,
                    "home": "/home/cheese",
                    "state": "/home/cheese/.cheese/executor",
                },
            )
        )
        await session.commit()
        token = mint_scoped_token(
            project_id=str(project.id),
            topic_id=str(topic.id),
            resource_id=str(topic.id),
        )
        return project.id, topic.id, token


@pytest.fixture
async def owner_client(db_factory):
    """真的执行路由，架在真的属主 app 上 —— 它只挂在那儿（`device_connection_app`）。"""
    from app.device_connection_app import app as owner_app

    async def _db():
        async with db_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    owner_app.dependency_overrides[get_db] = _db
    async with AsyncClient(
        transport=ASGITransport(app=owner_app), base_url="http://owner"
    ) as client:
        yield client
    owner_app.dependency_overrides.clear()


async def _an_attached_machine(monkeypatch, answer=None) -> DeviceHub:
    hub = DeviceHub()
    link = _Link(hub, answer)
    await hub.attach_device(_MACHINE, link)
    await hub.on_device_message(_MACHINE, {"t": "hello", "executor": True})
    monkeypatch.setattr(execution, "device_hub", hub)
    return hub


async def _invoke(client: AsyncClient, topic_id, token: str, *, key: str = "tool-1"):
    return await client.post(
        f"/topics/{topic_id}/execution/{topic_id}",
        json={
            "method": "invoke",
            "params": {"id": key, "tool": "Bash", "args": {"command": "git push"}},
        },
        headers={"x-cheese-token": token},
    )


@pytest.mark.anyio
async def test_a_call_the_machine_answered_is_recorded_done(
    db_factory, owner_client, monkeypatch
):
    """机器答了，就是 `done` —— 这一行不再悬着，也不会去问任何人。"""
    _, topic, token = await _a_room_with_hands(db_factory)
    await _an_attached_machine(monkeypatch, _answers_with_a_result)

    response = await _invoke(owner_client, topic, token)

    assert response.status_code == 200
    assert await _outcomes(db_factory, topic) == [("tool-1", "done")]


@pytest.mark.anyio
async def test_a_call_that_never_left_the_platform_is_recorded_failed(
    db_factory, owner_client, monkeypatch
):
    """链路不在，帧一个字节都没出去 —— 只有这一档能说「确定没发生」。"""
    _, topic, token = await _a_room_with_hands(db_factory)
    monkeypatch.setattr(execution, "device_hub", DeviceHub())

    response = await _invoke(owner_client, topic, token)

    assert response.status_code == 409
    assert await _outcomes(db_factory, topic) == [("tool-1", "failed")]


@pytest.mark.anyio
async def test_a_link_that_died_after_the_frame_left_is_left_unsettled(
    db_factory, owner_client, monkeypatch
):
    """负向对照：帧出去之后链路才断，那就不是 `failed`。

    抛出来的异常和上一条一模一样（``DeviceOffline``），而这一次那台机器已经拿到了
    这次调用。记成 `failed` 的话，扫底会原样再发一遍 —— 同一次 git push 做两遍，而
    库里还留着一行声称它没发生过。
    """
    _, topic, token = await _a_room_with_hands(db_factory)
    await _an_attached_machine(monkeypatch, _drops_the_link)

    response = await _invoke(owner_client, topic, token)

    assert response.status_code == 409
    assert await _outcomes(db_factory, topic) == [("tool-1", None)]
    # 而对重派路径来说它当场就是那件要人确认的事：屏幕没了的房间里，一次没写回来的
    # 调用，结果再也到不了 agent 面前。
    async with db_factory() as session:
        window = _a_wide_window()
        pending = await dispatch_log.unsettled(session, topic, since=window)
    assert [d.outcome for d in pending] == [dispatch_log.Outcome.unknown]


@pytest.mark.anyio
async def test_a_machine_that_answered_with_a_failure_is_left_unsettled(
    db_factory, owner_client, monkeypatch
):
    """机器回话说这次调用出了错 —— 它收下过，所以这不是「没有受理」。

    执行器把任何 dispatch 异常包成 `{"error": …}` 回来，其中就有它自己那句
    「Request accepted; outcome is pending or unknown. Do not replay with a new
    ID」。那正是「已经受理、结果未知」，记成 `failed` 是把它读反。
    """
    _, topic, token = await _a_room_with_hands(db_factory)
    await _an_attached_machine(monkeypatch, _answers_with_a_failure)

    response = await _invoke(owner_client, topic, token)

    assert response.status_code == 502
    assert await _outcomes(db_factory, topic) == [("tool-1", None)]


@pytest.mark.anyio
async def test_a_call_that_timed_out_is_left_unsettled(
    db_factory, owner_client, monkeypatch
):
    """超时之后这次调用做没做过，这一侧不知道 —— 写下一个猜出来的结果最坏。"""
    _, topic, token = await _a_room_with_hands(db_factory)
    monkeypatch.setattr(execution, "device_hub", _NeverAnswers())

    response = await _invoke(owner_client, topic, token)

    assert response.status_code == 504
    assert await _outcomes(db_factory, topic) == [("tool-1", None)]


async def _answers_that_the_id_is_taken(link: _Link, call_id: str) -> None:
    """机器回话说这个 id 上已经有一行，而输入不是这一个 —— ``invoke`` 入口的那一句。"""
    await link.hub.on_device_message(
        _MACHINE,
        {
            "t": "execution.result",
            "id": call_id,
            "error": "Request ID already belongs to different input",
        },
    )


@pytest.mark.anyio
async def test_a_call_the_executor_refused_to_take_is_recorded_failed(
    db_factory, owner_client, monkeypatch
):
    """执行器说这个 id 上已经有别的输入 —— 它挡在 `invoke` 碰这次调用之前。

    和上一条一样是「机器回话说它失败了」，抛的也是同一个异常，而这一句说的是这次调
    用没有被执行。留着不结清，房间里会多出一件其实什么都没发生的事要人去确认。
    """
    _, topic, token = await _a_room_with_hands(db_factory)
    await _an_attached_machine(monkeypatch, _answers_that_the_id_is_taken)

    response = await _invoke(owner_client, topic, token)

    assert response.status_code == 502
    assert await _outcomes(db_factory, topic) == [("tool-1", "failed")]


async def _a_settle_that_cannot_reach_the_database(*_a, **_kw) -> None:
    """结清那一步失败：池子被占满的样子，路由上面那段注释记的就是那次事故。"""
    raise OSError("connection pool is exhausted")


@pytest.mark.anyio
async def test_a_settle_that_fails_does_not_change_what_the_call_answered(
    db_factory, owner_client, monkeypatch
):
    """结不上账，不许把一次已经回来的结果变成 500，也不许盖掉机器自己的话。

    这次结清是在远端调用结束**之后**才重新向池子要的那一次连接，而每一次工具调用都要
    一次。它抛出来会毁掉两样东西：``done`` 那一档排在 ``return answer`` 前面，一次已
    经 200 回来的结果变成 500 交给沙箱；``failed`` 那一档排在 ``raise`` 前面，409 连
    同要带进房间的机器原话一起退化成一个未处理的 500。留着不结清的代价只是这一行读出
    来是 ``unknown``，那本来就是它的默认那一档。
    """
    _, topic, token = await _a_room_with_hands(db_factory)
    monkeypatch.setattr(
        dispatch_log, "settle", _a_settle_that_cannot_reach_the_database
    )
    await _an_attached_machine(monkeypatch, _answers_with_a_result)

    answered = await _invoke(owner_client, topic, token, key="tool-1")

    monkeypatch.setattr(execution, "device_hub", DeviceHub())
    unreachable = await _invoke(owner_client, topic, token, key="tool-2")

    assert answered.status_code == 200
    assert unreachable.status_code == 409
    assert await _outcomes(db_factory, topic) == [("tool-1", None), ("tool-2", None)]


@pytest.mark.anyio
async def test_a_call_without_an_id_records_nothing(
    db_factory, owner_client, monkeypatch
):
    """不带 id 的调用按执行器自己的协议问两遍和问一遍一样，不留记录。

    留一行，一次超时的探活就会变成一件要人确认的事。
    """
    _, topic, token = await _a_room_with_hands(db_factory)
    monkeypatch.setattr(execution, "device_hub", _NeverAnswers())

    response = await owner_client.post(
        f"/topics/{topic}/execution/{topic}",
        json={"method": "ping", "params": {}},
        headers={"x-cheese-token": token},
    )

    assert response.status_code == 504
    assert await _outcomes(db_factory, topic) == []
