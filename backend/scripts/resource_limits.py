"""Read or update the deployment's cloud machine limit without a restart.

From backend/: uv run python -m scripts.resource_limits [--machines-per-project 50]
Uses the deployment's DATABASE_URL; access requires its database credentials.
"""

import argparse
import asyncio
import json

from app.core.db import async_session_factory, engine
from app.domain.machine.limits import get_machine_limit, set_machine_limit


def positive_int(raw: str) -> int:
    value = int(raw)
    if value < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return value


async def run(value: int | None) -> None:
    try:
        async with async_session_factory() as session:
            before = await get_machine_limit(session)
            if value is not None:
                await set_machine_limit(session, value)
                await session.commit()
        async with async_session_factory() as session:
            current = await get_machine_limit(session)
        print(json.dumps({"previous": before, "max_machines_per_project": current}))
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--machines-per-project", type=positive_int)
    args = parser.parse_args()
    asyncio.run(run(args.machines_per_project))


if __name__ == "__main__":
    main()
