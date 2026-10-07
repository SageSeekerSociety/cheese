"""空间公告，一条一个地址。

读：空间里的每个人（和空间本身同一道门，`_ensure_space_visible`）。
写（发布、修改、置顶、删除）：这个空间的所有者与管理员（`is_space_admin`）。
"""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Path, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.routes.spaces import _ensure_space_visible, require_reviewed_space
from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.auth.space_access import is_space_admin
from app.core.errors import ForbiddenError
from app.db.session import get_db
from app.domain.space.announcement_service import (
    TITLE_MAX_LENGTH,
    AnnouncementView,
    SpaceAnnouncementService,
)
from app.domain.user.services import user_service

router = APIRouter(
    prefix="/spaces", tags=["Spaces"], dependencies=[Depends(require_reviewed_space)]
)

SpaceId = Annotated[int, Path(ge=1, alias="spaceId")]
AnnouncementId = Annotated[int, Path(ge=1, alias="announcementId")]


class PublishAnnouncementRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    title: str = Field(..., min_length=1, max_length=TITLE_MAX_LENGTH)
    content: str = ""
    pinned: bool = False
    #: Epoch milliseconds; null = it does not expire.
    expires_at: int | None = Field(default=None, alias="expiresAt", ge=0)


class PatchAnnouncementRequest(BaseModel):
    """Each field present is changed; ``expiresAt: null`` clears the expiry."""

    model_config = ConfigDict(populate_by_name=True)

    title: str | None = Field(default=None, min_length=1, max_length=TITLE_MAX_LENGTH)
    content: str | None = None
    pinned: bool | None = None
    expires_at: int | None = Field(default=None, alias="expiresAt", ge=0)


def _from_ms(value: int | None) -> datetime | None:
    return None if value is None else datetime.fromtimestamp(value / 1000, tz=UTC)


def _ms(value: datetime | None) -> int | None:
    return None if value is None else int(value.timestamp() * 1000)


async def _chosen_avatar_ids(db, views: list[AnnouncementView]) -> dict[int, int]:
    """这一批公告的作者里，谁**自己挑过**头像、挑的是哪一张。

    ``Author.avatar_id`` 是原始的 profile 值，注册时人人都被写上全局默认那一行，直接
    回它会让所有没挑过的人共用一张脸。判据只有一处（``UserService.chosen_avatar_ids``
    → ``UserProfileRepository.chosen_avatar_ids``），这里不重复；没挑过的人不在映射里，
    序列化时回 null，交给前端的 ``UserAvatar`` 画彩色首字母。
    """
    ids = [v.author.id for v in views if v.author is not None]
    return await user_service(db).chosen_avatar_ids(ids)


def _to_api(view: AnnouncementView, *, chosen_avatar_ids: dict[int, int]) -> dict:
    row, author = view.row, view.author
    return {
        "id": row.id,
        "spaceId": row.space_id,
        "title": row.title,
        "content": row.content,
        "pinned": row.pinned,
        "expiresAt": _ms(row.expires_at),
        "createdAt": _ms(row.created_at),
        "updatedAt": _ms(row.updated_at),
        "author": (
            {
                "id": author.id,
                "username": author.username,
                "nickname": author.nickname,
                # 只给这个人自己挑过的那个 id；没挑过是 None（见 _chosen_avatar_ids）。
                "avatarId": chosen_avatar_ids.get(author.id),
            }
            if author is not None
            else None
        ),
    }


async def _ensure_space_manager(*, db, space_id: int, user_id: int) -> None:
    await _ensure_space_visible(db=db, space_id=space_id, user_id=user_id)
    if not await is_space_admin(session=db, space_id=space_id, user_id=user_id):
        raise ForbiddenError("Only space admins can perform this action")


@router.get("/{spaceId}/announcements", summary="List a space's announcements")
async def list_announcements(
    space_id: SpaceId,
    auth_user: AuthUserInfo = Depends(require_auth_user),
    db=Depends(get_db),
) -> dict:
    """``current`` (pinned first, then newest) and ``expired``, split by the
    server's clock. ``notifyCount`` is how many people a new announcement from
    the caller would notify — given to managers only, null for everyone else."""
    await _ensure_space_visible(db=db, space_id=space_id, user_id=auth_user.user_id)
    service = SpaceAnnouncementService(db)
    listed = await service.list_for_space(space_id)
    manager = await is_space_admin(
        session=db, space_id=space_id, user_id=auth_user.user_id
    )
    notify_count = (
        await service.audience_size(space_id, author_id=auth_user.user_id)
        if manager
        else None
    )
    chosen = await _chosen_avatar_ids(db, [*listed.current, *listed.expired])
    return {
        "code": 200,
        "message": "OK",
        "data": {
            "current": [_to_api(v, chosen_avatar_ids=chosen) for v in listed.current],
            "expired": [_to_api(v, chosen_avatar_ids=chosen) for v in listed.expired],
            "notifyCount": notify_count,
        },
    }


@router.post(
    "/{spaceId}/announcements",
    summary="Publish an announcement",
    status_code=status.HTTP_201_CREATED,
)
async def publish_announcement(
    space_id: SpaceId,
    payload: PublishAnnouncementRequest,
    auth_user: AuthUserInfo = Depends(require_auth_user),
    db=Depends(get_db),
) -> dict:
    await _ensure_space_manager(db=db, space_id=space_id, user_id=auth_user.user_id)
    view = await SpaceAnnouncementService(db).publish(
        space_id=space_id,
        author_id=auth_user.user_id,
        title=payload.title,
        content=payload.content,
        pinned=payload.pinned,
        expires_at=_from_ms(payload.expires_at),
    )
    chosen = await _chosen_avatar_ids(db, [view])
    return {
        "code": 201,
        "message": "Created",
        "data": {"announcement": _to_api(view, chosen_avatar_ids=chosen)},
    }


@router.patch(
    "/{spaceId}/announcements/{announcementId}", summary="Update an announcement"
)
async def patch_announcement(
    space_id: SpaceId,
    announcement_id: AnnouncementId,
    payload: PatchAnnouncementRequest,
    auth_user: AuthUserInfo = Depends(require_auth_user),
    db=Depends(get_db),
) -> dict:
    await _ensure_space_manager(db=db, space_id=space_id, user_id=auth_user.user_id)
    view = await SpaceAnnouncementService(db).update(
        space_id=space_id,
        announcement_id=announcement_id,
        title=payload.title,
        content=payload.content,
        pinned=payload.pinned,
        expires_at=_from_ms(payload.expires_at),
        set_expires_at="expires_at" in payload.model_fields_set,
    )
    chosen = await _chosen_avatar_ids(db, [view])
    return {
        "code": 200,
        "message": "OK",
        "data": {"announcement": _to_api(view, chosen_avatar_ids=chosen)},
    }


@router.delete(
    "/{spaceId}/announcements/{announcementId}",
    summary="Delete an announcement",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_announcement(
    space_id: SpaceId,
    announcement_id: AnnouncementId,
    auth_user: AuthUserInfo = Depends(require_auth_user),
    db=Depends(get_db),
) -> None:
    await _ensure_space_manager(db=db, space_id=space_id, user_id=auth_user.user_id)
    await SpaceAnnouncementService(db).delete(
        space_id=space_id, announcement_id=announcement_id
    )
    return None
