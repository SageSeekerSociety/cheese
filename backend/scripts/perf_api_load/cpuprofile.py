"""Profile one request of a read endpoint and print where the CPU went.

The load harness shows the shaped box — one event loop saturating at ~14 rps on
``GET /topics`` while the database answers in a few milliseconds. This script
opens the box: it drives the same endpoint in-process and reports cumulative
time by function, so a claim like "pydantic re-validation is half of it" is a
measurement rather than a guess.

    DATABASE_URL=... REDIS_URL=... JWT_SECRET=perf-secret \\
    PERF_PROJECT_ID=... PERF_ROOM_ID=... PERF_TASK_ID=... \\
        uv run --project backend python backend/scripts/perf_api_load/profile.py topics.list

The profile is CPU (``cProfile``), not wall time: coroutine awaits on the
socket show up as ``select.epoll.poll`` and should be read as waiting, not as
work. Sort the output by ``tottime`` to see the leaves.
"""

from __future__ import annotations

import cProfile
import io
import os
import pstats
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))

from fastapi.testclient import TestClient  # noqa: E402

from app.common.auth import create_access_token  # noqa: E402
from app.main import app  # noqa: E402

PROJECT_ID = os.environ["PERF_PROJECT_ID"]
ROOM_ID = os.environ["PERF_ROOM_ID"]
TASK_ID = os.environ["PERF_TASK_ID"]
HANDLE = os.environ.get("PERF_HANDLE", "perfviewer")
USER_ID = int(os.environ.get("PERF_USER_ID", "9"))

ENDPOINTS = {
    "topics.list": f"/topics?project_id={PROJECT_ID}",
    "history.page": f"/topics/{ROOM_ID}/history?limit=50",
    "history.channel": f"/topics/{ROOM_ID}/history?limit=50&channel=true",
    "tasks.list": f"/projects/{PROJECT_ID}/tasks",
    "accept-card": f"/topics/{TASK_ID}/accept-card",
}


def main() -> None:
    which = sys.argv[1] if len(sys.argv) > 1 else "topics.list"
    path = ENDPOINTS[which]
    headers = {"Authorization": f"Bearer {create_access_token(USER_ID, handle=HANDLE)}"}
    with TestClient(app) as client:
        client.get(path, headers=headers)  # warm
        profile = cProfile.Profile()
        profile.enable()
        for _ in range(3):
            response = client.get(path, headers=headers)
        profile.disable()
        assert response.status_code == 200, response.text[:300]
    for order, limit in (("tottime", 30), ("cumulative", 60)):
        stream = io.StringIO()
        stats = pstats.Stats(profile, stream=stream).sort_stats(order)
        stats.print_stats(limit)
        print(f"===== sorted by {order} =====")
        print(stream.getvalue())


if __name__ == "__main__":
    main()
