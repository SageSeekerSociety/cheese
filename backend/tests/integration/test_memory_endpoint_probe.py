"""The memory backend's self-check, exercised with no key and no network.

The behaviour under test is the one the probe exists for: on the openviking
backend, a model endpoint that does not answer must produce a *visible*
verdict, and on the db backend the check must cost nothing at all — no packet,
and none of the openviking module tree loaded.

Everything here runs against ``tests.support.fake_model_endpoint`` (the local
OpenAI-protocol stand-in) or against a socket that is deliberately not
listening, so nothing needs a real key.
"""

import json
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from app.core.config import settings
from app.domain.memory import endpoint_probe
from tests.support.fake_model_endpoint import fake_model_server

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def _fresh_verdict():
    """Each test gets its own verdict; the cache is process-wide otherwise."""
    endpoint_probe.reset_cache()
    yield
    endpoint_probe.reset_cache()


@pytest.fixture
def openviking_settings(monkeypatch):
    """Point the openviking settings somewhere, on the openviking backend."""

    # 2048 is what the stand-in returns when nothing asks for a width — the
    # healthy case is "the configured width is the one the endpoint gives you".
    def configure(base_url: str, *, dimension: int = 2048) -> None:
        monkeypatch.setattr(settings, "memory_backend", "openviking")
        monkeypatch.setattr(settings, "openviking_llm_api_base", base_url)
        monkeypatch.setattr(settings, "openviking_embedding_api_base", base_url)
        monkeypatch.setattr(settings, "openviking_llm_api_key", "sk-fake-llm")
        monkeypatch.setattr(
            settings, "openviking_embedding_api_key", "sk-fake-embedding"
        )
        monkeypatch.setattr(settings, "openviking_llm_model", "fake-chat")
        monkeypatch.setattr(settings, "openviking_embedding_model", "fake-embedding")
        monkeypatch.setattr(settings, "openviking_embedding_dimension", dimension)

    return configure


async def test_a_working_endpoint_reports_up(openviking_settings):
    with fake_model_server() as server:
        openviking_settings(server["base_url"])

        health = await endpoint_probe.memory_backend_health()

    assert health["status"] == "up"
    assert health["backend"] == "openviking"
    assert health["endpoints"]["embedding"]["status"] == "up"
    assert health["endpoints"]["chat"]["status"] == "up"
    assert "error" not in health
    # Both endpoints were really called — this is a self-check, not a guess.
    kinds = {call["kind"] for call in server["calls"]}
    assert kinds == {"embeddings", "chat"}


async def test_a_rejected_key_reports_down_and_says_where_the_key_came_from(
    monkeypatch, openviking_settings
):
    """The production trap: the key falls back to the gateway token and 401s."""
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    app = FastAPI()

    @app.post("/v1/{path:path}")
    async def unauthorized(path: str) -> JSONResponse:  # pyright: ignore[reportUnusedFunction]
        return JSONResponse({"error": {"message": "invalid api key"}}, status_code=401)

    with _serve(app) as base_url:
        openviking_settings(base_url)
        # No openviking-specific key configured: exactly the shape a deployment
        # lands in when someone flips MEMORY_BACKEND and forgets the key.
        monkeypatch.setattr(settings, "openviking_llm_api_key", None)
        monkeypatch.setattr(settings, "openviking_embedding_api_key", None)
        monkeypatch.setattr(settings, "anthropic_auth_token", "sk-che-gateway-virtual")

        health = await endpoint_probe.memory_backend_health()

    assert health["status"] == "down"
    for role in ("embedding", "chat"):
        assert health["endpoints"][role]["status"] == "down"
        assert "401" in health["endpoints"][role]["error"]
        # The vendor's own words survive to the reader.
        assert "invalid api key" in health["endpoints"][role]["error"]
        # And so does the answer to "which key did it use?"
        assert health["endpoints"][role]["key_from"] == "anthropic_auth_token"
    assert "401" in health["error"]
    assert "anthropic_auth_token" in health["error"]


