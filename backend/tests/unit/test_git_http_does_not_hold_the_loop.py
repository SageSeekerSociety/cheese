"""Serving a repo must not freeze everything else in the process.

`git http-backend` runs as a subprocess, and `subprocess.run` does not yield:
it waits for the child and reads its output with the event loop held. This
process runs every request on that one loop, so for as long as a clone takes,
nothing else moves. On dev at 02:10:22 UTC on 2026-09-20 a 169 MB clone took
3.7 s and the loop-lag watchdog logged a 3.6 s stall in the same second; what
that surfaced as elsewhere was `Timeout reading from …:6379` — a Redis answer
that had arrived with nobody free to take it, against a 5 s client timeout.
"""

import asyncio
import time

import pytest

from app.api.routes import git_http


class _Request:
    """The few things `_cgi` asks of a request."""

    method = "POST"
    headers: dict[str, str] = {}

    class url:
        query = ""


@pytest.mark.anyio
async def test_a_slow_repo_does_not_stop_everything_else(tmp_path, monkeypatch):
    slow = tmp_path / "slow-http-backend"
    slow.write_text(
        "#!/bin/sh\n"
        "sleep 0.5\n"
        "printf 'Status: 200 OK\\r\\nContent-Type: x\\r\\n\\r\\nok'\n"
    )
    slow.chmod(0o755)
    monkeypatch.setattr(git_http, "_backend_path", lambda: str(slow))

    lags: list[float] = []

    async def watching() -> None:
        while True:
            began = asyncio.get_running_loop().time()
            await asyncio.sleep(0.02)
            lags.append(asyncio.get_running_loop().time() - began - 0.02)

    watcher = asyncio.create_task(watching())
    began = time.monotonic()
    response = await git_http._cgi(tmp_path, "/git-upload-pack", _Request(), b"")
    took = time.monotonic() - began
    watcher.cancel()

    assert response.status_code == 200
    assert took >= 0.5, "the repo really was served, slowly"
    # The loop kept its appointments the whole time it was being served.
    assert max(lags) < 0.25, f"the loop was held for {max(lags):.3f}s"
    assert len(lags) > 10, "the watcher ran throughout, not once"
