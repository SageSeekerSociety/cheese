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
COLUMNS = (
    ("seat", 16),
    ("model", 30),
    ("supply", 13),
    ("turns", 6),
    ("output_tokens", 13),
    ("calls", 6),
    ("turn_seconds", 12),
    ("p50_tps", 9),
    ("p90_tps", 9),
    ("window_tps", 10),
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
    """ "表要的字段载荷里没有" —— 说出来，别默默打成 `-`。"""
    return [name for name, _ in COLUMNS if rows and name not in rows[0]]


def _fmt(value, width, numeric):
    text = "-" if value is None else (f"{value:g}" if numeric else str(value))
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
    for title, which in (("seats", "seats"), ("by seat x model x supply", "rows")):
        rows = data.get(which) or []
        print(
            f"\n[{title}] window={data['window']} since={data['since']} "
            f"({len(rows)} rows)"
        )
        missing = _missing(rows)
        if missing:
            print(f"(cannot print: payload has no {missing})", file=sys.stderr)
            return 1
        print(_table(rows) if rows else "(none)")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Per-seat generation rate, read straight from the database."
    )
    parser.add_argument("--window", default="24h", choices=["24h", "7d", "30d"])
    parser.add_argument("--json", action="store_true", help="raw payload, not a table")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.window, args.json)))
