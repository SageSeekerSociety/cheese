"""Forge requests are attributed to the periodic job or to everyone else.

The counters answer one question about a shared installation's minute: which of
the traffic is the platform's own clock, and which periodic job it belongs to?
They count attempts that were actually sent (a caller hook that yields first is
not one), and they bucket the raw response before ``forge_etags`` turns a 304
into a cached 200.

Only requests carrying a token this platform minted for an installation are
attributable, and the labels stay at a registered job name (or `other`) plus
that installation number — no path, URL or token is kept.
"""

import asyncio
import time
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.core import forge_quota, metrics
from app.core.background import PeriodicRunner
from app.core.forge_http import background_job, forge_client

pytestmark = pytest.mark.anyio


class _Runs:
    """Every tick belongs to this process; the attribution tests run one."""

    async def claim(
        self, name: str, *, interval_s: float, who: str, now: datetime
    ) -> bool:
        return True


def _auth(installation: int, token: str) -> dict[str, str]:
    forge_quota.own(token, installation, time.time() + 3600)
    return {"Authorization": f"Bearer {token}"}


def _started(job: str, installation: int) -> int:
    return metrics.registry.counter(
        "forge_requests_started_total",
        {"job": job, "installation": str(installation)},
    ).value


def _responses(bucket: str, job: str, installation: int) -> int:
    return metrics.registry.counter(
        f"forge_responses_{bucket}_total",
        {"job": job, "installation": str(installation)},
    ).value


async def test_each_periodic_job_is_tagged_by_its_registered_name():
    installation = 9910001
    auth = _auth(installation, "ghs_attribution_job")
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={}))
    url = "https://api.invalid/repos/o/r/actions/runs"

    async def run_once(name: str) -> None:
        ran = asyncio.Event()

        async def job() -> None:
            async with forge_client(transport=transport) as client:
                await client.get(url, headers=auth)
            ran.set()

        runner = PeriodicRunner(name, 60, job)
        # Overdue, so the first tick runs at once and the next is a minute away.
        runner.start(_Runs(), datetime.now(UTC) - timedelta(seconds=120))
        try:
            await asyncio.wait_for(ran.wait(), timeout=2)
        finally:
            await runner.stop()

    await run_once("ci activity sweep")
    await run_once("notification digest")

    async with forge_client(transport=transport) as client:
        await client.get(url, headers=auth)

    assert _started("ci activity sweep", installation) == 1
    assert _started("notification digest", installation) == 1
    assert _started("other", installation) == 1
    for name in ("ci activity sweep", "notification digest", "other"):
        assert _responses("core_rest_non_304", name, installation) == 1


async def test_the_job_name_is_reset_when_a_job_raises_or_is_cancelled():
    installation = 9910006
    auth = _auth(installation, "ghs_attribution_leak")
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={}))

    for exc_type, suffix in (
        (RuntimeError, "raise"),
        (asyncio.CancelledError, "cancel"),
    ):
        with pytest.raises(exc_type):
            with background_job("ci activity sweep"):
                raise exc_type()
        async with forge_client(transport=transport) as client:
            await client.get(
                f"https://api.invalid/repos/o/r/commits/{suffix}", headers=auth
            )

    assert _started("ci activity sweep", installation) == 0
    assert _started("other", installation) == 2


async def test_a_raw_304_is_counted_once_before_the_etag_layer_restores_the_body():
    installation = 9910002
    auth = _auth(installation, "ghs_attribution_304")
    url = "https://api.invalid/repos/o/r/commits/9910002/status"
    etag = '"v1"'

    def handle(request: httpx.Request) -> httpx.Response:
        if request.headers.get("if-none-match") == etag:
            return httpx.Response(304, headers={"etag": etag})
        return httpx.Response(200, headers={"etag": etag}, json={"ok": True})

    async with forge_client(transport=httpx.MockTransport(handle)) as client:
        first = await client.get(url, headers=auth)
        second = await client.get(url, headers=auth)

    assert first.status_code == 200
    assert second.status_code == 200  # restored from the kept answer
    assert _responses("core_rest_non_304", "other", installation) == 1
    assert _responses("core_rest_304", "other", installation) == 1


async def test_graphql_and_the_free_rate_limit_probe_are_not_core_rest():
    installation = 9910003
    auth = _auth(installation, "ghs_attribution_graphql")
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={}))

    async with forge_client(transport=transport) as client:
        await client.post(
            "https://api.invalid/graphql", headers=auth, json={"query": "{}"}
        )
        await client.get("https://api.invalid/rate_limit", headers=auth)

    assert _responses("graphql", "other", installation) == 1
    assert _responses("free_rate_limit", "other", installation) == 1
    assert _responses("core_rest_non_304", "other", installation) == 0


async def test_a_request_refused_by_a_caller_hook_is_not_counted():
    installation = 9910004
    auth = _auth(installation, "ghs_attribution_refused")

    async def refuse(request: httpx.Request) -> None:
        raise RuntimeError("yielded before sending")

    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={}))
    async with forge_client(
        transport=transport, event_hooks={"request": [refuse]}
    ) as client:
        with pytest.raises(RuntimeError):
            await client.get("https://api.invalid/repos/o/r", headers=auth)

    assert _started("job", installation) == 0
    assert _started("other", installation) == 0


async def test_a_token_the_platform_did_not_mint_is_not_counted():
    installation = 9910005
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={}))

    async with forge_client(transport=transport) as client:
        await client.get(
            "https://api.invalid/repos/o/r",
            headers={"Authorization": "Bearer someone-elses-token"},
        )

    assert _started("job", installation) == 0
    assert _started("other", installation) == 0


def test_export_identifies_the_process_and_its_sample_window():
    first = metrics.MetricsRegistry().export()
    second = metrics.MetricsRegistry().export()

    assert first["instance"] and second["instance"]
    assert first["instance"] != second["instance"]
    started = datetime.fromisoformat(first["started_at"])
    assert started.tzinfo is not None
    assert first["uptime_seconds"] >= 0
