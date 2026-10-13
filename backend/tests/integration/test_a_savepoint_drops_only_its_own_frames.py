"""一条 savepoint 回滚只丢它自己排的帧，释放一条更不是提交。

页面上的实时帧攒在会话的 `session.info` 里，等会话提交才发出去 —— 三处各有一个
槽位：平台说的话（`agent/live_notices` 的 `SHOW_ONCE_COMMITTED`，`room_task/live`
把任务的帧排进同一个槽）、项目频道列表（`agent/project_feed`）、验收卡
（`review/live`）。回滚了就不发。

#3511 挡住了这件事的一半：`after_commit` 在 savepoint 被释放时也会响，而在那时发
帧，另一个连接读到的还是提交之前的库。三处在 `after_commit` 里各加了一句
`in_nested_transaction()` 就返回 —— 但三处的 `after_rollback` 照旧把整个槽位 pop
掉，于是

    排一帧 → 释放一个 savepoint → 回滚**另一个** savepoint → 提交

提交成功、库里也写进去了，帧却一帧不剩：释放时被挡着没发，回滚时被整槽清掉，提交
时没得发。释放过的 savepoint 排的帧也是这样被一次无关的回滚带走的。反过来只在回滚
那侧也加一句 savepoint 判断是另一种错：释放过的 savepoint 排的帧那时已经交给外层
了，会被当成「它回滚掉了」而永远发不出去。

所以帧按 savepoint 分桶（`core/live_frames`）：每层 savepoint 一桶，回滚只丢自己那
一桶（连同它里面已经释放的那几层），其余等会话自己那次提交一起发。下面三节各钉一
处槽位，每节都能在把分桶去掉、或者把那句「释放不是提交」去掉时变红。
"""

import asyncio
import uuid

import pytest
from sqlalchemy import text

from app.core.live_frames import show_once_committed
from app.domain.agent import live_notices, project_feed
from app.domain.review import live as review_live
from app.domain.review.models import AcceptCard, AcceptStatus
from tests.integration.conftest import session_auth_headers
from tests.integration.test_accept import _make_project, _make_topic


class _Published:
    """What a listener handed the broker, in order, instead of a broker."""

    def __init__(self) -> None:
        self.frames: list[tuple[str, dict]] = []

    async def publish(self, channel: str, frame: dict) -> None:
        self.frames.append((channel, frame))


@pytest.fixture
def published(monkeypatch) -> _Published:
    broker = _Published()
    monkeypatch.setattr(live_notices, "get_broker", lambda: broker)
    monkeypatch.setattr(review_live, "get_broker", lambda: broker)
    return broker


@pytest.fixture
def told(monkeypatch) -> list[tuple]:
    """What `project_feed` asked `tell_project` to say, instead of saying it."""
    said: list[tuple] = []

    async def tell(conversation_id, *resources, **kwargs):
        said.append((conversation_id, resources))

    monkeypatch.setattr(project_feed, "tell_project", tell)
    return said


# —— 平台说的话：agent/live_notices 的 SHOW_ONCE_COMMITTED ————————————————

_CHANNEL = "room-under-test"
_NOTICE = {"type": "event_block", "id": "announcement", "block": {"id": "one"}}


def test_a_released_savepoint_waits_for_the_session_s_commit(
    db_session, _portal, published
):
    async def scenario() -> None:
        show_once_committed(db_session, _CHANNEL, dict(_NOTICE))
        async with db_session.begin_nested():
            pass  # released: it commits nothing
        await asyncio.sleep(0.05)  # a frame published here would have gone out by now
        assert published.frames == [], "a released savepoint is not a commit"
        await db_session.commit()
        await asyncio.sleep(0.05)

    _portal.call(scenario)
    assert published.frames == [(_CHANNEL, _NOTICE)]


def test_another_savepoint_s_rollback_leaves_the_frame(db_session, _portal, published):
    """回滚一个 savepoint，不该把别的 savepoint 排的帧一起清掉。"""

    async def scenario() -> None:
        show_once_committed(db_session, _CHANNEL, dict(_NOTICE))
        async with db_session.begin_nested():
            pass  # released: the frame is the transaction's now
        other = await db_session.begin_nested()
        await db_session.execute(text("SELECT 1"))
        await other.rollback()
        await db_session.commit()
        await asyncio.sleep(0.05)

    _portal.call(scenario)
    assert published.frames == [(_CHANNEL, _NOTICE)], "一次无关的回滚把帧带走了"


def test_a_rolled_back_savepoint_s_frame_is_never_published(
    db_session, _portal, published
):
    async def scenario() -> None:
        inside = await db_session.begin_nested()
        show_once_committed(db_session, _CHANNEL, dict(_NOTICE))
        await inside.rollback()
        await db_session.commit()
        await asyncio.sleep(0.05)

    _portal.call(scenario)
    assert published.frames == []


def test_an_outer_rollback_takes_the_released_inner_frame_with_it(
    db_session, _portal, published
):
    """库里那笔写没留住，为它排的帧也就没有了，哪怕中间那层已经释放。"""

    async def scenario() -> None:
        outer = await db_session.begin_nested()
        inner = await db_session.begin_nested()
        show_once_committed(db_session, _CHANNEL, dict(_NOTICE))
        await inner.commit()  # released into the outer savepoint
        await outer.rollback()  # undoes the writes the frame is about
        await db_session.commit()
        await asyncio.sleep(0.05)

    _portal.call(scenario)
    assert published.frames == []


