#!/usr/bin/env python3
"""Sweep a channel's agent transcripts for recurring friction.

Reads every task in one channel that moved inside a time window, pulls the
tool-call transcript of each, and counts the tool errors by kind. A kind of
error that two or more different tasks ran into on their own is a candidate
for a deeper look: it is the signal that agents keep rediscovering the same
problem instead of the repository (or the platform) fixing it once.

    OKCHEESE_TOKEN=... python3 .claude/scripts/trace-sweep.py --channel <id>
    python3 .claude/scripts/trace-sweep.py --channel <id> --hours 24 --ledger-doc <doc-id>
    python3 .claude/scripts/trace-sweep.py --self-test

Credentials come from the environment only: OKCHEESE_TOKEN, or
OKCHEESE_USERNAME + OKCHEESE_PASSWORD to log in. OKCHEESE_API overrides the
API root (default https://okcheese.com/api). Nothing read is written to disk;
samples are redacted before they are printed or stored.

With --ledger-doc the result is merged into a library document that keeps the
running ledger: one row per error signature, the tasks it appeared in, first
and last seen, and a status. A signature reaching two tasks is marked
「待深挖」; a status a person set by hand (any other text) is kept.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from collections import defaultdict
from datetime import UTC, datetime, timedelta

API = os.environ.get("OKCHEESE_API", "https://okcheese.com/api")
LEDGER_OPEN = "<!-- trace-sweep-ledger"
LEDGER_CLOSE = "trace-sweep-ledger -->"
DEEP_DIVE = "待深挖"
NEW = "新"

# (kind, who should fix it, pattern). First match wins; the order matters.
KINDS = [
    ("工具参数类型不符", "平台", r"InputValidationError|failed schema validation|invalid_type|缺少参数"),
    ("未回复消息就调别的工具", "平台", r"has sent a message you have not answered"),
    ("凭据只管另一段对话", "平台", r"credential works another conversation"),
    ("改文件前没读/读后被改", "平台", r"has not been read yet|modified since read"),
    ("文档版本冲突或撞上建议", "平台", r"文档现在是第|一处都没改|待处理的修改建议"),
    ("平台接口 5xx", "平台", r"Bad Gateway|HTTP 50\d|Platform HTTP 5"),
    ("资源锁被占", "平台", r"没占到"),
    ("平台工具或钩子不可用", "平台", r"No such tool available|hook error|Central execution is disabled"),
    (
        "目录不是仓库/路径不存在",
        "环境",
        r"not a git repository|No such file or directory|File does not exist",
    ),
    ("命令不存在", "环境", r"command not found|ModuleNotFoundError|No module named"),
    ("命令超时", "环境", r"timed out|Timeout"),
    ("内存不足被杀", "环境", r"out of memory|OOM|Killed|heap out of memory"),
    ("检查或测试失败", "仓库", r"FAILED|AssertionError|ruff|pyright|eslint|tsc|vitest|pytest|lint"),
    ("git 操作失败", "仓库", r"fatal:|CONFLICT|rejected|non-fast-forward"),
    ("命令非零退出（未细分）", "未分", r"^Exit code \d+"),
]

_REDACT = [
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{12,}"), r"\1[REDACTED]"),
    (
        re.compile(
            r"gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9\-_]{16,}"
            r"|eyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"
        ),
        "[REDACTED]",
    ),
    (
        re.compile(
            r"(?i)(\"?(?:password|passwd|secret|token|api[_-]?key|accesstoken)\"?\s*[:=]\s*\"?)[^\s\",}]{4,}"
        ),
        r"\1[REDACTED]",
    ),
]


def redact(text: str) -> str:
    for pattern, repl in _REDACT:
        text = pattern.sub(repl, text)
    return text


def signature(error: str) -> str:
    """The error with its volatile parts (ids, numbers, paths) folded away."""
    s = re.sub(r"</?tool_use_error>|\[\{\"type\": ?\"text\", ?\"text\": ?\"", "", error)
    s = re.sub(r"[0-9a-f]{8}-[0-9a-f-]{27}", "<id>", s)
    s = re.sub(r"/(?:home|tmp|var)/\S+", "<path>", s)
    s = re.sub(r"\d+", "N", s)
    return re.sub(r"\s+", " ", s).strip()[:140]


def classify(error: str) -> tuple[str, str]:
    for kind, owner, pattern in KINDS:
        if re.search(pattern, error):
            return kind, owner
    return "其他", "未分"


def _request(method: str, path: str, token: str | None, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)["data"]


def token_from_env() -> str:
    if os.environ.get("OKCHEESE_TOKEN"):
        return os.environ["OKCHEESE_TOKEN"]
    user, password = os.environ.get("OKCHEESE_USERNAME"), os.environ.get("OKCHEESE_PASSWORD")
    if not (user and password):
        sys.exit("set OKCHEESE_TOKEN, or OKCHEESE_USERNAME and OKCHEESE_PASSWORD")
    data = _request("POST", "/users/auth/login", None, {"username": user, "password": password})
    return data["accessToken"]


def _tasks(channel: str, token: str, since: str) -> list[dict]:
    data = _request("GET", f"/topics/{channel}/tasks?limit=200", token)
    tasks = data["data"] if isinstance(data, dict) else data
    return [t for t in tasks if (t.get("updated_at") or "") >= since]


def _errors(task: dict, token: str, since: str, until: str):
    """Yield (at, tool, error) for every errored tool call inside the window."""
    before = None
    for _ in range(100):
        query = f"/topics/{task['id']}/transcript?limit=200" + (f"&before={before}" if before else "")
        page = _request("GET", query, token)
        items = page.get("data", [])
        oldest = None
        for block in items:
            meta = block.get("meta") or {}
            at = (meta.get("at") or block.get("created_at") or "")[:19]
            oldest = at if oldest is None or at < oldest else oldest
            error = meta.get("error")
            if isinstance(error, str) and error and since <= at <= until:
                yield at, meta.get("tool") or "?", error
        before = page.get("oldest_id")
        if not page.get("has_more") or not before or (oldest and oldest < since):
            return


def sweep(channel: str, token: str, since: str, until: str) -> dict:
    found: dict[str, dict] = {}
    tasks = _tasks(channel, token, since)
    calls = 0
    for task in tasks:
        for at, tool, error in _errors(task, token, since, until):
            calls += 1
            sig = signature(error)
            kind, owner = classify(error)
            row = found.setdefault(
                sig,
                {
                    "kind": kind,
                    "owner": owner,
                    "tool": tool,
                    "count": 0,
                    "tasks": set(),
                    "first": at,
                    "last": at,
                    "sample": redact(error)[:200],
                },
            )
            row["count"] += 1
            row["tasks"].add(task["number"])
            row["first"], row["last"] = min(row["first"], at), max(row["last"], at)
    return {"since": since, "until": until, "tasks": len(tasks), "errors": calls, "rows": found}


def summarize(result: dict) -> str:
    by_kind: dict[tuple[str, str], int] = defaultdict(int)
    for row in result["rows"].values():
        by_kind[(row["owner"], row["kind"])] += row["count"]
    lines = [
        (
            f"窗口 {result['since']} ~ {result['until']} UTC：{result['tasks']} 条任务线，"
            f"{result['errors']} 次工具报错。"
        ),
        "",
        "| 归属 | 类别 | 次数 |",
        "|---|---|---|",
    ]
    for (owner, kind), n in sorted(by_kind.items(), key=lambda kv: -kv[1]):
        lines.append(f"| {owner} | {kind} | {n} |")
    shared = [(sig, r) for sig, r in result["rows"].items() if len(r["tasks"]) >= 2]
    lines += [
        "",
        f"多条线各自撞到的（{len(shared)} 种）：",
        "",
        "| 线数 | 次数 | 归属 | 类别 | 报错（打码） | 任务 |",
        "|---|---|---|---|---|---|",
    ]
    for sig, r in sorted(shared, key=lambda kv: (-len(kv[1]["tasks"]), -kv[1]["count"]))[:30]:
        sample = r["sample"].replace("|", "/").replace("\n", " ")[:120]
        tasks = ", ".join(f"#{n}" for n in sorted(r["tasks"]))
        lines.append(
            f"| {len(r['tasks'])} | {r['count']} | {r['owner']} | {r['kind']} | {sample} | {tasks} |"
        )
    return "\n".join(lines)


def merge_ledger(ledger: dict, result: dict) -> dict:
    for sig, r in result["rows"].items():
        entry = ledger.setdefault(
            sig,
            {
                "kind": r["kind"],
                "owner": r["owner"],
                "sample": r["sample"],
                "tasks": [],
                "first": r["first"],
                "last": r["last"],
                "count": 0,
                "status": NEW,
            },
        )
        entry["tasks"] = sorted(set(entry["tasks"]) | r["tasks"])
        entry["first"], entry["last"] = min(entry["first"], r["first"]), max(entry["last"], r["last"])
        entry["count"] += r["count"]
        if entry["status"] == NEW and len(entry["tasks"]) >= 2:
            entry["status"] = DEEP_DIVE
    return ledger


def render_ledger(ledger: dict, swept: str) -> str:
    rows = sorted(ledger.items(), key=lambda kv: (-len(kv[1]["tasks"]), -kv[1]["count"]))
    out = [
        (
            f"最近一次轻扫：{swept} UTC。一行一种报错；同一种在 2 条以上不同的线出现就标「{DEEP_DIVE}」。"
            "「状态」列可以手改（例如「已开 #任务号」「不处理：原因」），脚本不会覆盖手改的状态。"
        ),
        "",
        "| 状态 | 线数 | 次数 | 归属 | 类别 | 报错（打码） | 出现过的线 | 首次 | 最近 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for _, e in rows[:80]:
        sample = e["sample"].replace("|", "/").replace("\n", " ")[:100]
        tasks = ", ".join(f"#{n}" for n in e["tasks"][-8:])
        out.append(
            f"| {e['status']} | {len(e['tasks'])} | {e['count']} | {e['owner']} | {e['kind']} | "
            f"{sample} | {tasks} | {e['first'][:16]} | {e['last'][:16]} |"
        )
    out += ["", LEDGER_OPEN, json.dumps(ledger, ensure_ascii=False), LEDGER_CLOSE]
    return "\n".join(out)


def parse_ledger(content: str) -> dict:
    start = content.find(LEDGER_OPEN)
    end = content.find(LEDGER_CLOSE)
    if start < 0 or end < 0:
        return {}
    return json.loads(content[start + len(LEDGER_OPEN) : end])


def _apply_hand_statuses(ledger: dict, content: str) -> None:
    """A status edited in the table wins over the stored one."""
    for line in content.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 9 or cells[0] in ("状态", "---"):
            continue
        for e in ledger.values():
            if e["sample"].replace("|", "/").replace("\n", " ")[:100] == cells[5] and cells[0] not in (
                NEW,
                DEEP_DIVE,
            ):
                e["status"] = cells[0]


def update_doc(doc: str, token: str, result: dict) -> None:
    current = _request("GET", f"/documents/{doc}", token)
    ledger = parse_ledger(current["content"])
    _apply_hand_statuses(ledger, current["content"])
    merge_ledger(ledger, result)
    _request(
        "PUT",
        f"/documents/{doc}",
        token,
        {"content": render_ledger(ledger, result["until"]), "expected_version": current["doc_version"]},
    )


def self_test() -> int:
    failures = []

    def check(name, got, want):
        if got != want:
            failures.append(f"{name}: got {got!r}, want {want!r}")

    check(
        "redacts bearer", redact("Authorization: Bearer abcdefghijklmnop"), "Authorization: Bearer [REDACTED]"
    )
    check("redacts password", redact('{"password":"hunter22"}'), '{"password":"[REDACTED]"}')
    check("folds ids", signature("Topic 3f2b1c4d-1111-2222-3333-444455556666 gone"), "Topic <id> gone")
    check("folds numbers", signature("exit code 137"), "exit code N")
    check("classifies schema", classify("InputValidationError: Bash failed")[0], "工具参数类型不符")
    check("classifies env", classify("/bin/bash: python: command not found")[1], "环境")
    check("unknown is other", classify("something odd")[0], "其他")
    result = {
        "rows": {
            "s": {
                "kind": "k",
                "owner": "o",
                "sample": "x",
                "tasks": {1, 2},
                "first": "a",
                "last": "b",
                "count": 3,
            }
        },
        "until": "z",
    }
    ledger = merge_ledger({}, result)
    check("two tasks flag deep dive", ledger["s"]["status"], DEEP_DIVE)
    ledger["s"]["status"] = "已开 #1"
    merge_ledger(ledger, result)
    check("hand status kept", ledger["s"]["status"], "已开 #1")
    check("ledger round-trips", parse_ledger(render_ledger(ledger, "z")), ledger)
    for f in failures:
        print("FAIL", f)
    print("self-test:", "ok" if not failures else f"{len(failures)} failed")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--channel", help="channel (room) topic id to sweep")
    parser.add_argument("--hours", type=float, default=3.0, help="window length ending now (default 3)")
    parser.add_argument("--since", help="window start, ISO UTC; overrides --hours")
    parser.add_argument("--ledger-doc", help="library document id to merge the result into")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    if not args.channel:
        parser.error("--channel is required")
    now = datetime.now(UTC)
    since = args.since or (now - timedelta(hours=args.hours)).strftime("%Y-%m-%dT%H:%M:%S")
    until = now.strftime("%Y-%m-%dT%H:%M:%S")
    token = token_from_env()
    result = sweep(args.channel, token, since, until)
    print(summarize(result))
    if args.ledger_doc:
        update_doc(args.ledger_doc, token, result)
        print(f"\n总账已更新：document {args.ledger_doc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
