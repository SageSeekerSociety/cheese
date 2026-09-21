"""这一轮读哪几个池（`pools_for_turn`）。

守的是结论 54 那一条：关于人的池随时可读。所以这个函数**不认识房间**——它的入参里
没有 topic、没有 is_private、没有任何能用来「这种房间才读」的东西，在场的人给几个
就还几份池。哪天有人想把过滤加回来，第一步一定是先给它加一个这样的参数。
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


def test_it_is_not_told_what_kind_of_room_this_is():
    """签名守卫：只要参数里出现 topic / is_private / private 一类的东西，就是把
    「这种房间才读」的判断又接回来了（结论 54 删掉的正是它）。"""
    import inspect

    params = set(inspect.signature(pools_for_turn).parameters)
    assert params == {"project_id", "agent_handle", "people_present"}
