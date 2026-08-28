"""What the durable write on POST /sandbox/hooks/{topic} costs, measured.

That route is on the agent's critical path: a Claude Code hook is a synchronous
command, so `claude` is stopped until this answers. It now writes the event to
the topic's spool before it acks, and "is that write too expensive to sit in
front of every tool call?" is a question to answer with a number.

Two modes, both against the real route through a real ASGI stack:

* ``ab`` — alternates request by request between the route as it stands and the
  same route with the spool write stubbed out. Interleaving is the point: this
  laptop's noise drifts across both arms equally, which two separate runs
  cannot promise. The difference between the arms IS the cost of the write.
* ``scale`` — appends into one spool directory and reports the write's latency
  per thousand files already in it. Retention keeps a day of events, so a busy
  topic's spool is thousands of files, and a write that degraded with directory
  size would make every tool call pay for the ones before it.

    PYTHONPATH=. uv run python scripts/hook_ingest_probe.py [ab|scale] [n]

Absolute numbers include ASGI + test-client overhead and say nothing about the
network a real machine crosses; the ab difference is the number to read.
"""

import json
import os
import statistics
import sys
import tempfile
import time
import uuid
from pathlib import Path

os.environ.setdefault("OPENAI_API_KEY", "probe")
os.environ.setdefault("ANTHROPIC_AUTH_TOKEN", "probe")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import settings  # noqa: E402

settings.workspace_root = tempfile.mkdtemp(prefix="hook-ingest-probe-")

from app.api.deps import get_chat_service  # noqa: E402
from app.api.routes import sandbox as sandbox_routes  # noqa: E402
from app.core.sandbox_auth import mint_scoped_token  # noqa: E402
from app.domain.agent.harness.claude_code import append_event, hook_router  # noqa: E402
from app.domain.workspace import service as ws  # noqa: E402

# What a Bash tool call sends — the hook shape that dominates a turn.
HOOK = {
    "hook_event_name": "PreToolUse",
    "tool_name": "Bash",
    "tool_input": {"command": "uv run pytest -x", "description": "run the tests"},
}


class _StubChat:
    """The route only ever asks the chat service to schedule a settle."""

    def schedule_spool_settle(self, *args: object, **kwargs: object) -> None:
        return None


def _percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))]


def _stats(samples: list[float]) -> dict[str, float]:
    return {
        "p50_ms": round(statistics.median(samples), 3),
        "p95_ms": round(_percentile(samples, 0.95), 3),
        "p99_ms": round(_percentile(samples, 0.99), 3),
        "mean_ms": round(statistics.fmean(samples), 3),
        "max_ms": round(max(samples), 3),
    }


def ab(n: int) -> dict[str, object]:
    app = FastAPI()
    app.include_router(sandbox_routes.router)
    app.dependency_overrides[get_chat_service] = _StubChat
    topic_id, project_id = uuid.uuid4(), uuid.uuid4()
    token = mint_scoped_token(project_id=str(project_id), topic_id=str(topic_id))
    sink = hook_router.subscribe(str(topic_id))  # a live screen: the fixed case
    url = f"/sandbox/hooks/{topic_id}"
    real = sandbox_routes.append_event

    def stubbed(*args: object, **kwargs: object) -> None:
        return None

    with_write: list[float] = []
    without_write: list[float] = []
    with TestClient(app) as client:
        for i in range(2 * (n + 50)):  # the first 100 are warmup
            writes = i % 2 == 0
            sandbox_routes.append_event = real if writes else stubbed
            start = time.perf_counter()
            response = client.post(
                url,
                json=HOOK,
                headers={
                    "X-Cheese-Token": token,
                    "X-Cheese-Event-Id": str(uuid.uuid4()),
                },
            )
            elapsed = (time.perf_counter() - start) * 1000.0
            if response.status_code != 200:
                raise SystemExit(f"unexpected {response.status_code}: {response.text}")
            if i >= 100:
                (with_write if writes else without_write).append(elapsed)
            while not sink.queue.empty():
                sink.queue.get_nowait()
    sandbox_routes.append_event = real
    hook_router.unsubscribe(str(topic_id), sink)
    return {
        "with_spool_write": _stats(with_write),
        "without_spool_write": _stats(without_write),
    }


def scale(n: int) -> list[dict[str, object]]:
    spool: Path = ws.spool_dir(uuid.uuid4(), uuid.uuid4())
    bucket = max(n // 10, 1)
    out: list[dict[str, object]] = []
    samples: list[float] = []
    for i in range(n):
        start = time.perf_counter()
        append_event(spool, str(uuid.uuid4()), HOOK)
        samples.append((time.perf_counter() - start) * 1000.0)
        if len(samples) == bucket:
            out.append({"files_already_there": i + 1 - bucket, **_stats(samples)})
            samples = []
    return out


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "ab"
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 500
    if mode == "ab":
        print(json.dumps({"mode": mode, "n": n, **ab(n)}, indent=2))
    elif mode == "scale":
        print(json.dumps({"mode": mode, "n": n, "buckets": scale(n)}, indent=2))
    else:
        raise SystemExit(f"unknown mode {mode!r} (ab | scale)")


if __name__ == "__main__":
    main()
