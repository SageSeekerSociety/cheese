"""Read or update the deployment's cloud machine limit without a restart.

From backend/: uv run python -m scripts.resource_limits --machines-per-team 50
Override one team: add --team-id ID. --inherit-machines restores its default.
Issue shared token credits: --team-id ID --grant-credits 1000.
Uses the deployment's DATABASE_URL; access requires its database credentials.
"""

import argparse
import asyncio
import json

from app.core.db import async_session_factory, engine
from app.domain.machine.limits import (
    get_machine_limit,
    reset_team_machine_limit,
    set_machine_limit,
)
from app.domain.team.models import Team
from app.domain.usage.repositories import ComputeGrantRepository


def positive_int(raw: str) -> int:
    value = int(raw)
    if value < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return value


async def run(
    value: int | None,
    team_id: int | None = None,
    *,
    inherit: bool = False,
    credits: float | None = None,
) -> None:
    try:
        async with async_session_factory() as session:
            if team_id is not None:
                team = await session.get(Team, team_id)
                if team is None or team.deleted_at is not None:
                    raise ValueError("team not found")
            before = await get_machine_limit(session, team_id)
            if value is not None:
                await set_machine_limit(session, value, team_id)
            if inherit and team_id is not None:
                await reset_team_machine_limit(session, team_id)
            grant = None
            if credits is not None and team_id is not None:
                grant = await ComputeGrantRepository(session).grant_team(
                    team_id, credits
                )
            await session.commit()
        async with async_session_factory() as session:
            current = await get_machine_limit(session, team_id)
        result: dict = {"previous": before, "max_machines_per_team": current}
        if team_id is not None:
            result["team_id"] = team_id
        if grant is not None:
            result["grant_id"] = str(grant.id)
            result["credits_granted"] = credits
        print(json.dumps(result))
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    changes = parser.add_mutually_exclusive_group()
    changes.add_argument("--machines-per-team", type=positive_int)
    changes.add_argument("--inherit-machines", action="store_true")
    parser.add_argument("--team-id", type=positive_int)
    parser.add_argument("--grant-credits", type=float)
    args = parser.parse_args()
    if (
        args.inherit_machines or args.grant_credits is not None
    ) and args.team_id is None:
        parser.error("--inherit-machines and --grant-credits require --team-id")
    asyncio.run(
        run(
            args.machines_per_team,
            args.team_id,
            inherit=args.inherit_machines,
            credits=args.grant_credits,
        )
    )


if __name__ == "__main__":
    main()
