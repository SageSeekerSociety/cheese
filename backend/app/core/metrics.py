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
        for i, bucket in enumerate(self.buckets):
            if value <= bucket:
                self._counts[i] += 1
                return
        self._counts[-1] += 1


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