async def test_an_endpoint_that_never_answers_reports_down(openviking_settings):
    """A hung endpoint is a down endpoint, and the probe says so within its
    timeout rather than waiting on it forever."""
    # A listening socket with a full backlog and nobody accepting: connections
    # are held open and never answered.
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(0)
    port = listener.getsockname()[1]
    try:
        openviking_settings(f"http://127.0.0.1:{port}/v1")
        result = await endpoint_probe.probe(timeout=0.5)
    finally:
        listener.close()

    health = result.as_dict()
    assert health["status"] == "down"
    assert health["endpoints"]["embedding"]["status"] == "down"
    assert health["endpoints"]["chat"]["status"] == "down"
    assert "0.5s" in health["error"]


async def test_a_key_that_works_but_returns_the_wrong_width_is_not_healthy(
    openviking_settings,
):
    """A working key can still build a broken index, and that must not read
    as healthy: OpenViking does not send `dimensions` for provider "openai",
    so the model's native width has to match the configured one."""
    with fake_model_server() as server:
        # The stand-in returns vectors of whatever `dimensions` asks for and
        # defaults to 2048; the configured index width says something else.
        openviking_settings(server["base_url"], dimension=1024)

        health = await endpoint_probe.memory_backend_health()

    assert health["status"] == "down"
    assert health["endpoints"]["chat"]["status"] == "up"
    embedding_error = health["endpoints"]["embedding"]["error"]
    assert "2048" in embedding_error
    assert "1024" in embedding_error


async def test_the_db_backend_check_sends_no_packet(monkeypatch):
    """On db there is nothing to check, and checking anyway would mean every
    deployment paying for a config path it does not use."""
    monkeypatch.setattr(settings, "memory_backend", "db")

    def refuse(*args, **kwargs):
        raise AssertionError("the db backend must not open a socket")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)

    health = await endpoint_probe.memory_backend_health()

    assert health == {
        "status": "skipped",
        "backend": "db",
        "detail": "this backend calls no model endpoint",
    }


async def test_the_db_backend_startup_check_does_nothing(monkeypatch, caplog):
    monkeypatch.setattr(settings, "memory_backend", "db")

    def refuse(*args, **kwargs):
        raise AssertionError("the db backend must not open a socket")

    monkeypatch.setattr(socket.socket, "connect", refuse)

    await endpoint_probe.check_on_startup()

    assert "memory:" not in caplog.text


def test_the_db_backend_never_loads_the_openviking_module_tree():
    """`openviking` drags in litellm and friends. A db deployment must not pay
    for that, so the check has to reach its verdict without importing any of
    it — asserted in a fresh interpreter, since another test in this session
    may well have imported it already."""
    script = """
import asyncio, json, sys
from app.domain.memory.endpoint_probe import memory_backend_health
verdict = asyncio.run(memory_backend_health())
loaded = sorted(
    name for name in sys.modules
    if name.split(".")[0] in {"openviking", "openviking_cli", "litellm"}
)
print(json.dumps({"verdict": verdict, "loaded": loaded}))
"""
    backend_root = Path(__file__).resolve().parents[2]
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=backend_root,
        capture_output=True,
        text=True,
        env=_env_on_db_backend(),
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout.strip().splitlines()[-1])
    assert report["verdict"]["status"] == "skipped"
    assert report["loaded"] == []


def _env_on_db_backend() -> dict[str, str]:
    import os

    env = dict(os.environ)
    env["MEMORY_BACKEND"] = "db"
    env.setdefault("JWT_SECRET", "test-secret")
    env.setdefault("ENVIRONMENT", "test")
    env.setdefault("ANTHROPIC_AUTH_TOKEN", "dummy")
    return env


class _Serve:
    """Run an ASGI app on a free local port for the duration of a `with`."""

    def __init__(self, app) -> None:
        self._app = app

    def __enter__(self) -> str:
        import threading
        import time

        import uvicorn

        config = uvicorn.Config(
            self._app, host="127.0.0.1", port=0, log_level="warning"
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        deadline = time.monotonic() + 30
        while not self._server.started and time.monotonic() < deadline:
            if not self._thread.is_alive():
                raise RuntimeError("stand-in endpoint died during startup")
            time.sleep(0.05)
        if not self._server.started:
            raise RuntimeError("stand-in endpoint did not start in time")
        port = self._server.servers[0].sockets[0].getsockname()[1]
        return f"http://127.0.0.1:{port}/v1"

    def __exit__(self, *exc_info) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=30)


def _serve(app) -> _Serve:
    return _Serve(app)
