"""后台的设置页：平台上只该有一份、由管理员填一次的凭据。

两个集成各占一条地址：飞书应用（`/admin/integrations/feishu`）和外站（YOJ）教师
账号（`/admin/integrations/external-problem`）。都放在这一个模块里，因为是**同一个
页面**上的两块设置；也因为一个模块只能有一个 `APIRouter` 被挂上（`main.py` 的自动
发现按 `id()` 去重，同模块第二个 router 会被静默丢掉 —— 见 `admin_feedback.py`
头上那段）。

飞书：成员不再各建一个企业自建应用（`domain/integration/models.py` 的 `FeishuApp`
讲了为什么），于是平台上只有这一处能写这套凭据，也只有这一处该读它。

外站教师账号：同步靠它登录 YOJ。密码由服务端密封存起来（`core/crypto.py` 的
`Purpose.EXTERNAL_PROBLEM_SECRET`），进去就再也读不出来 —— 交给校方的凭据不该有
第二条读的路径。正式上线要校方书面点头，这里只是「先能用」。

门是后台共用的 `PlatformAdminDep` —— 进来先问「是不是平台管理员」，这里不自己判，
理由和 `admin_common.py` 头上那段一样：判据写两份就会漂开。

**Secret 只写不回显。** 两个 `GET` 的返回里都没有它；`PUT` 的空串表示「不改已经存
下的那一个」，因为页面看不到它，也就无从重打。这样改域名或账号名不必先把口令找回
来。
"""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.request_deps import DbSession
from app.api.response import ok, typed_response
from app.api.routes.admin_common import PlatformAdminDep
from app.api.write_access import ROUTE_DECIDES
from app.domain.external_problem.schemas import ExternalProblemAccountOut
from app.domain.external_problem.service import (
    ExternalProblemService,
    account_view,
)
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


class ExternalProblemAccountIn(BaseModel):
    """账号名（对 YOJ 来说是学号或教师工号）。"""

    username: str
    #: 留空 = 不改。只有平台管理员能到这里，而回显里永远没有它。
    password: str = ""


@router.get("/external-problem", **typed_response(ExternalProblemAccountOut))
async def read_external_problem_account(
    db: DbSession, handle: PlatformAdminDep
) -> dict:
    """配没配、配的是谁、谁配的。**没有密码。**

    ``configured: false`` 是那个页面要问的第一件事：没人配过时同步一律失败
    （``service.py`` 里 ``no_account``），而那是「还没到那一步」，不是错误。
    """
    return ok(account_view(await ExternalProblemService(db).account()))


@router.put(
    "/external-problem",
    dependencies=[ROUTE_DECIDES],
    **typed_response(ExternalProblemAccountOut),
)
async def save_external_problem_account(
    body: ExternalProblemAccountIn, db: DbSession, handle: PlatformAdminDep
) -> dict:
    """存下账号。密码留空 = 只改账号名，密码用原来那个。

    换账号名**不动**已经存下的密码：页面看不到它，管理员也无从重打，要求重打就等于
    逼他把口令找回来。要换密码就连密码一起填。
    """
    row = await ExternalProblemService(db).save_account(
        username=body.username, password=body.password, by=handle
    )
    view = account_view(row)
    await db.commit()
    return ok(view)
