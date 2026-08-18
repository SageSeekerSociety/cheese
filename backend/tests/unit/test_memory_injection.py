"""Memory injection: core is unconditional, the rest earns its seat, and what
did not come in is said out loud.

Two failures are covered here. The old one is "memory is missing and nothing
says so" — a reader that cannot tell an empty pool from a truncated one
concludes there is nothing to know, which is exactly how a written-down pitfall
gets walked into anyway. The new one is a pool that outgrew the prompt: when
only half of it fits, taking the newest half hands the seats to whatever was
written last, which has nothing to do with what the turn is about.
"""

from types import SimpleNamespace

import pytest

from app.domain.agent.chat import _build_system_prompt
from app.domain.memory.models import MemoryScope
from app.domain.memory.store import recall_pools

pytestmark = pytest.mark.anyio


def _store(**pools: list[str]):
    """A MemoryStore with named pools, keyed by scope_id, oldest first.

    A fact written as ``"core:某条"`` belongs to the core layer. Ranking stands
    in for the real backend's keyword coverage with the crudest thing that has
    the same shape: a fact containing the query scores 1.0, everything else 0.0,
    newest first within a score. What is under test here is what injection does
    with a ranking, not how the ranking is computed.
    """

    def _split(scope_id: str) -> tuple[list[str], list[str]]:
        core = [
            f.removeprefix("core:") for f in pools[scope_id] if f.startswith("core:")
        ]
        rest = [f for f in pools[scope_id] if not f.startswith("core:")]
        return core, rest

    async def recall_core(scope: MemoryScope, scope_id: str) -> list[str]:
        return _split(scope_id)[0]

    async def rank_facts(
        scope: MemoryScope, scope_id: str, query: str, limit: int = 500
    ) -> list[tuple[float, str]]:
        newest_first = list(reversed(_split(scope_id)[1]))[:limit]
        scored = [(1.0 if query and query in f else 0.0, f) for f in newest_first]
        scored.sort(key=lambda pair: -pair[0])
        return scored

    async def count(scope: MemoryScope, scope_id: str) -> int:
        return len(pools[scope_id])

    return SimpleNamespace(recall_core=recall_core, rank_facts=rank_facts, count=count)


# --- core: in every turn, whatever the turn is about ----------------------


async def test_core_is_injected_even_when_the_turn_is_about_nothing_it_says():
    store = _store(p=["core:我是芝士", "core:说人话"] + [f"事实{i}" for i in range(5)])

    got = await recall_pools(store, [(MemoryScope.project, "p")], query="报销流程")

    assert got.facts[:2] == ["我是芝士", "说人话"]
    assert got.core_count == 2


async def test_core_keeps_its_seat_when_facts_would_eat_the_whole_budget():
    store = _store(p=["core:我是芝士"] + [f"{i}" + "长" * 999 for i in range(10)])

    got = await recall_pools(
        store, [(MemoryScope.project, "p")], query="", char_budget=1000
    )

    assert got.facts[0] == "我是芝士"
    assert got.core_count == 1
    assert got.core_omitted == 0


async def test_core_over_its_own_budget_is_reported_not_silently_dropped():
    store = _store(p=[f"core:核心{i}" + "长" * 200 for i in range(10)])

    got = await recall_pools(
        store, [(MemoryScope.project, "p")], query="", core_char_budget=600
    )

    assert 0 < got.core_count < 10
    assert got.core_omitted == 10 - got.core_count
    assert got.omitted == got.core_omitted


async def test_one_core_fact_survives_a_budget_that_fits_none_of_it():
    """An empty core layer reads as "this agent has no identity", which is worse
    than one oversized line — so the first one always goes in."""
    store = _store(p=["core:" + "长" * 5000, "core:第二条"])

    got = await recall_pools(
        store, [(MemoryScope.project, "p")], query="", core_char_budget=100
    )

    assert got.core_count == 1
    assert got.core_omitted == 1


