from functools import partial

from fastapi import Depends, Request

from app.api.doc_access import ensure_team_documents
from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.core.errors import (
    NotFoundError,
)
from app.db.session import get_db
from app.domain.materials.services import MaterialService
from app.domain.space.analytics_service import SpaceAnalyticsService
from app.domain.space.analytics_view_service import SpaceAnalyticsViewService
from app.domain.space.member_publishing_service import SpaceMemberPublishingService
from app.domain.space.review_service import SpaceReviewService
from app.domain.space.services import SpaceService
from app.domain.space.tags_service import SpaceTagsService
from app.domain.task.repositories import TaskMembershipRepository, TaskRepository
from app.domain.user.repositories import (
    UserProfileRepository,
    UserRepository,
)


async def require_reviewed_space(
    request: Request,
    user: AuthUserInfo = Depends(require_auth_user),
    db=Depends(get_db),
) -> None:
    space_id = request.path_params.get("spaceId")
    if space_id is None:
        return
    try:
        space_id = int(space_id)
    except ValueError:
        return  # Path validation supplies the normal 422 response.
    space = await SpaceService.for_visibility(db).get_space(space_id)
    if space is None or space.review_status == "APPROVED":
        return
    # Applications are edited through resubmission, so a review covers fixed text.
    if request.method == "GET" and request.url.path.rstrip("/").endswith(
        f"/spaces/{space_id}"
    ):
        if await SpaceReviewService(db).is_owner(space_id, user.user_id):
            return
    raise NotFoundError("Space not found")


async def get_space_service(db=Depends(get_db)) -> SpaceService:
    # The space domain wires its own repositories (see `SpaceService.for_routes`);
    # the route hands in only the three collaborators that belong to other
    # domains. `document_check` / `material_service` guard the write side of
    # `teaching`'s references — a 项目集 may only name team documents its teacher
    # could already read, 课件 that exist, and 课件 an ordinary member could read
    # (never one locked to a board's managers).
    return SpaceService.for_routes(
        db,
        task_repo=TaskRepository(session=db),
        document_check=partial(ensure_team_documents, db),
        material_service=MaterialService.for_lookup(db),
    )


async def get_space_analytics_service(db=Depends(get_db)) -> SpaceAnalyticsService:
    task_repo = TaskRepository(session=db)
    membership_repo = TaskMembershipRepository(session=db)
    user_repo = UserRepository(session=db)
    profile_repo = UserProfileRepository(session=db)
    return SpaceAnalyticsService(
        task_repo=task_repo,
        membership_repo=membership_repo,
        user_repo=user_repo,
        profile_repo=profile_repo,
    )


async def get_space_member_publishing_service(
    db=Depends(get_db),
) -> SpaceMemberPublishingService:
    return SpaceMemberPublishingService(session=db)


async def get_space_analytics_view_service(
    db=Depends(get_db),
) -> SpaceAnalyticsViewService:
    return SpaceAnalyticsViewService(session=db)


async def get_space_topics_service(
    db=Depends(get_db),
) -> SpaceTagsService:
    return SpaceTagsService(session=db)
