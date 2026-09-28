"""A user has one personal team, however many first requests arrive together.

Two first joins by the same user used to both find no personal team and both
insert one. From then on every lookup of that user's personal team matched two
rows, and each later join answered 500 (``MultipleResultsFound``).
"""

import asyncio

from sqlalchemy import func, select

from app.domain.team.models import Team
from app.domain.team.services import team_service
from app.domain.user.repositories import UserRepository
from tests.conftest import seed_user


async def _personal_teams_of(factory, user_id: int) -> int:
    async with factory() as session:
        return await session.scalar(
            select(func.count(Team.id)).where(
                Team.personal_owner_user_id == user_id, Team.deleted_at.is_(None)
            )
        )


def test_two_first_requests_at_once_leave_one_personal_team(client) -> None:
    token = seed_user(client, "twin-joiner")
    factory = client.test_factory

    async def race() -> tuple[int, int, int]:
        async with factory() as seed:
            user = await UserRepository(seed).get_by_username("twin-joiner")
            assert user is not None
            user_id = user.id

        async with factory() as first, factory() as second:
            # The first request has provisioned the team but not yet committed
            # when the second one asks for it: the moment two joins overlap.
            mine = await team_service(first).ensure_personal_team(user_id)

            async def the_second() -> int:
                team = await team_service(second).ensure_personal_team(user_id)
                await second.commit()
                return team.id

            pending = asyncio.create_task(the_second())
            await asyncio.sleep(0.5)
            await first.commit()
            theirs = await asyncio.wait_for(pending, timeout=10)
            return user_id, mine.id, theirs

    user_id, mine, theirs = asyncio.run(race())

    assert mine == theirs
    assert asyncio.run(_personal_teams_of(factory, user_id)) == 1

    resp = client.get("/teams/my-teams", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    personal = [t for t in resp.json()["data"]["teams"] if t["personal"]]
    assert [t["id"] for t in personal] == [mine]
