"""Count the SQL statements each read endpoint issues for one request.

It drives the real app in-process (``TestClient`` runs the lifespan, so the
engine and pool are the ones a served request uses) and counts every statement
the request's session sends, through the same helper the test suite uses
(``backend/tests/support/counting_session.py``). That helper is imported rather
than copied so the harness and the regression tests cannot disagree about what
"one statement" is.

Run it against the seeded throwaway database::

    DATABASE_URL=postgresql+asyncpg://cheesex:cheesex@127.0.0.1:5433/cheese_perf \\
    REDIS_URL=redis://127.0.0.1:6379/0 JWT_SECRET=perf-secret \\
    PERF_PROJECT_ID=... PERF_ROOM_ID=... PERF_TASK_ID=... \\
        uv run --project backend python backend/scripts/perf_api_load/sqlcount.py

Several samples per endpoint; the report keeps every sample, not just the
median, because a background sweep landing inside one sample is exactly the
thing that would hide an N+1 if only the median were printed.
"""

from __future__ import annotations

import json
import os
import statistics
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))

from fastapi.testclient import TestClient  # noqa: E402

from app.common.auth import create_access_token  # noqa: E402
from app.core import db as appdb  # noqa: E402
from app.main import app  # noqa: E402
from tests.support.counting_session import counting_sql  # noqa: E402

PROJECT_ID = os.environ["PERF_PROJECT_ID"]
ROOM_ID = os.environ["PERF_ROOM_ID"]
TASK_ID = os.environ["PERF_TASK_ID"]
HANDLE = os.environ.get("PERF_HANDLE", "perfviewer")
USER_ID = int(os.environ.get("PERF_USER_ID", "9"))
SAMPLES = int(os.environ.get("PERF_SAMPLES", "7"))

#: ``(label, method, path)``. Kept next to the load harness's own table so the
#: two cannot drift into measuring different URLs.
ENDPOINTS = [
    ("topics.list", "GET", f"/topics?project_id={PROJECT_ID}"),
    ("history.page", "GET", f"/topics/{ROOM_ID}/history?limit=50"),
    ("tasks.list", "GET", f"/projects/{PROJECT_ID}/tasks"),
    ("accept-card", "GET", f"/topics/{TASK_ID}/accept-card"),
]


def main() -> None:
    token = create_access_token(USER_ID, handle=HANDLE)
    headers = {"Authorization": f"Bearer {token}"}
    report = []
    with TestClient(app) as client:
        client.test_app_engine = appdb.engine  # type: ignore[attr-defined]
        for label, method, path in ENDPOINTS:
            client.request(method, path, headers=headers)  # warm statements
            samples = []
            sizes = []
            for _ in range(SAMPLES):
                started = time.perf_counter()
                with counting_sql(client) as seen:
                    response = client.request(method, path, headers=headers)
                wall = time.perf_counter() - started
                assert response.status_code == 200, (label, response.text[:200])
                samples.append(len(seen))
                sizes.append((response.json().get("data") or {}).get("total"))
            report.append(
                {
                    "endpoint": label,
                    "path": path,
                    "statements": samples,
                    "median": statistics.median(samples),
                    "min": min(samples),
                    "max": max(samples),
                    "wall_ms": round(wall * 1000, 1),
                    "total_field": sizes[-1] if sizes else None,
                }
            )
            print(
                f"{label:14s} statements={samples} median={statistics.median(samples)}"
                f" wall={wall * 1000:.1f}ms",
                flush=True,
            )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
