"""Write what each endpoint answers, so a change can be shown not to alter it.

`probe_slow_endpoints.py` says what a request COSTS; this says what it SAYS.
Run it before and after a read-path change and diff the two directories: a
change that only stops rebuilding a statement has no business moving a byte.

Two fields are derived from the clock rather than from a row (`last_output_at`
on a channel, and anything else `datetime.now()` feeds), so the diff is taken
on parsed JSON with those keys normalised out — a wall-clock pass-through
between the two runs would otherwise show up as a change.

Run from the repo root, with the perf database up and seeded (see README.md)::

    PERF_DB_URL=postgresql+asyncpg://cheesex:cheesex@127.0.0.1:5433/cheese_perf \\
        uv run --project backend python scripts/perf-awaiting-me/dump_endpoint_bodies.py /tmp/before

The directory is required and is created if missing; nothing is overwritten
silently — an existing file of the same name is replaced, which is the point
when re-running one side.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# This import has to come before `app`: `probe_slow_endpoints` sets the app's
# environment from `PERF_DB_URL` at import time.
from probe_slow_endpoints import VIEWER, discover_ids, endpoint_paths  # noqa: E402

#: Keys whose value is the moment of the request, not a fact about a row.
CLOCK_KEYS = {"last_output_at"}


def normalise(value):
    """Drop every clock-derived key, anywhere in the tree."""
    if isinstance(value, dict):
        return {k: normalise(v) for k, v in value.items() if k not in CLOCK_KEYS}
    if isinstance(value, list):
        return [normalise(v) for v in value]
    return value


def slug(label: str) -> str:
    return label.split(" (")[0].replace(" ", "-")


async def main() -> None:
    import httpx

    from app.common.auth import create_access_token
    from app.main import app

    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(2)
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)

    ids = await discover_ids()
    token = create_access_token(None, handle=VIEWER)
    headers = {"Authorization": f"Bearer {token}"}

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://perf.local", headers=headers, timeout=120.0
    ) as client:
        for label, method, path in endpoint_paths(ids):
            response = await client.request(method, path)
            target = out / f"{slug(label)}.json"
            target.write_text(
                json.dumps(
                    {"status": response.status_code, "body": normalise(response.json())},
                    ensure_ascii=False,
                    indent=1,
                    sort_keys=True,
                )
                + "\n"
            )
            print(f"{label:26} HTTP {response.status_code}  -> {target}")


if __name__ == "__main__":
    asyncio.run(main())
