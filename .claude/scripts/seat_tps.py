#!/usr/bin/env python3
"""每个席位生成得有多快 —— 一条命令出数。

  OKCHEESE_PASS=<password> python3 .claude/scripts/seat_tps.py --topic <房间 uuid> \
      [--window 24h|7d|30d] [--seats] [--json]
  python3 .claude/scripts/seat_tps.py --self-test      (offline: no token, no network)

它以开发号登录，调 `GET /throughput/seats`，把每个席位 × 模型 × 供给一行的产出打
成一张表。口径写在 `backend/app/domain/platform_stats/throughput.py` 的模块
docstring 上：**窗口吞吐**（`output_tokens ÷ 窗口秒数`）是唯一的速率。按轮速率
（一轮的 token ÷ 这一轮的秒数）2026-10-15 拆掉了 —— 那个配对在平台上不成立（网关
那半边分子是整把 key 的增量，见 `docs/evidence/seat-tps-2026-10-15.md`）。「慢」和
「闲着」配着 `turns` / `calls` 读：那两个正常而吞吐低，是这一组在跑但慢了。

`--seats` 只打席位汇总那一段，问「谁快谁慢」时用它；`--json` 打原始 payload，给要
自己再算一遍的调用方。`--topic` 是调用方自己所在的房间 —— 接口的门就在这个参数上，
只在平台自己的项目里读得到。

凭据只从环境变量来，不进 argv（`ps` 看得见）也不进文件：`OKCHEESE_PASS` 必填，
`OKCHEESE_USER` 默认开发号。密码不写进仓库、PR、提交、聊天或文档。

退出码：0 打出了表 · 2 命令行或环境不对 · 3 平台拒绝了这次调用。
"""
import json
import os
import sys
import urllib.error
import urllib.request

API = os.environ.get("OKCHEESE_API", "https://okcheese.com/api")
USER = os.environ.get("OKCHEESE_USER", "wangchangxin")
WINDOWS = ("24h", "7d", "30d")

#: 表头就是接口吐的字段名（`SeatRateOut` 的字段），不另起简称。另起过一次，
#: `output` / `secs` 这两个假名对不上 `output_tokens` / `turn_seconds`，那两列
#: 就永远打成 `-`，而自检样例用的也是假名，所以自检照过、没人发现。
#: 列宽按 `fmt` 打得出来的最长样子给：带小数的列是整数位加小数位（秒 3 位、速率
#: 4 位），收窄一位就把右边的列顶歪，测试按每一列的宽度盯着。
COLUMNS = (
    ("seat", 16),
    ("model", 30),
    ("supply", 13),
    ("turns", 8),
    ("output_tokens", 13),
    ("calls", 8),
    ("turn_seconds", 13),
    ("window_tps", 13),
)


