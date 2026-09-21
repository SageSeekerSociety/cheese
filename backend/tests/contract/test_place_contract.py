"""The contract between the platform and a place.

One side real (`agent/place.py`: the states, the term, the receipt gate, the
capability table), the other side a double (`tests/support/ledger_place.py`,
whose machine is a ledger). What is pinned here is that a lease has three
states and not two, that the way out of each of them is decided by what the
place CAN do rather than by which class it is, and that the three receipts are
taken on the way to 已归还 and on no other transition.

结论 24（地点是有期限、可归还的租约，回收前三张收据）、39（休眠是第三态，只对
平台自己开的机器，这条路上不取收据）、40 与 55（到期是感知不是决策）。
不变量 I19、I25。
"""

from datetime import timedelta

import pytest

from app.domain.agent import place
from app.domain.device.supply import Supply
from tests.support.ledger_place import LedgerPlace


async def test_a_lease_starts_in_use_and_carries_its_own_term():
    """在用：期限跟着租约走，而且只有这一个来源。

    没有第二个各自为政的超时常量可以让它提前结束，也没有第二处可以延长它——问「这
    份租约到期没有」只有 `Lease.expired` 一种问法。
    """
    hands = LedgerPlace()
    lease = await hands.acquire(timedelta(hours=2))

    assert lease.state is place.LeaseState.in_use
    assert lease.expired(hands.now) is False
    assert lease.expired(hands.now + timedelta(hours=2)) is True

    forever = await hands.acquire(None)
    assert forever.expires_at is None
    assert forever.expired(hands.now + timedelta(days=365)) is False


async def test_expiry_arrives_as_one_event_and_never_as_a_swapped_machine():
    """到期是一条送到 agent 面前的事件，不是平台替它做的一个决定。

    两件事一起断言，因为拆开任何一件都还能悄悄成立：读到的那句话里没有裸 HTTP 状
    态码（它要知道的是这一轮还剩哪些通路，不是「有东西坏了」），而这一条分类明说
    它不算在机器头上——按时长把一台好机器隔离掉，正是结论 55 否掉的那种决策。
    """
    from app.domain.agent.platform_failures import classify_platform_failure

    hands = LedgerPlace()
    lease = await hands.acquire(timedelta(seconds=1))

    expired = place.LeaseExpired(lease)
    said = str(expired)

    assert not any(str(status) in said for status in (400, 403, 404, 500, 502, 503))
    failure = classify_platform_failure(expired)
    assert failure is not None
    assert failure.meta["event_type"] == "platform_error"
    assert failure.host_scoped is False
    # 租约还在它自己手上：抛一条到期，机器一台也没换。
    assert hands.calls == ["acquire"]


async def test_sleeping_takes_no_receipts_and_leaves_the_workspace_where_it_is():
    """休眠：第三态，且这条路上一张收据都不取（结论 39）。

    把休眠也套上收据，等于每次闲置都跑一遍记忆整理——而休眠期间 transcript、记忆和
    没推的改动一样都没有离开那台机器。reconnect window 之内醒来是同一台机器、工作
    区原样，所以这里连工作区一起断言。
    """
    hands = LedgerPlace(
        receipts=place.Receipts()  # 一张都没有
    )
    hands.workspace["notes.md"] = "半截的活"
    lease = await hands.acquire(timedelta(hours=2))

    asleep = await hands.sleep(lease)
    assert asleep.state is place.LeaseState.asleep
    assert asleep.wakeable(hands.now) is True

    awake = await hands.wake(asleep)
    assert awake.state is place.LeaseState.in_use
    assert awake.machine == lease.machine
    assert hands.workspace == {"notes.md": "半截的活"}
    assert hands.calls == ["acquire", "sleep", "wake"]


async def test_past_the_reconnect_window_a_sleeping_lease_can_only_be_returned():
    """window 过了才转成归还——而归还那一档要的是三张收据。"""
    hands = LedgerPlace()
    lease = await hands.acquire(None)
    asleep = await hands.sleep(lease)

    hands.now = hands.now + hands.reconnect_window + timedelta(minutes=1)
    assert asleep.wakeable(hands.now) is False
    with pytest.raises(RuntimeError):
        await hands.wake(asleep)


