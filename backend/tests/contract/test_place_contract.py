"""The contract between the platform and a place.

What a place gives out is decided by a physical fact about the machine, not by
which class the channel is. Two questions live on that fact today: can a harness
that keeps its process and its workspace on one machine be hung on this channel
(`HANDS_HERE`), and which channel claims a machine that enrolled this way
(`owns`). Both used to be answered by a rule each channel wrote for itself, and
one of those rules was written inside out.

结论 24（地点是一个可以问「你给得出什么」的东西）、60（手是 agent 的）。
"""

from unittest.mock import AsyncMock

from app.domain.agent import place
from app.domain.device.supply import Supply


def channels():
    from app.domain.agent.central_provider import CentralChannel
    from app.domain.agent.cloud_provider import CloudChannel
    from app.domain.agent.device_provider import DeviceChannel

    enrolled = DeviceChannel()
    opened = CloudChannel(
        configured=True,
        ensure_topic_cloud=AsyncMock(),
        read_topic_cloud=AsyncMock(),
    )
    return enrolled, opened, CentralChannel


def test_hands_here_says_where_the_hands_are_not_which_class_the_channel_is():
    """三个实现填这张表，而且是从物理事实填的。

    `compute.py` 问的是「这双手在不在跑会话的那台机器上」，而中心通道的答案是
    「不在」——手在执行机上，工具要再跳一程。按类问，问出来的是恒真：那两条能进
    compute 池的通道都继承 `DeviceChannel`。
    """
    enrolled, opened, central = channels()

    assert enrolled.capabilities() == frozenset({place.HANDS_HERE})
    assert opened.capabilities() == frozenset({place.HANDS_HERE})
    # 包一层中心通道，手就搬到执行机上去了——这是这张表上唯一会变的那一位。
    assert central(enrolled).capabilities() == frozenset()
    assert central(opened).capabilities() == frozenset()


def test_a_channel_claims_a_machine_by_supply_not_by_a_rule_of_its_own():
    """一条绑定归谁，由机器的供给说，两条通道读同一位。

    以前是各写一条、其中一条反着写（`supply is not cloud`）。轴上多一个取值的那
    天，反着写的那条会把新的那一类一起认领走，而两条通道同时认领一个房间就是把它
    的事件队列劈成两份。
    """
    enrolled, opened, _ = channels()

    for supply in Supply:
        assert enrolled.owns(supply) != opened.owns(supply)
    assert enrolled.owns(Supply.self_hosted)
    assert opened.owns(Supply.cloud)
