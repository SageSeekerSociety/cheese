"""Memory injection: what fits goes in, what does not is said out loud.

The failure these cover is not "memory is missing" but "memory is missing and
nothing says so" — a reader that cannot tell an empty pool from a truncated one
concludes there is nothing to know, which is exactly how a written-down pitfall
gets walked into anyway.
"""

from types import SimpleNamespace

import pytest

from app.domain.agent.chat import _build_system_prompt
from app.domain.memory.models import MemoryScope
from app.domain.memory.store import recall_pools

pytestmark = pytest.mark.anyio


def _store(**pools: list[str]):
    """A MemoryStore with named pools, keyed by scope_id."""

    async def recall(scope: MemoryScope, scope_id: str, limit: int = 50) -> list[str]:
        return pools[scope_id][-limit:]  # newest `limit`, oldest first

    async def count(scope: MemoryScope, scope_id: str) -> int:
        return len(pools[scope_id])

    return SimpleNamespace(recall=recall, count=count)


async def test_facts_beyond_the_limit_are_counted_not_dropped():
    store = _store(p=[f"事实{i}" for i in range(60)])

    got = await recall_pools(store, [(MemoryScope.project, "p")], limit=50)

    assert len(got.facts) == 50
    assert got.omitted == 10
    # The newest survive; the oldest are the ones reported missing.
    assert got.facts[-1] == "事实59"
    assert "事实0" not in got.facts


async def test_nothing_is_reported_missing_when_everything_fits():
    store = _store(p=["只有这一条"])

    got = await recall_pools(store, [(MemoryScope.project, "p")], limit=50)

    assert got.facts == ["只有这一条"]
    assert got.omitted == 0


async def test_a_bloated_pool_is_capped_by_size_and_still_reported():
    """Ten facts, well under the count limit, but 1000 chars each."""
    store = _store(p=[f"{i}" + "长" * 999 for i in range(10)])

    got = await recall_pools(
        store, [(MemoryScope.project, "p")], limit=50, char_budget=3000
    )

    assert len(got.facts) < 10
    assert got.omitted == 10 - len(got.facts)
    assert got.facts[-1].startswith("9")  # newest kept


async def test_both_pools_report_into_one_total():
    store = _store(
        own=[f"我的{i}" for i in range(30)], shared=[f"共享{i}" for i in range(59)]
    )

    got = await recall_pools(
        store,
        [(MemoryScope.agent_project, "own"), (MemoryScope.project, "shared")],
        limit=50,
    )

    assert len(got.facts) == 30 + 50
    assert got.omitted == 9
    assert got.facts[0] == "我的0"  # own pool first, oldest-first inside it


def test_truncation_is_stated_in_the_prompt():
    prompt = _build_system_prompt("base", "", None, ["记住这条"], memories_omitted=9)

    assert "记住这条" in prompt
    assert "9" in prompt
    assert "cheese recall" in prompt


def test_no_truncation_notice_when_nothing_was_cut():
    prompt = _build_system_prompt("base", "", None, ["记住这条"])

    assert "记住这条" in prompt
    assert "cheese recall" not in prompt
