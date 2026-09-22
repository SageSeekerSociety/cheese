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
from app.domain.platform_stats.repositories import MachineInventoryRepository
from app.domain.platform_stats.windows import dense_series, utc_day_window
from app.domain.usage.services import UsageService
from app.domain.user.services import AccountService

#: 用量柱状图上画几根。有上界是这个查询的一部分而不是优化：窗口收时间范围、`limit`
#: 收行数，两个都少一个都会让这个读在全平台最长的表上无界。
TOP_PROJECTS = 10


class PlatformStatsService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        # 三个别的领域的读：各自走它们自己那道 service 门，不摸它们的仓储。
        self._usage = UsageService(session)
        self._users = AccountService(session)
        self._feedback = FeedbackService(session)
        self._machines = MachineInventoryRepository(session)

    async def feedback(self, *, days: int, handle: str) -> dict:
        """反馈那一块：五个栏位的数（+ 未读、未指派）与按天的新增/解决/上线。

        `counts` 走 `FeedbackService.counts`，**不是**在这里再数一遍：那五个数是
        列表页上那一行标签页的同一批数字，两处各数一次的表现是「看板和反馈管理页
        对同一天给出两个数」，而两边各自看着都对。这里只挑出响应形状要的那七个键。

        `counts` 是全量口径（不含窗口），`series` 才是窗口内的 —— 和页面上「现在是
        多少 / 这七天怎么变的」这两个问题一一对应。
        """
        since, until, buckets = utc_day_window(days)
        counts = await self._feedback.counts(handle=handle, is_admin=True)
        created = await self._feedback.created_series(since=since, until=until)
        resolved = await self._feedback.reached_series(
            status=FeedbackStatus.resolved, since=since, until=until
        )
        deployed = await self._feedback.reached_series(
            status=FeedbackStatus.deployed, since=since, until=until
        )
        return {
            "days": days,
            "counts": {
                key: counts[key]
                for key in (
                    "all",
                    "hot",
                    "active",
                    "resolved",
                    "deployed",
                    "unread",
                    "unassigned",
                )
            },
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
        admins = await AdminService(self._session).admin_handles()
        return {
            "days": days,
            "people": {
                "total": await self._users.count_accounts(),
                "new": sum(created.values()),
                "admins": len(admins),
                "series": dense_series(buckets, {"created": created}),
            },
            "machines": await self._machines.counts(),
        }
