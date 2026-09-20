"""An alert exists to be read, which is a budget before it is a feature."""

import asyncio
from types import SimpleNamespace

import pytest

from app.core import alerting


@pytest.fixture(autouse=True)
def _fresh_budget(monkeypatch):
    monkeypatch.setattr(alerting, "budget", alerting._Budget())
    monkeypatch.setattr(alerting, "repeated", alerting._Repeats())


@pytest.fixture
def posted(monkeypatch):
    """What would reach the channel, with no webhook and no network.

    Captured at call time rather than on delivery: `send` is fire-and-forget,
    and in a test there is no loop to deliver on.
    """
    sent: list[str] = []
    monkeypatch.setattr(
        alerting.settings, "feishu_alert_webhook", "https://example/hook"
    )
    monkeypatch.setattr(alerting, "_post", lambda text: sent.append(text))
    return sent


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


def test_a_failure_that_keeps_failing_is_told_once(posted):
    """The 60-second poller against a machine somebody switched off. Its first
    report is the news; the 59 after it are the same news, and they spend a
    budget that the NEXT, different failure then cannot have."""
    for _ in range(50):
        alerting.send("设备离线", ["device：machine-1"], key="device-offline")

    assert len(posted) == 1
    assert "设备离线" in posted[0]


def test_two_different_failures_are_two_alerts(posted):
    alerting.send("设备离线", [], key="device-offline")
    alerting.send("数据库连接被拒", [], key="db-refused")

    assert len(posted) == 2


def test_the_repeats_are_counted_and_reported_when_it_comes_back(posted, monkeypatch):
    """Suppressed is not forgotten: what the reader needs after an hour of
    silence is that it never stopped, and how often."""
    clock = [1000.0]
    monkeypatch.setattr(alerting, "time", SimpleNamespace(time=lambda: clock[0]))

    alerting.send("设备离线", [], key="device-offline")
    for _ in range(7):
        clock[0] += 60
        alerting.send("设备离线", [], key="device-offline")
    clock[0] += alerting.REPEAT_WINDOW_S
    alerting.send("设备离线", [], key="device-offline")

    assert len(posted) == 2
    assert "又发生了 7 次" in posted[1]


def test_every_alert_says_when_it_happened(posted):
    """Feishu stamps its own messages with the moment it accepted them. That is
    not when the error was, and for a repeat it can be an hour out."""
    alerting.send("设备离线", [], key="device-offline", when=1757000000.0)

    assert "时间：" in posted[0]
    stamped = [line for line in posted[0].split("\n") if line.startswith("时间：")]
    assert len(stamped) == 1
    assert "2025-09-04" in stamped[0] or "2025-09-05" in stamped[0], stamped


def test_the_same_key_alerts_again_once_the_window_has_passed(posted, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(alerting, "time", SimpleNamespace(time=lambda: clock[0]))

    alerting.send("设备离线", [], key="device-offline")
    clock[0] += alerting.REPEAT_WINDOW_S + 1
    alerting.send("设备离线", [], key="device-offline")

    assert len(posted) == 2


def test_remembering_failures_is_bounded(posted):
    """A platform that invents a new distinct failure forever must not make this
    process grow forever with it."""
    for i in range(alerting.MAX_TRACKED * 2):
        alerting.send("something broke", [], key=f"failure-{i}")

    assert len(alerting.repeated._first) <= alerting.MAX_TRACKED
