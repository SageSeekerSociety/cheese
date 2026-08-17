"""The two halves of the tunnel, driven against each other over a real socket.

The unit tests on either side can both pass while the pair is broken: the
machine half masks its frames and the backend half unmasks them, and a
disagreement about framing shows up only when real bytes cross a real
connection. So this runs the actual server (uvicorn on loopback), the actual
helper, and a fake meter, and drives the whole chain a turn would take:

    a plain socket ─► helper ─► uvicorn ─► /llm/tunnel ─► fake meter

What it proves is the property the tunnel exists for: bytes arrive unaltered in
both directions. The fake meter upper-cases what it receives, so a pipe that
echoes locally, drops a fragment, or reorders directions cannot pass.
"""

import socket
import threading
import time

import pytest
import uvicorn

from app.core.config import settings
from app.core.sandbox_auth import mint_scoped_token
from app.domain.agent import machine_tunnel

_PROJECT = "7c9e6679-7425-40de-944b-e07fc1f90ae7"
# What HTTPS_PROXY actually opens with. The helper reads this head in full so
# it can stamp the scoped token on, so every test has to speak it.
_CONNECT_HEAD = (
    b"CONNECT api.anthropic.com:443 HTTP/1.1\r\nHost: api.anthropic.com\r\n\r\n"
)


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class _FakeMeter:
    """Stands in for the meter's CONNECT listener: echoes upper-cased, so a
    silent short-circuit cannot read as success."""

    def __init__(self) -> None:
        self.received = bytearray()
        self._sock = socket.socket()
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(4)
        self.port = self._sock.getsockname()[1]
        self._stop = threading.Event()
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            threading.Thread(target=self._session, args=(conn,), daemon=True).start()

    def _session(self, conn: socket.socket) -> None:
        with conn:
            while not self._stop.is_set():
                try:
                    data = conn.recv(65536)
                except OSError:
                    return
                if not data:
                    return
                self.received.extend(data)
                try:
                    conn.sendall(data.upper())
                except OSError:
                    return

    def close(self) -> None:
        self._stop.set()
        try:
            self._sock.close()
        except OSError:
            pass


@pytest.fixture
def live_stack(monkeypatch):
    """A real backend on loopback, pointed at a fake meter, plus the helper."""
    from app.main import app

    meter = _FakeMeter()
    monkeypatch.setattr(settings, "subscription_proxy_host", "127.0.0.1")
    monkeypatch.setattr(settings, "subscription_proxy_connect_port", meter.port)

    api_port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=api_port, log_level="error")
    )
    threading.Thread(target=server.run, daemon=True).start()
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and not server.started:
        time.sleep(0.05)
    assert server.started, "the backend never came up"

    helper_port = _free_port()
    token = mint_scoped_token(project_id=_PROJECT)
    threading.Thread(
        target=machine_tunnel.serve,
        args=(helper_port, f"ws://127.0.0.1:{api_port}/llm/tunnel", token),
        daemon=True,
    ).start()
    # The helper binds before it can accept; poll rather than sleeping a guess.
    for _ in range(100):
        try:
            with socket.create_connection(("127.0.0.1", helper_port), timeout=0.2):
                break
        except OSError:
            time.sleep(0.05)

    try:
        yield helper_port, meter, api_port
    finally:
        server.should_exit = True
        meter.close()


def test_a_turns_bytes_survive_the_whole_chain(live_stack):
    helper_port, meter, _ = live_stack
    with socket.create_connection(("127.0.0.1", helper_port), timeout=10) as client:
        # A real client's first bytes are always a CONNECT head; the helper reads
        # it in full so it can stamp the scoped token on, then goes back to being
        # a pipe. Every test therefore opens the way HTTPS_PROXY does.
        client.sendall(_CONNECT_HEAD)
        assert b"CONNECT API.ANTHROPIC.COM:443" in client.recv(4096)

        # A second write on the same connection: the meter answers a CONNECT and
        # then relays a TLS stream over the same socket, so one exchange proving
        # nothing about the next would miss the case that matters.
        client.sendall(b"second write")
        assert client.recv(4096) == b"SECOND WRITE"

    time.sleep(0.1)
    assert bytes(meter.received).startswith(b"CONNECT api.anthropic.com:443")


