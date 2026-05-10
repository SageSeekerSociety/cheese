from typing import Annotated

from fastapi import APIRouter, Body, Depends, Path, Query

from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.core.errors import BadRequestError
from app.db.session import get_db
from app.domain.groups.repositories import (
    GroupMembershipRepository,
    GroupProfileRepository,
    GroupQuestionRepository,
    GroupRepository,
    GroupTargetRepository,
)
from app.domain.groups.services import GroupQuestionService, GroupsService, GroupTargetService
from app.domain.user.repositories import UserProfileRepository

router = APIRouter(prefix="/groups", tags=["Groups"])


async def get_groups_service(db=Depends(get_db)) -> GroupsService:
    repo = GroupRepository(session=db)
    profile_repo = GroupProfileRepository(session=db)
    membership_repo = GroupMembershipRepository(session=db)
    user_profile_repo = UserProfileRepository(session=db)
    return GroupsService(
        repo=repo,
        profile_repo=profile_repo,
        membership_repo=membership_repo,
        user_profile_repo=user_profile_repo,
    )


@router.get(
    "",
    summary="List Groups",
)
async def list_groups(
    q: str | None = Query(default=None),
    page_start: int | None = Query(default=None, alias="page_start"),
    page_size: int = Query(default=20, ge=1, le=100, alias="page_size"),
    joined: bool | None = Query(default=None),
    managed: bool | None = Query(default=None),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: GroupsService = Depends(get_groups_service),
) -> dict:
    user_id = auth_user.user_id if auth_user.user_id > 0 else None
    groups, page = await service.list_groups(
        keyword=q,
        page_start=page_start,
        page_size=page_size,
        user_id=user_id,
        joined=joined,
        managed=managed,
    )
    return {"code": 200, "message": "OK", "data": {"groups": groups, "page": page}}


@router.post(
    "",
    summary="Create Group",
    status_code=201,
)
async def create_group(
    payload: dict = Body(...),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: GroupsService = Depends(get_groups_service),
) -> dict:
    name = payload.get("name")
    intro = payload.get("intro", "")
    avatar_id = payload.get("avatarId")
    if not isinstance(name, str) or not name.strip():
        raise BadRequestError("name is required")
    result = await service.create_group(
        user_id=auth_user.user_id,
        name=name,
        intro=intro,
        avatar_id=avatar_id,
    )
    return {"code": 201, "message": "Created", "data": {"group": result}}


@router.get(
    "/{group_id}",
    summary="Get Group",
)
async def get_group(
    group_id: Annotated[int, Path(ge=0)],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: GroupsService = Depends(get_groups_service),
) -> dict:
    user_id = auth_user.user_id if auth_user.user_id > 0 else None
    group = await service.get_group(group_id, user_id=user_id)
    return {"code": 200, "message": "OK", "data": {"group": group}}


@router.put(
    "/{group_id}",
    summary="Update Group",
)
async def update_group(
    group_id: Annotated[int, Path(ge=0)],
    payload: dict = Body(...),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: GroupsService = Depends(get_groups_service),
) -> dict:
    name = payload.get("name")
    intro = payload.get("intro")
    avatar_id = payload.get("avatarId")
    group = await service.update_group(
        group_id=group_id,
        user_id=auth_user.user_id,
        name=name,
        intro=intro,
        avatar_id=avatar_id,
    )
    return {"code": 200, "message": "OK", "data": {"group": group}}


@router.delete(
    "/{group_id}",
    summary="Delete Group",
    status_code=204,
)
async def delete_group(
    group_id: Annotated[int, Path(ge=0)],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: GroupsService = Depends(get_groups_service),
) -> None:
    await service.delete_group(group_id=group_id, user_id=auth_user.user_id)


@router.get(
    "/{group_id}/members",
    summary="List Group Members",
)
async def list_group_members(
    group_id: Annotated[int, Path(ge=0)],
    page_start: int | None = Query(default=None, alias="page_start"),
    page_size: int = Query(default=20, alias="page_size"),
    service: GroupsService = Depends(get_groups_service),
) -> dict:
    members, page = await service.list_members(
        group_id=group_id,
        page_start=page_start,
        page_size=page_size,
    )
    return {"code": 200, "message": "OK", "data": {"members": members, "page": page}}


@router.post(
    "/{group_id}/members",
    summary="Join Group",
    status_code=201,
)
async def join_group(
    group_id: Annotated[int, Path(ge=0)],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: GroupsService = Depends(get_groups_service),
) -> dict:
    result = await service.join_group(group_id=group_id, user_id=auth_user.user_id)
    return {"code": 201, "message": "Created", "data": result}


@router.delete(
    "/{group_id}/members",
    summary="Leave Group",
)
async def leave_group(
    group_id: Annotated[int, Path(ge=0)],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: GroupsService = Depends(get_groups_service),
) -> dict:
    result = await service.leave_group(group_id=group_id, user_id=auth_user.user_id)
    return {"code": 200, "message": "OK", "data": result}


async def get_target_service(db=Depends(get_db)) -> GroupTargetService:
    group_repo = GroupRepository(session=db)
    target_repo = GroupTargetRepository(session=db)
    membership_repo = GroupMembershipRepository(session=db)
    return GroupTargetService(
        group_repo=group_repo,
        target_repo=target_repo,
        membership_repo=membership_repo,
    )


async def get_question_service(db=Depends(get_db)) -> GroupQuestionService:
    group_repo = GroupRepository(session=db)
    question_repo = GroupQuestionRepository(session=db)
    membership_repo = GroupMembershipRepository(session=db)
    return GroupQuestionService(
        group_repo=group_repo,
        question_repo=question_repo,
        membership_repo=membership_repo,
    )


