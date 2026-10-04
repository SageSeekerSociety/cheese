"""A warm pool whose provider keeps failing says so when it starts and when it
recovers, not on every sweep.

The sweep runs every few seconds; a provider that is down fails it the same way
each time, and every line it wrote was an alert.
"""

import logging

from app.core.config import settings
from app.domain.machine import warm
from app.domain.machine.microcloud import MicroCloudError
from tests.microcloud import FakeMicroCloud


class FlakyCloud(FakeMicroCloud):
    def __init__(self) -> None:
        super().__init__()
        self.down = True

    async def list_offerings(self):
        if self.down:
            raise MicroCloudError("provider unavailable", status=503)
        return await super().list_offerings()


def _said(caplog) -> list[tuple[int, str]]:
    return [
        (record.levelno, record.getMessage())
        for record in caplog.records
        if record.name == warm.logger.name and "warm pool" in record.getMessage()
    ]


def test_a_persistently_failing_pool_says_so_once_then_again_on_recovery(
    client, monkeypatch, caplog
):
    monkeypatch.setattr(settings, "microcloud_warm_pool_size", 1)
    monkeypatch.setattr(settings, "connector_public_base", "https://example.invalid")
    monkeypatch.setattr(warm, "_health", warm.PoolHealth())
    cloud = FlakyCloud()

    async def sweeps(times: int) -> None:
        for _ in range(times):
            await warm.sweep_warm_pool(client.test_request_factory, cloud)

    caplog.set_level(logging.INFO, logger=warm.logger.name)
    client.portal.call(sweeps, 5)
    failing = _said(caplog)
    assert [level for level, _ in failing] == [logging.ERROR]
    assert "provider unavailable" in failing[0][1]

    caplog.clear()
    cloud.down = False
    client.portal.call(sweeps, 3)
    assert [level for level, _ in _said(caplog) if level >= logging.INFO] == [
        logging.INFO
    ]
    assert "recovered" in _said(caplog)[0][1]

    caplog.clear()
    cloud.down = True
    monkeypatch.setattr(settings, "microcloud_warm_pool_size", 2)
    client.portal.call(sweeps, 3)
    assert [level for level, _ in _said(caplog)] == [logging.ERROR]


def test_a_pool_failing_for_long_is_reminded_of_at_the_reminder_interval(
    client, monkeypatch, caplog
):
    monkeypatch.setattr(settings, "microcloud_warm_pool_size", 1)
    monkeypatch.setattr(settings, "connector_public_base", "https://example.invalid")
    monkeypatch.setattr(warm, "_health", warm.PoolHealth())
    cloud = FlakyCloud()
    caplog.set_level(logging.INFO, logger=warm.logger.name)

    client.portal.call(warm.sweep_warm_pool, client.test_request_factory, cloud)
    client.portal.call(warm.sweep_warm_pool, client.test_request_factory, cloud)
    assert len(_said(caplog)) == 1

    monkeypatch.setattr(warm, "FAILING_REMINDER_S", 0.0)
    client.portal.call(warm.sweep_warm_pool, client.test_request_factory, cloud)
    reminded = _said(caplog)
    assert len(reminded) == 2
    assert "still failing" in reminded[1][1]
