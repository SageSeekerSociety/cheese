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
from app.core.metrics import registry

#: 看板上列几条。按 p95 从大到小排，取前这么多个 —— 这一类的读法是「哪一条最慢」，
#: 不是「一共有多少条路由」。
ROUTES_SHOWN = 12


def performance_snapshot(*, routes_registered: int | None = None) -> dict:
    """这一刻的接口耗时：按路由的 p50 / p95 / p99，加两个全局数。

    **`None` 不是 0**：一条样本都没有的路由，分位数是 `None`，页面画成「—」。
    画成 0 的话，一条从没人访问过的路由会以「0ms」排在最前面，读起来像它快得惊人。
    """
    rows: list[dict] = []
    for hist in registry.histograms_named("http_request_duration_seconds"):
        if hist.count == 0:
            continue
        labels = hist.labels
        rows.append(
            {
                "method": labels.get("method", ""),
                "route": labels.get("route", ""),
                "status": labels.get("status", ""),
                "count": hist.count,
                # 秒 → 毫秒，在服务端换一次：客户端拿到的单位只有一种。
                **{
                    q: (None if v is None else round(v * 1000, 1))
                    for q, v in (
                        ("p50", hist.quantile(0.5)),
                        ("p95", hist.quantile(0.95)),
                        ("p99", hist.quantile(0.99)),
                    )
                },
            }
        )

    rows.sort(key=lambda r: r["p95"] if r["p95"] is not None else -1, reverse=True)
    shown = rows[:ROUTES_SHOWN]
    return {
        # 「被看过多少条路」和「画出来几条」是两个数：截断要说出来，否则读者会以为
        # 这就是全部（`ROUTES_SHOWN` 之外的慢路由就静默消失了）。
        #
        # **`routes_total` 是「有样本的路」，不是「这个 app 有多少条路由」**：没有
        # 被访问过的路由在这里根本不出现（`hist.count == 0` 的那条被 `continue` 掉
        # 了）。所以它天然是「重启后到现在的累计」，看起来少不代表路由少 ——
        # `routes_registered` 把分母补上，页面上写「有样本 X / 共 Y」，两个数一起
        # 读才答得了「是不是太少了」。
        "routes_total": len(rows),
        "routes_shown": len(shown),
        "routes_registered": routes_registered,
        "routes": shown,
        # 进程内存里的东西，所以这两个数必须写清口径，不然会被当成「平台的」数。
        # **读这个数的那一条请求自己也在里面**：中间件在 `call_next` 外面一进一出，
        # 而这个快照只能从请求里画出来，读到它的时候它正好被算进了那个 +1。不扣掉的
        # 话平台空着的时候这一格也写 1，读起来像「有一条请求一直没处理完」。
        "active_requests": max(0, registry.gauge("http_requests_active").value - 1),
        "uptime_seconds": registry.export()["uptime_seconds"],
        # 事件循环的滞后（`core/loop_lag.py` 一直在测）：接口慢而 p95 不高时，答案
        # 常常在这里 —— 循环被什么东西占住了，谁都得排队。
        "loop_lag": lag_status(),
    }
