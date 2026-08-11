"""Contract-test fixtures.

The pure-Python contract tests exercise our own API through the ``python_client``
ASGI fixture (defined in the root ``tests/conftest.py``) and assert the response
SHAPE. Most of the endpoints they hit require an authenticated user; without one
they get a blanket 401 which hides the real contract.

``authed_client`` returns the same ``python_client`` with an
``Authorization: Bearer <token>`` default header attached, issued for the seeded
platform agent user (id=1, "cheese") that ``python_client`` already creates via
``IdentityService.ensure_agent_user()`` in its own setup. We reuse that row rather
than seeding a fresh user here because a separate seeding session opened from the
fixture would bind an asyncpg connection to a *different* event loop than the one
anyio drives the test body on ("attached to a different loop"). The agent user has
a real profile, so it satisfies endpoints that join the user.

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

# The platform agent user seeded by python_client (IdentityService.ensure_agent_user
# allocates it as the first row → id=1, username "cheese").
_AGENT_USER_ID = 1
_AGENT_HANDLE = "cheese"


@pytest.fixture
async def authed_client(python_client: AsyncClient) -> AsyncClient:
    """``python_client`` with a valid bearer token attached to every request, so
    endpoints guarded by ``require_auth_user`` see a real principal instead of
    returning 401."""
    token = create_access_token(_AGENT_USER_ID, handle=_AGENT_HANDLE)
    python_client.headers["Authorization"] = f"Bearer {token}"
    return python_client


@pytest.fixture
async def seeded_team(authed_client: AsyncClient) -> int:
    """A team with the authed user (agent, id=1) as OWNER, seeded on the client's
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
                user_id=_AGENT_USER_ID,
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
