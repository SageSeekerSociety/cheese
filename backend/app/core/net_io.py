"""Platform network throughput — two planes, both labelled, neither faking 0.

The board's performance section answers「平台现在网络好不好」, and that question
has two honest answers that are not the same number:

- **uplink** is the box's default-route NIC byte counters. ``rx_bytes`` is into
  the box, ``tx_bytes`` is out of it. This is what an admin means by "the
  platform's network", and it is deliberately *not* "our users' traffic": the
  metering proxy's LLM egress, forge git, R2 backups and tailscale all ride the
  same NIC. Say so wherever the number is shown.
- **api** is this backend process's HTTP request-body bytes in and
  response-body bytes out. That one is ours.

History is process memory (same caveat as ``core/loop_lag.py``): it dies on
restart and covers this box only — production's device-connection process is a
separate box and a separate number.

The one rule that matters more than the arithmetic: **an unreadable counter is
None, never 0**. 0 says "the network is idle"; None says "we cannot see it".
A dashboard that prints 0 for the second has told the reader the opposite of
the truth.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections import deque
from pathlib import Path

from app.core.obs import get_logger

logger = get_logger("cheesex.net")

#: Host NIC stats, mounted read-only in the compose files as /host-sys-class/net.
#: Not a directory (Docker Desktop, CI) -> fall back to the container's own
#: /sys/class/net and mark scope "container".
NET_ROOT_ENV = "NET_STATS_ROOT"
DEFAULT_NET_ROOT = "/host-sys-class/net"
FALLBACK_NET_ROOT = "/sys/class/net"
#: Pin one interface instead of following the default route.
NET_IFACE_ENV = "NET_STATS_IFACE"

SAMPLE_INTERVAL_S = 5.0
#: ~8 minutes of history at a 5s interval.
RING_MAX = 96
#: Spark points handed to the wire. 96 -> 24 keeps the payload small.
SPARK_OUT = 24

_note_keys = {
    "host": "perf.netHost",
    "container": "perf.netContainer",
    "missing": "perf.netMissing",
    "process": "perf.apiIO",
}

# (monotonic_ts, rx_bps, tx_bps)
_uplink: deque[tuple[float, float, float]] = deque(maxlen=RING_MAX)
_api: deque[tuple[float, float, float]] = deque(maxlen=RING_MAX)
_http_req_bytes = 0
_http_res_bytes = 0
_api_prev: tuple[float, int, int] | None = None


def _net_root() -> tuple[Path | None, str]:
    """(root, scope). ``root`` None means we can see no counter at all."""
    preferred = Path(os.environ.get(NET_ROOT_ENV) or DEFAULT_NET_ROOT)
    if preferred.is_dir():
        return preferred, "host"
    fallback = Path(FALLBACK_NET_ROOT)
    if fallback.is_dir():
        return fallback, "container"
    return None, "missing"


def default_iface(root: Path) -> str | None:
    """The interface the box routes its default through.

    Summing every interface under ``/sys/class/net`` is the tempting mistake and
    it double-counts: veth and bridge members carry the same bytes again, and
    tailscale0 is a different plane entirely. One interface, the default route's.

    ``NET_STATS_IFACE`` overrides the whole lookup — an operator who knows the
    topology better than ``/proc/net/route`` does gets the last word.
    """
    pinned = os.environ.get(NET_IFACE_ENV)
    if pinned:
        return pinned if (root / pinned).is_dir() else None
    try:
        for line in Path("/proc/net/route").read_text().splitlines()[1:]:
            cols = line.split()
            if len(cols) >= 2 and cols[1] == "00000000":
                iface = cols[0]
                if (root / iface).is_dir():
                    return iface
    except OSError:  # noqa: PERF203 — a missing /proc is the container case
        pass
    for child in sorted(root.iterdir()):
        if child.is_dir() and child.name != "lo":
            return child.name
    return None


def read_counters(root: Path, iface: str) -> tuple[int, int] | None:
    """(rx_bytes, tx_bytes) for one interface, or None if unreadable."""
    try:
        rx = int((root / iface / "statistics" / "rx_bytes").read_text().strip())
        tx = int((root / iface / "statistics" / "tx_bytes").read_text().strip())
        return rx, tx
    except (OSError, ValueError):
        return None


def _rates(
    prev: tuple[float, int, int] | None, now: float, rx: int, tx: int
) -> tuple[float, float]:
    """Bytes/sec since ``prev``. A counter that went *backwards* is a wrap or a
    reset, and the honest rate for that tick is 0 — a spike of `old - new` is a
    fabrication the graph would draw as a 10 Gb/s event."""
    if prev is None:
        return 0.0, 0.0
    t_prev, rx_prev, tx_prev = prev
    dt = now - t_prev
    if dt <= 0 or rx < rx_prev or tx < tx_prev:
        return 0.0, 0.0
    return (rx - rx_prev) / dt, (tx - tx_prev) / dt


def note_http_bytes(*, request_body: int = 0, response_body: int = 0) -> None:
    """Accumulate this process's HTTP payload bytes (see ``api_io_status``)."""
    global _http_req_bytes, _http_res_bytes
    if request_body:
        _http_req_bytes += int(request_body)
    if response_body:
        _http_res_bytes += int(response_body)


