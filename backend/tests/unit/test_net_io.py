"""Network throughput: delta/rate math, wrap safety, honest unreadables."""

import math
from pathlib import Path

import pytest

from app.core import net_io


def _fake_nic(root: Path, iface: str, rx: int, tx: int) -> None:
    d = root / iface / "statistics"
    d.mkdir(parents=True, exist_ok=True)
    (d / "rx_bytes").write_text(str(rx))
    (d / "tx_bytes").write_text(str(tx))


def setup_function() -> None:
    net_io.reset()


@pytest.mark.parametrize("started", [100.0, math.nextafter(128.0, 0.0)])
def test_rate_math_from_a_known_delta(monkeypatch, tmp_path, started):
    _fake_nic(tmp_path, "eth0", 1_000_000, 2_000_000)
    monkeypatch.setenv(net_io.NET_ROOT_ENV, str(tmp_path))
    monkeypatch.setenv(net_io.NET_IFACE_ENV, "eth0")

    prev = (started, 1_000_000, 2_000_000)
    _fake_nic(tmp_path, "eth0", 1_100_000, 2_250_000)
    # Crossing 128 seconds changes float spacing, so subtracting these clock
    # readings can produce a duration slightly above two seconds.
    now = prev[0] + 2.0
    rx_bps, tx_bps = net_io._rates(prev, now, 1_100_000, 2_250_000)
    assert (rx_bps, tx_bps) == pytest.approx((50_000.0, 125_000.0), rel=1e-12, abs=0)


def test_counter_wrap_is_zero_not_a_spike():
    prev = (100.0, 5_000, 5_000)
    rx_bps, tx_bps = net_io._rates(prev, 105.0, 50, 50)  # counters went backwards
    assert (rx_bps, tx_bps) == (0.0, 0.0)


def test_missing_root_is_none_never_zero(monkeypatch, tmp_path):
    monkeypatch.setenv(net_io.NET_ROOT_ENV, str(tmp_path / "nope"))
    monkeypatch.setattr(net_io, "FALLBACK_NET_ROOT", str(tmp_path / "also-nope"))
    status = net_io.net_io_status()
    assert status["available"] is False
    assert status["rx_bps"] is None and status["tx_bps"] is None
    assert status["rx_bps"] != 0  # the whole point
    assert status["note_key"] == "perf.netMissing"


def test_container_scope_when_only_sys_class_net(monkeypatch, tmp_path):
    monkeypatch.setenv(net_io.NET_ROOT_ENV, str(tmp_path / "nope"))
    fallback = tmp_path / "sys"
    _fake_nic(fallback, "eth0", 10, 20)
    monkeypatch.setattr(net_io, "FALLBACK_NET_ROOT", str(fallback))
    monkeypatch.setenv(net_io.NET_IFACE_ENV, "eth0")
    status = net_io.net_io_status()
    assert status["available"] is True
    assert status["scope"] == "container"
    assert status["note_key"] == "perf.netContainer"


def test_default_route_wins_over_lo_and_veth(monkeypatch, tmp_path):
    _fake_nic(tmp_path, "lo", 1, 1)
    _fake_nic(tmp_path, "veth0abc", 2, 2)
    _fake_nic(tmp_path, "eth0", 3, 3)
    monkeypatch.setenv(net_io.NET_ROOT_ENV, str(tmp_path))
    monkeypatch.delenv(net_io.NET_IFACE_ENV, raising=False)
    # /proc/net/route is real on Linux; if its default route's iface is not our
    # fake eth0, the fallback (first non-lo) must not pick `lo`.
    picked = net_io.default_iface(tmp_path)
    assert picked != "lo"
    assert picked in {"eth0", "veth0abc"}


def test_pinned_iface_override(monkeypatch, tmp_path):
    _fake_nic(tmp_path, "eth0", 3, 3)
    _fake_nic(tmp_path, "eth1", 4, 4)
    monkeypatch.setenv(net_io.NET_ROOT_ENV, str(tmp_path))
    monkeypatch.setenv(net_io.NET_IFACE_ENV, "eth1")
    assert net_io.default_iface(tmp_path) == "eth1"


def test_ring_is_bounded():
    for i in range(net_io.RING_MAX * 3):
        net_io._uplink.append((float(i), 1.0, 2.0))
    assert len(net_io._uplink) == net_io.RING_MAX


@pytest.mark.parametrize("started", [100.0, math.nextafter(128.0, 0.0)])
def test_api_io_reflects_http_byte_deltas(started):
    net_io.note_http_bytes(request_body=100, response_body=250)
    prev = (started, 100, 250)  # the baseline after the first batch
    net_io.note_http_bytes(request_body=50, response_body=100)
    now = prev[0] + 1.0
    rx_bps, tx_bps = net_io._rates(prev, now, 150, 350)
    assert (rx_bps, tx_bps) == pytest.approx((50.0, 100.0), rel=1e-12, abs=0)
    status = net_io.api_io_status()
    assert status["note_key"] == "perf.apiIO"
    assert status["scope"] == "process"
