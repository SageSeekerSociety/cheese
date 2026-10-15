"""A model stream on the backend a rollout is leaving ends whole.

A rollout moves traffic to the next backend and then stops the old one. A model
stream that old backend is still relaying through ``/llm/v1`` cannot move with
the traffic, so the rollout asks it how many it still relays and stops it only
once none are left (``deploy/wait-model-streams.sh``).

Real processes where it matters: the two backends are two uvicorn processes
running the model route (``tests/support/model_route_backend.py``), the model
upstream holds its answer open, the rollout's own wait script asks the old
one, and an HTTP client reads the answer the way a harness does. The key a
caller is given is the only thing stood in for.
"""

import os
import socket
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

from app.core.sandbox_auth import SANDBOX_TOKEN, mint_personal_credential

BACKEND_DIR = Path(__file__).resolve().parents[2]
WAIT = BACKEND_DIR.parent / "deploy" / "wait-model-streams.sh"

FIRST = b'data: {"choices":[{"delta":{"content":"thinking"}}]}\n\n'
REST = b'data: {"choices":[{"delta":{"content":" done"}}]}\n\ndata: [DONE]\n\n'


# Everything here is on loopback; a proxy the machine running the tests happens
# to name would sit between the processes this test is about.
_ENV = {
    key: value
    for key, value in os.environ.items()
    if key.lower() not in ("http_proxy", "https_proxy", "all_proxy")
}


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class _Model(BaseHTTPRequestHandler):
    """Answers a request that asks for ``slow`` with its first event, then the
    rest once the test lets it; one that asks for ``late`` with nothing at all,
    not even its headers, until the test lets it; one that asks for ``drop`` by
    hanging up; any other request at once."""

    let_go: threading.Event

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("content-length") or 0))
        if b'"drop"' in body:
            self.close_connection = True
            return
        if b'"late"' in body:
            self.let_go.wait(15)
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.end_headers()
        self.wfile.write(FIRST)
        self.wfile.flush()
        if b'"slow"' in body:
            self.let_go.wait(15)
        self.wfile.write(REST)
        self.wfile.flush()

    def log_message(self, *_args):
        pass


@pytest.fixture
def model():
    finish = threading.Event()
    handler = type("Model", (_Model,), {"let_go": finish})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", finish
    finally:
        finish.set()
        server.shutdown()
        server.server_close()


