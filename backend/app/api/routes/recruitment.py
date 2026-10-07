"""Team Recruitment routes.

Team-scoped endpoints live on the teams router
(POST/GET under /teams/{teamId}/recruitment).
This module provides the global recruitment plaza endpoints:
  GET  /recruitment         — browse all OPEN posts
  PATCH  /recruitment/{postId} — edit a post (creator only)
  DELETE /recruitment/{postId} — delete a post (team ADMIN+)
"""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.routes.teams import get_team_service
from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.common.auth import get_optional_user_id
from app.core.errors import BadRequestError
from app.db.session import get_db
from app.domain.team.models import RecruitmentStatus, TeamRecruitmentPost
from app.domain.team.recruitment_repositories import RecruitmentRepository
from app.domain.team.recruitment_services import RecruitmentService
from app.domain.team.repositories import TeamRepository
from app.domain.team.services import TeamService
from app.domain.team.summary import team_summary, team_summary_seen_by
from app.domain.user.repositories import UserProfileRepository, UserRepository

# ── Request Models ────────────────────────────────────────────────────────────


class CreateRecruitmentPostRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    title: str = Field(..., min_length=1)
    content: str = Field(..., min_length=1)
    contact: str | None = None
    max_members: int | None = Field(default=None, alias="maxMembers")
    expires_at: int | None = Field(default=None, alias="expiresAt")


class PatchRecruitmentPostRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    title: str | None = None
    content: str | None = None
    contact: str | None = None
    max_members: int | None = Field(default=None, alias="maxMembers")
    status: str | None = None
    expires_at: int | None = Field(default=None, alias="expiresAt")


router = APIRouter(tags=["Recruitment"])


async def _get_recruitment_service(db=Depends(get_db)) -> RecruitmentService:
    return RecruitmentService(
        repo=RecruitmentRepository(session=db),
        team_repo=TeamRepository(session=db),
    )


def _creator_summary(user, profile, *, fallback_id: int, chosen_map: dict) -> dict:
    if user is None:
        return {"id": fallback_id, "nickname": "", "avatarId": None, "intro": ""}
    nickname = (
        profile.nickname
        if profile and getattr(profile, "nickname", None)
        else user.username
    )
    return {
        "id": user.id,
        "nickname": nickname,
        # 只有真挑过头像才有值，判据在 ``chosen_avatar_ids``（非 ``default`` 那张脸
        # 才算挑过）。读 ``profile.avatar_id`` 会把默认脸当成人挑过的头像发出去。
        "avatarId": chosen_map.get(user.id),
        "intro": profile.intro if profile else "",
    }


def _ts_ms(dt: datetime | None) -> int | None:
    if dt is None:
        return None
    return int(dt.timestamp() * 1000)


def _post_to_api(
    post: TeamRecruitmentPost,
    *,
    teams_map: dict,
    users_map: dict,
    profiles_map: dict,
    chosen_map: dict,
) -> dict:
    return {
        "id": post.id,
        "team": team_summary(teams_map.get(post.team_id), fallback_id=post.team_id),
        "title": post.title,
        "content": post.content,
        "contact": post.contact,
        "maxMembers": post.max_members,
        "status": post.status,
        "creator": _creator_summary(
            users_map.get(post.created_by),
            profiles_map.get(post.created_by),
            fallback_id=post.created_by,
            chosen_map=chosen_map,
        ),
        "createdAt": _ts_ms(post.created_at),
        "updatedAt": _ts_ms(post.updated_at),
        "expiresAt": _ts_ms(post.expires_at),
    }


async def _load_maps_for_posts(
    db, posts: list[TeamRecruitmentPost]
) -> tuple[dict, dict, dict, dict]:
    if not posts:
        return {}, {}, {}, {}
    team_ids = list({p.team_id for p in posts})
    user_ids = list({p.created_by for p in posts})
    team_repo = TeamRepository(session=db)
    user_repo = UserRepository(session=db)
    profile_repo = UserProfileRepository(session=db)
    teams_map = await team_repo.get_by_ids(team_ids)
    users_map = await user_repo.get_by_ids(user_ids)
    profiles_map = await profile_repo.get_profiles_by_user_ids(user_ids)
    # 作者头像单查一张「真挑过」的映射：没挑过的不在这里出现，creator 的 avatarId
    # 就回 None（前端退首字母），而不是把默认脸当成人挑过的头像。
    chosen_map = await profile_repo.chosen_avatar_ids(user_ids)
    return teams_map, users_map, profiles_map, chosen_map