@router.get(
    "/{group_id}/targets",
    summary="List Group Targets",
)
async def list_group_targets(
    group_id: Annotated[int, Path(ge=0)],
    page_start: int | None = Query(default=None, alias="page_start"),
    page_size: int = Query(default=20, ge=1, le=100, alias="page_size"),
    service: GroupTargetService = Depends(get_target_service),
) -> dict:
    targets, page = await service.list_targets(
        group_id=group_id,
        page_start=page_start,
        page_size=page_size,
    )
    return {"code": 200, "message": "OK", "data": {"targets": targets, "page": page}}


@router.post(
    "/{group_id}/targets",
    summary="Create Group Target",
    status_code=201,
)
async def create_group_target(
    group_id: Annotated[int, Path(ge=0)],
    payload: dict = Body(...),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: GroupTargetService = Depends(get_target_service),
) -> dict:
    from datetime import UTC, datetime

    name = payload.get("name")
    intro = payload.get("intro", "")
    started_at = payload.get("startedAt")
    ended_at = payload.get("endedAt")
    attendance_frequency = payload.get("attendanceFrequency", "DAILY")

    if not isinstance(name, str) or not name.strip():
        raise BadRequestError("name is required")
    if not started_at or not ended_at:
        raise BadRequestError("startedAt and endedAt are required")

    started_at_dt = datetime.fromtimestamp(started_at / 1000, tz=UTC)
    ended_at_dt = datetime.fromtimestamp(ended_at / 1000, tz=UTC)

    result = await service.create_target(
        group_id=group_id,
        user_id=auth_user.user_id,
        name=name,
        intro=intro,
        started_at=started_at_dt,
        ended_at=ended_at_dt,
        attendance_frequency=attendance_frequency,
    )
    return {"code": 201, "message": "Created", "data": result}


@router.get(
    "/{group_id}/targets/{target_id}",
    summary="Get Group Target",
)
async def get_group_target(
    group_id: Annotated[int, Path(ge=0)],
    target_id: Annotated[int, Path(ge=0)],
    service: GroupTargetService = Depends(get_target_service),
) -> dict:
    target = await service.get_target(group_id=group_id, target_id=target_id)
    return {"code": 200, "message": "OK", "data": {"target": target}}


@router.put(
    "/{group_id}/targets/{target_id}",
    summary="Update Group Target",
)
async def update_group_target(
    group_id: Annotated[int, Path(ge=0)],
    target_id: Annotated[int, Path(ge=0)],
    payload: dict = Body(...),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: GroupTargetService = Depends(get_target_service),
) -> dict:
    from datetime import UTC, datetime

    name = payload.get("name")
    intro = payload.get("intro")
    started_at = payload.get("startedAt")
    ended_at = payload.get("endedAt")
    attendance_frequency = payload.get("attendanceFrequency")

    started_at_dt = datetime.fromtimestamp(started_at / 1000, tz=UTC) if started_at else None
    ended_at_dt = datetime.fromtimestamp(ended_at / 1000, tz=UTC) if ended_at else None

    target = await service.update_target(
        group_id=group_id,
        target_id=target_id,
        user_id=auth_user.user_id,
        name=name,
        intro=intro,
        started_at=started_at_dt,
        ended_at=ended_at_dt,
        attendance_frequency=attendance_frequency,
    )
    return {"code": 200, "message": "OK", "data": {"target": target}}


@router.delete(
    "/{group_id}/targets/{target_id}",
    summary="Delete Group Target",
    status_code=204,
)
async def delete_group_target(
    group_id: Annotated[int, Path(ge=0)],
    target_id: Annotated[int, Path(ge=0)],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: GroupTargetService = Depends(get_target_service),
) -> None:
    await service.delete_target(group_id=group_id, target_id=target_id, user_id=auth_user.user_id)


@router.get(
    "/{group_id}/questions",
    summary="List Group Questions",
)
async def list_group_questions(
    group_id: Annotated[int, Path(ge=0)],
    page_start: int | None = Query(default=None, alias="page_start"),
    page_size: int = Query(default=20, ge=1, le=100, alias="page_size"),
    service: GroupQuestionService = Depends(get_question_service),
) -> dict:
    question_ids, page = await service.list_questions(
        group_id=group_id,
        page_start=page_start,
        page_size=page_size,
    )
    return {"code": 200, "message": "OK", "data": {"questionIds": question_ids, "page": page}}


@router.post(
    "/{group_id}/questions",
    summary="Add Question to Group",
)
async def add_group_question(
    group_id: Annotated[int, Path(ge=0)],
    payload: dict = Body(...),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: GroupQuestionService = Depends(get_question_service),
) -> dict:
    question_id = payload.get("questionId")
    if not isinstance(question_id, int) or question_id < 1:
        raise BadRequestError("questionId is required")
    result = await service.add_question(
        group_id=group_id,
        question_id=question_id,
        user_id=auth_user.user_id,
    )
    return {"code": 201, "message": "Created", "data": result}


@router.delete(
    "/{group_id}/questions/{question_id}",
    summary="Remove Question from Group",
    status_code=204,
)
async def remove_group_question(
    group_id: Annotated[int, Path(ge=0)],
    question_id: Annotated[int, Path(ge=0)],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: GroupQuestionService = Depends(get_question_service),
) -> None:
    await service.remove_question(
        group_id=group_id,
        question_id=question_id,
        user_id=auth_user.user_id,
    )
