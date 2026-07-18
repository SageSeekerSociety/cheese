"""LiteLLM gateway admin client + exactly-once usage-drain math."""

import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.domain.agent import gateway as gw

PID = uuid.uuid4()


def _transport(handler):
    return httpx.MockTransport(handler)


@pytest.mark.anyio
async def test_mint_set_budget_and_daily_spend_roundtrip():
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        assert request.headers["Authorization"] == "Bearer mk"
        if request.url.path == "/key/generate":
            body = json.loads(request.content)
            assert body["user_id"] == f"project:{PID}"
            return httpx.Response(200, json={"key": "sk-virtual"})
        if request.url.path == "/key/update":
            assert json.loads(request.content)["max_budget"] == 1.5
            return httpx.Response(200, json={})
        if request.url.path == "/spend/logs":
            import hashlib
            expected = hashlib.sha256(b"sk-virtual").hexdigest()
            assert request.url.params["api_key"] == expected
            return httpx.Response(
                200,
                json=[
                    {"prompt_tokens": 10, "completion_tokens": 2, "spend": 0.001},
                    {"prompt_tokens": 5, "completion_tokens": 1, "spend": 0.0005},
                    "not-a-dict",  # tolerated
                ],
            )
        return httpx.Response(404)

    g = gw.LlmGateway("http://gw", "mk", transport=_transport(handler))
    assert await g.mint_project_key(PID) == "sk-virtual"
    assert await g.set_key_budget("sk-virtual", 1.5) is True
    day = await g.daily_spend("sk-virtual", gw.utc_today())
    assert (day.prompt_tokens, day.completion_tokens) == (15, 3)
    assert day.spend_usd == pytest.approx(0.0015)
    assert [p for _m, p in calls] == ["/key/generate", "/key/update", "/spend/logs"]


@pytest.mark.anyio
async def test_admin_failures_never_raise():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    g = gw.LlmGateway("http://gw", "mk", transport=_transport(handler))
    assert await g.mint_project_key(PID) is None
    assert await g.set_key_budget("k", 1.0) is False
    day = await g.daily_spend("sk-virtual", gw.utc_today())
    assert day.prompt_tokens == 0 and day.spend_usd == 0.0


class _StubGateway:
    """daily_spend stub: {date: (prompt, completion, usd)}."""

    def __init__(self, days: dict) -> None:
        self.days = days

    async def daily_spend(self, key, date):
        p, c, usd = self.days.get(date, (0, 0, 0.0))
        return gw.DailySpend(
            date=date, prompt_tokens=p, completion_tokens=c, spend_usd=usd
        )


@pytest.mark.anyio
async def test_drain_same_day_delta_is_exactly_once():
    today = gw.utc_today()
    g = _StubGateway({today: (100, 30, 0.01)})
    # First drain from a checkpoint that already saw (60, 10).
    p, c, usd, ckpt = await gw.drain_new_usage(
        g, "k", {"date": today, "prompt": 60, "completion": 10, "spend_usd": 0.004}
    )
    assert (p, c) == (40, 20) and usd == pytest.approx(0.006)
    # Nothing new → second drain yields zero (cumulative sums are monotone).
    p2, c2, _usd2, _ = await gw.drain_new_usage(g, "k", ckpt)
    assert (p2, c2) == (0, 0)


@pytest.mark.anyio
async def test_drain_day_rollover_finalizes_yesterday():
    today = gw.utc_today()
    yesterday = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%d")
    # Yesterday ended at (50, 5); the old checkpoint had only seen (30, 5) —
    # 20 late-logged prompt tokens must still be billed. Today adds (7, 3).
    g = _StubGateway({yesterday: (50, 5, 0.0), today: (7, 3, 0.0)})
    p, c, _usd, ckpt = await gw.drain_new_usage(
        g, "k", {"date": yesterday, "prompt": 30, "completion": 5, "spend_usd": 0.0}
    )
    assert (p, c) == (20 + 7, 0 + 3)
    assert ckpt["date"] == today and ckpt["prompt"] == 7


@pytest.mark.anyio
async def test_drain_without_checkpoint_starts_today():
    today = gw.utc_today()
    g = _StubGateway({today: (11, 4, 0.0)})
    p, c, _usd, ckpt = await gw.drain_new_usage(g, "k", None)
    assert (p, c) == (11, 4) and ckpt["date"] == today