def _post_to_api_seen_by(
    post: TeamRecruitmentPost,
    *,
    teams_map: dict,
    users_map: dict,
    profiles_map: dict,
    chosen_map: dict,
    viewer_team_ids: set[int],
    contact_visible: bool,
) -> dict:
    """一条广场帖按「谁在看」裁过再交出去。

    广场是公开的，但公开的是帖子，不是它背后那支队伍：隐身 / 个人队的
    name、handle、intro、avatar 对外人不点名（判据与 ``TeamService.visible_team``
    逐字同一条）。联系方式是帖子自己写下的招人渠道，但它是唯一一个能直接联系到
    真人的字段，所以只给登录的人 —— 和团队作用域那条列表的注释同一句理由
    （「帖子可以带联系方式」正是它要过可见性门的原因）。
    """
    item = _post_to_api(
        post,
        teams_map=teams_map,
        users_map=users_map,
        profiles_map=profiles_map,
        chosen_map=chosen_map,
    )
    item["team"] = team_summary_seen_by(
        teams_map.get(post.team_id),
        fallback_id=post.team_id,
        viewer_team_ids=viewer_team_ids,
    )
    if not contact_visible:
        item["contact"] = None
    return item


async def _viewer_team_ids(db, viewer_id: int | None) -> set[int]:
    """看客自己所在的队 —— 一次查完，不按帖子逐条问。"""
    if viewer_id is None:
        return set()
    teams = await TeamRepository(session=db).list_teams_of_user(user_id=viewer_id)
    return {t.id for t in teams}


# ---------------------------------------------------------------------------
# Global recruitment plaza
# ---------------------------------------------------------------------------


@router.get("/recruitment", summary="Browse Recruitment Plaza")
async def list_recruitment_posts(
    keyword: str | None = Query(default=None),
    page_start: int | None = Query(default=None, ge=0, alias="pageStart"),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
    viewer_id: int | None = Depends(get_optional_user_id),
    service: RecruitmentService = Depends(_get_recruitment_service),
    db=Depends(get_db),
) -> dict:
    # 广场对所有人开放（含未登录），只列 OPEN 帖 —— 这条口径不动。要裁的是
    # 每条帖子吐出去的那两处：隐身队的团队身份，和能直接联系到真人的联系方式。
    posts, has_more, next_start = await service.list_open(
        page_size=page_size, page_start=page_start, keyword=keyword
    )
    teams_map, users_map, profiles_map, chosen_map = await _load_maps_for_posts(
        db, posts
    )
    viewer_team_ids = await _viewer_team_ids(db, viewer_id)
    items = [
        _post_to_api_seen_by(
            p,
            teams_map=teams_map,
            users_map=users_map,
            profiles_map=profiles_map,
            chosen_map=chosen_map,
            viewer_team_ids=viewer_team_ids,
            contact_visible=viewer_id is not None,
        )
        for p in posts
    ]
    return {
        "code": 200,
        "message": "OK",
        "data": {
            "posts": items,
            "page": {
                "pageStart": page_start,
                "pageSize": len(items),
                "hasMore": has_more,
                "nextStart": next_start,
            },
        },
    }


