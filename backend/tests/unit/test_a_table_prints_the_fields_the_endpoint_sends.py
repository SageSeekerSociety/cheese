"""出表脚本认的字段名，必须就是 `SeatRateOut` 真的送出来的那几个。

这两个脚本（`backend/scripts/seat_throughput.py`、`.claude/scripts/seat_tps.py`）
此前一个测试都没碰过，于是各自坏在一处都没人知道：表对着 pydantic 模型调 `.get`
当场炸；`COLUMNS` 写的是 `output` / `secs` 这种假名，而载荷送的是 `output_tokens` /
`turn_seconds`，那两列永远打成 `-`，`--self-test` 的样例行用的也是假名，所以自检
照绿。这里把两件事钉住：列名对得上模型字段，以及表拿到真模型能出得了数。
"""

import importlib.util
import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from app.domain.platform_stats.throughput import SeatRateOut
from scripts.seat_throughput import COLUMNS as DB_COLUMNS
from scripts.seat_throughput import _missing, _plain, _table

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


def test_the_columns_are_the_fields_the_endpoint_actually_sends():
    """列名写成载荷里没有的名字，表就把那一列打成 `-`，谁也看不出少了数。"""
    fields = set(SeatRateOut.model_fields)
    assert {name for name, _ in DB_COLUMNS} == fields
    assert {name for name, _ in _cli().COLUMNS} == fields


def test_the_table_reads_the_models_the_aggregation_returns():
    """`seats()` 交的是 pydantic 模型，不是 dict —— 表和 `--json` 都得先摊平。"""
    assert isinstance(_rate(), BaseModel)
    data = _plain(
        {
            "window": "24h",
            "since": "",
            "now": "",
            "window_seconds": 1.0,
            "rows": [_rate()],
            "seats": [_rate()],
        }
    )
    assert isinstance(data["rows"][0], dict)

    out = _table(data["rows"])
    assert "48000" in out and "612.5" in out, out

    # `--json` 走同一条路，出的必须是对象，不是每个模型的字符串长相。
    payload = json.loads(json.dumps(data["rows"]))
    assert payload[0]["output_tokens"] == 48000


def test_a_column_the_payload_does_not_send_is_reported_not_blanked():
    """少了字段要出声。默默打成 `-` 骗过一次了，那次 `--self-test` 还是绿的。"""
    cli = _cli()
    with pytest.raises(KeyError, match="output_tokens"):
        cli.table([{"seat": "cheese"}])
    assert _missing([{"seat": "cheese"}]) == [
        name for name, _ in DB_COLUMNS if name != "seat"
    ]


def test_a_rate_that_was_never_computed_still_renders_as_a_dash():
    """`None` 是「这组里没有一轮秒数大于 0」，不是缺字段——照旧写成 `-`。"""
    out = _table(_plain({"rows": [_rate(p50_tps=None, p90_tps=None)]})["rows"])
    assert "   -  " in out, out
