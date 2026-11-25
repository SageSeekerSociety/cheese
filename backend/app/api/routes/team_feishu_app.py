"""团队设置里的「飞书应用」：这个团队连接成员飞书时用的那一个企业自建应用，以及每个成员
自己通过它的连接（连接本身走 `/me/integrations/feishu`，用的是本人的飞书授权）。

企业自建应用只在它自己那家企业的飞书里能用，所以应用设在团队上：公司的团队设公司的
应用，一个人自己用就设在他的个人团队上。只有团队里的人读得到它（不含 Secret）；其中能改
团队设置的人（团队管理员；个人团队是它的主人）能设、改、删（`Can(TeamPermission.edit_settings)`
之外还要真在团队里：没有席位、只凭平台管理员身份管着这个团队的人碰不到它）。

**Secret 只写不回显。** 读回来的结构里没有它；`PUT` 的空串表示不改已经存下的那一个。
保存前先拿这套凭据向飞书换一次应用令牌，Secret 不对当场报错、什么也不存。成员的连接
只记是哪个团队的应用，凭据每次调用时从这里读，所以换了 Secret 所有人下一次调用就用上。
"""

from typing import Annotated

from fastapi import APIRouter, Path
from pydantic import BaseModel, Field

from app.api.auth import ActorResolverDep
from app.api.request_deps import DbSession
from app.api.response import ok, typed_response
from app.api.write_access import Can
from app.domain.integration.service import (
    TeamFeishuAppService,
    feishu_connection_view,
    team_app_view,
)
from app.domain.team.permissions import TEAM
from app.domain.team.permissions import Permission as TeamPermission

router = APIRouter(prefix="/teams", tags=["Teams"])

TeamId = Annotated[int, Path(ge=1, alias="teamId")]


class FeishuConnectionOut(BaseModel):
    """本人通过这个团队的应用连的那一个飞书连接。"""

    id: str
    status: str
    user_authorized: bool


class TeamFeishuAppOut(BaseModel):
    """团队的飞书应用，设置页看到的样子。没有 Secret。"""

    configured: bool
    app_id: str
    domain: str
    updated_by: str
    updated_at: str | None
    #: 要填进应用「重定向 URL」的授权回调地址。
    redirect_uri: str
    #: 读的这个人能不能设：团队管理员，个人团队是它的主人。
    can_manage: bool = False
    #: 读的这个人自己通过这个应用连的飞书；没连是 null。
    connection: FeishuConnectionOut | None = None


class TeamFeishuAppIn(BaseModel):
    app_id: str = Field(min_length=1, max_length=64)
    #: 留空 = 不改已经存下的那一个。回显里永远没有它。
    app_secret: str = Field(default="", max_length=256)
    domain: str = "feishu"


@router.get(
    "/{teamId}/feishu-app",
    **typed_response(TeamFeishuAppOut),
    summary="Read the Team's Feishu App",
    dependencies=[Can(TeamPermission.view_members)],
)
async def read_team_feishu_app(
    team_id: TeamId, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """团队里每个成员都读得到：用的是哪个应用（不含 Secret）、自己能不能设、自己通过它
    连上没有。连接、重新连接、断开都在这一栏里做，用的是本人自己的飞书授权。"""
    actor = await resolver.resolve()
    assert actor.user_id is not None
    service = TeamFeishuAppService(db)
    await service.require_member(team_id, actor.user_id)
    return ok(
        {
            **team_app_view(await service.get(team_id)),
            "can_manage": await TEAM.allows(
                db, team_id, actor.user_id, TeamPermission.edit_settings
            ),
            "connection": feishu_connection_view(
                await service.connection_for(actor.user_id, team_id)
            ),
        }
    )


@router.put(
    "/{teamId}/feishu-app",
    **typed_response(TeamFeishuAppOut),
    summary="Set the Team's Feishu App",
    dependencies=[Can(TeamPermission.edit_settings)],
)
async def save_team_feishu_app(
    team_id: TeamId,
    body: TeamFeishuAppIn,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    actor = await resolver.resolve()
    assert actor.user_id is not None
    service = TeamFeishuAppService(db)
    await service.require_member(team_id, actor.user_id)
    row = await service.save(
        team_id=team_id,
        app_id=body.app_id.strip(),
        app_secret=body.app_secret.strip(),
        domain=body.domain,
        by=actor.handle,
    )
    await db.commit()
    return ok(team_app_view(row))


@router.delete(
    "/{teamId}/feishu-app",
    **typed_response(TeamFeishuAppOut),
    summary="Remove the Team's Feishu App",
    dependencies=[Can(TeamPermission.edit_settings)],
)
async def remove_team_feishu_app(
    team_id: TeamId, db: DbSession, resolver: ActorResolverDep
) -> dict:
    actor = await resolver.resolve()
    assert actor.user_id is not None
    service = TeamFeishuAppService(db)
    await service.require_member(team_id, actor.user_id)
    await service.remove(team_id)
    await db.commit()
    return ok(team_app_view(None))