async def test_returning_needs_all_three_receipts_and_says_which_one_is_missing():
    """已归还：三张一张不能少，缺哪张就说哪张。

    机器一删，缺的那一张再也补不回来，所以收据不齐的正确结果是停在原地并说明理
    由，不是删了再说。
    """
    for absent, said in (
        (place.Receipts(memory_tidied=True, work_published=True), "transcript"),
        (place.Receipts(transcript_stored=True, work_published=True), "记忆整理"),
        (place.Receipts(transcript_stored=True, memory_tidied=True), "推上去"),
    ):
        hands = LedgerPlace(receipts=absent)
        hands.workspace["notes.md"] = "半截的活"
        lease = await hands.acquire(None)
        with pytest.raises(RuntimeError, match=said):
            await hands.release(lease)
        # 没归还成，工作区一个字没动。
        assert hands.workspace == {"notes.md": "半截的活"}
        assert hands.calls == ["acquire", "release-refused"]

    hands = LedgerPlace()
    hands.workspace["notes.md"] = "做完了"
    lease = await hands.acquire(None)
    receipts = await hands.release(lease)
    assert receipts.missing() == ()
    assert hands.workspace == {}


async def test_only_a_machine_the_platform_opened_gives_out_the_third_state():
    """第三态从供给推出来，不从类名推。

    人接入的机器平台停不了，所以它给不出休眠——它能声明的只有「在用」和「已归
    还」。而平台自己开的那台，销毁权和休眠是同一位 `supply` 的两个推论。
    """
    borrowed = LedgerPlace(supply=Supply.self_hosted)
    opened = LedgerPlace(supply=Supply.cloud)

    assert place.CAN_SLEEP not in borrowed.capabilities()
    assert place.PLATFORM_MAY_DESTROY not in borrowed.capabilities()
    assert {place.CAN_SLEEP, place.PLATFORM_MAY_DESTROY} <= opened.capabilities()

    with pytest.raises(RuntimeError):
        await borrowed.sleep(await borrowed.acquire(None))


def test_the_production_places_declare_the_same_three_capabilities():
    """三个实现填满这张表，而且是从物理事实填的。

    这是上游能读能力位的前提：`compute.py` 问的是「这双手在不在跑会话的那台机器
    上」，而中心通道的答案是「不在」——手在执行机上，工具要再跳一程。按类问过一次，
    问出来的是恒真，因为那两个都继承 `DeviceChannel`。
    """
    from unittest.mock import AsyncMock

    from app.domain.agent.central_provider import CentralChannel
    from app.domain.agent.cloud_provider import CloudChannel
    from app.domain.agent.device_provider import DeviceChannel

    enrolled = DeviceChannel()
    opened = CloudChannel(
        configured=True,
        ensure_topic_cloud=AsyncMock(),
        read_topic_cloud=AsyncMock(),
    )
    central = CentralChannel(enrolled)

    assert enrolled.capabilities() == frozenset({place.HANDS_HERE})
    assert opened.capabilities() == frozenset(
        {place.HANDS_HERE, place.CAN_SLEEP, place.PLATFORM_MAY_DESTROY}
    )
    assert central.capabilities() == frozenset()
    # 中心通道包住谁，销毁权就跟着谁：手是那台机器的。
    assert CentralChannel(opened).capabilities() == frozenset(
        {place.CAN_SLEEP, place.PLATFORM_MAY_DESTROY}
    )


def test_a_channel_claims_a_machine_by_supply_not_by_a_rule_of_its_own():
    """一条绑定归谁，由机器的供给说，两条通道读同一位。

    以前是各写一条、其中一条反着写（`supply is not cloud`）。轴上多一个取值的那
    天，反着写的那条会把新的那一类一起认领走，而两条通道同时认领一个房间就是把它
    的事件队列劈成两份。
    """
    from unittest.mock import AsyncMock

    from app.domain.agent.cloud_provider import CloudChannel
    from app.domain.agent.device_provider import DeviceChannel

    enrolled = DeviceChannel()
    opened = CloudChannel(
        configured=True,
        ensure_topic_cloud=AsyncMock(),
        read_topic_cloud=AsyncMock(),
    )
    for supply in Supply:
        assert enrolled.owns(supply) != opened.owns(supply)
    assert enrolled.owns(Supply.self_hosted)
    assert opened.owns(Supply.cloud)
