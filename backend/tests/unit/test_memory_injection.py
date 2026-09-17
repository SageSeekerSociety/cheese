"""Memory injection: core is carried, the rest is counted, and the count is
said out loud.

The failure being covered is "memory is missing and nothing says so" — a reader
that cannot tell an empty pool from a partial one concludes there is nothing to
know, which is exactly how a written-down pitfall gets walked into anyway.

Injection used to also carry a budget's worth of facts ranked against the turn,
which is what the pool-size line is now the only trace of: everything past core
is reached with `recall`, so the line that names the number is the only thing
standing between a stored fact and never being asked for.
"""

from types import SimpleNamespace

import pytest

from app.domain.agent.harness.prompt import build_system_prompt
from app.domain.memory.models import MemoryScope
from app.domain.memory.store import recall_pools

pytestmark = pytest.mark.anyio


def _store(**pools: list[str]):
    """A MemoryStore with named pools, keyed by scope_id, oldest first.

    A fact written as ``"core:某条"`` belongs to the core layer; everything else
    is an ordinary fact, which injection counts and does not carry.
    """

    async def recall_core(scope: MemoryScope, scope_id: str) -> list[str]:
        return [
            f.removeprefix("core:") for f in pools[scope_id] if f.startswith("core:")
        ]

    async def count(scope: MemoryScope, scope_id: str) -> int:
        return len(pools[scope_id])

    return SimpleNamespace(recall_core=recall_core, count=count)


# --- core: in every turn, whatever the turn is about ----------------------


async def test_core_is_carried_and_everything_else_is_only_counted():
    store = _store(p=["core:我是芝士", "core:说人话"] + [f"事实{i}" for i in range(5)])

    got = await recall_pools(store, [(MemoryScope.project, "p")])

    assert got.facts == ["我是芝士", "说人话"]
    assert got.omitted == 5


async def test_core_over_its_own_budget_is_reported_not_silently_dropped():
    store = _store(p=[f"core:核心{i}" + "长" * 200 for i in range(10)])

    got = await recall_pools(store, [(MemoryScope.project, "p")], core_char_budget=600)

    assert 0 < len(got.facts) < 10
    assert got.core_omitted == 10 - len(got.facts)
    assert got.omitted == got.core_omitted


async def test_one_core_fact_survives_a_budget_that_fits_none_of_it():
    """An empty core layer reads as "this agent has no identity", which is worse
    than one oversized line — so one always goes in. The newest one: core is
    filled newest-first, and what was written last is the current answer to
    「你是谁」."""
    store = _store(p=["core:" + "长" * 5000, "core:第二条"])

    got = await recall_pools(store, [(MemoryScope.project, "p")], core_char_budget=100)

    assert got.facts == ["第二条"]
    assert got.core_omitted == 1


async def test_nothing_is_reported_missing_when_the_pool_is_only_core():
    store = _store(p=["core:只有这一条"])

    got = await recall_pools(store, [(MemoryScope.project, "p")])

    assert got.facts == ["只有这一条"]
    assert got.omitted == 0


async def test_both_pools_report_into_one_total():
    store = _store(
        own=["core:我是芝士"] + [f"我的{i}" for i in range(30)],
        shared=[f"共享{i}" for i in range(59)],
    )

    got = await recall_pools(
        store, [(MemoryScope.agent_project, "own"), (MemoryScope.project, "shared")]
    )

    assert got.facts == ["我是芝士"]
    assert got.omitted == 89


# --- what the prompt says about all this ----------------------------------


def test_the_prompt_names_the_pool_it_did_not_bring():
    """The one line that keeps a stored fact reachable. Without it the prompt
    looks complete, and a prompt that looks complete is one nobody searches."""
    prompt = build_system_prompt("base", "", None, ["记住这条"], memories_omitted=9)

    assert "记住这条" in prompt
    assert "9" in prompt
    assert "cheese_recall" in prompt


def test_no_notice_when_nothing_was_left_out():
    prompt = build_system_prompt("base", "", None, ["记住这条"])

    assert "记住这条" in prompt
    assert "cheese_recall" not in prompt


def test_core_overflow_gets_its_own_warning():
    """A fact left to `recall` is normal. A missing *core* fact means the layer
    that is supposed to be unconditional has stopped being unconditional."""
    prompt = build_system_prompt(
        "base", "", None, ["我是芝士"], memories_core_omitted=3
    )

    assert "核心记忆超预算" in prompt
    assert "3" in prompt


# --- 路径要以引用的形状进 prompt ------------------------------------------


def test_a_bare_path_in_a_fact_arrives_as_a_reference_token():
    """模型照抄它在 prompt 里看到的形状：裸路径进去，裸路径就会出现在它写的文档和
    回复里，而裸路径在前端点不开。"""
    prompt = build_system_prompt("base", "", None, ["配置在 backend/app/core/db.py 里"])

    assert "<&backend/app/core/db.py>" in prompt


def test_the_lines_a_fact_points_at_stay_inside_the_token():
    """`db.py:55-60` 指的是文件里的一段。行号被留在尖括号外面，token 就只剩半截——
    前端认出来的是文件，而那条事实真正在说的是那六行。"""
    prompt = build_system_prompt(
        "base", "", None, ["连接池在 backend/app/core/db.py:55-60"]
    )

    assert "<&backend/app/core/db.py:55-60>" in prompt