def call(method, path, token=None, body=None, timeout=60):
    """One platform call. Raises OSError/HTTPError; returns the parsed JSON body."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", "replace")
    return json.loads(raw) if raw.strip() else {}


def unwrap(x):
    """The payload of a platform response: `{"data": …}`, however many times nested."""
    while isinstance(x, dict) and "data" in x:
        x = x["data"]
    return x


def login(user, password):
    return unwrap(call("POST", "/users/auth/login",
                       body={"username": user, "password": password}))["accessToken"]


def fmt(value, width, numeric=False):
    """右对齐的数字、左对齐的字。None 是没算出来（这组里没有一轮秒数大于 0）。

    数是多少就打多少，不用 `:g` —— 它把 `1234567` 打成 `1.23457e+06`，丢掉一位；
    带小数的 `2591999.757` 也被它收成 `2.592e+06`。整值的 float 打成整数
    （`2592000.0` → `2592000`），其余照原样。"""
    if value is None:
        text = "-"
    elif not numeric:
        text = str(value)
    else:
        number = float(value)
        text = str(int(number)) if number.is_integer() else str(number)
    return text.rjust(width) if numeric else text.ljust(width)


TEXT_COLUMNS = ("seat", "model", "supply")


def table(rows):
    head = "  ".join(
        name.ljust(width) if name in TEXT_COLUMNS else name.rjust(width)
        for name, width in COLUMNS
    )
    lines = [head, "-" * len(head)]
    for row in rows:
        # 表要的字段载荷里没有就当场炸。默默打成 `-` 骗过一次了：`output` / `secs`
        # 对不上 `output_tokens` / `turn_seconds`，那两列一直空着，没人发现。
        missing = [name for name, _ in COLUMNS if name not in row]
        if missing:
            raise KeyError(
                f"payload row has no {missing}; it sends {sorted(row)}. "
                "COLUMNS names fields the endpoint does not send."
            )
        lines.append("  ".join(
            fmt(row.get(name), width, numeric=name not in TEXT_COLUMNS)
            for name, width in COLUMNS
        ))
    return "\n".join(lines)


def self_test():
    """离线核对表的排版、None 的写法，和「字段名对不上要当场炸」。不碰网络不碰凭据。

    样例行的键一律照接口吐的字段名写。上一版写的是 `output` / `secs` 这种假名，
    于是自检验的是一个接口从来不会给的形状，真行里的 `output_tokens` /
    `turn_seconds` 两列空着也照样绿。
    """
    sample = [
        {"seat": "cheese", "model": "claude-sonnet-5", "supply": "subscription",
         "turns": 12, "output_tokens": 48000, "calls": 14, "turn_seconds": 612.5,
         "window_tps": 0.5556},
        {"seat": "cheese-mimo", "model": "mimo-v2.6-pro", "supply": "gateway",
         "turns": 3, "output_tokens": 900, "calls": 3, "turn_seconds": None,
         "window_tps": 0.0104},
    ]
    out = table(sample)
    assert "cheese" in out and "claude-sonnet-5" in out, out
    assert "78.4" in out and "0.5556" in out, out
    assert "48000" in out and "612.5" in out, out
    # 没有值的列写成 `-`，不是 0 —— 0 是一个算出来的数。
    assert "   -  " in out, out
    try:
        table([{"seat": "cheese"}])
    except KeyError as exc:
        assert "output_tokens" in str(exc), exc
    else:
        raise AssertionError("a row missing payload fields must not render")
    print(out)
    return 0


def parse_args(argv):
    """(topic, window, seats_only, as_json)；返回 None 表示命令行不对。"""
    topic, window, seats_only, as_json = "", "24h", False, False
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--topic" and i + 1 < len(argv):
            topic, i = argv[i + 1], i + 2
        elif a == "--window" and i + 1 < len(argv):
            window, i = argv[i + 1], i + 2
        elif a == "--seats":
            seats_only, i = True, i + 1
        elif a == "--json":
            as_json, i = True, i + 1
        else:
            print(f"unknown argument: {a}", file=sys.stderr)
            return None
    if not topic:
        print("--topic <房间 uuid> is required", file=sys.stderr)
        return None
    if window not in WINDOWS:
        print(f"--window must be one of {'/'.join(WINDOWS)}; {window!r} is not",
              file=sys.stderr)
        return None
    return topic, window, seats_only, as_json


def main(argv):
    if "--self-test" in argv:
        return self_test()
    parsed = parse_args([a for a in argv if a != "--self-test"])
    if parsed is None:
        return 2
    topic, window, seats_only, as_json = parsed

    password = os.environ.get("OKCHEESE_PASS")
    if not password:
        print("OKCHEESE_PASS is not set", file=sys.stderr)
        return 2
    try:
        token = login(USER, password)
        data = unwrap(call("GET",
                           f"/throughput/seats?topic={topic}&window={window}",
                           token))
    except urllib.error.HTTPError as exc:
        print(f"the platform refused the call: {exc.code} {exc.read()[:400]!r}",
              file=sys.stderr)
        return 3
    except (OSError, KeyError, ValueError) as exc:
        print(f"the call did not go through: {exc}", file=sys.stderr)
        return 3

    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0

    for name, which in (("seats", "seats"), ("by seat × model × supply", "rows")):
        if seats_only and which == "rows":
            continue
        rows = data.get(which) or []
        print(f"\n[{name}] window={data.get('window')} "
              f"since={data.get('since')} ({len(rows)} rows)")
        print(table(rows) if rows else "(none)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
