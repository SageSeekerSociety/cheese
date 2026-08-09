"""Create the eval database schema (run as a subprocess by the runner).

Executed with cwd=backend and DATABASE_URL pointing at the fresh per-run sqlite
file, so `app.core.config.settings` binds to the eval DB — the runner process
itself never imports `app` (its own env must not leak into settings).
"""

import asyncio

import app.models  # noqa: F401  (registers every table on Base.metadata)
from app.core.db import Base, engine


async def main() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