def _downsample(
    ring: deque[tuple[float, float, float]],
) -> list[dict[str, float | None]]:
    out: list[dict[str, float | None]] = []
    step = max(1, RING_MAX // SPARK_OUT)
    items = list(ring)
    for i in range(0, len(items), step):
        chunk = items[i : i + step]
        out.append(
            {
                "rx_bps": round(sum(c[1] for c in chunk) / len(chunk), 1),
                "tx_bps": round(sum(c[2] for c in chunk) / len(chunk), 1),
            }
        )
    return out


def net_io_status() -> dict:
    """The box's uplink, honestly scoped. See the module docstring."""
    root, scope = _net_root()
    iface = default_iface(root) if root else None
    if root is None or iface is None:
        return {
            "available": False,
            "iface": None,
            "scope": None,
            "rx_bps": None,
            "tx_bps": None,
            "samples": [],
            "note_key": _note_keys["missing"],
        }
    latest = _uplink[-1] if len(_uplink) >= 2 else None
    return {
        "available": True,
        "iface": iface,
        "scope": scope,
        "rx_bps": round(latest[1], 1) if latest else None,
        "tx_bps": round(latest[2], 1) if latest else None,
        "samples": _downsample(_uplink),
        "note_key": _note_keys[scope],
    }


def api_io_status() -> dict:
    """This process's HTTP bytes in/out. Same shape as ``net_io_status``."""
    latest = _api[-1] if len(_api) >= 2 else None
    return {
        "available": True,
        "iface": None,
        "scope": "process",
        "rx_bps": round(latest[1], 1) if latest else None,
        "tx_bps": round(latest[2], 1) if latest else None,
        "samples": _downsample(_api),
        "note_key": _note_keys["process"],
    }


def _tick_uplink(prev: tuple[float, int, int] | None) -> tuple[float, int, int] | None:
    root, _scope = _net_root()
    if root is None:
        return prev
    iface = default_iface(root)
    if iface is None:
        return prev
    cur = read_counters(root, iface)
    if cur is None:
        return prev
    now = time.monotonic()
    rx, tx = _rates(prev, now, cur[0], cur[1])
    _uplink.append((now, rx, tx))
    return (now, cur[0], cur[1])


def _tick_api(prev: tuple[float, int, int] | None) -> tuple[float, int, int] | None:
    global _api_prev
    now = time.monotonic()
    rx_bps, tx_bps = (
        _rates(prev, now, _http_req_bytes, _http_res_bytes) if prev else (0.0, 0.0)
    )
    if prev is None:
        return (now, _http_req_bytes, _http_res_bytes)
    _api.append((now, rx_bps, tx_bps))
    return (now, _http_req_bytes, _http_res_bytes)


async def watch_net_io(
    *, interval_s: float = SAMPLE_INTERVAL_S, iterations: int | None = None
) -> None:
    """Sample the uplink forever (or ``iterations`` times in a test).

    The first tick only primes the baseline — a rate needs two points.
    """
    prev: tuple[float, int, int] | None = None
    n = 0
    while iterations is None or n < iterations:
        prev = _tick_uplink(prev)
        n += 1
        if iterations is not None and n >= iterations:
            return
        await asyncio.sleep(interval_s)


async def watch_api_io(
    *, interval_s: float = SAMPLE_INTERVAL_S, iterations: int | None = None
) -> None:
    """Sibling of ``watch_net_io`` for the HTTP byte counters."""
    prev: tuple[float, int, int] | None = None
    n = 0
    while iterations is None or n < iterations:
        prev = _tick_api(prev)
        n += 1
        if iterations is not None and n >= iterations:
            return
        await asyncio.sleep(interval_s)


def reset() -> None:
    """Tests only."""
    global _http_req_bytes, _http_res_bytes, _api_prev
    _uplink.clear()
    _api.clear()
    _http_req_bytes = 0
    _http_res_bytes = 0
    _api_prev = None
