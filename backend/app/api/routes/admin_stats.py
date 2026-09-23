"""管理后台的看板 —— 六个分类，六条路由。

管理台第三块。和 `admin_members.py` 一样，它管的不是反馈：这里出现的每一个字在反馈
功能删掉之后仍然成立（用量、账号、设备、交付），所以门是共用的
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

**新加的三条**（`/pipeline`、`/product`、`/integrations`）沿用同一纪律。其中
`/integrations` 故意**没有 `days`**：凭据与投递是存量问题，不是「这七天怎么变的」。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
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
    """用量那一块：窗口内的总量、按天序列、top-N 项目、以及**额度燃尽**。

    `top_projects` 每一项都带 `project_id` 和名字 —— 柱子要能点进去，只给名字的
    柱子点不开。`totals.unpriced_tokens` 与 `totals.cost_usd` 一起读才对。
    `credits` 那一组把「已耗尽 / 快烧完 / unlimited」三个互斥名单分开给 —— 三者
    不能加在一起，理由在 `gaps.py` 的模块 docstring 第 2 条。
    """
    return ok(await service.usage(days=days))


@router.get("/platform")
async def platform_stats(
    service: StatsServiceDep,
    handle: PlatformAdminDep,
    days: int = Query(default=7, ge=1, le=90),
) -> dict:
    """平台那一块：账号的存量与新增、设备/机器的存量、以及磁盘/预览/机器普查。

    `machines` 那一组是**存量，不是在线数**（`MachineInventoryRepository` 的模块
    docstring）。`extras` 里的三样各有各的口径，写在 `gaps.py` 对应方法上 ——
    磁盘只覆盖后端这一台，预览连接活在进程内存里，机器普查数的是台账行不是容器。
    """
    return ok(await service.platform(days=days))



def _http_endpoints(app) -> list[tuple[str, str]]:
    """整张 HTTP 路由表：`(method, path_template)`，一条端点一个方法一行。

    FastAPI 0.137 起 ``app.routes`` 不再摊平 ``include_router`` 进来的路由：它们
    各自是一个 ``_IncludedRouter`` 壳（本仓库 73 个壳里是全部业务端点），只扫
    ``isinstance(r, APIRoute)`` 会**只剩 main.py 直挂的那 3 条**，看板上于是
    「很多 api 都没显示」。壳上 ``effective_route_contexts()`` 会递归展开到真
    正的 ``APIRoute``，所以走它，不再靠 ``app.routes`` 的形状。

    跳过 `HEAD`（它和 GET 是同一个处理器，两行说的是同一件事）和 `OPTIONS`。
    FastAPI 自带的 `/openapi.json`、`/docs` 也列出来 —— 这一页的全部意义就是不漏。
    """
    from fastapi.routing import APIRoute

    def walk(node) -> list[APIRoute]:
        if isinstance(node, APIRoute):
            return [node]
        if hasattr(node, "effective_route_contexts"):
            out: list[APIRoute] = []
            for ctx in node.effective_route_contexts():
                out.extend(walk(getattr(ctx, "original_route", None)))
            return out
        original = getattr(node, "original_router", None)
        if original is not None:
            out = []
            for child in getattr(original, "routes", ()) or ():
                out.extend(walk(child))
            return out
        return []

    seen: set[tuple[str, str]] = set()
    endpoints: list[tuple[str, str]] = []
    for entry in app.routes:
        for route in walk(entry):
            for method in route.methods or ():
                if method in ("HEAD", "OPTIONS"):
                    continue
                key = (method, route.path)
                if key not in seen:
                    seen.add(key)
                    endpoints.append(key)
    return endpoints


@router.get("/performance")
async def performance_stats(
    service: StatsServiceDep,
    handle: PlatformAdminDep,
    request: Request,
) -> dict:
    """第四类：**这一刻**的接口耗时，按路由，外加投递与事件积压。

    和上面三条有一处不同，写在签名上：**没有 `days`**（接口耗时这一类没有窗口 ——
    数据在进程内存里，重启即清零），而且只覆盖这一个进程。`reliability` 那半是
    库里的存量（投递账本）加上本机 spool 的未读上界。

    「过去一周怎么变的」是另一个问题，原料在日志里（`main.py` 每个请求一行带
    毫秒），要的话是另做一件只读的事 —— 不是把这一条加上 `days`。
    """
    endpoints = list(_http_endpoints(request.app))
    return ok(await service.performance(routes_registered=endpoints))


@router.get("/pipeline")
async def pipeline_stats(
    service: StatsServiceDep,
    handle: PlatformAdminDep,
    days: int = Query(default=7, ge=1, le=90),
) -> dict:
    """交付管线那一块：验收卡积压、各段停留时长、待人动手的清单、轮次失败。

    这是产品自己的主链（「AI 干活、人验收」）。口径的三条硬事实写在
    `domain/platform_stats/pipeline.py` 的模块 docstring 里：机器闸门已退役、
    `void` 不是一个状态、`decided_at` 会被 revoke 覆写。页面上的注脚对应它们。
    """
    return ok(await service.pipeline(days=days))


@router.get("/product")
async def product_stats(
    service: StatsServiceDep,
    handle: PlatformAdminDep,
    days: int = Query(default=7, ge=1, le=90),
) -> dict:
    """产品健康那一块：北极星（每周被验收通过的 AI 成果数）与护栏。

    响应里的 `unavailable` 是**算不出来的那两条**，带一句「要先加什么埋点」。
    这里绝不放一个假的 0 —— 空值画破折号，只有真 0 才画 0（`AdminKpiCard` 的
    纪律）。
    """
    return ok(await service.product(days=days))


@router.get("/integrations")
async def integrations_stats(
    service: StatsServiceDep,
    handle: PlatformAdminDep,
) -> dict:
    """集成、凭据与准入那一块：专治静默降级。

    **没有 `days`**：凭据过期、投递未送出都是此刻的存量问题，不是一条窗口曲线。
    能查的直接出数；不能查的进 `unavailable`（GitHub 权限缺口、登录锁定历史、
    计量落账心跳都要先加埋点才查得到）。
    """
    return ok(await service.integrations())
