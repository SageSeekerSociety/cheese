"""An alert exists to be read, which is a budget before it is a feature."""

import asyncio

import pytest

from app.core import alerting


@pytest.fixture(autouse=True)
def _fresh_budget(monkeypatch):
    monkeypatch.setattr(alerting, "budget", alerting._Budget())


def test_no_webhook_means_no_alerting_and_no_cost(monkeypatch):
    """Every developer machine and every test runs with this unset. The call
    sites must not have to know that, so `send` is a no-op rather than an error."""
    monkeypatch.setattr(alerting.settings, "feishu_alert_webhook", "")
    posted: list[str] = []
    monkeypatch.setattr(alerting, "_post", lambda text: posted.append(text))

    alerting.send("something broke", ["detail"])

    assert posted == []
    assert alerting.configured() is False


def test_a_flood_says_so_once_instead_of_arriving_in_full(monkeypatch):
    """A bad deploy makes many DIFFERENT errors at once, so first-occurrence is
    not by itself a bound. A channel that receives all of them is muted by the
    end of the day, and a muted alert reads as coverage while providing none."""
    monkeypatch.setattr(
        alerting.settings, "feishu_alert_webhook", "https://example/hook"
    )
    now = 1000.0
    attempts = range(alerting.MAX_PER_WINDOW + 5)
    verdicts = [alerting.budget.take(now + i * 0.1) for i in attempts]

    assert verdicts[: alerting.MAX_PER_WINDOW] == ["send"] * alerting.MAX_PER_WINDOW
    assert verdicts[alerting.MAX_PER_WINDOW] == "flood"
    assert set(verdicts[alerting.MAX_PER_WINDOW + 1 :]) == {"drop"}


def test_the_budget_refills_so_tomorrow_is_not_silenced_by_today(monkeypatch):
    now = 1000.0
    for i in range(alerting.MAX_PER_WINDOW):
        alerting.budget.take(now + i * 0.1)
    assert alerting.budget.take(now + 1) == "flood"

    assert alerting.budget.take(now + alerting.WINDOW_S + 1) == "send"


@pytest.mark.anyio
async def test_a_webhook_that_fails_does_not_reach_its_caller(monkeypatch):
    """The caller is a user's request. An alert is the least important thing
    happening on it, and must not be able to fail it or slow it down."""
    monkeypatch.setattr(
        alerting.settings, "feishu_alert_webhook", "https://example/hook"
    )

    class Exploding:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def post(self, *_args, **_kwargs):
            raise RuntimeError("feishu is down")

    monkeypatch.setattr(alerting.httpx, "AsyncClient", lambda **_: Exploding())

    alerting.send("something broke", ["detail"])
    await asyncio.sleep(0)
    await asyncio.gather(*list(alerting._running), return_exceptions=True)