def test_a_payload_larger_than_one_frame_arrives_whole(live_stack):
    """126 and 65536 bytes are where WebSocket switches to 2- and 8-byte length
    fields; a body that crosses either is where a hand-written framer breaks."""
    helper_port, meter, _ = live_stack
    payload = bytes(range(256)) * 400  # 102400 bytes: past both boundaries
    with socket.create_connection(("127.0.0.1", helper_port), timeout=15) as client:
        client.sendall(_CONNECT_HEAD)
        client.recv(4096)
        client.sendall(payload)
        seen = bytearray()
        while len(seen) < len(payload):
            chunk = client.recv(65536)
            if not chunk:
                break
            seen.extend(chunk)
    assert bytes(seen) == payload.upper()


def test_a_helper_with_a_bad_token_closes_instead_of_hanging(live_stack, monkeypatch):
    """`claude` waiting on a CONNECT that can never succeed looks exactly
    like a stalled model — the most expensive failure to diagnose. A REFUSED
    upgrade (the backend answered and said no: bad token) must reach it as a
    closed socket, on the first attempt — the patience window is only for
    ABSENCE (see test_a_restarting_backend_is_ridden_out)."""
    _, meter, api_port = live_stack  # a REAL backend, refusing the bad token
    port = _free_port()
    threading.Thread(
        target=machine_tunnel.serve,
        args=(port, f"ws://127.0.0.1:{api_port}/llm/tunnel", "not-a-real-token"),
        daemon=True,
    ).start()
    for _ in range(100):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                break
        except OSError:
            time.sleep(0.05)

    with socket.create_connection(("127.0.0.1", port), timeout=10) as client:
        client.sendall(b"CONNECT api.anthropic.com:443 HTTP/1.1\r\n\r\n")
        client.settimeout(10)
        # EOF or RST — either satisfies the property under test, and which one
        # arrives is the kernel's call rather than the helper's: closing a
        # socket that still holds the caller's unread CONNECT sends RST instead
        # of FIN. The requirement is "fails now, does not hang", so pinning
        # either outcome would be testing the OS.
        try:
            assert client.recv(4096) == b"", "must close, not hang"
        except ConnectionResetError:
            pass
    assert not meter.received


def test_a_restarting_backend_is_ridden_out_not_surfaced(live_stack, monkeypatch):
    """A deploy swaps the backend container for tens of seconds (#551). A
    CONNECT arriving in that window must be HELD and completed when the
    backend returns — not answered with a closed socket, which claude renders
    as 'Unable to connect to API' (measured across 7 deploys, 2026-08-17)."""
    helper_port_ignored, meter, api_port = live_stack
    monkeypatch.setattr(machine_tunnel, "_OPEN_RETRY_START_S", 0.2)

    # The backend's stand-in starts DEAD: a port with nothing listening, that
    # a forwarder to the real backend claims only after the client is already
    # waiting — exactly a container swap seen from the machine.
    late_port = _free_port()
    port = _free_port()
    token = mint_scoped_token(project_id=_PROJECT)
    threading.Thread(
        target=machine_tunnel.serve,
        args=(port, f"ws://127.0.0.1:{late_port}/llm/tunnel", token),
        daemon=True,
    ).start()
    for _ in range(100):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                break
        except OSError:
            time.sleep(0.05)

    def _pipe(a: socket.socket, b: socket.socket) -> None:
        try:
            while True:
                data = a.recv(65536)
                if not data:
                    break
                b.sendall(data)
        except OSError:
            pass
        for s in (a, b):
            try:
                s.close()
            except OSError:
                pass

    def _forwarder_comes_up() -> None:
        time.sleep(1.0)  # the client is already inside the patience window
        server = socket.socket()
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", late_port))
        server.listen(8)
        while True:
            try:
                conn, _ = server.accept()
            except OSError:
                return
            upstream = socket.create_connection(("127.0.0.1", api_port))
            threading.Thread(target=_pipe, args=(conn, upstream), daemon=True).start()
            threading.Thread(target=_pipe, args=(upstream, conn), daemon=True).start()

    threading.Thread(target=_forwarder_comes_up, daemon=True).start()

    payload = b"held across the deploy window"
    with socket.create_connection(("127.0.0.1", port), timeout=30) as client:
        client.settimeout(30)
        client.sendall(b"CONNECT api.anthropic.com:443 HTTP/1.1\r\n\r\n")
        # This first read spans the whole patience window: the backend was not
        # there when the CONNECT went in, and the echo can only arrive after
        # the helper outwaited the outage.
        head_echo = client.recv(4096)
        assert head_echo, "the held CONNECT must complete, not be closed"
        assert b"CONNECT API.ANTHROPIC.COM:443" in head_echo
        client.sendall(payload)
        seen = bytearray()
        while len(seen) < len(payload):
            chunk = client.recv(4096)
            assert chunk, "the pipe must stay open after the ride-out"
            seen.extend(chunk)
    assert bytes(seen) == payload.upper()


