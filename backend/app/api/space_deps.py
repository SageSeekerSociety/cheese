from fastapi import Depends, Request

from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.auth.space_access import is_space_admin
from app.core.errors import (
    ForbiddenError,
    NotFoundError,
)
from app.db.session import get_db
from app.domain.knowledge.services import KnowledgeService
from app.domain.materials.services import MaterialService
from app.domain.space.analytics_service import SpaceAnalyticsService
from app.domain.space.analytics_view_service import SpaceAnalyticsViewService
from app.domain.space.member_publishing_service import SpaceMemberPublishingService
from app.domain.space.review_service import SpaceReviewService
from app.domain.space.services import SpaceLabels, SpaceService
from app.domain.space.tags_service import SpaceTagsService
from app.domain.task.repositories import TaskMembershipRepository, TaskRepository
from app.domain.user.realname_services import UserRealNameService
from app.domain.user.repositories import (
    UserProfileRepository,
    UserRealNameRepository,
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
    # domains. `knowledge_service` / `material_service` guard the write side of
    # `teaching`'s references — a 项目集 may only name 知识 its teacher could
    # already read, 课件 that exist, and 课件 an ordinary member could read
    # (never one locked to a board's managers).
    return SpaceService.for_routes(
        db,
        task_repo=TaskRepository(session=db),
        knowledge_service=KnowledgeService.for_lookup(db),
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


async def get_space_user_realname_service(
    db=Depends(get_db),
) -> UserRealNameService:
    user_repo = UserRepository(session=db)
    profile_repo = UserProfileRepository(session=db)
    realname_repo = UserRealNameRepository(session=db)
    return UserRealNameService(
        session=db,
        user_repo=user_repo,
        profile_repo=profile_repo,
        realname_repo=realname_repo,
        space_labels=SpaceLabels(session=db),
    )


async def _ensure_space_visible(*, db, space_id: int, user_id: int) -> None:
    """404 rather than 403 — a 题目版 you are not in is not confirmed to exist.

    Same answer the task routes give for an invisible task, and the same
    question ``list_spaces`` asks for the list query: one rule, so a direct
    link and the list cannot drift apart.
    """
    if not await SpaceService.for_visibility(db).is_member(
        space_id=space_id, user_id=user_id
    ):
        raise NotFoundError(
            "Resource space not found", data={"type": "space", "id": space_id}
        )


async def _ensure_space_admin(*, db, space_id: int, user_id: int) -> None:
    """「管理员版面的门」: 只有题目板的管理员/创建者能过。

    先按 ``_ensure_space_visible`` 答 404 —— 一个你不在的题目板不该被确认存在；
    再看是不是管理员，不是就明确 403（不静默返回空内容：空 CSV 会让导出的人以为
    「这个班没人」，而真相是「你没权限」）。

    和 ``_ensure_space_visible`` 一样，判据只有一处 —— ``app.auth.space_access``
    的 ``is_space_admin``，与打分、发题、项目对话读权同一个答案。

    挂在这道门上的是一整块管理员版面：参与者花名册的导出与分组统计（逐人/分组地
    解密年级、专业、班级），以及概览、题目、发布者、提醒与其导出。前端本来就把
    整个「数据分析」入口挂在 ``isCurrentUserAtLeastAdmin`` 下面，所以这几次收窄
    是把 API 对齐到界面已经说的那句话：这版只有管理员看得到。
    学习看板（``/analytics/learning/*``）不在此列 —— 它读的是成员项目里的对话，
    由 ``app.auth.project_access`` 逐个项目判，两套数据、两个门。
    """
    await _ensure_space_visible(db=db, space_id=space_id, user_id=user_id)
    if not await is_space_admin(session=db, space_id=space_id, user_id=user_id):
        raise ForbiddenError("Only a board manager can perform this action")
