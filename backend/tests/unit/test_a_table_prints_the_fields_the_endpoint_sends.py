"""出表脚本打出来的，必须是接口真送出来的那几个字段。

这两个脚本（`backend/scripts/seat_throughput.py`、`.claude/scripts/seat_tps.py`）
此前一个测试都没碰过，于是各自坏在一处都没人知道：表对着 pydantic 模型调 `.get`
当场炸；`COLUMNS` 写的是 `output` / `secs` 这种假名，而载荷送的是 `output_tokens` /
`turn_seconds`，那两列永远打成 `-`，`--self-test` 的样例行用的也是假名，所以自检
照绿。

守的是四条能在写代码之前就说出来的规则：表打的是接口送的字段；表打得出聚合算出来
的数；`--json` 的行是对象不是字符串；载荷里没有的字段要出声，不能默默打成横杠。
走文档里那条命令的入口 `main()`，所以老代码上红的是断言，不是 import。
"""

import asyncio
import importlib.util
import json
import sys
from io import StringIO
from pathlib import Path

import pytest

import scripts.seat_throughput as db_script
from app.domain.platform_stats import throughput
from app.domain.platform_stats.throughput import SeatRateOut

REPO = Path(__file__).resolve().parents[3]
CLI = REPO / ".claude" / "scripts" / "seat_tps.py"


def _cli():
    spec = importlib.util.spec_from_file_location("seat_tps_under_test", CLI)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _rate(**over) -> SeatRateOut:
    base = {
        "seat": "cheese",
        "model": "claude-sonnet-5",
        "supply": "subscription",
        "turns": 12,
        "output_tokens": 48000,
        "calls": 14,
        "turn_seconds": 612.5,
        "p50_tps": 78.4,
        "p90_tps": 120.0,
        "window_tps": 0.5556,
    }
    base.update(over)
    return SeatRateOut(**base)


class _NoSession:
    """`main()` 只是 `async with async_session_factory() as session`。"""

    async def __aenter__(self):
        return object()

    async def __aexit__(self, *exc):
        return False


def _printed(monkeypatch, rows: list[SeatRateOut], as_json: bool) -> str:
    """跑文档里那条命令，收它打出来的东西。`seats()` 交回的是 pydantic 模型。"""
    payload = {
        "window": "24h",
        "since": "2026-10-13T00:00:00+00:00",
        "now": "2026-10-14T00:00:00+00:00",
        "window_seconds": 86400.0,
        "rows": rows,
        "seats": rows,
    }

    async def seats(session, window="24h"):
        return payload

    monkeypatch.setattr(db_script, "async_session_factory", lambda: _NoSession())
    monkeypatch.setattr(throughput, "seats", seats)
    out = StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    try:
        asyncio.run(db_script.main("24h", as_json))
    except AttributeError as exc:
        raise AssertionError(
            f"the print path cannot render what seats() returns: {exc}"
        ) from exc
    return out.getvalue()


def test_the_columns_are_the_fields_the_endpoint_actually_sends():
    """列名写成载荷里没有的名字，表就把那一列打成 `-`，谁也看不出少了数。"""
    fields = set(SeatRateOut.model_fields)
    assert {name for name, _ in db_script.COLUMNS} == fields
    assert {name for name, _ in _cli().COLUMNS} == fields


def test_the_table_prints_the_numbers_the_aggregation_computed(monkeypatch):
    """`seats()` 交的是 pydantic 模型，不是 dict —— 打印路径必须吃得下它。"""
    out = _printed(monkeypatch, [_rate()], as_json=False)
    assert "48000" in out and "612.5" in out, out


def test_json_rows_are_objects_with_the_fields_the_endpoint_sends(monkeypatch):
    """`--json` 的行是对象。打成每个模型的字符串长相，谁都取不到数。"""
    parsed = json.loads(_printed(monkeypatch, [_rate()], as_json=True))
    row = parsed["rows"][0]
    assert isinstance(row, dict), row
    assert row["output_tokens"] == 48000 and row["turn_seconds"] == 612.5


def test_a_column_the_payload_does_not_send_is_reported_not_blanked():
    """少了字段要出声。默默打成 `-` 骗过一次了，那次 `--self-test` 还是绿的。"""
    with pytest.raises(KeyError, match="output_tokens"):
        _cli().table([{"seat": "cheese"}])


def test_a_rate_that_was_never_computed_still_renders_as_a_dash():
    """`None` 是「这组里没有一轮秒数大于 0」，不是缺字段——照旧写成 `-`。"""
    row = {
        "seat": "cheese",
        "model": "mimo-v2.6-pro",
        "supply": "gateway",
        "turns": 3,
        "output_tokens": 900,
        "calls": 3,
        "turn_seconds": 0.0,
        "p50_tps": None,
        "p90_tps": None,
        "window_tps": 0.0104,
    }
    out = _cli().table([row])
    assert "   -  " in out, out