# —— 项目频道列表：agent/project_feed 的 _ONCE_COMMITTED ———————————————————


def test_the_project_feed_waits_for_the_session_s_commit(db_session, _portal, told):
    conversation = uuid.uuid4()

    async def scenario() -> None:
        project_feed.tell_project_once_committed(
            db_session, conversation, project_feed.TOPICS
        )
        async with db_session.begin_nested():
            pass  # released
        await asyncio.sleep(0.05)
        assert told == [], "a released savepoint is not a commit"
        await db_session.commit()
        await asyncio.sleep(0.05)

    _portal.call(scenario)
    assert told == [(conversation, (project_feed.TOPICS,))]


def test_another_savepoint_s_rollback_leaves_the_feed(db_session, _portal, told):
    conversation = uuid.uuid4()

    async def scenario() -> None:
        project_feed.tell_project_once_committed(
            db_session, conversation, project_feed.TOPICS
        )
        other = await db_session.begin_nested()
        await db_session.execute(text("SELECT 1"))
        await other.rollback()
        await db_session.commit()
        await asyncio.sleep(0.05)

    _portal.call(scenario)
    assert told == [(conversation, (project_feed.TOPICS,))], "一次无关的回滚把帧带走了"


def test_a_rolled_back_savepoint_s_feed_is_never_sent(db_session, _portal, told):
    conversation = uuid.uuid4()

    async def scenario() -> None:
        inside = await db_session.begin_nested()
        project_feed.tell_project_once_committed(
            db_session, conversation, project_feed.TOPICS
        )
        await inside.rollback()
        await db_session.commit()
        await asyncio.sleep(0.05)

    _portal.call(scenario)
    assert told == []


# —— 验收卡：review/live 的 _PENDING ——————————————————————————————————————


@pytest.fixture
def signed_in(client):
    client.headers.update(session_auth_headers("alice"))
    yield client
    client.headers.pop("Authorization", None)


def _a_card(client) -> tuple[str, str]:
    """一个房间和它上面的一张待审阅卡：卡直接写进库，够监听器认出来就行。"""
    project = _make_project(client)
    room = _make_topic(client, project)

    async def _file() -> str:
        async with client.test_factory() as session:
            card = AcceptCard(
                topic_id=uuid.UUID(room),
                reviewer_handle="alice",
                status=AcceptStatus.pending,
                pr_number=1,
            )
            session.add(card)
            await session.commit()
            return str(card.id)

    return asyncio.run(_file()), room


def _change_the_card(client, card_id: str, dance) -> None:
    """把 ``dance`` 在卡上做的事写下去，然后提交。

    提交之后让一跳，帧是发在提交之后的（`background.spawn`）。
    """

    async def _run() -> None:
        async with client.test_factory() as session:
            card = await session.get(AcceptCard, uuid.UUID(card_id))
            assert card is not None
            await dance(session, card)
            await session.commit()
            await asyncio.sleep(0.05)

    asyncio.run(_run())


_ACCEPT_AND_TASKS = [
    {"type": "state", "resource": "accept"},
    {"type": "state", "resource": "tasks"},
]


def test_a_released_savepoint_waits_for_the_card_s_commit(signed_in, published):
    card_id, room = _a_card(signed_in)
    published.frames.clear()

    async def scenario(session, card) -> None:
        async with session.begin_nested():
            card.merge_state = {"state": "blocked"}
            await session.flush()  # 卡变了，帧排在这一层 savepoint 里
        await asyncio.sleep(0.05)
        assert published.frames == [], "a released savepoint is not a commit"

    _change_the_card(signed_in, card_id, scenario)
    assert published.frames == [(room, frame) for frame in _ACCEPT_AND_TASKS]


def test_another_savepoint_s_rollback_leaves_the_card_s_pages(signed_in, published):
    card_id, room = _a_card(signed_in)
    published.frames.clear()

    async def scenario(session, card) -> None:
        async with session.begin_nested():
            card.merge_state = {"state": "blocked"}
            await session.flush()
        other = await session.begin_nested()
        await session.execute(text("SELECT 1"))
        await other.rollback()

    _change_the_card(signed_in, card_id, scenario)
    assert published.frames == [(room, frame) for frame in _ACCEPT_AND_TASKS], (
        "一次无关的回滚把帧带走了"
    )


def test_a_rolled_back_savepoint_s_card_change_tells_nobody(signed_in, published):
    card_id, _room = _a_card(signed_in)
    published.frames.clear()

    async def scenario(session, card) -> None:
        inside = await session.begin_nested()
        card.merge_state = {"state": "blocked"}
        await session.flush()
        await inside.rollback()

    _change_the_card(signed_in, card_id, scenario)
    assert published.frames == []


def test_a_card_staged_in_two_savepoints_tells_its_pages_once(signed_in, published):
    """同一段对话在两个 savepoint 里各排了一次，页面还是只被告知一次。"""
    card_id, room = _a_card(signed_in)
    published.frames.clear()

    async def scenario(session, card) -> None:
        async with session.begin_nested():
            card.merge_state = {"state": "blocked"}
            await session.flush()
        card.merge_state = {"state": "clean"}
        await session.flush()

    _change_the_card(signed_in, card_id, scenario)
    assert published.frames == [(room, frame) for frame in _ACCEPT_AND_TASKS]
