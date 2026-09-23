"""平台管理员那道门，以及后台各模块共用的会话/服务依赖。

管理后台的每个模块都从这里进来。这个模块**自己不是一处后台**、没有 `APIRouter`：
它只回答「这个请求的人是不是平台管理员」，谁要用谁依赖它。写成依赖而不是每处调一次
函数，是因为「这个端点归管理员」这件事在签名上就该看得见 —— 读一个 handler 不用翻
到函数体中间才知道它有没有过门。

搬到这里的理由和 `app/domain/admin/` 那次搬家是同一件：这道门以前长在
`admin_feedback.py` 里，`admin_members.py` 从那边借。管理员一旦不只是管反馈的
（`platform_admin_handles` 那个名字说的就是这件事），「成员管理要向一个反馈路由要
它的门」就成了错的形状；两边真正的共同点不是反馈，是**同一个管理员判据**。

进来先过 `require_platform_admin`：判据是**配置里的根管理员 ∪ `platform_admins`
表**（`AdminService.admin_handles`，理由见 `app/domain/admin/services.py`）。名单本身
在 `admin_members.py`（`/admin/admins`）里改 —— 后台两块问的是同一个问题，判据写两份
就会漂开，症状是「反馈管理进得去、成员管理进不去」。
"""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolverDep
from app.core.db import get_db
from app.core.errors import ForbiddenError
from app.domain.admin.services import AdminService
from app.domain.authz import policy
from app.domain.identity.services import IdentityService

DbSession = Annotated[AsyncSession, Depends(get_db)]


async def get_admin_service(db: DbSession) -> AdminService:
    return AdminService(db)


AdminServiceDep = Annotated[AdminService, Depends(get_admin_service)]


async def require_platform_admin(
    db: DbSession,
    admins: AdminServiceDep,
    resolver: ActorResolverDep,
) -> str:
    """The gates every admin handler passes, in one place: returns the handle.

    Two questions, and the route asks neither itself. `require_admin` answers
    「是不是平台管理员」 from the allow-list; §4.3's 「管理动作 agent 不能做」 is
    `authz.policy.refuse_management_action`, which is where 「这个 actor 能干
    什么」 is answered — the route supplies the DB-backed binding read and
    raises what comes back.

    The refusal being reached **from the route body** is the part that is not
    negotiable: `/admin/*` is not in `_CHEESE_WRITE_PATHS`, that table is a
    whitelist, so nothing in the middleware looks at this prefix and a refusal
    left to it would not exist. Reachable, not theoretical: a device screen's
    token (`X-Cheese-Screen`) resolves on any path, including one with no topic
    in it, unlike a per-turn `cheese` credential, which `ActorResolver` refuses
    when there is no project to scope it to.

    Returns the handle rather than the actor because every caller wants exactly
    that: it names who did the thing in `added_by_handle` / `by_handle`, and the
    refusal for an unauthenticated caller has already happened by then (`who.
    handle if who.authenticated else None` is what 「没有身份」 means here).

    `db` is threaded in because answering 「这个 handle 带不带 agent 绑定」 is a
    query: `IdentityService` needs a session, and moving the rule into the policy
    moved the rule, not the read it depends on.
    """
    who = await resolver.resolve(fallback_handle=None)
    refusal = await policy.refuse_management_action(
        who, carries_agent_binding=IdentityService(db).is_agent
    )
    if refusal is not None:
        raise ForbiddenError(refusal)
    return await admins.require_admin(who.handle if who.authenticated else None)


PlatformAdminDep = Annotated[str, Depends(require_platform_admin)]
