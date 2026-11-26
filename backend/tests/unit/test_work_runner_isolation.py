"""A test that entered the app must not decide the next test's recovery.

``get_work_runner`` is one runner per process, and its two admission flags say
what the PROCESS is doing: may a turn start here, does this process listen to
sessions. Entering the app runs ``main.lifespan``, which clears both — a backend
arriving does not listen yet and one on its way out stops — and leaving it clears
them again. A test process does neither; it goes on to run the next test. So the
tests that enter the app for one response used to leave every later test in the
same xdist worker unable to recover a machine's sessions, and the failure landed
in another file entirely (``test_recovery_burst_is_bounded.py``, ``assert 0 == 8``
— backend/pure, 2026-10-26, three times in one night).

``_a_test_process_never_hands_the_work_over`` in ``tests/conftest.py`` is the
fix; this is the guard on it. The offender is a fixture rather than a test case,
so it runs before whichever case this file holds in whichever shard process
holds it: CI partitions cases by node id, and a pair ordered by file position
would stop guarding the moment a split fell between its halves.
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api.routes.connector import devices
from app.main import app


@pytest.fixture(scope="module", autouse=True)
def _an_earlier_test_entered_the_app() -> Iterator[None]:
    """What any test that wants one response from the app does to the process."""
    with TestClient(app):
        pass
    yield


@pytest.fixture
def quiet_reconnect(monkeypatch):
    monkeypatch.setattr("app.core.background.spawn", lambda coro, *, name: coro.close())


@pytest.mark.anyio
async def test_a_machine_that_reconnects_still_has_its_sessions_recovered(
    monkeypatch,
    quiet_reconnect,
) -> None:
    """The machine's owner reconnects it and its sessions come back.

    A process that listens to no sessions returns from recovery before it reads
    the machine, and nothing tells the machine's owner anything went wrong.
    """
    recovered: list[str] = []

    class Chat:
        async def recover_sessions(self, device_id: str) -> int:
            recovered.append(device_id)
            return 0

    monkeypatch.setattr("app.api.deps.get_chat_service", lambda: Chat())
    await devices.recover_business_state("machine-1")
    assert recovered == ["machine-1"]
