"""The trust boundary for the workspace routes.

`resolve_actor` turns the authenticated `user_id` (injected by `require_auth_user`,
never read from a request body) into the `UserSummary` the domain works with. The
human-vs-agent distinction is applied *only here*, at the edge, as a presentation
hint — the mock store and the future real services never branch on it.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.user.repositories import UserProfileRepository, UserRepository

from .mock_store import AGENT_USER_IDS, STORE
from .schemas import UserSummary


async def resolve_actor(db: AsyncSession, user_id: int) -> UserSummary:
    user = await UserRepository(session=db).get_by_id(user_id)
    profile = await UserProfileRepository(session=db).get_profile_by_user_id(user_id)
    username = user.username if user is not None else f"user{user_id}"
    summary = UserSummary(
        id=user_id,
        username=username,
        nickname=profile.nickname if profile is not None else username,
        avatar_id=profile.avatar_id if profile is not None else None,
        is_agent=user_id in AGENT_USER_IDS,
    )
    return STORE.remember_user(summary)


def ok(data: dict, *, code: int = 200, message: str = "success") -> dict:
    return {"code": code, "message": message, "data": data}
