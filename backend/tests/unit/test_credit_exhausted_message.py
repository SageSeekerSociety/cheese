"""What a turn says when a model account has no money left.

The platform's own account with a model provider can run dry. Each provider
says so in its own words and status — a 429 like a rate limit, a 400 like a bad
request, or the gateway's 402 once no row of the model has credit — and only the
platform's administrators can top it up. The room is told they have it in hand,
never sent to pay, and never told it is an overload that waiting fixes.

The other way a turn's money runs out (#715): the PROJECT's compute credits are
spent, the metering proxy 429s every `/v1/messages` call, and Claude Code reads
ten retries of that as a bad API key — an assistant record with `error:
authentication_failed` saying "Invalid API key", then a failed `result`. Admission
stamps the turn it refused (`credits_refused_at`) the moment it happens, and the
turn's own end reads that stamp back to decide whose wording the room gets.
"""

import uuid
from datetime import UTC, datetime

import pytest

from app.api import deps as session_turn_deps
from app.domain.agent.chat import ChatService
from app.domain.agent.repositories import AgentTurnRepository
from app.domain.block.repositories import BlockRepository
from app.domain.event_author import AuthorType
from app.domain.usage.credits import CREDITS_EXHAUSTED_EVENT
from app.domain.usage.ledger import month_end, month_of
from tests.conftest import (
    StubChannel,
    close_topic_subscriptions,
    settle_turn,
    stub_compute,
)
from tests.turn_log import a_topic

# What a turn ended on when a provider account behind the platform's model
# gateway had no money left, in Claude Code's wording around the gateway's.
PLATFORM_OUT_OF_CREDIT = [
    # Verbatim from the failure this came from.
    "API Error: Request rejected (429) · [1113][余额不足或无用资源包，请充值。]"
    "[20260731025202bd72819043d8441f]",
    # The gateway's own answer once no row of the model has credit left
    # (deploy/gateway/provider_credit.py).
    'API Error: 402 {"type":"error","error":{"type":"api_error","message":'
    "\"cheese_upstream_credit_exhausted: the platform's own account with every "
    'provider serving deepseek-flash is out of credit."}}',
    # Moonshot and Volcengine word it as a rate limit, which Claude Code
    # wraps in "temporarily limiting requests" (2026-10-22, dev gateway).
    "API Error: Server is temporarily limiting requests (not your usage limit)"
    ' · litellm.RateLimitError: AnthropicException - {"error":{"message":"Your '
    "account org-1 <ak-1> is suspended due to insufficient balance, please "
    'recharge your account","type":"exceeded_current_quota_error"}}',
    "API Error: Server is temporarily limiting requests (not your usage limit)"
    ' · litellm.RateLimitError: {"error":{"code":"SetLimitExceeded","message":'
    '"Your account [2100] has reached the set usage limit for this model, and '
    'the model service has been paused."}}',
    'API Error: 400 litellm.BadRequestError: DeepseekException - {"error":'
    '{"message":"Insufficient Balance","code":"invalid_request_error"}}',
    "insufficient balance",
    "Your account has insufficient_quota",
    "quota exceeded for this key",
    # OpenAI's other two refusals about a billing state. Both mean a state no
    # wait changes, and neither is matched by any of the alternatives above —
    # dropping the bare word "billing" from the rule lost them with the
    # false positive it was removed for (2026-10-27 review of #4932).
    "Billing hard limit has been reached",
    "Your account is not active, please check your billing details",
]


@pytest.mark.parametrize("detail", PLATFORM_OUT_OF_CREDIT)
def test_a_platform_account_out_of_credit_is_the_platforms_to_fix(detail):
    """Only the platform's administrators can top up its account with a
    provider, so the room's card says the platform has it in hand — the same
    card whichever provider ran dry and however Claude Code wrapped it — never
    one that sends the reader to pay or reads as a passing overload. A retry
    is offered: it succeeds once the account is topped up."""
    from app.domain.agent.turn.state.notices import _turn_failure_notice

    line, meta = _turn_failure_notice(detail, None)
    reference, _ = _turn_failure_notice(PLATFORM_OUT_OF_CREDIT[0], None)
    assert line == reference
    assert meta["who"] == "platform"
    assert meta["retryable"] is True
    assert detail in meta["detail"]


