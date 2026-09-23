"""接口耗时快照 —— **这一刻**的，不是历史。

看板第四类。它和另外三类有一个根本区别，值得写在最前面：**另外三类读库，这一类读
进程内存**。所以它**没有窗口、没有 `days` 参数、重启即清零**，而且**只覆盖这一个进程**
（生产上业务 API 就一个 backend 进程；dev/base 栈另外那个 device-connection 进程是
另一份）。

要历史的话，原料其实已经在写了（`main.py` 每个请求一行带毫秒的日志，落 journald），
那是一条只读脚本的事，不用建表 —— 但「过去一周」和「现在快不快」是两个问题，这一版
只回答后者。相关的取舍写在话题文档里。

数据来源是 `core/metrics.py` 里那套一直定义着、却**没有任何调用点**的指标；`main.py`
的中间件在 2026-09-22 把它们接上了。标签只取 `method / 路由模板 / status`，理由见
`main.py` 的 `_route_label`：拿带 UUID 的原始路径当标签，等于给每条反馈建一条时间序列。
"""

from app.core.loop_lag import lag_status

#: 看板上列几条。按 p95 从大到小排，取前这么多个 —— 这一类的读法是「哪一条最慢」，
#: 不是「一共有多少条路由」。
ROUTES_SHOWN = 12


def performance_snapshot(
    *, routes_registered: list[tuple[str, str]] | int | None = None
) -> dict:
    """这一刻的接口耗时 + 网络吞吐。

    **`None` 不是 0**：一条样本都没有的路由，分位数是 `None`，页面画成「—」。
    画成 0 的话，一条从没人访问过的路由会以「0ms」排在最前面，读起来像它快得惊人。

    `routes_registered` 现在是**路由表本身**（`(method, path)` 列表），不是一个数：
    看板要列出**每一条**注册过的端点，没有样本的那些也占一行（`count: 0`、分位数
    `None`）。老调用点仍可传 `int`，那一种只报 `routes_registered` 的计数、不生成
    空行 —— 「有样本的路」和「画出来几条」也因此不再需要两个字段。
    """
    from app.core import net_io
    from app.core import route_metrics as rm

    observed = {(r["method"].upper(), r["route"]): r for r in rm.snapshot()}

    rows: list[dict] = []
    if isinstance(routes_registered, list):
        for method, route in routes_registered:
            key = (method.upper(), route)
            hit = observed.get(key)
            if hit is not None:
                rows.append(hit)
            else:
                # 未命中的端点也占一行。**分位数是 None 不是 0** —— 0 读起来是
                # 「快得惊人」，而事实是「没有数据」。
                rows.append(
                    {
                        "method": method.upper(),
                        "route": route,
                        "count": 0,
                        "error_count": 0,
                        "status": {"2xx": 0, "3xx": 0, "4xx": 0, "5xx": 0},
                        "p50": None,
                        "p95": None,
                        "p99": None,
                        "spark": [],
                    }
                )
        registered = len(routes_registered)
    else:
        rows.extend(observed.values())
        registered = (
            routes_registered if isinstance(routes_registered, int) else len(observed)
        )

    rows.sort(
        key=lambda r: (
            r["p95"] is not None,
            r["p95"] if r["p95"] is not None else -1,
        ),
        reverse=True,
    )
    # 线上护栏：路由表真长到几千条时截断并**说出来**（静默截断读起来像「就这些」）。
    omitted = 0
    if len(rows) > 2000:
        omitted = len(rows) - 2000
        rows = rows[:2000]


    snap = {
        "routes": rows,
        "routes_registered": registered,
        "routes_with_samples": sum(1 for r in rows if r["count"] > 0),
        "routes_omitted": omitted,
        "dropped_series": rm.dropped_series(),
        "active_requests": None,  # filled by the caller (needs the live counter)
        "uptime_seconds": None,
        "loop_lag": lag_status(),
        # 两面都要：上行是**这台机器的网卡**（含计量代理到 LLM 的出向流量），
        # api 是**本进程**的 HTTP 载荷。口径写在 `core/net_io.py` 的模块 docstring。
        "network": {
            "uplink": net_io.net_io_status(),
            "api": net_io.api_io_status(),
        },
    }
    return snap


def performance_snapshot_legacy() -> dict:
    """Back-compat shim for the pre-split shape (status was part of the key)."""
    return performance_snapshot()
