"""The relay balance clock says so while there is still time to top up."""

import pytest

from app.core.config import settings
from app.domain.agent import relay_balance


def _balance(**over):
    body = {
        "available": True,
        "total": 380.0,
        "granted": 20.0,
        "topped_up": 360.0,
        "currency": "CNY",
    }
    body.update(over)
    return relay_balance.Balance(**body)


@pytest.fixture
def said(monkeypatch):
    calls = []

    def send(title, lines, *, key=None, when=None):
        calls.append((title, lines, key))

    monkeypatch.setattr(relay_balance.alerting, "send", send)
    monkeypatch.setattr(settings, "deepseek_api_key", "sk-test")
    monkeypatch.setattr(settings, "deepseek_balance_alert_cny", 50.0)
    return calls


def test_the_endpoint_is_read_as_the_largest_total():
    got = relay_balance.read(
        {
            "is_available": False,
            "balance_infos": [
                {"currency": "USD", "total_balance": "1.00"},
                {"currency": "CNY", "total_balance": "120.50"},
            ],
        }
    )
    assert got == relay_balance.Balance(
        available=False, total=120.5, granted=0.0, topped_up=0.0, currency="CNY"
    )


async def test_a_deployment_without_a_key_reads_no_balance(said):
    async def fetch(key):
        raise AssertionError("must not call the endpoint without a key")

    settings.deepseek_api_key = ""
    assert await relay_balance.sweep(fetch_balance=fetch) is None
    assert said == []


async def test_a_nearly_spent_balance_is_called_out(said):
    async def fetch(key):
        assert key == "sk-test"
        return _balance(total=12.0, granted=0.0, topped_up=12.0)

    assert await relay_balance.sweep(fetch_balance=fetch) == _balance(
        total=12.0, granted=0.0, topped_up=12.0
    )
    assert [call[0] for call in said] == ["DeepSeek 余额快见底了"]
    assert "12 CNY" in said[0][1][0]
    assert said[0][2] == relay_balance.ALERT_KEY


async def test_a_balance_with_room_says_nothing(said):
    async def fetch(key):
        return _balance(total=500.0)

    await relay_balance.sweep(fetch_balance=fetch)
    assert said == []


async def test_a_refused_upstream_is_called_out_as_already_stopped(said):
    async def fetch(key):
        return _balance(available=False, total=0.0, granted=0.0, topped_up=0.0)

    await relay_balance.sweep(fetch_balance=fetch)
    assert [call[0] for call in said] == ["DeepSeek 余额不足，调用已经被拒"]


async def test_a_failed_read_is_not_an_alert(said):
    async def fetch(key):
        raise RuntimeError("connection reset")

    assert await relay_balance.sweep(fetch_balance=fetch) is None
    assert said == []
