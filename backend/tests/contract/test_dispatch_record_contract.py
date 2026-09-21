"""平台侧执行记录与重派路径之间的契约（结论 57，ARCH 6.5）。

一次工具调用的落点以前只有两个：结果，或者异常。中间那一个 —— 发出去了，而结果永远
不会回来了 —— 今天有了名字，重派路径按它分叉。三条：

1. 发出后崩，重启时这条记录是 ``unknown``；
2. ``unknown`` 的操作不被自动重发，而是产生一条送到人面前的事件；
3. ``failed``（确定没发出去）的可以直接重派。

第 1 条不经过路由：崩溃这件事就是「记录已经提交，写回它的那个进程没了」，而一个能在
这两步中间死掉的路由替身，除了模拟同一件事之外什么也不多说。第 2、3 条走真的扫底
路径 —— 要断言的正是它读没读这条记录。
"""

import asyncio
import uuid

import pytest

from app.domain.agent import dispatch_log
from app.domain.agent.runtime import AgentWorkRunner, InProcessBroker
from tests.turn_log import a_topic, open_turn

# 在任何测试把 `asyncio.sleep` 换掉之前拿住真的那个：重派是一个先睡 3 秒再发的任务，
# 而它要等的那次落库是真的数据库往返。
_REAL_SLEEP = asyncio.sleep


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


async def _dispatched(factory, topic_id, *, key: str, method: str = "invoke"):
    """派出去一次，然后这个进程就没了 —— 记录提交了，结果没人写回来。"""
    async with factory() as session:
        dispatch = dispatch_log.record(
            session, place_id=topic_id, key=key, method=method
        )
        await session.commit()
    return dispatch


@pytest.mark.anyio
async def test_a_dispatch_whose_process_died_reads_unknown(db_factory):
    """①发出后崩，重启时这条记录是 `unknown`。

    `unknown` 是唯一一个写不出来的结果：该写它的那个进程，正是没了的那个。所以库里
    它是「没有人写回来」，读出来才是 `unknown` —— 而不是某处写下的一句猜测。
    """
    topic = await a_topic(db_factory)
    await _dispatched(db_factory, topic, key="tool-1", method="invoke")

    async with db_factory() as session:
        pending = await dispatch_log.unsettled(session, topic)

    assert [(d.key, d.method, d.outcome) for d in pending] == [
        ("tool-1", "invoke", dispatch_log.Outcome.unknown)
    ]


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
    await open_turn(db_factory, topic, content="把迁移跑上去", age_s=90)
    await _dispatched(db_factory, topic, key="tool-1", method="invoke")
    chat = _Chat(db_factory)
    runner = AgentWorkRunner(InProcessBroker())

    assert await runner.resume_orphans(chat) == 0
    await _let_a_resend_land(chat, rounds=50)

    assert chat.converse_calls == []
    assert [meta["event_type"] for _, _, meta in chat.events] == ["dispatch_unknown"]
    _, text, meta = chat.events[0]
    assert meta["who"] == "human"
    # 人要确认的是哪一次调用，这句话里得说得出来。
    assert "tool-1" in meta["detail"]
    assert "重试" in text

    # 问过一次就结清：同一个人不该在这个房间此后每一次扫底里被问同一件事。
    async with db_factory() as session:
        assert await dispatch_log.unsettled(session, topic) == []


@pytest.mark.anyio
async def test_a_failed_dispatch_does_not_stand_in_the_way_of_a_resend(
    db_factory, monkeypatch
):
    """③`failed`（确定没发出去）的可以直接重派。

    链路不在、或者机器回话说它没能转交 —— 这些是答复，说的是「没有受理」。什么都没
    发生过的调用不该把这条活扣在人手上。
    """
    _instant_sleep(monkeypatch)
    topic = await a_topic(db_factory)
    await open_turn(db_factory, topic, content="把迁移跑上去", age_s=90)
    dispatch = await _dispatched(db_factory, topic, key="tool-1", method="invoke")
    async with db_factory() as session:
        await dispatch_log.settle(session, dispatch, dispatch_log.Outcome.failed)
        await session.commit()
    chat = _Chat(db_factory)
    runner = AgentWorkRunner(InProcessBroker())

    assert await runner.resume_orphans(chat) == 1
    await _let_a_resend_land(chat)

    assert [call["content"] for call in chat.converse_calls] == ["把迁移跑上去"]
    assert chat.events == []
