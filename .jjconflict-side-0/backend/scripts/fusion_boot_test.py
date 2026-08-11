"""A6-lite: prove the merged app runs against ONE DB carrying BOTH schemas.

Imports app.main (registers all routers → all product + agent models), then
create_all()s BOTH metadatas (cheesex core.db.Base + main db.base_class.Base)
into the configured DATABASE_URL. Run with DATABASE_URL pointed at a throwaway DB.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.main  # noqa: E402,F401 — imports routers → services → all models
from app.core.db import Base as CxBase  # noqa: E402
from app.core.db import engine as cx_engine  # noqa: E402
from app.db.base_class import Base as MainBase  # noqa: E402


async def main() -> None:
    async with cx_engine.begin() as conn:
        await conn.run_sync(CxBase.metadata.create_all)
        await conn.run_sync(MainBase.metadata.create_all)
    print(
        f"created {len(CxBase.metadata.tables)} cheesex tables "
        f"+ {len(MainBase.metadata.tables)} product tables in one DB"
    )


if __name__ == "__main__":
    asyncio.run(main())
