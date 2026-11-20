"""The whole body one `/awaiting-me` request returns, written out.

A cheaper query is only worth having if it still answers the same. The probes
say what the request costs; this says what it returns, so the two trees can be
compared field by field:

    cd <before tree> && python scripts/perf-awaiting-me/dump_body.py --out /tmp/before.json
    cd <after tree>  && python scripts/perf-awaiting-me/dump_body.py --out /tmp/after.json
    diff /tmp/before.json /tmp/after.json

It runs the code of the tree it sits in (``sys.path`` points at that tree's
``backend``), against the database the probes use. Run from the repo root.

Items come back in the order the endpoint sorts them, newest first, so a
difference in the diff is a difference in what the page shows — except for two
items that share a timestamp, whose order is not promised.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

import app.models  # noqa: E402,F401
from app.api.awaiting_deps import waiting_items  # noqa: E402

DB_URL = os.environ.get(
    "PERF_DB_URL", "postgresql+asyncpg://cheesex:cheesex@127.0.0.1:5433/cheese_perf"
)
VIEWER = os.environ.get("PERF_HANDLE", "perfviewer")


async def main(out: str | None) -> None:
    engine = create_async_engine(DB_URL)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as db:
        items = await waiting_items(db, handle=VIEWER, user_id=None)
    body = json.dumps(
        [item.as_dict() for item in items], sort_keys=True, indent=1, default=str
    )
    if out is None:
        print(body)
    else:
        Path(out).write_text(body)
        print(f"{len(items)} items -> {out}")
    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", help="write here instead of stdout")
    args = parser.parse_args()
    asyncio.run(main(args.out))
