"""管理后台的看板 —— 三个分类，三条路由。

管理台第三块。和 `admin_members.py` 一样，它管的不是反馈：这里出现的每一个字在反馈
功能删掉之后仍然成立（用量、账号、设备），所以门是共用的
`admin_common.PlatformAdminDep`、服务是 `domain/platform_stats/`，两者都不从反馈
那边借。

**一个分类一条路由**，不是一条接口把所有东西一次吐出来：页面上的分类控件切到哪一类
才拉哪一类。合成一条的话，切到第二、第三类时读的是几十秒前的数 —— 而那三类里有两类
读的是全平台增长最快的表（`resource_usage` 每调一次 `/v1/messages` 长一行），没有
理由让「看一眼反馈」把用量也读一遍。

单独的模块而不是塞进 `admin_feedback.py`：`main.py` 的自动发现是「一个模块一个
router」，同一个文件里的第二个 `APIRouter` 会被静默丢掉，理由写在
`admin_feedback.py` 的 docstring 里；这里是同一件事的第三半。

`days` 的上下界写死在签名上（`ge=1` / `le=90`）：上界不是防谁，是这个读本身有成本
（见上面那张表），而一个「把平台开板以来的用量都聚合一遍」的请求没有任何人要得起。
下界是 1，因为「0 天」的窗口画不出一条长度为 0 的折线 —— 那是一个只有调用方才会
写错的参数，400 比一张空图诚实。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.response import ok
from app.api.routes.admin_common import PlatformAdminDep
from app.core.db import get_db
from app.domain.platform_stats.services import PlatformStatsService

router = APIRouter(prefix="/admin/stats", tags=["admin"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


async def get_platform_stats_service(db: DbSession) -> PlatformStatsService:
    return PlatformStatsService(db)


StatsServiceDep = Annotated[PlatformStatsService, Depends(get_platform_stats_service)]


@router.get("/feedback")
async def feedback_stats(
    service: StatsServiceDep,
    handle: PlatformAdminDep,
    days: int = Query(default=7, ge=1, le=90),
) -> dict:
    """反馈那一块：栏位计数 + 按天的新增 / 解决 / 上线。

    形状（钉死的，前端按它写）：`counts` 是七个键的字典，`series` 恒有 `days` 行、
    最早一天在前、缺的那天补 0。口径在 `PlatformStatsService.feedback` 里。
    """
    return ok(await service.feedback(days=days, handle=handle))


@router.get("/usage")
async def usage_stats(
    service: StatsServiceDep,
    handle: PlatformAdminDep,
    days: int = Query(default=7, ge=1, le=90),
) -> dict:
    """用量那一块：窗口内的总量、按天序列、top-N 项目。

    `top_projects` 每一项都带 `project_id` 和名字 —— 柱子要能点进去，只给名字的
    柱子点不开。`totals.unpriced_tokens` 与 `totals.cost_usd` 一起读才对：前者是
    「这些 token 算不出价钱」的说明，不是零花钱。
    """
    return ok(await service.usage(days=days))


@router.get("/platform")
async def platform_stats(
    service: StatsServiceDep,
    handle: PlatformAdminDep,
    days: int = Query(default=7, ge=1, le=90),
) -> dict:
    """平台那一块：账号的存量与新增、设备/机器的存量。

    `machines` 那一组是**存量，不是在线数**：在线状态住在进程内存里，库里没有可以
    查的那一列，理由写在 `MachineInventoryRepository` 的模块 docstring 里。
    """
    return ok(await service.platform(days=days))
