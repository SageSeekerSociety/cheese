"""邀请码管理（平台管理员）。"""

from typing import TYPE_CHECKING, Annotated

from fastapi import (
    APIRouter,
    Depends,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.admin_common import PlatformAdminDep
from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.db.session import get_db

if TYPE_CHECKING:
    pass

from app.api.routes.users._common import CreateInviteCodeRequest
from app.domain.invite.services import InviteCodeService

router = APIRouter(prefix="/users", tags=["Users"])

# ── Invite Code Management ──────────────────────────────────────────────
#
# 邀请码是**注册凭据**：`settings.require_invite_code` 打开时，`/users/auth/email-code`
# 和 `/users/oauth/create` 都要拿它换一个账号（`domain/invite/services.py` 的
# `validate_code` / `consume_code` 就是那道交换）。所以这三条路由的判据是**平台
# 管理员**，不是「有一个登录会话」—— 建码等于发账号，停用等于关掉别人的注册通道。
#
# 门用的是 `admin_common.PlatformAdminDep`（部署配置里的根名单 ∪ `platform_admins`
# 表），和 `/admin/*` 是同一道、同一份名单：这里不新判据，只把这道门装上。
#
# 每个 handler 上还留着 `require_auth_user`，两件事：
#   * 匿名 → **401**（缺凭据），有会话但不在名单上 → **403**（缺资格）。这两个
#     答案不该混成一个：`/users/*` 这一族没身份的写路由本来就回 401
#     （`test_questions.py::test_cancel_invitation_no_auth` 钉着同一件事）。
#   * `create_invite_code` 的 `created_by` 记的是 user id（`InviteCode.created_by`
#     是 Integer），那个 id 只有这层有。列表和停用这两条用不到它，它在那儿是为了
#     让「匿名 401」在三条上是同一个答案 —— 同 `admin_models.py` 那句「这层用不到，
#     但它必须出现」。
#
# `GET /users/invite-codes` **今天打不到**：`GET /users/{userId}` 在本文件里更早注册
# （`/{userId}` 在 1722 行附近，这里在 4560 之后），`invite-codes` 落到那条的 int
# 转换上回 400 int_parsing。它也被装上同一道门 —— 谁哪天把顺序改对，这条一出生就是
# 管理员专有的，不会再有一个「登录即可读全部邀请码」的窗口。改顺序不在这次改动里：
# `/{userId}` 那一大家子（`/{userId}/questions`、`/{userId}/identity`……）都靠着它
# 现在的位置，动它要单独一轮。


@router.get(
    "/invite-codes",
    summary="List invite codes (admin)",
)
async def list_invite_codes(
    auth_user: Annotated[AuthUserInfo, Depends(require_auth_user)],
    admin_handle: PlatformAdminDep,
    session: AsyncSession = Depends(get_db),
) -> dict:

    service = InviteCodeService(session)
    codes = await service.list_codes()
    return {
        "code": 200,
        "message": "Success",
        "data": {
            "codes": [
                {
                    "id": c.id,
                    "code": c.code,
                    "maxUses": c.max_uses,
                    "useCount": c.use_count,
                    "isActive": c.is_active,
                    "createdBy": c.created_by,
                    "note": c.note,
                    "createdAt": c.created_at.isoformat() if c.created_at else None,
                    "expiresAt": c.expires_at.isoformat() if c.expires_at else None,
                }
                for c in codes
            ]
        },
    }


@router.post(
    "/invite-codes",
    summary="Create invite code (admin)",
)
async def create_invite_code(
    payload: CreateInviteCodeRequest,
    auth_user: Annotated[AuthUserInfo, Depends(require_auth_user)],
    admin_handle: PlatformAdminDep,
    session: AsyncSession = Depends(get_db),
) -> dict:

    service = InviteCodeService(session)
    invite = await service.create_code(
        max_uses=payload.max_uses,
        created_by=auth_user.user_id,
        note=payload.note,
    )
    await session.commit()
    return {
        "code": 201,
        "message": "Invite code created.",
        "data": {
            "code": invite.code,
            "id": invite.id,
            "maxUses": invite.max_uses,
        },
    }


@router.delete(
    "/invite-codes/{code_id}",
    summary="Deactivate invite code (admin)",
)
async def deactivate_invite_code(
    code_id: int,
    auth_user: Annotated[AuthUserInfo, Depends(require_auth_user)],
    admin_handle: PlatformAdminDep,
    session: AsyncSession = Depends(get_db),
) -> dict:

    service = InviteCodeService(session)
    await service.deactivate_code(code_id)
    await session.commit()
    return {"code": 200, "message": "Invite code deactivated.", "data": None}
