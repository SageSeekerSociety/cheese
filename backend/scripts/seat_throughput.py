"""每个席位生成得有多快，直接对 `DATABASE_URL` 那个库出数。

    python -m scripts.seat_throughput [--window 24h|7d|30d] [--json]

给在后端容器里（`docker exec -i cheese-backend-1 python -m scripts.seat_throughput`）
或者手上就有库连接的人用：不签 token、不打 HTTP，走的是和
`GET /throughput/seats` 同一段聚合（`app.domain.platform_stats.throughput`），所以
两边的数永远一致。会话机器上没有库也够不着平台库（团队记忆《会话容器够不着平台
库》），这条命令要进得了容器才跑得动。

口径在那段聚合的模块 docstring 上，这里不重复。
"""

import argparse
import asyncio
import json
import sys

from app.core.db import async_session_factory
from app.domain.platform_stats import throughput

#: 表头就是 `SeatRateOut` 的字段名，不另起简称 —— 另起过一次，两列打成 `-` 而没人发现。
#: 列宽按 `_fmt` 打得出来的最长样子给：带小数的列是整数位加小数位（秒 3 位、速率
#: 4 位），收窄一位就把右边的列顶歪，测试按每一列的宽度盯着。
COLUMNS = (
    ("seat", 16),
    ("model", 30),
    ("supply", 13),
    ("turns", 8),
    ("output_tokens", 13),
    ("calls", 8),
    ("turn_seconds", 13),
    ("p50_tps", 13),
    ("p90_tps", 13),
    ("window_tps", 13),
)
NUMERIC = {
    "turns",
    "output_tokens",
    "calls",
    "turn_seconds",
    "p50_tps",
    "p90_tps",
    "window_tps",
}


def _plain(data: dict) -> dict:
    """`seats()` 交出来的是 pydantic 模型，表和 `--json` 都要普通 dict。

    这里一并换成 dict，之后两处都按 dict 取值：不换的话表直接
    `AttributeError: 'SeatRateOut' object has no attribute 'get'`，
    `--json` 又会把每一行打成它的字符串长相而不是对象。
    """
    out = dict(data)
    for key in ("rows", "seats"):
        out[key] = [row.model_dump() for row in (out.get(key) or [])]
    return out


def _missing(rows: list[dict]) -> list[str]:
    """表要的字段载荷里没有 —— 说出来，别默默打成 `-`。

    每一行都查：只看第一行的话，第二行起漏了字段照样打成横杠。
    """
    absent = {name for row in rows for name, _ in COLUMNS if name not in row}
    return [name for name, _ in COLUMNS if name in absent]


def _fmt(value, width, numeric):
    """数字右对齐、字左对齐。数是多少就打多少，不用 `:g` —— 它把 `1234567` 打成
    `1.23457e+06`，丢掉一位；带小数的 `2591999.757` 也被它收成 `2.592e+06`。
    整值的 float 打成整数（`2592000.0` → `2592000`），其余照原样。"""
    if value is None:
        text = "-"
    elif not numeric:
        text = str(value)
    else:
        number = float(value)
        text = str(int(number)) if number.is_integer() else str(number)
    return text.rjust(width) if numeric else text.ljust(width)


def _table(rows: list[dict]) -> str:
    head = "  ".join(
        name.rjust(width) if name in NUMERIC else name.ljust(width)
        for name, width in COLUMNS
    )
    lines = [head, "-" * len(head)]
    for row in rows:
        lines.append(
            "  ".join(
                _fmt(row.get(name), width, name in NUMERIC) for name, width in COLUMNS
            )
        )
    return "\n".join(lines)


async def main(window: str, as_json: bool) -> int:
    async with async_session_factory() as session:
        data = _plain(await throughput.seats(session, window=window))
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    code = 0
    for title, which in (("seats", "seats"), ("by seat x model x supply", "rows")):
        rows = data.get(which) or []
        print(
            f"\n[{title}] window={data['window']} since={data['since']} "
            f"({len(rows)} rows)"
        )
        missing = _missing(rows)
        if missing:
            print(
                f"(cannot print [{title}]: payload has no {missing})", file=sys.stderr
            )
            code = 1
            continue
        print(_table(rows) if rows else "(none)")
    return code


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Per-seat generation rate, read straight from the database."
    )
    parser.add_argument("--window", default="24h", choices=["24h", "7d", "30d"])
    parser.add_argument("--json", action="store_true", help="raw payload, not a table")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.window, args.json)))
