"""The spend cap alert says each crossing once: 80% of the cap, then the cap.

The gateway is a stub that answers `/model/info` with capped rows and the usage
endpoint with the ledger by upstream model (`breakdown.models`), which is the
column spend_cap.py sums. Redis is a dict.
"""

import httpx
import pytest

from app.domain.agent import spend_cap_alerts
from app.domain.agent.gateway_admin import GatewayAdmin

_ARGON = "gemini/gemini-4-argon"


class _Gateway:
    def __init__(self) -> None:
        self.caps: dict[str, float] = {}  # model name -> cap on its row
        self.ledger: dict[str, float] = {}

    def _handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/model/info":
            rows = [
                {
                    "model_name": name,
                    "litellm_params": {"model": _ARGON},
                    "model_info": {
                        "id": f"id-{name}",
                        "db_model": True,
                        "cheese_spend_cap_usd": cap,
                    },
                }
                for name, cap in self.caps.items()
            ]
            return httpx.Response(200, json={"data": rows})
        if request.url.path == "/user/daily/activity/aggregated":
            assert request.url.params["start_date"] == "2000-01-01"
            models = {k: {"metrics": {"spend": v}} for k, v in self.ledger.items()}
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"date": "2026-10-20", "breakdown": {"models": models}}
                    ],
                    "metadata": {},
                },
            )
        return httpx.Response(404, json={"detail": "stub"})

    def admin(self) -> GatewayAdmin:
        return GatewayAdmin(
            "http://gw", "mk", transport=httpx.MockTransport(self._handle)
        )


class _Redis:
    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}

    async def get(self, key: str) -> bytes | None:
        return self.data.get(key)

    async def set(self, key: str, value: str) -> None:
        self.data[key] = value.encode()


@pytest.fixture
def sent(monkeypatch) -> list[tuple[str, list[str]]]:
    out: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(
        spend_cap_alerts.alerting,
        "send",
        lambda title, lines, **_: out.append((title, lines)),
    )
    return out


async def _tick(gw: _Gateway, redis: _Redis) -> list:
    return await spend_cap_alerts.check(gw.admin(), redis, today="2026-10-22")


@pytest.mark.anyio
async def test_each_crossing_is_said_once(sent):
    gw, redis = _Gateway(), _Redis()
    gw.caps = {"argon": 2000.0, "argon-b": 2000.0}

    gw.ledger = {_ARGON: 1000.0}
    assert await _tick(gw, redis) == []

    gw.ledger = {_ARGON: 1700.0}
    assert await _tick(gw, redis) == [(_ARGON, 0.8)]
    assert await _tick(gw, redis) == []

    gw.ledger = {_ARGON: 2000.5}
    assert await _tick(gw, redis) == [(_ARGON, 1.0)]
    assert await _tick(gw, redis) == []

    assert [title for title, _ in sent] == [
        "模型消费上限已用 80%",
        "模型已达消费上限，调用被拒绝",
    ]
    assert sent[0][1][0] == f"argon、argon-b（上游 {_ARGON}）"
    assert sent[0][1][1] == "累计已用 $1700.00 / 上限 $2000.00（85%）"


@pytest.mark.anyio
async def test_a_raised_cap_is_news_again(sent):
    gw, redis = _Gateway(), _Redis()
    gw.caps = {"argon": 2000.0}
    gw.ledger = {_ARGON: 2010.0}
    assert await _tick(gw, redis) == [(_ARGON, 1.0)]

    gw.caps = {"argon": 4000.0}
    assert await _tick(gw, redis) == []

    gw.ledger = {_ARGON: 3300.0}
    assert await _tick(gw, redis) == [(_ARGON, 0.8)]


@pytest.mark.anyio
async def test_straight_past_the_cap_says_only_the_cap(sent):
    gw, redis = _Gateway(), _Redis()
    gw.caps = {"argon": 100.0}
    gw.ledger = {_ARGON: 150.0}

    assert await _tick(gw, redis) == [(_ARGON, 1.0)]
    assert len(sent) == 1


@pytest.mark.anyio
async def test_no_cap_reads_no_ledger(sent):
    gw, redis = _Gateway(), _Redis()
    gw.caps = {}
    assert await _tick(gw, redis) == []
    assert sent == []


@pytest.mark.anyio
async def test_without_admin_credentials_the_clock_does_nothing(monkeypatch, sent):
    monkeypatch.setattr(spend_cap_alerts.settings, "llm_gateway_admin_base", "")
    assert await spend_cap_alerts.sweep() == []
    assert sent == []


@pytest.mark.parametrize(
    ("used", "expected"), [(0, 0.0), (79.9, 0.0), (80, 0.8), (99.9, 0.8), (100, 1.0)]
)
def test_the_level_is_the_highest_mark_reached(used, expected):
    assert spend_cap_alerts.level(used, 100.0) == expected
