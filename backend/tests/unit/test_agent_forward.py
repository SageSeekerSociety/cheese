"""Unit tests for agent forward routing: per-agent delivery gating by attention
policy / @-mentions, and the basic per-thread triage lock (immediate wake of the
highest-role agent, deferral of the rest, early release via ``finish_triage``).

No DB, no real hub: the DeviceHub is a fake exposing only ``all_online_screens``
and an ``AsyncMock`` ``call_screen``. Timers are driven deterministically — we
never sleep out the ~30s triage timeout; ``finish_triage`` releases it early.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agent.orchestrator import AgentService, ThreadAgentPolicy

pytestmark = pytest.mark.anyio

THREAD_ID = 77
BLOCK_ID = 4242


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _screen(agent_user_id: int, *, sid: str = "sid", device_id: str = "dev") -> SimpleNamespace:
    return SimpleNamespace(agent_user_id=agent_user_id, sid=sid, device_id=device_id)


def _make_service(online_agent_ids: list[int]) -> tuple[AgentService, AsyncMock]:
    """An AgentService whose only live dependency is a fake hub exposing the given
    online agent screens. Returns the service and the hub's ``call_screen`` mock."""
    call_screen = AsyncMock()
    hub = SimpleNamespace(
        all_online_screens=lambda: [_screen(uid) for uid in online_agent_ids],
        call_screen=call_screen,
    )
    svc = AgentService(
        session_factory=MagicMock(),
        hub=hub,  # type: ignore[arg-type]
        device_service=MagicMock(),
        cheeselet_source="",
    )
    return svc, call_screen


def _delivered_agent_ids(svc: AgentService, thread_id: int = THREAD_ID) -> set[int]:
    """Agents that actually received a screen call (recorded via _last_delivery)."""
    return {uid for (tid, uid) in svc._last_delivery if tid == thread_id}


# ---------------------------------------------------------------------------
# Per-agent delivery gating
# ---------------------------------------------------------------------------


async def test_mention_policy_agent_only_delivered_when_mentioned() -> None:
    svc, call_screen = _make_service([1])
    policy = ThreadAgentPolicy(user_id=1, mode="MENTION", interval_minutes=None, role=0)

    reached = await svc.forward_message_to_thread_agents(
        thread_id=THREAD_ID,
        block_id=BLOCK_ID,
        text="hi",
        speaker="alice",
        agent_policies=[policy],
        mentioned_ids=set(),
    )
    assert reached == 0
    call_screen.assert_not_awaited()


async def test_mention_policy_agent_delivered_when_mentioned() -> None:
    svc, call_screen = _make_service([1])
    policy = ThreadAgentPolicy(user_id=1, mode="MENTION", interval_minutes=None, role=0)

    reached = await svc.forward_message_to_thread_agents(
        thread_id=THREAD_ID,
        block_id=BLOCK_ID,
        text="hi",
        speaker="alice",
        agent_policies=[policy],
        mentioned_ids={1},
    )
    assert reached == 1
    call_screen.assert_awaited_once()


async def test_all_policy_agent_always_delivered() -> None:
    svc, call_screen = _make_service([1])
    policy = ThreadAgentPolicy(user_id=1, mode="ALL", interval_minutes=None, role=0)

    reached = await svc.forward_message_to_thread_agents(
        thread_id=THREAD_ID,
        block_id=BLOCK_ID,
        text="hi",
        speaker="alice",
        agent_policies=[policy],
        mentioned_ids=set(),
    )
    assert reached == 1
    call_screen.assert_awaited_once()


async def test_interval_policy_gated_before_elapsed() -> None:
    svc, call_screen = _make_service([1])
    # Last delivered 2 minutes ago; interval is 5 → not yet eligible.
    svc._last_delivery[(THREAD_ID, 1)] = datetime.now(UTC) - timedelta(minutes=2)
    policy = ThreadAgentPolicy(user_id=1, mode="INTERVAL", interval_minutes=5, role=0)

    reached = await svc.forward_message_to_thread_agents(
        thread_id=THREAD_ID,
        block_id=BLOCK_ID,
        text="hi",
        speaker="alice",
        agent_policies=[policy],
        mentioned_ids=set(),
    )
    assert reached == 0
    call_screen.assert_not_awaited()


async def test_interval_policy_delivered_after_elapsed() -> None:
    svc, call_screen = _make_service([1])
    # Last delivered 6 minutes ago; interval is 5 → eligible.
    svc._last_delivery[(THREAD_ID, 1)] = datetime.now(UTC) - timedelta(minutes=6)
    policy = ThreadAgentPolicy(user_id=1, mode="INTERVAL", interval_minutes=5, role=0)

    reached = await svc.forward_message_to_thread_agents(
        thread_id=THREAD_ID,
        block_id=BLOCK_ID,
        text="hi",
        speaker="alice",
        agent_policies=[policy],
        mentioned_ids=set(),
    )
    assert reached == 1
    call_screen.assert_awaited_once()


async def test_interval_policy_delivered_when_never_delivered() -> None:
    svc, call_screen = _make_service([1])
    policy = ThreadAgentPolicy(user_id=1, mode="INTERVAL", interval_minutes=5, role=0)

    reached = await svc.forward_message_to_thread_agents(
        thread_id=THREAD_ID,
        block_id=BLOCK_ID,
        text="hi",
        speaker="alice",
        agent_policies=[policy],
        mentioned_ids=set(),
    )
    assert reached == 1
    call_screen.assert_awaited_once()


