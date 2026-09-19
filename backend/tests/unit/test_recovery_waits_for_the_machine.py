"""A machine that is not ready yet is waited out, not reported as a fault.

Every device that connects has its sessions recovered
(connector.recover_business_state → ChatService.recover_sessions). A machine
whose runner socket is not up yet, or whose room home is gone, answers the
recovery call with a failure of its own — `DeviceCallError`, carrying the
machine's words. That was logged at ERROR, so one such machine reconnecting
produced one alert per reconnect (「dial unix /tmp/cheese-execution-…sock: no
such file」, 2026-09-19). Its next connection runs recovery again, which is what
makes this a state to wait out.
"""

import logging

import pytest

from app.api.routes.connector import recover_business_state
from app.domain.agent.device_hub import DeviceCallError

WORDS = "dial unix /tmp/cheese-execution-1000-2d8dc1e0.sock: no such file or directory"


class _Chat:
    def __init__(self, failure: Exception) -> None:
        self._failure = failure

    async def recover_sessions(self, device_id: str) -> int:
        raise self._failure


class _Wakeup:
    async def wake_device(self, device_id: str) -> None:
        return None


@pytest.fixture
def quiet_reconnect(monkeypatch):
    monkeypatch.setattr("app.core.background.spawn", lambda coro, *, name: coro.close())
    monkeypatch.setattr("app.api.deps.get_cloud_wakeup", lambda: _Wakeup())


@pytest.mark.anyio
async def test_a_machine_that_says_no_is_waited_out_with_its_own_words(
    monkeypatch, caplog, quiet_reconnect
) -> None:
    monkeypatch.setattr(
        "app.api.deps.get_chat_service", lambda: _Chat(DeviceCallError(WORDS))
    )
    with caplog.at_level(logging.WARNING, logger="app.api.routes.connector"):
        await recover_business_state("machine-7")
    records = [r for r in caplog.records if r.name == "app.api.routes.connector"]
    assert [r.levelname for r in records] == ["WARNING"]
    assert "no such file" in records[0].getMessage()


@pytest.mark.anyio
async def test_a_real_recovery_failure_is_still_an_error(
    monkeypatch, caplog, quiet_reconnect
) -> None:
    monkeypatch.setattr(
        "app.api.deps.get_chat_service", lambda: _Chat(ValueError("bad row"))
    )
    with caplog.at_level(logging.WARNING, logger="app.api.routes.connector"):
        await recover_business_state("machine-7")
    records = [r for r in caplog.records if r.name == "app.api.routes.connector"]
    assert [r.levelname for r in records] == ["ERROR"]


@pytest.mark.anyio
async def test_one_session_saying_no_does_not_stop_the_others(monkeypatch) -> None:
    """The same standing inside recover_sessions: the topic whose machine said
    no is skipped, every other session is still recovered."""
    import uuid

    from app.domain.agent.chat import ChatService

    replayed = []
    sessions = [
        type("S", (), {"topic_id": uuid.uuid4()})(),
        type("S", (), {"topic_id": uuid.uuid4()})(),
    ]

    class Compute:
        async def recover_sessions(self, device_id):
            return sessions

        async def replay(self, session, known_texts):
            if session is sessions[0]:
                raise DeviceCallError(WORDS)
            replayed.append(session)

    chat = ChatService.__new__(ChatService)
    chat._compute = Compute()
    chat._said = lambda session: _said()

    async def _said():
        return set()

    assert await chat.recover_sessions("machine-7") == 2
    assert replayed == [sessions[1]]