class _Backend:
    """A backend process; the two of a rollout share nothing but the upstream."""

    def __init__(self, upstream: str) -> None:
        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.process = subprocess.Popen(
            [sys.executable, "-m", "tests.support.model_route_backend", str(self.port)],
            cwd=BACKEND_DIR,
            env={**_ENV, "MODEL_UPSTREAM": upstream, "SANDBOX_TOKEN": SANDBOX_TOKEN},
        )

    def wait_until_up(self) -> None:
        deadline = time.monotonic() + 20
        while True:
            assert self.process.poll() is None, "the backend exited on start"
            try:
                httpx.get(
                    f"{self.base}/_internal/llm/streams", timeout=1, trust_env=False
                )
                return
            except httpx.TransportError:
                assert time.monotonic() < deadline, "the backend never came up"
                time.sleep(0.05)

    def stop(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            self.process.wait(timeout=15)


@pytest.fixture
def backends(model):
    upstream, _ = model
    old, new = _Backend(upstream), _Backend(upstream)
    try:
        old.wait_until_up()
        new.wait_until_up()
        yield old, new
    finally:
        old.stop()
        new.stop()


def _open_streams(http: httpx.Client, backend: _Backend) -> int:
    return http.get(f"{backend.base}/_internal/llm/streams").json()["open"]


def test_a_stream_on_the_old_backend_ends_whole_before_the_rollout_stops_it(
    model, backends
):
    _, finish = model
    old, new = backends
    caller = {
        "authorization": "Bearer "
        + mint_personal_credential(user_id=1, conversation_id=str(uuid.uuid4()))
    }
    with httpx.Client(timeout=15, trust_env=False) as http:
        with http.stream(
            "POST",
            f"{old.base}/llm/v1/chat/completions",
            headers=caller,
            json={"stream": True, "slow": True},
        ) as answer:
            assert answer.status_code == 200
            received = b""
            chunks = answer.iter_raw()
            while len(received) < len(FIRST):
                received += next(chunks)
            assert received == FIRST
            assert _open_streams(http, old) == 1

            # Traffic has moved: the next call is answered by the new backend,
            # in full, while the old one is still relaying the first.
            moved = http.post(
                f"{new.base}/llm/v1/chat/completions", headers=caller, json={}
            )
            assert moved.content == FIRST + REST
            assert _open_streams(http, new) == 0

            wait = subprocess.Popen(
                ["bash", str(WAIT), str(old.port), "old-backend", "30", "1"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=_ENV,
            )
            time.sleep(1.5)
            assert wait.poll() is None, "the rollout stopped waiting on an open stream"

            finish.set()
            for chunk in chunks:
                received += chunk
        said, _ = wait.communicate(timeout=10)
        assert wait.returncode == 0, said
        assert "old-backend finished its model streams" in said, said
        assert _open_streams(http, old) == 0
    old.stop()
    assert received == FIRST + REST


def test_a_stream_the_harness_hung_up_on_holds_no_rollout(model, backends):
    """A caller that goes away mid-answer is not a stream left to wait for:
    counting it would hold every rollout after it to the full bound."""
    old, _ = backends
    caller = {
        "authorization": "Bearer "
        + mint_personal_credential(user_id=1, conversation_id=str(uuid.uuid4()))
    }
    with httpx.Client(timeout=15, trust_env=False) as http:
        with http.stream(
            "POST",
            f"{old.base}/llm/v1/chat/completions",
            headers=caller,
            json={"stream": True, "slow": True},
        ) as answer:
            received = b""
            chunks = answer.iter_raw()
            while len(received) < len(FIRST):
                received += next(chunks)
            assert _open_streams(http, old) == 1
        # Leaving the block closes the connection mid-answer.
    with httpx.Client(timeout=15, trust_env=False) as http:
        deadline = time.monotonic() + 5
        while _open_streams(http, old):
            assert time.monotonic() < deadline, "a stream nobody reads is still counted"
            time.sleep(0.05)


def _caller() -> dict[str, str]:
    return {
        "authorization": "Bearer "
        + mint_personal_credential(user_id=1, conversation_id=str(uuid.uuid4()))
    }


def test_a_call_still_waiting_for_the_models_first_byte_holds_the_rollout(
    model, backends
):
    """A reasoning model can take over a minute to send even its headers. The
    call is the old backend's to finish from the moment the model is asked."""
    _, let_go = model
    old, _ = backends
    answered: dict[str, bytes] = {}

    def call() -> None:
        with httpx.Client(timeout=15, trust_env=False) as caller:
            answered["body"] = caller.post(
                f"{old.base}/llm/v1/chat/completions",
                headers=_caller(),
                json={"stream": True, "late": True},
            ).content

    asking = threading.Thread(target=call)
    asking.start()
    with httpx.Client(timeout=15, trust_env=False) as http:
        deadline = time.monotonic() + 5
        while _open_streams(http, old) != 1:
            assert time.monotonic() < deadline, (
                "a call waiting for the model's first byte is not counted"
            )
            time.sleep(0.05)
        wait = subprocess.Popen(
            ["bash", str(WAIT), str(old.port), "old-backend", "30", "1"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=_ENV,
        )
        time.sleep(1.5)
        assert wait.poll() is None, "the rollout stopped waiting on an unanswered call"
        assert "body" not in answered
        let_go.set()
        asking.join(timeout=10)
        said, _ = wait.communicate(timeout=10)
        assert "old-backend finished its model streams" in said, said
    old.stop()
    assert answered["body"] == FIRST + REST


def test_a_model_that_never_answers_holds_no_rollout(model, backends):
    old, _ = backends
    with httpx.Client(timeout=15, trust_env=False) as http:
        refused = http.post(
            f"{old.base}/llm/v1/chat/completions",
            headers=_caller(),
            json={"drop": True},
        )
        assert refused.status_code == 503
        assert _open_streams(http, old) == 0
