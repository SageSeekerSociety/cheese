"""这一轮读哪几个池（`pools_for_turn`）。

守的是结论 54 那一条：关于人的池随时可读——在场的人给几个就还几份池，不因为房间
是什么类型而少还一份。「私聊和普通房间读到的东西」那一侧由
`tests/integration/test_a_pool_belongs_to_one_instance_in_one_project.py` 按注入到
system prompt 里的内容来守，这里守的是键怎么拼。
"""

import uuid

from app.domain.memory.models import (
    MemoryScope,
    agent_project_scope_id,
    user_scope_id,
)
from app.domain.memory.pools import pools_for_turn

PROJECT = uuid.UUID("11111111-1111-1111-1111-111111111111")
OTHER = uuid.UUID("22222222-2222-2222-2222-222222222222")


def test_its_own_pool_comes_first_and_is_always_there():
    """一个人都没有的时候，读的仍然是它自己在这个项目里学到的东西。"""
    assert pools_for_turn(PROJECT, "cheese-abc", []) == [
        (MemoryScope.agent_project, agent_project_scope_id(PROJECT, "cheese-abc"))
    ]


def test_every_person_present_gets_a_pool_in_roster_order():
    pools = pools_for_turn(PROJECT, "cheese-abc", ["alice", "bob"])
    assert pools[1:] == [
        (MemoryScope.user, user_scope_id(PROJECT, "cheese-abc", "alice")),
        (MemoryScope.user, user_scope_id(PROJECT, "cheese-abc", "bob")),
    ]


def test_the_same_person_twice_is_one_pool():
    """名册和线程名册各算一次的时候，同一份 core 层不该被接进提示词两遍。"""
    pools = pools_for_turn(PROJECT, "cheese-abc", ["alice", "alice", ""])
    assert len(pools) == 2


def test_two_projects_never_name_the_same_pool():
    """同一位芝士的 handle、同一个人，两个项目拼出来的键必须不同——跨项目读不到
    这件事，靠的就是「别的项目的键在这里拼不出来」（结论 8）。"""
    here = pools_for_turn(PROJECT, "cheese-abc", ["alice"])
    there = pools_for_turn(OTHER, "cheese-abc", ["alice"])
    assert {scope_id for _, scope_id in here} & {
        scope_id for _, scope_id in there
    } == set()
