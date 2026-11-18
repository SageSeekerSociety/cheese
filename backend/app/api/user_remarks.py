"""Private display metadata for authenticated API responses only."""

from collections.abc import Mapping
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped

from app.domain.user.repositories import UserRepository
from app.domain.user.services import remarks_by_handles


class _Person(Protocol):
    username: Mapped[str]


async def remarks_for_viewer(
    db: AsyncSession, viewer_id: int | None, users: Mapping[int, _Person]
) -> dict[str, str]:
    """Resolve the authenticated id to its real handle, then batch the page."""
    if viewer_id is None or not users:
        return {}
    viewer = users.get(viewer_id)
    if viewer is None:
        viewer = await UserRepository(session=db).get_by_id(viewer_id)
    if viewer is None:
        return {}
    return await remarks_by_handles(
        db, viewer.username, (user.username for user in users.values())
    )