@router.patch("/recruitment/{postId}", summary="Edit Recruitment Post")
async def edit_recruitment_post(
    post_id: Annotated[int, Path(ge=1, alias="postId")],
    payload: PatchRecruitmentPostRequest,
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: RecruitmentService = Depends(_get_recruitment_service),
    db=Depends(get_db),
) -> dict:
    if payload.title is not None and not payload.title.strip():
        raise BadRequestError("title must be a non-empty string")
    if payload.status is not None:
        valid_statuses = {s.value for s in RecruitmentStatus}
        if payload.status not in valid_statuses:
            raise BadRequestError(f"Invalid status. Must be one of {valid_statuses}")

    # Convert expiresAt ms timestamp to datetime if provided
    expires_at: datetime | None = ...  # type: ignore[assignment]
    if "expires_at" in payload.model_fields_set:
        if payload.expires_at is None:
            expires_at = None
        else:
            expires_at = datetime.fromtimestamp(payload.expires_at / 1000, tz=UTC)

    # Use ... sentinel for fields not included in request
    contact: str | None = ...  # type: ignore[assignment]
    if "contact" in payload.model_fields_set:
        contact = payload.contact
    max_members: int | None = ...  # type: ignore[assignment]
    if "max_members" in payload.model_fields_set:
        max_members = payload.max_members

    post = await service.update_post(
        post_id=post_id,
        actor_user_id=auth_user.user_id,
        title=payload.title,
        content=payload.content,
        contact=contact,
        max_members=max_members,
        status=payload.status,
        expires_at=expires_at,
    )
    teams_map, users_map, profiles_map, chosen_map = await _load_maps_for_posts(
        db, [post]
    )
    return {
        "code": 200,
        "message": "OK",
        "data": {
            "post": _post_to_api(
                post,
                teams_map=teams_map,
                users_map=users_map,
                profiles_map=profiles_map,
                chosen_map=chosen_map,
            )
        },
    }


@router.delete(
    "/recruitment/{postId}",
    summary="Delete Recruitment Post",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_recruitment_post(
    post_id: Annotated[int, Path(ge=1, alias="postId")],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: RecruitmentService = Depends(_get_recruitment_service),
) -> Response:
    await service.delete_post(post_id=post_id, actor_user_id=auth_user.user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Team-scoped recruitment endpoints (mounted on teams router)
# ---------------------------------------------------------------------------

team_recruitment_router = APIRouter(prefix="/teams", tags=["Teams", "Recruitment"])


@team_recruitment_router.post(
    "/{teamId}/recruitment",
    summary="Create Recruitment Post",
    status_code=status.HTTP_201_CREATED,
)
async def create_recruitment_post(
    team_id: Annotated[int, Path(ge=1, alias="teamId")],
    payload: CreateRecruitmentPostRequest,
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: RecruitmentService = Depends(_get_recruitment_service),
    db=Depends(get_db),
) -> dict:
    expires_at: datetime | None = None
    if payload.expires_at is not None:
        expires_at = datetime.fromtimestamp(payload.expires_at / 1000, tz=UTC)

    post = await service.create_post(
        team_id=team_id,
        actor_user_id=auth_user.user_id,
        title=payload.title.strip(),
        content=payload.content,
        contact=payload.contact,
        max_members=payload.max_members,
        expires_at=expires_at,
    )
    teams_map, users_map, profiles_map, chosen_map = await _load_maps_for_posts(
        db, [post]
    )
    return {
        "code": 201,
        "message": "Created",
        "data": {
            "post": _post_to_api(
                post,
                teams_map=teams_map,
                users_map=users_map,
                profiles_map=profiles_map,
                chosen_map=chosen_map,
            )
        },
    }


@team_recruitment_router.get(
    "/{teamId}/recruitment",
    summary="List Team Recruitment Posts",
)
async def list_team_recruitment_posts(
    team_id: Annotated[int, Path(ge=1, alias="teamId")],
    service: RecruitmentService = Depends(_get_recruitment_service),
    team_service: TeamService = Depends(get_team_service),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    db=Depends(get_db),
) -> dict:
    # 团队作用域的招募列表不比它挂着的那支队伍更公开（帖子可以带联系方式），
    # 所以先过这里的那道门，和 ``GET /teams/{teamId}`` 逐字同一句话：成员看得见
    # 自己的队，公开共享队谁都看得见，隐身 / 个人队对外人答 404 —— 不确认它存在。
    # 复用 ``TeamService.visible_team``，不在这里另写一套可见性判据。
    await team_service.visible_team(team_id, auth_user.user_id)
    posts = await service.list_by_team(team_id)
    teams_map, users_map, profiles_map, chosen_map = await _load_maps_for_posts(
        db, posts
    )
    items = [
        _post_to_api(
            p,
            teams_map=teams_map,
            users_map=users_map,
            profiles_map=profiles_map,
            chosen_map=chosen_map,
        )
        for p in posts
    ]
    return {
        "code": 200,
        "message": "OK",
        "data": {"posts": items},
    }
