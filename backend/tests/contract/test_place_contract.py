"""The contract between the platform and a place.

Which channel claims a machine that enrolled a given way is decided by a
physical fact about the machine (`supply`), not by a rule each channel writes
for itself — one of those rules was once written inside out.

结论 24（地点是一个可以问「你给得出什么」的东西）。
"""

from app.domain.device.supply import Supply


def channels():
    from app.domain.agent.cloud_provider import CloudChannel
    from app.domain.agent.device_provider import DeviceChannel

    enrolled = DeviceChannel()
    opened = CloudChannel(
        configured=True,
    )
    return enrolled, opened


def test_a_channel_claims_a_machine_by_supply_not_by_a_rule_of_its_own():
    """一条绑定归谁，由机器的供给说，两条通道读同一位。

    以前是各写一条、其中一条反着写（`supply is not cloud`）。轴上多一个取值的那
    天，反着写的那条会把新的那一类一起认领走，而两条通道同时认领一个房间就是把它
    的事件队列劈成两份。
    """
    enrolled, opened = channels()

    for supply in Supply:
        assert enrolled.owns(supply) != opened.owns(supply)
    assert enrolled.owns(Supply.self_hosted)
    assert opened.owns(Supply.cloud)