# --- facts: chosen by what the turn is about ------------------------------


async def test_facts_are_chosen_by_relevance_not_by_recency():
    """The oldest fact wins a seat the 30 newest ones do not, because it is the
    one the turn is about. Under the old newest-N rule it was the first to go."""
    store = _store(
        p=["alembic 迁移要改 HEAD 哨兵"] + [f"无关事实{i}" for i in range(30)]
    )

    got = await recall_pools(
        store, [(MemoryScope.project, "p")], query="alembic", char_budget=25
    )

    assert got.facts == ["alembic 迁移要改 HEAD 哨兵"]
    assert got.omitted == 30


async def test_a_turn_with_no_signal_still_fills_its_budget_newest_first():
    """Degradation, not a cliff: nothing to rank by means the room goes to the
    newest facts — where this started — rather than going unused."""
    store = _store(p=[f"事实{i}" for i in range(60)])

    got = await recall_pools(
        store, [(MemoryScope.project, "p")], query="", char_budget=100
    )

    assert got.facts[0] == "事实59"
    assert len(got.facts) < 60
    assert got.omitted == 60 - len(got.facts)


async def test_nothing_is_reported_missing_when_everything_fits():
    store = _store(p=["只有这一条"])

    got = await recall_pools(store, [(MemoryScope.project, "p")], query="这一条")

    assert got.facts == ["只有这一条"]
    assert got.omitted == 0
    assert got.core_count == 0


async def test_relevance_decides_across_pools_not_which_pool_it_sits_in():
    """The shared pool's matching fact beats the agent's own non-matching ones:
    which pool a fact happens to live in says nothing about this turn."""
    store = _store(
        own=[f"我的{i}" for i in range(5)], shared=["共享的 alembic 那条", "共享的别的"]
    )

    got = await recall_pools(
        store,
        [(MemoryScope.agent_project, "own"), (MemoryScope.project, "shared")],
        query="alembic",
        char_budget=30,
    )

    assert got.facts[0] == "共享的 alembic 那条"


async def test_both_pools_report_into_one_total():
    store = _store(
        own=["core:我是芝士"] + [f"我的{i}" for i in range(30)],
        shared=[f"共享{i}" for i in range(59)],
    )

    got = await recall_pools(
        store,
        [(MemoryScope.agent_project, "own"), (MemoryScope.project, "shared")],
        query="",
        char_budget=200,
    )

    assert got.core_count == 1
    assert got.omitted == 90 - len(got.facts)


# --- what the prompt says about all this ----------------------------------


def test_core_and_retrieved_are_labelled_apart():
    """ "Always here" and "here because this turn mentioned it" are different
    promises; a reader that cannot tell them apart cannot trust either."""
    prompt = _build_system_prompt(
        "base", "", None, ["我是芝士", "某条事实"], memories_core=1
    )

    core_at = prompt.index("我是芝士")
    fact_at = prompt.index("某条事实")
    assert "每轮都在场" in prompt[:core_at]
    assert "不是全部" in prompt[core_at:fact_at]


def test_what_did_not_come_in_is_stated_in_the_prompt():
    prompt = _build_system_prompt("base", "", None, ["记住这条"], memories_omitted=9)

    assert "记住这条" in prompt
    assert "9" in prompt
    assert "cheese recall" in prompt


def test_no_notice_when_nothing_was_left_out():
    prompt = _build_system_prompt("base", "", None, ["记住这条"])

    assert "记住这条" in prompt
    assert "cheese recall" not in prompt


def test_core_overflow_gets_its_own_warning():
    """A missing fact is normal. A missing *core* fact means the layer that is
    supposed to be unconditional has stopped being unconditional."""
    prompt = _build_system_prompt(
        "base", "", None, ["我是芝士"], memories_core=1, memories_core_omitted=3
    )

    assert "核心记忆超预算" in prompt
    assert "3" in prompt
