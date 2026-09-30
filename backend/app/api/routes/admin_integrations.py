"""后台的「飞书应用」设置页：管理员填一次 App ID / Secret / 域名。

成员不再各建一个企业自建应用（`domain/integration/models.py` 的 `FeishuApp` 讲了
为什么），于是平台上只有这一处能写这套凭据，也只有这一处该读它。门是后台共用的
`PlatformAdminDep` —— 进来先问「是不是平台管理员」，这里不自己判，理由和
`admin_common.py` 头上那段一样：判据写两份就会漂开。

**Secret 只写不回显。** `GET` 返回的 `feishu_app_view` 里没有它；`PUT` 的空串表示
「不改已经存下的那一个」，因为页面看不到它，也就无从重打。这样改域名或 App ID
不必先把口令找回来。
"""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.response import ok
from app.api.routes.admin_common import DbSession, PlatformAdminDep
from app.domain.integration.service import FeishuAppService, feishu_app_view

router = APIRouter(prefix="/admin/integrations", tags=["admin"])


class FeishuAppIn(BaseModel):
    app_id: str = Field(min_length=1)
    #: 留空 = 不改。只有平台管理员能到这里，而回显里永远没有它。
    app_secret: str = ""
    domain: str = "feishu"


@router.get("/feishu")
async def read_feishu_app(db: DbSession, handle: PlatformAdminDep) -> dict:
    return ok(feishu_app_view(await FeishuAppService(db).current()))


@router.put("/feishu")
async def save_feishu_app(
    body: FeishuAppIn, db: DbSession, handle: PlatformAdminDep
) -> dict:
    row = await FeishuAppService(db).save(
        app_id=body.app_id, app_secret=body.app_secret, domain=body.domain, by=handle
    )
    await db.commit()
    return ok(feishu_app_view(row))
