"""A newer notice about a PR replaces the one its agent has not been handed yet.

Each notice the platform sends a task's agent about its PR — the checks
failed, it conflicts with its base — restates that thing's whole current state.
When the agent is busy, they wait; on dev (2026-10) the same PR's CI failure
stacked up to 23 times, and the agent would have read every stale one before
the one that mattered.

The rules, as stated before the code was written:

- a newer notice of the same kind for the same PR, while the earlier one is
  still waiting, leaves only the newer one owed to the agent;
- notices of different kinds never replace each other;
- a notice the agent's session may already have been handed is left alone:
  nothing it might have read is withdrawn or sent again.
"""

import asyncio
import uuid

import pytest
from sqlalchemy import select

from app.domain.block.models import Block
from app.domain.delivery.models import Delivery
from app.domain.review.models import AcceptCard
from tests.conftest import wait_work_idle
from tests.integration.test_accept_pr import _poll
from tests.integration.test_accept_pr import app_world as _app_world_fixture
from tests.integration.test_nudge_pr_signals import _authorized

#: The world `test_nudge_pr_signals` runs in: a platform GitHub App opened the
#: PR, and `review/pr_poll.py` polls it.
app_world = pytest.fixture(_app_world_fixture.__wrapped__)  # type: ignore[attr-defined]

_OWED = ("pending", "claimed")


def _owed(client, card_id: str) -> list[tuple[str, str]]:
    """Each delivery to the card's task: its notice kind and state."""

    async def read():
        async with client.test_factory() as session:
            card = await session.get(AcceptCard, uuid.UUID(card_id))
            rows = list(
                await session.scalars(
                    select(Delivery)
                    .where(Delivery.conversation_id == card.task_id)
                    .order_by(Delivery.recorded_at)
                )
            )
            out = []
            for row in rows:
                block = await session.get(Block, row.event_id)
                out.append(
                    (
                        (block.meta or {}).get("event_type"),
                        row.state,
                        row.payload["content"],
                    )
                )
            return out

    return asyncio.run(read())


def _hand_over_first(client, card_id: str) -> None:
    """The first delivery is being sent to the session as this happens."""

    async def mark():
        async with client.test_factory() as session:
            card = await session.get(AcceptCard, uuid.UUID(card_id))
            row = await session.scalar(
                select(Delivery)
                .where(Delivery.conversation_id == card.task_id)
                .order_by(Delivery.recorded_at)
                .limit(1)
            )
            row.state = "sending"
            await session.commit()

    asyncio.run(mark())


def test_a_newer_ci_failure_replaces_the_one_still_waiting(client, app_world):
    fake = app_world["fake"]
    _tid, cid, _number, head_sha = _authorized(client, app_world)

    fake.check_state_by_sha[head_sha] = ("failure", "Backend Test: failure")
    _poll(client)
    wait_work_idle()
    fake.check_state_by_sha[head_sha] = ("failure", "Backend Test: failure、Lint: x")
    _poll(client)
    wait_work_idle()

    owed = [row for row in _owed(client, cid) if row[1] in _OWED]
    assert len(owed) == 1, "两条同一个 PR 的 CI 失败都还排着等芝士读"
    assert "Lint" in owed[0][2], "留下的不是最新那条"


def test_a_conflict_and_a_ci_failure_both_stay(client, app_world):
    fake = app_world["fake"]
    _tid, cid, number, head_sha = _authorized(client, app_world)

    fake.mergeable_by_number[number] = False
    fake.check_state_by_sha[head_sha] = ("pending", "等待中：Backend Test")
    _poll(client)
    wait_work_idle()
    fake.check_state_by_sha[head_sha] = ("failure", "Backend Test: failure")
    _poll(client)
    wait_work_idle()

    kinds = sorted(kind for kind, state, _ in _owed(client, cid) if state in _OWED)
    assert kinds == ["ci_failed", "pr_conflict"]


def test_one_already_being_handed_over_is_left_alone(client, app_world):
    fake = app_world["fake"]
    _tid, cid, _number, head_sha = _authorized(client, app_world)

    fake.check_state_by_sha[head_sha] = ("failure", "Backend Test: failure")
    _poll(client)
    wait_work_idle()
    _hand_over_first(client, cid)
    fake.check_state_by_sha[head_sha] = ("failure", "Backend Test: failure、Lint: x")
    _poll(client)
    wait_work_idle()

    states = [state for _, state, _ in _owed(client, cid)]
    assert states == ["sending", "pending"]