@pytest.mark.parametrize(
    "detail",
    [
        "upstream timed out",
        "API Error: 429 Too Many Requests",  # a real rate limit: waiting DOES help
        "connection reset by peer",
        # The metering proxy's own 503 when its admission check cannot answer
        # (`deploy/metering-proxy/cheese_billing_core.py`), verbatim from the
        # failures of 2026-10-27 11:00. It says "billing", and the bare-billing
        # alternative in the out-of-credit rule read it as the platform's
        # account running dry: the room was told to wait for a top-up that was
        # not owed, and the turn was never resumed.
        "API Error: 503 cheese: task billing attribution is temporarily "
        "unavailable; retry shortly. This is a server-side issue, usually "
        "temporary — try again in a moment. If it persists, check "
        "https://status.claude.com.（server_error）",
    ],
)
def test_a_transient_failure_is_not_mistaken_for_it(detail):
    """Wrongly calling a blip 'out of credit' would tell the room an account
    is empty when it is fine — the error in the other direction."""
    from app.domain.agent.turn.state.notices import _turn_failure_notice

    line, _ = _turn_failure_notice(detail, None)
    reference, _ = _turn_failure_notice(PLATFORM_OUT_OF_CREDIT[0], None)
    assert line != reference


# --- #715: a turn admission refused for spent PROJECT credits -----------------


class _CreditsRefusedScreen(StubChannel):
    """The turn Claude Code's ten retries into a spent metering-proxy budget
    produce: the build's own line worded like a bad API key, then a result that
    failed — the records the pinned build prints for a refused key
    (`tests/fixtures/harness-contract/the-turn-failed.json`)."""

    def emit_turn(
        self,
        topic_id: uuid.UUID,
        prompt: str,
        reply: str,
        *,
        agent: str | None = None,
    ) -> None:
        del reply
        self.starts(topic_id)
        self.acknowledges(topic_id, prompt)
        self.says(topic_id, "Invalid API key", error="authentication_failed")
        self.record(
            topic_id,
            type="result",
            subtype="success",
            is_error=True,
            result="Invalid API key",
            terminal_reason="api_error",
            api_error_status=401,
        )


async def _run_refused_turn(
    chat: ChatService,
    topic_id: uuid.UUID,
    turn_id: uuid.UUID,
) -> None:
    async for _ in chat.converse(
        topic_id=topic_id, author="u", content="做事", summon=True, turn_id=turn_id
    ):
        pass
    await settle_turn(chat, topic_id)
    # This test owns the runtime, so it stops the reader too.
    await close_topic_subscriptions(chat, topic_id)


async def _system_event_lines(factory, topic_id: uuid.UUID) -> list[str]:
    """What the room can read of each platform line: the line, and the original
    words folded under it."""
    async with factory() as session:
        blocks = await BlockRepository(session).list_for_topic(topic_id)
    return [
        "\n".join(filter(None, [b.content, (b.meta or {}).get("detail")]))
        for b in blocks
        if b.author_type == AuthorType.platform
    ]


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
            conversation_id=topic_id,
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
        work_runner=session_turn_deps.get_work_runner(),
        session_factory=db_factory,
        compute=stub_compute(screen),
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
    )
    await _run_refused_turn(chat, topic_id, turn_id)

    lines = await _system_event_lines(db_factory, topic_id)
    assert any(line.startswith(CREDITS_EXHAUSTED_EVENT) for line in lines)
    assert not any("Invalid API key" in line for line in lines)


class _BudgetBrakeScreen(StubChannel):
    """A turn admission let through that the gateway's budget brake stopped
    part-way: the metering proxy answers the call with its spent-credits
    refusal, and the build ends the turn on it."""

    def emit_turn(
        self,
        topic_id: uuid.UUID,
        prompt: str,
        reply: str,
        *,
        agent: str | None = None,
    ) -> None:
        del reply
        self.starts(topic_id)
        self.acknowledges(topic_id, prompt)
        self.says(topic_id, "cheese project budget: 额度已用完。", error="rate_limit")
        self.record(
            topic_id,
            type="result",
            subtype="success",
            is_error=True,
            result="cheese project budget: 额度已用完。",
            terminal_reason="api_error",
            api_error_status=429,
        )


