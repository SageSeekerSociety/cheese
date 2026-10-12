"""看板的时间窗口与按天分桶 —— 一处定义，三块看板共用。

**为什么是 UTC 的午夜**：窗口的两端要和按天的桶对齐，否则 `days=7` 的窗口会跨进第
八天的一小段，而 series 里没有它的位置 —— 那一段数据被算进 totals 却不出现在图上，
两个数字对不上而两边各自都「对」。仓库既有的时间读法就是 UTC（`datetime.now(UTC)`，
见项目约定里的 datetime 规则），这里沿用。

**为什么 series 一定要补 0**：缺的那天不补，前端画出来的折线会把 7 天连成 5 天，
而且**没有人看得出来** —— 断点处是一条平滑的线，不是一段空白。补 0 的判据只能是
窗口本身（`days` 个日期），不能是「有数据的那些天」。
"""

from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func


def utc_day_window(days: int) -> tuple[datetime, datetime, list[date]]:
    """最近 `days` 天（**含今天**）的窗口：``[since, until)`` 与逐日的桶。

    窗口是**半开**的（`>= since AND < until`），不是「只判下界」：只判下界的话，
    今天之后的行（时钟偏移、回填）会一起算进来，而它们不属于「最近 7 天」。`until`
    取整天的下一天午夜，所以它等于 `since + days`，两端正好围出 `days` 个整日。

    返回的日期列表**最早的一天在前**，长度恒等于 `days` —— 调用方按它补 0，不自己
    数有数据的那些天。
    """
    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    since = today - timedelta(days=days - 1)
    until = since + timedelta(days=days)
    buckets = [(since + timedelta(days=i)).date() for i in range(days)]
    return since, until, buckets


def utc_day(column: Any) -> Any:
    """把一列时间戳按 **UTC 的天**分桶。

    第三个参数不能省：单参数的 `date_trunc('day', timestamptz)` 按**会话时区**切天，
    而部署的会话时区不一定是 UTC（本机是 `Asia/Shanghai`）。差别不报错、也不改变行
    数，只是把每天的边界挪了几小时 —— 图还是画得出来，只是和窗口的两端对不齐。
    """
    return func.date_trunc("day", column, "UTC")


def dense_series(
    buckets: list[date], columns: dict[str, Mapping[date, float]]
) -> list[dict]:
    """把仓储回来的「有几天就有几行」铺成「`len(buckets)` 行」，缺的那天补 0。

    补哪几列由调用方点名（`columns` 的名字），不猜：一条折线的字段是它的语义，
    猜错的表现是一栏全是 0 而没人报错。

    仓储只回有数据的那几天（SQL 的 `GROUP BY`），这里按**窗口**补 —— 补 0 的判据
    只能来自 `utc_day_window` 那一份日期列表，不能来自「有数据的那些天」，否则缺天
    这件事在折线上看不出来。
    """
    return [
        {
            "date": day.isoformat(),
            **{name: column.get(day, 0) for name, column in columns.items()},
        }
        for day in buckets
    ]
