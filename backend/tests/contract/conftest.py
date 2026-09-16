"""Contract-test fixtures.

The pure-Python contract tests exercise our own API through the ``python_client``
ASGI fixture (defined in the root ``tests/conftest.py``) and assert the response
SHAPE. Most of the endpoints they hit require an authenticated user; without one
they get a blanket 401 which hides the real contract.

``authed_client`` returns the same ``python_client`` with an
``Authorization: Bearer <token>`` default header attached, issued for the seeded
platform agent user ("cheese") that ``python_client`` already creates via
``IdentityService.ensure_agent_user()`` in its own setup. We reuse that row rather
than seeding a fresh user here because a separate seeding session opened from the
fixture would bind an asyncpg connection to a *different* event loop than the one
anyio drives the test body on ("attached to a different loop"). The agent user has
a real profile, so it satisfies endpoints that join the user.

Its uid is read back rather than assumed: agents now draw their id from their own
sequence (``app.domain.identity.uids``), so "the first row is id 1" stopped being
true. ``python_client`` is itself an anyio fixture, so looking the row up through
its session factory runs on the same loop as the test body.

The token is the same shape the main auth flow issues: ``create_access_token``
embeds ``sub`` = int user id, which ``get_current_user_id`` reads (it validates the
JWT, not the user row). We do NOT POST ``/users/auth/login`` because that route
depends on Redis (rate-limiter / 2FA session store), which this harness does not
run.
"""

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient

from app.common.auth import create_access_token
from app.domain.identity.handles import CHEESE_HANDLE
from app.domain.user.repositories import UserRepository


@pytest.fixture
async def agent_user_id(python_client: AsyncClient) -> int:
    """The platform agent's uid, read back rather than assumed to be 1."""
    async with python_client.test_factory() as session:  # type: ignore[attr-defined]
        agent = await UserRepository(session).get_by_username(CHEESE_HANDLE)
    assert agent is not None, "python_client should have seeded the platform agent"
    return agent.id


@pytest.fixture
async def authed_client(python_client: AsyncClient, agent_user_id: int) -> AsyncClient:
    """``python_client`` with a valid bearer token attached to every request, so
    endpoints guarded by ``require_auth_user`` see a real principal instead of
    returning 401."""
    token = create_access_token(agent_user_id, handle=CHEESE_HANDLE)
    python_client.headers["Authorization"] = f"Bearer {token}"
    return python_client


@pytest.fixture
async def seeded_team(authed_client: AsyncClient, agent_user_id: int) -> int:
    """A team with the authed user (the platform agent) as OWNER, seeded on the client's
    isolated per-worker DB. Lets the team/knowledge shape tests hit the real 200
    path (membership-gated) instead of a 403 for a non-existent team.

    Seeding goes through the client's ``test_factory`` (NullPool) on the current
    test loop — the same engine the app reads through the overridden get_db — so
    there's no cross-loop connection sharing.
    """
    from app.domain.team.models import Team, TeamMemberRole, TeamUserRelation

    factory = authed_client.test_factory  # type: ignore[attr-defined]
    now = datetime.now(UTC)
    async with factory() as session:
        team = Team(
            name="Contract Team",
            intro="",
            description="",
            avatar_id=1,
            created_at=now,
            updated_at=now,
        )
        session.add(team)
        await session.flush()
        session.add(
            TeamUserRelation(
                team_id=team.id,
                user_id=agent_user_id,
                role=TeamMemberRole.OWNER,
                created_at=now,
                updated_at=now,
            )
        )
        team_id = team.id
        await session.commit()
    return team_id


@pytest.fixture
async def seeded_space(authed_client: AsyncClient) -> int:
    """A Space on the client's isolated per-worker DB, so the task-list shape test
    can pass a real ``space`` id and exercise the 200 response instead of 404."""
    from app.domain.space.models import Space

    factory = authed_client.test_factory  # type: ignore[attr-defined]
    now = datetime.now(UTC)
    async with factory() as session:
        space = Space(
            name="Contract Space",
            intro="",
            description="",
            announcements=[],
            task_templates=[],
            created_at=now,
            updated_at=now,
        )
        session.add(space)
        await session.flush()
        space_id = space.id
        await session.commit()
    return space_id
