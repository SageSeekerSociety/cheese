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
    from app.domain.agent.session_host import host as session_host
    from app.domain.agent.session_host.contract import SessionRef
    from app.domain.agent.session_host.driver import Launched

    host = session_host.SessionHost(hub=object())
    ref = SessionRef("pi", "rooms/a-room/pi")
    running = session_host._Running(
        "center", Launched("", frozenset(), ""), None, "", None
    )
    reads = 0

    class Subscription:
        # A runner that holds reads, answering nothing besides records.
        heard: dict = {}

        async def drain(self, wait: float = 0.0) -> int:
            nonlocal reads
            reads += 1
            if reads > 2:
                running.stopping = True
                return 0
            raise httpx.RemoteProtocolError(
                "Server disconnected without sending a response."
            )

    class _NoSleep:
        """Real asyncio, minus the two-second wait between retries."""

        def __getattr__(self, name):
            return getattr(asyncio, name)

        async def sleep(self, _seconds):
            return None

    monkeypatch.setattr(session_host, "asyncio", _NoSleep())
    heard = []

    async def hand(read):
        heard.append(read.event)

    with caplog.at_level(logging.WARNING, logger=session_host.logger.name):
        await host._pump(ref, running, Subscription(), hand, False)

    said = [r for r in caplog.records if r.name == session_host.logger.name]
    assert len(said) == 1, said
    assert said[0].levelname == "WARNING"
    assert "waiting for the connection owner" in said[0].getMessage()
