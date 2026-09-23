from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Lock
from typing import Any


@dataclass
class Counter:
    name: str
    labels: dict[str, str] = field(default_factory=dict)
    value: int = 0

    def inc(self, amount: int = 1) -> None:
        self.value += amount


@dataclass
class Gauge:
    name: str
    labels: dict[str, str] = field(default_factory=dict)
    value: float = 0.0

    # allow-builtin-shadow: `set` is the metrics API's own name (prometheus Gauge.set)
    def set(self, value: float) -> None:
        self.value = value

    def inc(self, amount: float = 1.0) -> None:
        self.value += amount

    def dec(self, amount: float = 1.0) -> None:
        self.value -= amount


@dataclass
class Histogram:
    name: str
    labels: dict[str, str] = field(default_factory=dict)
    buckets: list[float] = field(
        default_factory=lambda: [
            0.005,
            0.01,
            0.025,
            0.05,
            0.1,
            0.25,
            0.5,
            1.0,
            2.5,
            5.0,
            10.0,
        ]
    )
    _counts: list[int] = field(default_factory=list)
    _sum: float = 0.0
    _count: int = 0

    def __post_init__(self) -> None:
        self._counts = [0] * (len(self.buckets) + 1)

    def observe(self, value: float) -> None:
        self._sum += value
        self._count += 1
        # 累加式（Prometheus 的形状）：`_counts[i]` 是「≤ buckets[i] 的样本数」，
        # 所以只要撞到第一个够大的桶就停 —— 后面的桶天然包含它。`_counts[-1]` 是
        # +Inf 那一格。
        for i, bucket in enumerate(self.buckets):
            if value <= bucket:
                self._counts[i] += 1
                return
        self._counts[-1] += 1

    @property
    def count(self) -> int:
        """观测到的样本数。和 `sum` 一样是读侧要的公开读法 —— 别的域不该去碰
        `_count`（那是这个类的实现细节，而跨模块读下划线属性是下次改这个类时的地雷）。"""
        return self._count

    def quantile(self, q: float) -> float | None:
        """q 分位（0..1），桶内线性插值。没有样本时返回 None，不返回 0。

        **不返回 0** 是有意的：0 秒是一个读数（「真的很快」），None 是「这一格没有
        数据」。看板上把两者画成同一个数，就等于用一条平线宣布平台健康，而那可能
        只是这一刻还没有人访问过。

        **`_counts` 是逐桶的，不是累加的。** `observe` 每个样本只往「它落进去的那
        一个桶」加一就返回（看它的循环），所以这里必须自己把前面的桶累起来。第一版
        直接拿逐桶的计数去跟 `q * count` 比，落进两个以上桶的分布永远比不过 ——
        于是循环走完、返回最后一个桶的上界：**每条忙一点的接口 p95 都报 10 秒**，
        而且它长得像一个读数，不像一个故障。

        插值是桶级的近似 —— 桶宽在 5ms..10s 之间，所以分位落在最后一个有限桶里时
        报的是那个桶的上界，不去追 +Inf。
        """
        if self._count == 0:
            return None
        target = q * self._count
        seen = 0
        prev_bound = 0.0
        for bound, count in zip(self.buckets, self._counts, strict=False):
            if seen + count >= target:
                # 这个桶里没有样本（前一个桶刚好够）：分位就落在它的上界上。
                if count == 0:
                    return bound
                frac = (target - seen) / count
                return prev_bound + (bound - prev_bound) * frac
            seen += count
            prev_bound = bound
        # 全落在 +Inf 那一格：报最后一个**有限**上界。
        return self.buckets[-1] if self.buckets else None


class MetricsRegistry:
    def __init__(self) -> None:
        self._lock = Lock()
        self._counters: dict[str, Counter] = {}
        self._gauges: dict[str, Gauge] = {}
        self._histograms: dict[str, Histogram] = {}
        self._start_time = datetime.now(UTC)

    def counter(self, name: str, labels: dict[str, str] | None = None) -> Counter:
        key = self._key(name, labels)
        with self._lock:
            if key not in self._counters:
                self._counters[key] = Counter(name=name, labels=labels or {})
            return self._counters[key]

    def gauge(self, name: str, labels: dict[str, str] | None = None) -> Gauge:
        key = self._key(name, labels)
        with self._lock:
            if key not in self._gauges:
                self._gauges[key] = Gauge(name=name, labels=labels or {})
            return self._gauges[key]

    def histogram(
        self,
        name: str,
        labels: dict[str, str] | None = None,
        buckets: list[float] | None = None,
    ) -> Histogram:
        key = self._key(name, labels)
        with self._lock:
            if key not in self._histograms:
                kwargs: dict = {"name": name, "labels": labels or {}}
                if buckets is not None:
                    kwargs["buckets"] = buckets
                self._histograms[key] = Histogram(**kwargs)
            return self._histograms[key]

    def histograms_named(self, name: str) -> list[Histogram]:
        """这个名下**所有**标签组合的直方图（一个标签组合一条）。"""
        with self._lock:
            return [h for h in self._histograms.values() if h.name == name]

    def _key(self, name: str, labels: dict[str, str] | None) -> str:
        if not labels:
            return name
        label_str = ",".join(f"{k}={v}" for k, v in sorted(labels.items()))
        return f"{name}{{{label_str}}}"

    def export(self) -> dict[str, Any]:
        with self._lock:
            return {
                "uptime_seconds": (
                    datetime.now(UTC) - self._start_time
                ).total_seconds(),
                "counters": {
                    k: {"name": v.name, "labels": v.labels, "value": v.value}
                    for k, v in self._counters.items()
                },
                "gauges": {
                    k: {"name": v.name, "labels": v.labels, "value": v.value}
                    for k, v in self._gauges.items()
                },
                "histograms": {
                    k: {
                        "name": v.name,
                        "labels": v.labels,
                        "count": v._count,
                        "sum": v._sum,
                        "buckets": dict(
                            zip(
                                [str(b) for b in v.buckets] + ["+Inf"],
                                v._counts,
                                strict=False,
                            )
                        ),
                    }
                    for k, v in self._histograms.items()
                },
            }


registry = MetricsRegistry()

http_requests_total = registry.counter("http_requests_total")
http_request_duration_seconds = registry.histogram(
    "http_request_duration_seconds",
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)
active_requests = registry.gauge("http_requests_active")

task_created_total = registry.counter("cheese_task_created_total")
notification_sent_total = registry.counter("cheese_notification_sent_total")
llm_calls_total = registry.counter("cheese_llm_calls_total")
llm_tokens_total = registry.counter("cheese_llm_tokens_total")
