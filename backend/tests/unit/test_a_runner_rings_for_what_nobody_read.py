"""A runner tells the backend when it holds records nobody has read, and the
backend reads exactly the seat whose credential rang.

The ring carries nothing: the read that follows takes the records from its own
cursor, so a ring that is lost only costs time.
"""

import asyncio
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from app.api.routes.sandbox import journal_written
from app.core.errors import UnauthorizedError
from app.domain.agent.harness.channel import mint_session_token
from app.domain.agent.harness.driven import runner
from tests.unit.test_driven_harness import Journal, call

PROXIES = ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY", "all_proxy")


class Session(runner.Runner[Journal]):
    """A session that writes when told to, read the way a backend reads."""

    def __init__(self, state: Path):
        super().__init__(state, Journal, "records.sqlite", idle_exit_s=0)

    async def start(self) -> None:
        self.claim()
        await self.listen(2**16)

    def busy(self) -> bool:
        return False

    async def dispatch(self, method: str, params: dict) -> dict:
        if method == "events":
            return {"events": self.records(int(params.get("after", 0)))}
        raise ValueError(method)


@pytest.fixture
def backend(monkeypatch):
    """Where the runner rings: what it was rung with, path and credential."""
    rings: list[tuple[str, str | None]] = []

    class Door(BaseHTTPRequestHandler):
        def do_POST(self):
            rings.append((self.path, self.headers.get("X-Cheese-Token")))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Door)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    for name in PROXIES + ("ALL_PROXY",):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CHEESE_API", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setenv("CHEESE_TOKEN", "the-session-credential")
    try:
        yield rings
    finally:
        server.shutdown()


async def _until(check, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < deadline, "the condition never came true"
        await asyncio.sleep(0.02)


@pytest.mark.anyio
async def test_a_record_nobody_has_read_rings_the_backend(tmp_path, backend):
    session = Session(tmp_path / "state")
    await session.start()
    try:
        # A background task finishing: nobody asked, nobody is reading.
        await asyncio.sleep(0.6)
        session.journal.append({"task": "done"})
        await _until(lambda: backend, timeout=2.0)
        assert backend[0] == ("/sandbox/journal-written", "the-session-credential")

        # Once read, it is not rung for again.
        await call(session.state, "events", {"after": 0})
        rung = len(backend)
        await asyncio.sleep(0.6)
        assert len(backend) == rung
    finally:
        await session.close()


@pytest.mark.anyio
async def test_a_backend_reading_at_its_floor_is_not_rung(tmp_path, backend):
    session = Session(tmp_path / "state")
    await session.start()
    stop = asyncio.Event()

    async def read() -> None:
        after = 0
        while not stop.is_set():
            rows = (await call(session.state, "events", {"after": after}))["events"]
            after = rows[-1]["sequence"] if rows else after
            await asyncio.sleep(0.1)

    reading = asyncio.create_task(read())
    try:
        for n in range(10):
            session.journal.append({"said": n})
            await asyncio.sleep(0.1)
        assert backend == []
    finally:
        stop.set()
        await reading
        await session.close()


class Pool:
    def __init__(self) -> None:
        self.woken: list[tuple[uuid.UUID, str]] = []

    def wake(self, topic_id: uuid.UUID, agent_handle: str) -> bool:
        self.woken.append((topic_id, agent_handle))
        return True


@pytest.mark.anyio
async def test_a_ring_wakes_the_seat_its_credential_names_and_nothing_else():
    project, topic = uuid.uuid4(), uuid.uuid4()
    pool = Pool()
    token = mint_session_token(project, topic, "cheese-kimi")

    answer = await journal_written(x_cheese_token=token, compute=pool)  # type: ignore[arg-type]
    assert answer["data"]["woken"] is True
    assert pool.woken == [(topic, "cheese-kimi")]

    for forged in ("", "not-a-credential", token[:-2] + "xx"):
        with pytest.raises(UnauthorizedError):
            await journal_written(x_cheese_token=forged, compute=pool)  # type: ignore[arg-type]
    assert pool.woken == [(topic, "cheese-kimi")]