def test_a_refreshed_token_takes_effect_without_restarting_the_helper(live_stack):
    """A scoped token has a session lifetime and this helper outlives one. If it
    baked the token at startup the failure would be #385's shape: the process
    stays healthy while its credential dies under it, every turn is refused, and
    nothing on the machine looks wrong. So the token is read per connection."""
    _, meter, api_port = live_stack
    import tempfile

    from app.domain.agent.machine_tunnel import TokenSource, serve

    with tempfile.NamedTemporaryFile("w", suffix=".tok", delete=False) as handle:
        token_path = handle.name
        handle.write("not-a-real-token")

    port = _free_port()
    threading.Thread(
        target=serve,
        args=(
            port,
            f"ws://127.0.0.1:{api_port}/llm/tunnel",
            TokenSource(path=token_path),
        ),
        daemon=True,
    ).start()
    for _ in range(100):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                break
        except OSError:
            time.sleep(0.05)

    # A bad token: refused, so nothing reaches the meter.
    before = len(meter.received)
    with socket.create_connection(("127.0.0.1", port), timeout=10) as client:
        client.sendall(_CONNECT_HEAD)
        client.settimeout(10)
        try:
            client.recv(4096)
        except ConnectionResetError:
            pass
    assert len(meter.received) == before

    # Drop a good one in place — no restart, no signal.
    with open(token_path, "w") as handle:
        handle.write(mint_scoped_token(project_id=_PROJECT))

    with socket.create_connection(("127.0.0.1", port), timeout=10) as client:
        client.sendall(_CONNECT_HEAD)
        assert b"CONNECT API.ANTHROPIC.COM:443" in client.recv(4096)


def test_the_scoped_token_reaches_the_meter_as_the_proxy_password(live_stack):
    """The meter answers 407 without one, and HTTPS_PROXY deliberately carries no
    userinfo (a credential baked there cannot be refreshed under a running
    claude — #385). So the helper is what puts it on the wire, per connection.

    Measured 2026-08-14: without this the turn died with
    `API Error: 407 cheese: a valid scoped token is required as the proxy
    password` — the whole chain was up, and refused at the last hop.
    """
    import base64

    helper_port, meter, _ = live_stack
    with socket.create_connection(("127.0.0.1", helper_port), timeout=10) as client:
        client.sendall(b"CONNECT api.anthropic.com:443 HTTP/1.1\r\nHost: x\r\n\r\n")
        client.recv(4096)

    time.sleep(0.1)
    head = bytes(meter.received)
    assert b"Proxy-Authorization: Basic " in head, head[:200]
    encoded = head.split(b"Proxy-Authorization: Basic ", 1)[1].split(b"\r\n", 1)[0]
    user, _, token = base64.b64decode(encoded).decode().partition(":")
    assert user == "cheese"
    # The real token, not a placeholder — the meter verifies its signature.
    assert token.count(".") == 1 and len(token) > 40
    # The request line itself must survive intact; the meter parses it.
    assert head.startswith(b"CONNECT api.anthropic.com:443 HTTP/1.1\r\n")
