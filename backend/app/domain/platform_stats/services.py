"""平台看板的三个分类 —— 一个分类一个方法、一条路由，各读各的窗口。

**为什么不合成一个方法**：页面上的分类控件切到哪一类才拉哪一类，一次把所有东西
吐出来意味着切到第二、第三类时读的是几十秒前的数（而且那三类里有两类读的是全平台
增长最快的表）。三条路由各自回答一个分类，客户端就能只问它要画的那一块。

**为什么窗口是必填的、不是默认值**：`resource_usage` 每调一次 `/v1/messages` 长一行，
没有 WHERE 的全表聚合在页面上一次也不允许出现。窗口由 `utc_day_window` 一处算出来
（半开的 `[since, until)`，UTC 的天），三个方法都从这里拿。

`series` 一律**补齐到 `days` 天**：缺天不补的话折线会把 7 天画成 5 天，而且没有人
看得出来（断点处是一条平滑的线，不是一段空白）。补 0 走 `dense_series`，判据是窗口
自己那份日期列表。
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.admin.services import AdminService
from app.domain.feedback.models import FeedbackStatus
from app.domain.feedback.services import FeedbackService
from app.domain.platform_stats.gaps import GapRepository
from app.domain.platform_stats.integrations import IntegrationsRepository
from app.domain.platform_stats.pipeline import PipelineRepository
from app.domain.platform_stats.product import ProductHealthRepository
from app.domain.platform_stats.repositories import MachineInventoryRepository
from app.domain.platform_stats.windows import dense_series, utc_day_window
from app.domain.usage.services import UsageService
from app.domain.user.services import AccountService

#: 用量柱状图上画几根。有上界是这个查询的一部分而不是优化：窗口收时间范围、`limit`
#: 收行数，两个都少一个都会让这个读在全平台最长的表上无界。
TOP_PROJECTS = 10

#: 用量按模型拆分时画几根。和 `TOP_PROJECTS` 同一个上界理由：模型是低基数维度
#: （个位数到十几），但窗口里的行数不是，所以仍然要 limit 收口。
TOP_MODELS = 10


class PlatformStatsService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        # 三个别的领域的读：各自走它们自己那道 service 门，不摸它们的仓储。
        self._usage = UsageService(session)
        self._users = AccountService(session)
        self._feedback = FeedbackService(session)
        self._machines = MachineInventoryRepository(session)
        self._pipeline = PipelineRepository(session)
        self._product = ProductHealthRepository(session)
        self._integrations = IntegrationsRepository(session)
        self._gaps = GapRepository(session)

    async def feedback(self, *, days: int, handle: str) -> dict:
        """反馈那一块：**全量口径**的总量/四栏/四级状态，加窗口内的三条曲线。

        口径是这一段的全部内容，所以写在最前面：**看板数的是整个板子**，不是公开
        那一臂。此前这里走 `FeedbackService.counts`（它被 `PUBLIC_ONLY` 收窄，因为
        那是反馈中心那一行标签页给匿名读者看的数），而旁边的 `series` 走的是全量
        —— 于是卡片上的「进行中」和曲线下的「进行中」是两个口径，两边各自都看着
        对。现在两边都问整个板子，`admin_board_counts` 就是那个口径的名字。

        `unread` 仍然走 `counts`，而且只取那一个键：它问的是**这个管理员**的读到
        哪儿了，本来就是人各一份，和板子有多大无关。
        """
        since, until, buckets = utc_day_window(days)
        board = await self._feedback.admin_board_counts()
        mine = await self._feedback.counts(handle=handle, is_admin=True)
        created = await self._feedback.created_series(since=since, until=until)
        resolved = await self._feedback.reached_series(
            status=FeedbackStatus.resolved, since=since, until=until
        )
        deployed = await self._feedback.reached_series(
            status=FeedbackStatus.deployed, since=since, until=until
        )
        return {
            "days": days,
            "total": board["total"],
            "columns": board["columns"],
            "status": board["status"],
            "unread": mine.get("unread", 0),
            "series": dense_series(
                buckets,
                {"created": created, "resolved": resolved, "deployed": deployed},
            ),
        }

    async def usage(self, *, days: int) -> dict:
        """用量那一块：窗口内的总量、按天序列、以及最花钱的几个项目。

        `totals.unpriced_tokens` 是「这些 token 算不出价钱」的诚实说明（订阅按月
        计费，行上的 `cost_usd = 0.0` 意思是**没有价**，不是免费），所以它和
        `totals.cost_usd` 一起给 —— 少了它，几百万 token 上印一个 `$0.0000` 读起来
        像「这个月没花钱」。
        """
        since, until, buckets = utc_day_window(days)
        totals = await self._usage.platform_totals(since=since, until=until)
        raw = await self._usage.platform_series(since=since, until=until)
        top = await self._usage.top_projects(
            since=since, until=until, limit=TOP_PROJECTS
        )
        # 两个正交的切口：模型回答「贵的是哪个模型」，通路回答「贵的是计费方式还是
        # 模型」（订阅那一半没有单价，`unpriced_tokens` 的来源就在这条拆分上）。
        models = await self._usage.by_model(since=since, until=until, limit=TOP_MODELS)
        routes = await self._usage.by_route(since=since, until=until)
        return {
            "days": days,
            "totals": totals,
            "series": dense_series(
                buckets,
                {
                    "tokens": {day: row["tokens"] for day, row in raw.items()},
                    "calls": {day: row["calls"] for day, row in raw.items()},
                    "cost_usd": {day: row["cost_usd"] for day, row in raw.items()},
                },
            ),
            "top_projects": top,
            "by_model": models,
            "by_route": routes,
            # 额度燃尽：三个项目同时停摆时，上面那条 token 曲线只是「今天用量下降」，
            # 看起来像好消息。`credits` 把「已耗尽 / 快烧完 / unlimited」三个互斥
            # 名单分开给 —— 理由见 `gaps.py` 模块 docstring 第 2 条。
            "credits": await self._gaps.credits_burnout(days=days),
        }

    async def platform(self, *, days: int) -> dict:
        """平台那一块：账号的存量与新增、以及设备/机器的**存量**。

        `admins` 的人数是 `AdminService.admin_handles` 的长度 —— 判据只有那一处
        （根 ∪ 页面上加的），这里**不另定一份**：另定一份的症状是「看板说 9 个，
        成员管理页列出来 8 个」。

        `new` 是窗口内新增的账号数，也就等于 `series` 里那些 `created` 的和：两个
        数来自同一份读数，一个给总量一个给形状。机器那半见
        `MachineInventoryRepository` —— 它是存量，**不是在线数**，原因写在那里。
        """
        since, until, buckets = utc_day_window(days)
        created = await self._users.accounts_series(since=since, until=until)
        by_kind = await self._users.accounts_series_by_kind(since=since, until=until)
        admins = await AdminService(self._session).admin_handles()
        kinds = await self._users.count_accounts_by_kind()
        return {
            "days": days,
            "people": {
                "total": await self._users.count_accounts(),
                "new": sum(created.values()),
                "admins": len(admins),
                # 真人 / agent 的拆分。判据是 `agent_bindings`，和
                # `IdentityService.is_agent` 同一份 —— 见 `count_accounts_by_kind`。
                # `total` / `new` / `series.created` 仍是**和**：拆分是附加列，不是
                # 把老口径改写成只数真人。
                "humans": kinds["humans"],
                "agents": kinds["agents"],
                "new_humans": sum(by_kind["humans"].values()),
                "new_agents": sum(by_kind["agents"].values()),
                "series": dense_series(
                    buckets,
                    {
                        "created": created,
                        "human_created": by_kind["humans"],
                        "agent_created": by_kind["agents"],
                    },
                ),
            },
            "machines": await self._machines.counts(),
            # 「平台现在健康吗」——和上面两组的差别是**这一刻**的，不是存量也不是
            # 窗口。复用 `/health/detailed` 那一套判据（`_REQUIRED_CHECKS` 的同一批
            # 检查），不在这里另写一份「什么算健康」：两处各写一份的话，看板说健康、
            # readyz 说不健康，而两边各自都看着对。
            "health": await _health_snapshot(),
            # 三样缺口：磁盘压力（**只覆盖后端这一台**）、预览连接（**进程内存**）、
            # 机器状态普查（**没有容器清单**）。各自的口径写在 `gaps.py` 上。
            "extras": await self._gaps.platform_extras(),
        }

    async def performance(
        self, *, routes_registered: list[tuple[str, str]] | int | None = None
    ) -> dict:
        """性能那一块：**这一刻**的接口耗时 + 投递与事件积压。

        前半在 `performance_snapshot()`（进程内存，重启即清零，只有这一个进程）。
        后半是新加的：接口很快而投递发不出去时，用户什么都没收到，p95 还是绿的。
        """
        from app.core.metrics import registry
        from app.domain.platform_stats.performance import performance_snapshot

        snap = performance_snapshot(routes_registered=routes_registered)
        snap["reliability"] = await self._gaps.reliability()
        # 这两个是活的读数，快照自己不碰（它只看路由指标和网络速率环）。
        # `active_requests` 减 1：读它的这一次请求自己就在里面。
        snap["active_requests"] = max(
            0, registry.gauge("http_requests_active").value - 1
        )
        snap["uptime_seconds"] = registry.export()["uptime_seconds"]
        return snap

    async def pipeline(self, *, days: int) -> dict:
        """交付管线那一块：积压、停留时长、待人动手、轮次失败。

        这是产品自己的主链（「AI 干活、人验收」），也是看板上第一次有它的数字。
        口径的三条硬事实（闸门已退役 / `void` 不是状态 / `decided_at` 会被覆写）写在
        `pipeline.py` 的模块 docstring 里，页面上的注脚对应的是它们。
        """
        since, until, _ = utc_day_window(days)
        return {
            "days": days,
            "backlog": await self._pipeline.backlog(),
            "stuck_cards": await self._pipeline.stuck_cards(),
            "dwell": await self._pipeline.dwell(since=since, until=until),
            "needs_you": await self._pipeline.needs_you(),
            "turn_failures": await self._pipeline.turn_failures(
                since=since, until=until
            ),
            "host_health": await self._pipeline.host_health(),
            "unsettled_dispatches": await self._pipeline.unsettled_dispatches(),
        }

    async def product(self, *, days: int) -> dict:
        """产品健康那一块：北极星 + 护栏。

        `unavailable` 里那两条**今天根本算不出来**（缺 `summon` 落库、缺
        `exhausted_at` 与活跃心跳），响应里带着理由而不是一个假 0。
        """
        return await self._product.snapshot(days=days)

    async def integrations(self) -> dict:
        """集成与凭据那一块：静默降级。

        能查的直接出数；不能查的进 `unavailable`。**没有窗口**（凭据与投递都是
        存量问题，不是「这七天怎么变的」）。
        """
        return await self._integrations.snapshot()


async def _health_snapshot() -> dict:
    """`/health/detailed` 的那几个检查，读成看板能画的形状。

    **不 import 路由模块**（`api.routes.health` 里是 FastAPI handler，import 它会
    把整条路由装配拖进领域层）。判据本身在那几个私有函数里，这里做的是同一件事的
    第二次回答 —— 所以它只取「状态 + 一句话」，绝不重算健康与否：`status` 原样带
    出来，页面照读。

    `overall` 是三者里最差的那一个（up < stalling < down），而不是「多数票」：
    一个 down 的 Redis 不该被两个 up 投成「healthy」。
    """
    from app.api.routes import health as health_routes

    checks = await health_routes.detailed_health_check()
    return {
        "overall": checks.get("status", "unknown"),
        "checks": {
            name: {
                "status": body.get("status", "unknown"),
                "detail": body.get("error") or body.get("recent_ms"),
            }
            for name, body in checks.get("checks", {}).items()
        },
    }
