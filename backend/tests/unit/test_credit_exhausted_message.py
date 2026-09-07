"""What a turn says when the AI relay has no money left.

An exhausted balance arrives as HTTP 429 — the same status as a rate limit — so
it fell into the generic branch and told the reader to try again later. Retrying
cannot refill a balance, so that advice sends someone into a loop that can never
succeed, and hides the one thing that has to happen.

The other way a turn's money runs out (#715): the PROJECT's compute credits are
spent, the metering proxy 429s every `/v1/messages` call, and Claude Code reads
ten retries of that as a bad API key — `StopFailure` with `error:
authentication_failed` and a last message like "Invalid API key". Admission
stamps the turn it refused (`credits_refused_at`) the moment it happens, and the
turn's own end reads that stamp back to decide whose wording the room gets.
"""

import asyncio
import uuid
from datetime import UTC, datetime

import pytest

from app.domain.agent.chat import ChatService, _is_out_of_credit
from app.domain.agent.repositories import AgentTurnRepository
from app.domain.block.models import AuthorType
from app.domain.block.repositories import BlockRepository
from app.domain.usage.credits import CREDITS_EXHAUSTED_EVENT
from tests.conftest import StubChannel, settle_turn, stub_compute
from tests.turn_log import a_topic


@pytest.mark.parametrize(
    "detail",
    [
        # Verbatim from the failure this came from.
        "API Error: Request rejected (429) · [1113][余额不足或无用资源包，请充值。]"
        "[20260731025202bd72819043d8441f]",
        "insufficient balance",
        "Your account has insufficient_quota",
        "quota exceeded for this key",
    ],
)
def test_a_spent_balance_is_recognised(detail):
    assert _is_out_of_credit(detail)


@pytest.mark.parametrize(
    "detail",
    [
        None,
        "",
        "upstream timed out",
        "429 Too Many Requests",  # a real rate limit: waiting DOES help
        "connection reset by peer",
    ],
)
def test_a_transient_failure_is_not_mistaken_for_it(detail):
    """Wrongly calling a blip 'out of credit' would send someone to top up an
    account that is fine — the error in the other direction."""
    assert not _is_out_of_credit(detail)


# --- #715: a turn admission refused for spent PROJECT credits -----------------


class _CreditsRefusedScreen(StubChannel):
    """The turn Claude Code's ten retries into a spent metering-proxy budget
    produce: `StopFailure`, never `Stop`, worded like a bad API key — exactly
    what a real refused-429-then-retry sequence looks like from here."""

    def emit_turn(self, topic_id: uuid.UUID, prompt: str, reply: str) -> None:
        del prompt, reply
        self.starts(topic_id)
        self.hook(
            topic_id,
            hook_event_name="StopFailure",
            error="authentication_failed",
            last_assistant_message="Invalid API key",
        )


async def _run_stop_failure(
    chat: ChatService,
    screen: _CreditsRefusedScreen,
    topic_id: uuid.UUID,
    turn_id: uuid.UUID,
) -> None:
    async for _ in chat.converse(
        topic_id=topic_id, author="u", content="做事", summon=True, turn_id=turn_id
    ):
        pass
    await settle_turn(chat, topic_id)
    # The hook subscription's consumer task otherwise outlives this test: the
    # ~40 other tests that hand a screen around go through `client`/
    # `python_client`, whose own teardown retires it; this one builds a
    # `ChatService` straight on `db_factory`, with nothing else to do that.
    # `runtime.close()` would also do this, but it routes through the session-
    # activity watchdog teardown (`_end_session_activity`/
    # `_watch_session_activity`), which under full-suite load has been seen to
    # hang; the consumer task is a plain `while True: await queue.get()` loop
    # with no such risk, so retire exactly that.
    subscription = screen.runtime._subscriptions.get(topic_id)
    if subscription is not None and subscription.consumer_task is not None:
        subscription.consumer_task.cancel()
        try:
            await subscription.consumer_task
        except asyncio.CancelledError:
            pass


async def _system_event_lines(factory, topic_id: uuid.UUID) -> list[str]:
    async with factory() as session:
        blocks = await BlockRepository(session).list_for_topic(topic_id)
    return [b.content for b in blocks if b.author_type == AuthorType.system]


@pytest.mark.anyio
async def test_a_turn_stamped_refused_gets_the_platforms_own_line(db_factory, tmp_path):
    """Admission already stamped this turn `credits_refused_at` before it ended
    — the room must see the SAME platform line admission itself posts, never
    Claude Code's "Invalid API key"."""
    topic_id = await a_topic(db_factory)
    turn_id = uuid.uuid4()
    async with db_factory() as session:
        await AgentTurnRepository(session).open(
            turn_id=turn_id,
            topic_id=topic_id,
            continuation_id=turn_id,
            author="u",
            content="做事",
            is_resume=False,
            resendable=False,
            started_at=datetime.now(UTC),
        )
        await AgentTurnRepository(session).mark_credits_refused(
            turn_id, datetime.now(UTC)
        )
        await session.commit()

    screen = _CreditsRefusedScreen()
    chat = ChatService(
        session_factory=db_factory,
        compute=stub_compute(screen),
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
    )
    await _run_stop_failure(chat, screen, topic_id, turn_id)

    lines = await _system_event_lines(db_factory, topic_id)
    assert CREDITS_EXHAUSTED_EVENT in lines
    assert not any("Invalid API key" in line for line in lines)


@pytest.mark.anyio
async def test_an_unstamped_stop_failure_keeps_todays_notice(db_factory, tmp_path):
    """No admission ever stamped this turn — a `StopFailure` for some OTHER
    reason must still fall through to today's generic wording, Claude Code's
    text included. The stamp must not swallow every failed turn, only the
    ones admission actually refused for spent credits."""
    topic_id = await a_topic(db_factory)
    turn_id = uuid.uuid4()

    screen = _CreditsRefusedScreen()
    chat = ChatService(
        session_factory=db_factory,
        compute=stub_compute(screen),
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
    )
    await _run_stop_failure(chat, screen, topic_id, turn_id)

    lines = await _system_event_lines(db_factory, topic_id)
    assert any("Invalid API key" in line for line in lines)
    assert CREDITS_EXHAUSTED_EVENT not in lines
