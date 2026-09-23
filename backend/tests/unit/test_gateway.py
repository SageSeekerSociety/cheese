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
    assert await g.daily_spend("sk-virtual", gw.utc_today()) is None


@pytest.mark.anyio
async def test_failed_read_cannot_reset_checkpoint_and_rebill_prior_spend():
    """An unknown cumulative total must not be persisted as an observed zero."""
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(500, text="temporary gateway failure")
        return httpx.Response(
            200,
            json=[
                {
                    "prompt_tokens": 150,
                    "completion_tokens": 30,
                    "spend": 0.015,
                }
            ],
        )

    gateway = gw.LlmGateway("http://gw", "mk", transport=_transport(handler))
    checkpoint = {
        "date": gw.utc_today(),
        "prompt": 100,
        "completion": 20,
        "spend_usd": 0.010,
    }

    failed = await gw.drain_new_usage(gateway, "sk-virtual", checkpoint)
    checkpoint_after_failure = checkpoint if failed is None else failed[3]
    prompt, completion, spend, _ = await gw.drain_new_usage(
        gateway, "sk-virtual", checkpoint_after_failure
    )

    assert (prompt, completion) == (50, 10)
    assert spend == pytest.approx(0.005)


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


def _model_info_response(rows):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/model/info"
        return httpx.Response(200, json={"data": rows})

    return handler


@pytest.mark.anyio
async def test_the_gateway_reports_what_it_routes_and_what_it_can_bill():
    """The price decides whether a model can be offered at all, and a deployment
    may write it in either of the two places LiteLLM reads it from — beside the
    route, or beside the metadata. Reading one place reports every model that
    used the other as unpriced."""
    client = gw.LlmGateway(
        "http://gw",
        "mk",
        transport=_transport(
            _model_info_response(
                [
                    {
                        "model_name": "priced-beside-the-route",
                        "litellm_params": {
                            "model": "anthropic/x",
                            "input_cost_per_token": 0.000004,
                            "output_cost_per_token": 0.000004,
                        },
                        "model_info": {"cheese_selectable": True},
                    },
                    {
                        "model_name": "priced-beside-the-metadata",
                        "litellm_params": {"model": "deepseek/y"},
                        "model_info": {
                            "cheese_selectable": True,
                            "cheese_label": "Y",
                            "input_cost_per_token": 0.0000003,
                            "output_cost_per_token": 0.0000012,
                        },
                    },
                    {
                        "model_name": "priced-one-way-only",
                        "litellm_params": {"model": "anthropic/z"},
                        "model_info": {
                            "cheese_selectable": True,
                            "input_cost_per_token": 0.000004,
                        },
                    },
                    {
                        "model_name": "routed-but-not-a-menu-item",
                        "litellm_params": {
                            "model": "anthropic/w",
                            "input_cost_per_token": 0.000001,
                            "output_cost_per_token": 0.000001,
                        },
                        "model_info": {},
                    },
                ]
            )
        ),
    )
    reported = {m.id: m for m in await client.models()}

    assert reported["priced-beside-the-route"].priced
    assert reported["priced-beside-the-metadata"].priced
    # Half-priced bills half its traffic at zero, which breaks the budget brake
    # just as thoroughly as no price at all.
    assert not reported["priced-one-way-only"].priced

    assert reported["routed-but-not-a-menu-item"].selectable is False
    assert reported["priced-beside-the-metadata"].label == "Y"
    # No label given, so the model names itself rather than going blank.
    assert reported["priced-beside-the-route"].label == "priced-beside-the-route"


@pytest.mark.anyio
async def test_a_gateway_that_cannot_be_asked_does_not_answer_nothing():
    """An empty catalogue and an unreachable gateway are opposites: the first
    says this deployment serves no models, the second says ask again. Returning
    an empty list for the second takes every agent offline for a blip."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="bad gateway")

    client = gw.LlmGateway("http://gw", "mk", transport=_transport(handler))
    assert await client.models() is None


@pytest.mark.anyio
async def test_a_priced_model_added_at_runtime_is_offered_like_a_config_one():
    """A runtime model (``db_model``, added through the gateway's admin API
    rather than ``config.yaml``) is offered on the same terms as one declared in
    config: the price decides, not the source. ``db_model`` is left in the
    report only so the admin page knows which models it may edit. The price
    invariant is enforced in code on this path, since config.yaml's second pair
    of eyes is exactly what adding a model at runtime skips."""
    priced = {
        "input_cost_per_token": 0.000004,
        "output_cost_per_token": 0.000004,
    }
    client = gw.LlmGateway(
        "http://gw",
        "mk",
        transport=_transport(
            _model_info_response(
                [
                    {
                        "model_name": "added-at-runtime",
                        "litellm_params": {"model": "anthropic/x", **priced},
                        "model_info": {"cheese_selectable": True, "db_model": True},
                    },
                    {
                        "model_name": "declared-in-config",
                        "litellm_params": {"model": "anthropic/y", **priced},
                        "model_info": {"cheese_selectable": True, "db_model": False},
                    },
                ]
            )
        ),
    )
    reported = {m.id: m for m in await client.models()}
    assert reported["added-at-runtime"].selectable is True
    assert reported["declared-in-config"].selectable is True


@pytest.mark.anyio
async def test_a_blocked_model_is_never_offered():
    """The gateway still lists a blocked model, so the catalogue alone would put
    it in the picker — and the gateway would then refuse to route it, handing
    someone a route that cannot be called. A price does not rescue it: being
    priced and being blocked are independent, and only both together with the
    selectable mark produce a model worth offering."""
    priced = {
        "input_cost_per_token": 0.000004,
        "output_cost_per_token": 0.000004,
    }
    client = gw.LlmGateway(
        "http://gw",
        "mk",
        transport=_transport(
            _model_info_response(
                [
                    {
                        "model_name": "closed-again",
                        "litellm_params": {"model": "anthropic/z", **priced},
                        "model_info": {"cheese_selectable": True, "blocked": True},
                    },
                    {
                        "model_name": "still-open",
                        "litellm_params": {"model": "anthropic/w", **priced},
                        "model_info": {"cheese_selectable": True, "blocked": False},
                    },
                ]
            )
        ),
    )
    reported = {m.id: m for m in await client.models()}
    assert reported["closed-again"].priced is True
    assert reported["closed-again"].selectable is False
    assert reported["still-open"].selectable is True
