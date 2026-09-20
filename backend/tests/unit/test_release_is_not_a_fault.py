"""Replacing the connection owner is a release, not an incident.

Two things happen every time that process is replaced, and both used to arrive
in the alert channel as faults of this server. At 01:47 UTC on 2026-09-20,
seconds after it was replaced: the pollers' in-flight reads came back
「Server disconnected without sending a response」 and were logged as
「pi entry read failed」 / 「Codex journal read failed」; and a call that
reached a machine whose connector had re-attached but not yet finished
updating raised a bare RuntimeError, which is an unhandled 500.
"""

import asyncio
import logging

import httpx
import pytest

from app.domain.agent.device_hub import DeviceCallError, DeviceHub, DeviceNotReady


class _Transport:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, msg: dict) -> None:
        self.sent.append(msg)


@pytest.mark.anyio
async def test_a_machine_still_updating_its_connector_is_not_a_server_fault() -> None:
    hub = DeviceHub()
    await hub.attach_device("machine-7", _Transport())
    # `hello` without `executor: True` is a connector that has re-attached and
    # is still updating itself — the state the fleet is in for a few seconds
    # after the owner is replaced.
    await hub.on_device_message("machine-7", {"t": "hello", "executor": False})

    with pytest.raises(DeviceNotReady) as raised:
        await hub.call_executor("machine-7", "/state", "ping", {})

    # A caller that waits out a machine's own answer waits this out too.
    assert isinstance(raised.value, DeviceCallError)
    assert "still updating" in str(raised.value)


@pytest.mark.anyio
async def test_the_owner_going_away_mid_read_is_waited_out_not_reported(
    monkeypatch, caplog
) -> None:
    from app.domain.agent.harness.pi import runtime as pi_runtime

    poller = pi_runtime.PiRuntime.__new__(pi_runtime.PiRuntime)
    topic = __import__("uuid").uuid4()
    reads = 0

    class Subscription:
        async def drain(self) -> int:
            nonlocal reads
            reads += 1
            if reads > 2:
                poller.subscriptions.clear()
                return 0
            raise httpx.RemoteProtocolError(
                "Server disconnected without sending a response."
            )

    poller.subscriptions = {topic: Subscription()}
    poller.work = {}
    poller.live = {}
    poller.woken = {}
    poller.tasks = {}

    class _NoSleep:
        """Real asyncio, minus the two-second wait between retries."""

        def __getattr__(self, name):
            return getattr(asyncio, name)

        async def sleep(self, _seconds):
            return None

    monkeypatch.setattr(pi_runtime, "asyncio", _NoSleep())

    with caplog.at_level(logging.WARNING, logger=pi_runtime.logger.name):
        await poller._poll(topic)

    said = [r for r in caplog.records if r.name == pi_runtime.logger.name]
    assert len(said) == 1, said
    assert said[0].levelname == "WARNING"
    assert "waiting for the connection owner" in said[0].getMessage()
