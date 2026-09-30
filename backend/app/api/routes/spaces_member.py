"""What a member has published inside one space, seen from their side.

A slice of `app/api/routes/spaces.py` (arch review, tracking issue #2143): the
`/me/publishing/tasks` route behind the task list's 我发布的 filter. It scopes its
query to the caller (`auth_user.user_id`) inside the space named in the path, and
guards it with `_ensure_space_visible` before the service runs -- the same 404
the rest of the file gives a space you are not in. The module mounts itself
through `app.main._discover_routers` like every other route module.

The helpers it shares with the routes that stay -- `_ensure_space_visible` and the
provider `get_space_member_publishing_service` -- stay in `spaces.py` and are
imported here, the shape `users_password.py`, `topics_side_routes.py` and
`spaces_organization.py` already use. `spaces.py` imports nothing from this
module, so there is no cycle.

Nothing here reads an `app.domain.*.models` module, so `.importlinter`'s C2
(`routes-touch-no-models`) is untouched and the frozen baseline does not move.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from app.api.routes.spaces import (
    _ensure_space_visible,
    get_space_member_publishing_service,
    require_reviewed_space,
)
from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.db.session import get_db
from app.domain.space.member_publishing_service import SpaceMemberPublishingService

router = APIRouter(
    prefix="/spaces", tags=["Spaces"], dependencies=[Depends(require_reviewed_space)]
)


# ---------------------------------------------------------------------------
# Space Member Self-Resources (NT-API aligned)
# ---------------------------------------------------------------------------


@router.get(
    "/{spaceId}/me/publishing/tasks",
    summary="Get Space My Published Tasks",
)
async def get_space_me_published_tasks(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    from_ts: int | None = Query(default=None, alias="from"),
    to_ts: int | None = Query(default=None, alias="to"),
    categoryId: int | None = Query(default=None),
    approved: str | None = Query(default=None),
    hasPendingParticipantApproval: bool | None = Query(default=None),
    hasPendingReview: bool | None = Query(default=None),
    sortBy: str = Query(default="createdAt"),
    sortOrder: str = Query(default="desc"),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceMemberPublishingService = Depends(
        get_space_member_publishing_service
    ),
    db=Depends(get_db),
) -> dict:
    """Return the authenticated user's published tasks in this space."""
    await _ensure_space_visible(db=db, space_id=space_id, user_id=auth_user.user_id)
    items = await service.get_my_published_tasks(
        space_id=space_id,
        user_id=auth_user.user_id,
        from_ts=from_ts,
        to_ts=to_ts,
        category_id=categoryId,
        approved=approved,
        has_pending_participant_approval=hasPendingParticipantApproval,
        has_pending_review=hasPendingReview,
        sort_by=sortBy,
        sort_order=sortOrder,
    )
    return {"code": 200, "message": "OK", "data": {"tasks": items}}