async def test_mentioned_agent_delivered_regardless_of_policy() -> None:
    svc, call_screen = _make_service([1])
    # MENTION-mode agent with no @ would be gated; but it IS mentioned here.
    svc._last_delivery[(THREAD_ID, 1)] = datetime.now(UTC)  # would block INTERVAL too
    policy = ThreadAgentPolicy(user_id=1, mode="INTERVAL", interval_minutes=999, role=0)

    reached = await svc.forward_message_to_thread_agents(
        thread_id=THREAD_ID,
        block_id=BLOCK_ID,
        text="hi",
        speaker="alice",
        agent_policies=[policy],
        mentioned_ids={1},
    )
    assert reached == 1
    call_screen.assert_awaited_once()


# ---------------------------------------------------------------------------
# Triage lock
# ---------------------------------------------------------------------------


async def test_triage_wakes_highest_role_and_defers_rest() -> None:
    svc, call_screen = _make_service([1, 2, 3])
    policies = [
        ThreadAgentPolicy(user_id=1, mode="ALL", interval_minutes=None, role=0),
        ThreadAgentPolicy(user_id=2, mode="ALL", interval_minutes=None, role=2),  # highest
        ThreadAgentPolicy(user_id=3, mode="ALL", interval_minutes=None, role=1),
    ]

    reached = await svc.forward_message_to_thread_agents(
        thread_id=THREAD_ID,
        block_id=BLOCK_ID,
        text="hi",
        speaker="alice",
        agent_policies=policies,
        mentioned_ids=set(),
    )
    # All three counted as forwarded (one immediate + two deferred-補投).
    assert reached == 3
    # Only the highest-role agent (2) was woken immediately.
    assert call_screen.await_count == 1
    assert _delivered_agent_ids(svc) == {2}
    # A triage lock exists holding the two deferred deliveries.
    assert THREAD_ID in svc._triage
    assert {d.agent_user_id for d in svc._triage[THREAD_ID].deliveries} == {1, 3}

    # finish_triage releases the deferred wakes early.
    released = await svc.finish_triage(THREAD_ID)
    assert released == 2
    assert THREAD_ID not in svc._triage
    assert _delivered_agent_ids(svc) == {1, 2, 3}


async def test_mentioned_agent_bypasses_triage_lock() -> None:
    svc, call_screen = _make_service([1, 2])
    policies = [
        ThreadAgentPolicy(user_id=1, mode="ALL", interval_minutes=None, role=5),
        ThreadAgentPolicy(user_id=2, mode="MENTION", interval_minutes=None, role=0),
    ]

    reached = await svc.forward_message_to_thread_agents(
        thread_id=THREAD_ID,
        block_id=BLOCK_ID,
        text="hey @two",
        speaker="alice",
        agent_policies=policies,
        mentioned_ids={2},
    )
    # Both woken immediately: 2 because mentioned, 1 as the sole broadcast-eligible.
    assert reached == 2
    assert _delivered_agent_ids(svc) == {1, 2}
    # No deferral needed — nobody left over.
    assert THREAD_ID not in svc._triage


async def test_single_eligible_agent_not_deferred() -> None:
    svc, _ = _make_service([1])
    policy = ThreadAgentPolicy(user_id=1, mode="ALL", interval_minutes=None, role=0)

    reached = await svc.forward_message_to_thread_agents(
        thread_id=THREAD_ID,
        block_id=BLOCK_ID,
        text="hi",
        speaker="alice",
        agent_policies=[policy],
        mentioned_ids=set(),
    )
    assert reached == 1
    assert THREAD_ID not in svc._triage
    assert _delivered_agent_ids(svc) == {1}


async def test_new_message_flushes_pending_triage() -> None:
    svc, call_screen = _make_service([1, 2, 3])
    policies = [
        ThreadAgentPolicy(user_id=1, mode="ALL", interval_minutes=None, role=2),
        ThreadAgentPolicy(user_id=2, mode="ALL", interval_minutes=None, role=1),
        ThreadAgentPolicy(user_id=3, mode="ALL", interval_minutes=None, role=0),
    ]
    await svc.forward_message_to_thread_agents(
        thread_id=THREAD_ID,
        block_id=BLOCK_ID,
        text="first",
        speaker="alice",
        agent_policies=policies,
        mentioned_ids=set(),
    )
    assert THREAD_ID in svc._triage  # 2 & 3 deferred

    # A second message flushes the still-pending deferral before re-triaging.
    await svc.forward_message_to_thread_agents(
        thread_id=THREAD_ID,
        block_id=BLOCK_ID,
        text="second",
        speaker="alice",
        agent_policies=policies,
        mentioned_ids=set(),
    )
    # Everyone got at least one wake across the two rounds.
    assert _delivered_agent_ids(svc) == {1, 2, 3}


async def test_last_thread_recorded_on_delivery() -> None:
    svc, _ = _make_service([1])
    assert svc.last_thread_for_agent(1) is None
    policy = ThreadAgentPolicy(user_id=1, mode="ALL", interval_minutes=None, role=0)
    await svc.forward_message_to_thread_agents(
        thread_id=THREAD_ID,
        block_id=BLOCK_ID,
        text="hi",
        speaker="alice",
        agent_policies=[policy],
        mentioned_ids=set(),
    )
    assert svc.last_thread_for_agent(1) == THREAD_ID
