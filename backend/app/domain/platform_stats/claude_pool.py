"""读计量代理发布的 Claude 账号池快照 —— 看板够不着的那一块。

池子只存在跑计量代理的那台机器上（`deploy/metering-proxy/claude_accounts.py`），
后端跟它没有任何通路。唯一读得到的是代理写在账本旁边的那个文件：`accounts.json`，
与 `subscription_usage_log` 同目录（`docker-compose.subscription.yml` 把那个目录
只读挂进后端，本来的用途是取用量）。所以这里是一次文件读，没有网络、没有凭据。

**读不到不是错误**：开发环境根本不跑订阅版代理，`SUBSCRIPTION_USAGE_LOG` 常常是空
的；用量那块看板不能因为这一小块看不见就整个红掉。读不到时 `accounts` 留空，把原因
写进 `reason`。

`reason` 是**代号**不是一句话（`REASONS` 那四个）：看板是中英双语的，这里写中文等于
英文界面里漏一句中文，写英文则反过来。代号由前端翻，认不出的代号给它自己的兜底句。
这条和看板别处「后端给机器名、前端 `UNAVAILABLE_KEY` 翻」是同一套。

**过期不等于没数据**：行上的 `until` 是绝对时刻，池子不变时它本来就不该变，所以
超过 `STALE_AFTER_S` 只说明「代理最近没写」。行照常返回，另给 `stale` 让界面注明这
是多久以前的快照；返回空会把「安静的箱子」说成「池子看不见」，那更不诚实。
"""

import json
import math
import time
from pathlib import Path

from app.core.config import settings

#: 多久没写入就不算新鲜。代理每个被计量的回合都会重写它，所以一小时还没动静，说明
#: 代理没在跑、或者这个箱子这段时间没有任何模型调用。
STALE_AFTER_S = 3600.0

#: 快照文件名 —— 代理侧写的是同一个名字（`billing_addon.py` 的 `snapshot_path`）。
SNAPSHOT_NAME = "accounts.json"

#: 一个账号的四种状态。`cooling` 带绝对解冻时刻；`disabled` 是「额度用完了、没有可等
#: 的解冻时刻，要人去重置」；`available` 是没有冷却记录。`unknown` 是读不懂的标签 ——
#: 代理和平台是两个发布链（代理有单独的发布工作流），所以新代理可能先写下这一版后端
#: 还不认识的状态，那时既不能画成可用、也不该按「需人工重置」解释它。
STATES = ("available", "cooling", "disabled", "unknown")

#: 看不见的原因，**代号**（前端翻成人话，理由见模块 docstring）。
#:   not-configured —— 这台部署没配 `SUBSCRIPTION_USAGE_LOG`，根本没有那个目录；
#:   missing        —— 配了，但代理还没写下过这个文件；
#:   unreadable     —— 文件在，读不动（权限、目录）；
#:   malformed      —— 读到了，但不是我们认得的形状（含「没有写入时刻」）。
REASONS = ("not-configured", "missing", "unreadable", "malformed")


def snapshot_path(usage_log: str | None = None) -> Path | None:
    """快照文件在哪。`SUBSCRIPTION_USAGE_LOG` 没配就是没得看。"""
    if usage_log is None:
        usage_log = settings.subscription_usage_log
    directory = str(usage_log or "").strip()
    return Path(directory).parent / SNAPSHOT_NAME if directory else None


def read_claude_pool(path: Path | None, *, now: float | None = None) -> dict:
    """读一份池子快照，或者一个「为什么看不见」。

    形状（钉死的，前端按它写）：`accounts` 是行列表，为空时 `reason` 是 `REASONS` 里
    的一个代号；`stale` 说这份快照是不是已经旧了（旧了也照样给行）；`retry_after` 是
    **所有冷却中的账号里最早的那个解冻时刻**，由行上的 `until` 现算，不取文件里那个
    同龄的计数 —— 那个数写下的那一刻就开始变旧。
    """
    if now is None:
        now = time.time()
    if path is None:
        return _nothing("not-configured")
    try:
        document = json.loads(path.read_text())
    except FileNotFoundError:
        return _nothing("missing")
    except OSError:
        return _nothing("unreadable")
    except (json.JSONDecodeError, ValueError):
        return _nothing("malformed")
    if not isinstance(document, dict):
        return _nothing("malformed")
    written_at = document.get("written_at")
    if not isinstance(written_at, (int, float)) or isinstance(written_at, bool):
        return _nothing("malformed")
    raw_rows = document.get("accounts")
    if not isinstance(raw_rows, list):
        return _nothing("malformed")
    rows = [row for row in (_row(raw, now) for raw in raw_rows) if row is not None]
    age = now - written_at
    return {
        "accounts": rows,
        "reason": None,
        "written_at": written_at,
        "age_seconds": age,
        "stale": age > STALE_AFTER_S,
        "retry_after": _retry_after(rows, now),
    }


def _nothing(reason: str) -> dict:
    """看不见时的形状：空列表加一个原因代号，别的字段留空。"""
    return {
        "accounts": [],
        "reason": reason,
        "written_at": None,
        "age_seconds": None,
        "stale": False,
        "retry_after": None,
    }


def _row(raw: object, now: float) -> dict | None:
    """一行账号；名字都不是字符串的那种坏行直接丢掉（读不出来就别画）。

    `cooling` 的解冻时刻如果已经过去，这个账号现在就是可用的 —— 代理只会在真有请求时
    去探测它，界面没有理由把一个能用的账号画成「冷却到 14:05」而 14:05 已经过了。所以
    在读取时按同一个判断收敛一次，行上的状态与 `until` 始终自洽：可用就没有待办时刻。
    """
    if not isinstance(raw, dict) or not isinstance(raw.get("name"), str):
        return None
    state = raw.get("state")
    if state not in STATES:
        state = "unknown"
    until = raw.get("until")
    if not isinstance(until, (int, float)) or isinstance(until, bool):
        until = None
    if state == "cooling" and (until is None or until <= now):
        state, until = "available", None
    failures = raw.get("failures")
    if not isinstance(failures, int) or isinstance(failures, bool):
        failures = None
    return {"name": raw["name"], "state": state, "until": until, "failures": failures}


def _retry_after(rows: list[dict], now: float) -> int | None:
    """最早的那个解冻时刻 —— 「全部冷却时最早何时恢复」问的就是它。

    只有 `cooling` 是有约在先的等待：`disabled` 等不来（要人去重置），`available` 不
    用等，`unknown` 连语义都不知道，都不该让它替界面承诺一个恢复时刻。
    """
    deadlines = [
        row["until"]
        for row in rows
        if row["state"] == "cooling" and row["until"] is not None
    ]
    if not deadlines:
        return None
    return max(1, math.ceil(min(deadlines) - now))
