"""怎么送到 —— 人走站内信，agent 读自己房间的时间线。

这是全系统唯一合法的人 / agent 分叉，分的是物理事实（人有浏览器，agent 有一条会
话），不是身份：谁该收到由 `delivery/addressing.py` 答，对两者是同一句话。
"""

import uuid

import pytest

from app.domain.identity.arrival import Arrival, how_it_arrives
from app.domain.identity.handles import (
    CHEESE_HANDLE,
    UNRESOLVED_AGENT_HANDLE,
    agent_instance_handle,
)


@pytest.mark.parametrize(
    "handle",
    [CHEESE_HANDLE, UNRESOLVED_AGENT_HANDLE, agent_instance_handle(uuid.uuid4())],
    ids=["平台那一行芝士", "认不出是哪个 agent", "某个项目里的芝士"],
)
def test_an_agent_reads_it_in_the_room(handle):
    """往 agent 的收件箱里塞一行，写的是一条谁都不会打开的记录。"""
    assert how_it_arrives(handle) is Arrival.turn


@pytest.mark.parametrize("handle", ["alice", "bob", "cheesemonger"])
def test_a_person_gets_it_in_the_mailbox(handle):
    """`cheesemonger` 不是芝士：命名规矩认的是 `cheese` 本身和 `cheese-` 前缀。"""
    assert how_it_arrives(handle) is Arrival.mailbox