@pytest.mark.anyio
async def test_a_turn_the_budget_brake_stopped_says_the_credits_ran_out(
    db_factory, tmp_path
):
    """The first turn to hit a spent budget is stopped by the gateway, not by
    admission, so nothing stamped it. The room must still say the credits ran
    out, not 「本轮未完成」 with the proxy's raw words and a retry that can only
    fail again."""
    topic_id = await a_topic(db_factory)
    chat = ChatService(
        work_runner=session_turn_deps.get_work_runner(),
        session_factory=db_factory,
        compute=stub_compute(_BudgetBrakeScreen()),
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
    )
    await _run_refused_turn(chat, topic_id, uuid.uuid4())

    async with db_factory() as session:
        blocks = await BlockRepository(session).list_for_topic(topic_id)
    notices = [b for b in blocks if b.author_type == AuthorType.platform]
    # The ledger had not settled the turn yet; the line still says the month's
    # credits are spent and when they come back, as admission says it.
    resets = month_end(month_of(datetime.now(UTC)))
    assert [b.content for b in notices] == [
        f"本月额度已用完，{resets.month}月{resets.day}日重置。这次没有运行。"
    ]
    assert not any((b.meta or {}).get("retryable") for b in notices)


@pytest.mark.anyio
async def test_an_unstamped_stop_failure_keeps_todays_notice(db_factory, tmp_path):
    """No admission ever stamped this turn — a failed turn for some OTHER
    reason must still fall through to today's generic wording, Claude Code's
    text included. The stamp must not swallow every failed turn, only the
    ones admission actually refused for spent credits."""
    topic_id = await a_topic(db_factory)
    turn_id = uuid.uuid4()

    screen = _CreditsRefusedScreen()
    chat = ChatService(
        work_runner=session_turn_deps.get_work_runner(),
        session_factory=db_factory,
        compute=stub_compute(screen),
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
    )
    await _run_refused_turn(chat, topic_id, turn_id)

    lines = await _system_event_lines(db_factory, topic_id)
    assert any("Invalid API key" in line for line in lines)
    assert not any(line.startswith(CREDITS_EXHAUSTED_EVENT) for line in lines)


@pytest.mark.anyio
async def test_a_refused_turn_ending_says_when_the_credits_come_back(
    db_factory, tmp_path
):
    """The turn's own end repeats what admission said: the month is spent and
    the day it resets, not a bare "spent"."""
    from app.domain.topic.services import TopicService
    from app.domain.usage.ledger import Ledger, month_end, month_of, payer_for_project
    from tests.integration.conftest import set_free_plan_credits

    topic_id = await a_topic(db_factory)
    turn_id = uuid.uuid4()
    async with db_factory() as session:
        await set_free_plan_credits(session, 1)
        topic = await TopicService(session).get(topic_id)
        assert topic is not None
        payer = await payer_for_project(session, topic.project_id)
        await Ledger(session).record(
            payer,
            credits=1,
            model="m",
            input_tokens=1,
            output_tokens=1,
            cost_usd=0.0,
            route="gateway",
        )
        await AgentTurnRepository(session).open(
            turn_id=turn_id,
            conversation_id=topic_id,
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

    chat = ChatService(
        work_runner=session_turn_deps.get_work_runner(),
        session_factory=db_factory,
        compute=stub_compute(_CreditsRefusedScreen()),
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
    )
    await _run_refused_turn(chat, topic_id, turn_id)

    resets = month_end(month_of(datetime.now(UTC)))
    lines = await _system_event_lines(db_factory, topic_id)
    assert any(f"{resets.month}月{resets.day}日重置" in line for line in lines)
    assert not any("Invalid API key" in line for line in lines)
