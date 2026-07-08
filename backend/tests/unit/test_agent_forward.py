"""Unit tests for agent forward routing: per-agent delivery gating by attention
policy / @-mentions, and the basic per-thread triage lock (immediate wake of the
highest-role agent, deferral of the rest, early release via ``finish_triage``).

No DB, no real hub: the DeviceHub is a fake exposing only ``all_online_screens``
and an ``AsyncMock`` ``call_screen``. Timers are driven deterministically — we
never sleep out the ~30s triage timeout; ``finish_triage`` releases it early.
"""

from contextlib import asynccontextmanager
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
    # Triage 降噪只作用于 INTERVAL 广播；ALL 恒立即，不参与「只唤醒最高 role 一个」。
    svc, call_screen = _make_service([1, 2, 3])
    policies = [
        ThreadAgentPolicy(user_id=1, mode="INTERVAL", interval_minutes=1, role=0),
        ThreadAgentPolicy(user_id=2, mode="INTERVAL", interval_minutes=1, role=2),  # highest
        ThreadAgentPolicy(user_id=3, mode="INTERVAL", interval_minutes=1, role=1),
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


async def test_all_agents_all_delivered_immediately_no_triage() -> None:
    """回归：ALL(「立即」) 语义是每条都立即送达，多个 ALL agent 全部立即唤醒，
    不因非最高 role 被 triage defer ~30s。"""
    svc, call_screen = _make_service([1, 2, 3])
    policies = [
        ThreadAgentPolicy(user_id=1, mode="ALL", interval_minutes=None, role=0),
        ThreadAgentPolicy(user_id=2, mode="ALL", interval_minutes=None, role=2),
        ThreadAgentPolicy(user_id=3, mode="ALL", interval_minutes=None, role=1),
    ]

    reached = await svc.forward_message_to_thread_agents(
        thread_id=THREAD_ID,
        block_id=BLOCK_ID,
        text="没有 @ 的普通消息",
        speaker="alice",
        agent_policies=policies,
        mentioned_ids=set(),
    )
    assert reached == 3
    assert call_screen.await_count == 3
    assert _delivered_agent_ids(svc) == {1, 2, 3}
    # ALL 不参与 triage 降噪 → 无 defer。
    assert THREAD_ID not in svc._triage


async def test_all_agent_immediate_even_with_higher_role_interval_peer() -> None:
    """回归重点场景：群里另有 role 更高的 INTERVAL agent 时，ALL agent 仍立即送达
    （而不是被那个更高 role 的 broadcast 抑制成 defer）。"""
    svc, _ = _make_service([1, 2])
    policies = [
        ThreadAgentPolicy(user_id=1, mode="ALL", interval_minutes=None, role=0),
        ThreadAgentPolicy(user_id=2, mode="INTERVAL", interval_minutes=1, role=9),  # 更高 role
    ]

    await svc.forward_message_to_thread_agents(
        thread_id=THREAD_ID,
        block_id=BLOCK_ID,
        text="hi",
        speaker="alice",
        agent_policies=policies,
        mentioned_ids=set(),
    )
    # ALL agent(1) 立即；INTERVAL agent(2) 作为唯一 broadcast-eligible 也立即。
    assert _delivered_agent_ids(svc) == {1, 2}
    assert THREAD_ID not in svc._triage


async def test_new_message_flushes_pending_triage() -> None:
    svc, call_screen = _make_service([1, 2, 3])
    policies = [
        ThreadAgentPolicy(user_id=1, mode="INTERVAL", interval_minutes=1, role=2),
        ThreadAgentPolicy(user_id=2, mode="INTERVAL", interval_minutes=1, role=1),
        ThreadAgentPolicy(user_id=3, mode="INTERVAL", interval_minutes=1, role=0),
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


# ---------------------------------------------------------------------------
# Unread backlog: an agent that missed messages gets the whole backlog, not just
# the latest line (from its read watermark up to and including the triggering block).
# ---------------------------------------------------------------------------


def test_thread_prompt_renders_full_backlog() -> None:
    svc, _ = _make_service([])
    prompt = svc._thread_prompt(
        thread_id=THREAD_ID,
        messages=[("alice", "first"), ("bob", "second"), ("alice", "third")],
        mentioned=True,
    )
    # Every unread line is present, in order.
    assert f"[thread:{THREAD_ID}] alice：first" in prompt
    assert f"[thread:{THREAD_ID}] bob：second" in prompt
    assert f"[thread:{THREAD_ID}] alice：third" in prompt
    assert prompt.index("first") < prompt.index("second") < prompt.index("third")
    # Multi-message header + the mention hint both show.
    assert "未读的群聊消息" in prompt
    assert "有人 @你" in prompt


def test_thread_prompt_single_message_has_no_backlog_header() -> None:
    svc, _ = _make_service([])
    prompt = svc._thread_prompt(
        thread_id=THREAD_ID, messages=[("alice", "hi")], mentioned=False
    )
    assert f"[thread:{THREAD_ID}] alice：hi" in prompt
    assert "未读的群聊消息" not in prompt  # single message → no backlog banner
    assert "有人 @你" not in prompt


def test_claude_command_fresh_vs_resume_and_cwd() -> None:
    svc, _ = _make_service([])
    fresh = svc._claude_command(session_id="abc-123", cwd=None, resume=False)
    assert fresh == ["bash", "-lc", "exec claude --session-id abc-123"]
    resume = svc._claude_command(session_id="abc-123", cwd=None, resume=True)
    assert resume == ["bash", "-lc", "exec claude --resume abc-123"]
    # cwd is prepended (Claude resolves a session by cwd) and shell-quoted.
    with_cwd = svc._claude_command(session_id="abc-123", cwd="/home/me/my repo", resume=True)
    assert with_cwd[-1] == "cd '/home/me/my repo' && exec claude --resume abc-123"


def _fake_sf(session: object):
    @asynccontextmanager
    async def factory():
        yield session

    return factory


async def test_collect_unread_gathers_backlog_skipping_own_and_deleted(monkeypatch) -> None:
    """From watermark 100 up to the trigger 104: include others' live messages in
    order, skip the agent's own message and deleted tombstones."""
    svc, _ = _make_service([])
    agent_id = 1
    svc._sf = _fake_sf(SimpleNamespace())  # type: ignore[assignment]

    blocks = [
        SimpleNamespace(id=101, author_id=5, content="a", deleted_at=None),
        SimpleNamespace(id=102, author_id=agent_id, content="mine", deleted_at=None),  # own → skip
        SimpleNamespace(id=103, author_id=6, content="gone", deleted_at=datetime.now(UTC)),  # skip
        SimpleNamespace(id=104, author_id=6, content="b", deleted_at=None),  # the trigger
        SimpleNamespace(id=105, author_id=6, content="future", deleted_at=None),  # > block_id → skip
    ]

    membership_repo = SimpleNamespace(
        get=AsyncMock(return_value=SimpleNamespace(last_read_block_id=100))
    )
    block_repo = SimpleNamespace(messages_since=AsyncMock(return_value=blocks))
    monkeypatch.setattr(
        "app.domain.thread.repositories.ThreadMembershipRepository", lambda _s: membership_repo
    )
    monkeypatch.setattr(
        "app.domain.block.repositories.BlockRepository", lambda _s: block_repo
    )

    async def _members(_session, _hub, ids):
        return [{"user_id": uid, "nickname": f"u{uid}"} for uid in ids]

    monkeypatch.setattr("app.agent.orchestrator.build_member_dicts", _members)

    messages, truncated = await svc._collect_unread(
        thread_id=THREAD_ID,
        agent_user_id=agent_id,
        block_id=104,
        fallback_speaker="x",
        fallback_text="y",
    )
    assert messages == [("u5", "a"), ("u6", "b")]
    assert truncated is False
    block_repo.messages_since.assert_awaited_once_with(THREAD_ID, 100)


async def test_collect_unread_falls_back_to_trigger_when_empty(monkeypatch) -> None:
    """Watermark already past the block (a re-delivery) → empty backlog → fall back to
    the single triggering message so delivery never sends an empty prompt."""
    svc, _ = _make_service([])
    svc._sf = _fake_sf(SimpleNamespace())  # type: ignore[assignment]

    membership_repo = SimpleNamespace(
        get=AsyncMock(return_value=SimpleNamespace(last_read_block_id=999))
    )
    block_repo = SimpleNamespace(messages_since=AsyncMock(return_value=[]))
    monkeypatch.setattr(
        "app.domain.thread.repositories.ThreadMembershipRepository", lambda _s: membership_repo
    )
    monkeypatch.setattr("app.domain.block.repositories.BlockRepository", lambda _s: block_repo)

    messages, truncated = await svc._collect_unread(
        thread_id=THREAD_ID,
        agent_user_id=1,
        block_id=104,
        fallback_speaker="alice",
        fallback_text="just this",
    )
    assert messages == [("alice", "just this")]
    assert truncated is False


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
