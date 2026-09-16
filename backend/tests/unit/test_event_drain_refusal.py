"""What the device drain does when the platform refuses its events.

Refusal is the case that hurt: a screen whose token the backend no longer
verified kept a spooled hook alive, and the drain asked for it once a second for
hours. These tests pin the two halves of the answer — nothing is thrown away,
and nothing is asked for at that rate.
"""

import json
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from app.domain.agent import event_drain
from app.domain.agent.event_drain import deliver_events
from app.domain.agent.hook_forwarder import CHEESE_HOOK_SCRIPT


@pytest.fixture
def hook_receiver():
    """Stands in for /sandbox/hooks/<topic>; ``state`` flips what it answers."""
    state = {"accepted": False}
    arrivals: list[float] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0) or 0))
            arrivals.append(time.monotonic())
            if state["accepted"]:
                body = {"code": 200, "data": None}
                self.send_response(200)
            else:
                # Byte-for-byte what the route answers an unverifiable token.
                body = {"code": 401, "message": "invalid sandbox token", "data": None}
                self.send_response(401)
            self.end_headers()
            self.wfile.write(json.dumps(body).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/sandbox/hooks/topic", state, arrivals
    server.shutdown()
    server.server_close()
    thread.join()


def _spool_with(directory: Path, count: int) -> Path:
    spool = directory / "spool"
    spool.mkdir(parents=True)
    for index in range(count):
        (spool / f"{index:06d}.e{index}").write_text(json.dumps({"n": index}))
    return spool


def test_refused_events_stay_on_disk_until_they_are_accepted(tmp_path, hook_receiver):
    url, state, _ = hook_receiver
    spool = _spool_with(tmp_path, 2)
    values = {"CHEESE_HOOK_URL": url, "CHEESE_TOKEN": "no-longer-verifies"}

    refused = deliver_events(spool, values)
    assert (refused.delivered, refused.rejected) == (0, 2)
    assert len(list(spool.glob("[0-9]*"))) == 2, (
        "a refused event is still the only copy"
    )

    state["accepted"] = True
    accepted = deliver_events(spool, values)
    assert (accepted.delivered, accepted.rejected) == (2, 0)
    assert list(spool.glob("[0-9]*")) == [], "an acknowledged event is ours now"


def test_a_spool_nobody_accepts_is_asked_for_less_and_less(tmp_path, hook_receiver):
    """The flat one-second retry this replaces would ask ~10 times in 10 seconds."""
    url, _, arrivals = hook_receiver
    spool = _spool_with(tmp_path, 1)
    script = tmp_path / "cheese-drain"
    script.write_text("# placeholder the drain checksums\n")
    Path(str(script) + ".env").write_text(
        " ".join(
            [
                f"CHEESE_HOOK_SPOOL={spool}",
                f"CHEESE_HOOK_URL={url}",
                "CHEESE_TOKEN=no-longer-verifies",
            ]
        )
    )

    drain = subprocess.Popen(
        [sys.executable, event_drain.__file__, str(script)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        time.sleep(10)
    finally:
        drain.terminate()
        drain.wait(timeout=10)

    # Doubling from a second reaches the tenth second after four attempts.
    assert 1 <= len(arrivals) <= 6, f"asked {len(arrivals)} times in 10s: {arrivals}"
    assert list(spool.glob("[0-9]*")), "backing off must not discard the event"


def _run_hook(tmp_path: Path, stdin: str) -> Path:
    """Run the forwarder exactly as a sandbox does: the script on stdin's body,
    spool-only so nothing is sent inline."""
    script = tmp_path / "cheese-hook"
    script.write_text(CHEESE_HOOK_SCRIPT)
    script.chmod(0o755)
    spool = tmp_path / "hook-spool"
    subprocess.run(
        ["sh", str(script)],
        input=stdin,
        text=True,
        env={
            "PATH": "/usr/bin:/bin",
            "HOME": str(tmp_path),
            "CHEESE_HOOK_SPOOL": str(spool),
            "CHEESE_HOOK_SPOOL_ONLY": "1",
        },
        check=True,
    )
    return spool


def test_an_empty_hook_body_is_never_spooled(tmp_path):
    """A zero-byte event can never be accepted, so it must never be written.

    The backend answers `hook body must be a JSON object` to anything that is not
    one, and the drain only removes an event on a 200 — so spooling an empty body
    creates a file that is asked for and refused for as long as the session runs.
    """
    spool = _run_hook(tmp_path, "")
    spooled = list(spool.glob("[0-9]*")) if spool.exists() else []
    assert spooled == [], f"an empty body was spooled: {spooled}"


def test_a_real_hook_body_is_still_spooled(tmp_path):
    spool = _run_hook(tmp_path, json.dumps({"hook_event_name": "Stop"}))
    spooled = list(spool.glob("[0-9]*"))
    assert len(spooled) == 1, f"expected one spooled event, got {spooled}"
    assert json.loads(spooled[0].read_text()) == {"hook_event_name": "Stop"}
