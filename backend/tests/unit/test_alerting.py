"""An alert exists to be read, which is a budget before it is a feature."""

import asyncio
import importlib
import uuid
from types import SimpleNamespace

import pytest

from app.core import alerting


@pytest.fixture(autouse=True)
def prefix(monkeypatch) -> str:
    """What this test remembers about repeats is its own: the state is shared
    Redis, and another test (or another run) reporting the same key must not
    make this one's first report look like a repeat."""
    mine = f"cheese:alert:test:{uuid.uuid4().hex}:"
    monkeypatch.setattr(alerting, "budget", alerting._Budget())
    monkeypatch.setattr(alerting, "REPEAT_KEY_PREFIX", mine, raising=False)
    return mine


def _capture(monkeypatch, sent: list[str]) -> None:
    monkeypatch.setattr(
        alerting.settings, "feishu_alert_webhook", "https://example/hook"
    )

    async def capture(text: str) -> None:
        sent.append(text)

    monkeypatch.setattr(alerting, "_post", capture)


@pytest.fixture
def posted(monkeypatch):
    """What would reach the channel, with no webhook and no network."""
    sent: list[str] = []
    _capture(monkeypatch, sent)
    return sent


@pytest.fixture
def clock(monkeypatch):
    now = [1_000_000.0]
    monkeypatch.setattr(alerting, "time", SimpleNamespace(time=lambda: now[0]))
    return now


async def _settled() -> None:
    """`send` is fire-and-forget; wait for what it started to finish."""
    while alerting._running:
        await asyncio.gather(*list(alerting._running), return_exceptions=True)


async def test_no_webhook_means_no_alerting_and_no_cost(monkeypatch):
    """Every developer machine and every test runs with this unset. The call
    sites must not have to know that, so `send` is a no-op rather than an error."""
    monkeypatch.setattr(alerting.settings, "feishu_alert_webhook", "")
    posted: list[str] = []
    monkeypatch.setattr(alerting, "_post", lambda text: posted.append(text))

    alerting.send("something broke", ["detail"])
    await _settled()

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


async def test_a_failure_that_keeps_failing_is_told_once(posted):
    """The 60-second poller against a machine somebody switched off. Its first
    report is the news; the 59 after it are the same news, and they spend a
    budget that the NEXT, different failure then cannot have."""
    for _ in range(50):
        alerting.send("设备离线", ["device：machine-1"], key="device-offline")
    await _settled()

    assert len(posted) == 1
    assert "设备离线" in posted[0]


async def test_two_different_failures_are_two_alerts(posted):
    alerting.send("设备离线", [], key="device-offline")
    alerting.send("数据库连接被拒", [], key="db-refused")
    await _settled()

    assert len(posted) == 2


async def test_the_repeats_are_counted_and_reported_when_it_comes_back(posted, clock):
    """Suppressed is not forgotten: what the reader needs after an hour of
    silence is that it never stopped, and how often."""
    alerting.send("设备离线", [], key="device-offline")
    await _settled()
    for _ in range(7):
        clock[0] += 60
        alerting.send("设备离线", [], key="device-offline")
        await _settled()
    clock[0] += 3600
    alerting.send("设备离线", [], key="device-offline")
    await _settled()

    assert len(posted) == 2
    assert "又发生了 7 次" in posted[1]


async def test_every_alert_says_when_it_happened(posted):
    """Feishu stamps its own messages with the moment it accepted them. That is
    not when the error was, and for a repeat it can be hours out."""
    alerting.send("设备离线", [], key="device-offline", when=1757000000.0)
    await _settled()

    assert "时间：" in posted[0]
    stamped = [line for line in posted[0].split("\n") if line.startswith("时间：")]
    assert len(stamped) == 1
    assert "2025-09-04" in stamped[0] or "2025-09-05" in stamped[0], stamped


async def test_the_same_key_alerts_again_once_the_window_has_passed(posted, clock):
    alerting.send("设备离线", [], key="device-offline")
    await _settled()
    clock[0] += 3600 + 1
    alerting.send("设备离线", [], key="device-offline")
    await _settled()

    assert len(posted) == 2


async def test_a_failure_that_never_stops_is_told_less_and_less_often(posted, clock):
    """A cleanup retrying every 61 minutes against a device that stayed off for
    three days was reported 40 times, each retry landing just past the hour.
    After the first day of it, once a day says it is still going on."""
    for _ in range(72):  # three days of hourly retries
        alerting.send("cleanup failed", [], key="cleanup-device-offline")
        await _settled()
        clock[0] += 61 * 60

    assert 3 <= len(posted) <= 6, len(posted)


async def test_a_failure_that_went_away_is_news_again_when_it_returns(posted, clock):
    """Telling a returning failure less often is right only while it is the same
    episode; one that stopped for days and is back is new news."""
    for _ in range(3):
        alerting.send("cleanup failed", [], key="cleanup-device-offline")
        await _settled()
        clock[0] += 61 * 60
    told = len(posted)
    clock[0] += 3 * 24 * 3600
    alerting.send("cleanup failed", [], key="cleanup-device-offline")
    await _settled()
    clock[0] += 3600 + 1
    alerting.send("cleanup failed", [], key="cleanup-device-offline")
    await _settled()

    assert len(posted) == told + 2


async def test_a_restart_does_not_make_an_ongoing_failure_news_again(
    posted, prefix, monkeypatch
):
    """Every deploy restarts the backend, several times a day. A failure that
    was reported a minute before the restart is not news a minute after it."""
    alerting.send("cleanup failed", [], key="cleanup-device-offline")
    await _settled()
    # A new process: nothing the old one held in memory survives.
    importlib.reload(alerting)
    alerting.get_redis_client.cache_clear()
    _capture(monkeypatch, posted)
    monkeypatch.setattr(alerting, "REPEAT_KEY_PREFIX", prefix, raising=False)
    alerting.send("cleanup failed", [], key="cleanup-device-offline")
    await _settled()

    assert len(posted) == 1


async def test_a_broken_redis_does_not_silence_alerts(posted, monkeypatch):
    """An alert held behind a Redis that is down is the outage nobody hears
    about; the budget, not the repeat memory, is what bounds it then."""

    class Down:
        async def eval(self, *_args, **_kwargs):
            raise ConnectionError("redis is down")

    monkeypatch.setattr(alerting, "get_redis_client", lambda: Down())
    alerting.send("数据库连接被拒", [], key="db-refused")
    await _settled()

    assert len(posted) == 1
