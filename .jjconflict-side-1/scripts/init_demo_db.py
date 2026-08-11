"""Create the full CheeseX schema in a throwaway SQLite file.

Usage (from backend/, so app imports resolve):
    PYTHONPATH=. uv run python ../scripts/init_demo_db.py /abs/path/demo.db

Used to run an isolated demo backend (DATABASE_URL=sqlite+aiosqlite:///<path>)
without touching the live Postgres — e.g. for UI screenshots of features whose
migration hasn't been applied to the shared dev DB yet.
"""

import asyncio
import sys
from pathlib import Path

from sqlalchemy.ext.asyncio import create_async_engine

import app.models  # noqa: F401  (registers all tables on Base.metadata)
from app.core.db import Base


async def main(db_path: str) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()
    print(f"schema created: {db_path}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
