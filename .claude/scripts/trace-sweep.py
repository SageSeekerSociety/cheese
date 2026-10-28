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
    python3 .claude/scripts/trace-sweep.py retro --since 2026-10-12T11:00 --until 2026-10-12T14:05 --room <id>

Credentials come from the environment only: OKCHEESE_TOKEN, or
OKCHEESE_USERNAME + OKCHEESE_PASSWORD to log in. OKCHEESE_API overrides the
API root (default https://okcheese.com/api). Nothing read is written to disk;
samples are redacted before they are printed or stored.

With --ledger-doc the result is merged into a library document that keeps the
running ledger: one row per error signature, the tasks it appeared in, first
and last seen, and a status. A signature reaching two tasks is marked
「待深挖」; a status a person set by hand (any other text) is kept.

The ledger also says whether a deep dive is due: 25 or more task lines since
the last one, any candidate newly reaching two lines, or seven days without
one. After running the deep dive, pass --mark-deep-dive once to reset the
counters. The deep dive itself (falsifying candidates, checking the in-flight
list, placing each by the fix-priority table) is a person's or an agent's
job, not this script's.

The sweep also lists tasks that made EDIT_BLOCK or more edits with no test or
check run between them. Like the error counts, it needs no judgement; signals
that do (handoff cost, sleep totals, whether a read was wasted) stay out.

How to dig, chosen in round 9 of the gap research (library doc 9f19a6dc):

  * Trigger on events as well as volume: a task line with a red CI round or a
    must-fix review item gets a deep dive.
  * Deep dive = escaped defects: list the line's must-fix review items, red CI
    rounds and corrections by people, and place each one on the fix-priority
    table (L0 platform ... L6 memory). Drop cancelled and superseded "reds".
  * Before calling anything new, match it against PR titles of the last few
    days (`gh pr list --state all --search "created:>=<date>"`); one already in
    flight is marked "已在做 #PR", not filed again.
  * Shortest-path comparison (ideal steps vs actual) only for the ten or fewer
    lines a week with the highest ratio; it is expensive and its ideal path is
    a guess.
  * Every conclusion points back at the evidence: task, block time, and a
    command that finds it again.

`retro` prints the orchestrator's review tables for a window (trace_retro.py):
merged PRs, first-push CI pass rate, CI rounds per PR, merge-queue drops,
card wait split into waiting for CI and waiting for review, start to first
PR, and unfinished turns by seat and reason. It needs `gh`; --room adds the
platform half and uses the same credentials as the sweep. `--cohort
first-push` swaps the PR set: the ones CI first judged inside the window,
merged or still open, instead of the ones merged inside it.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

API = os.environ.get("OKCHEESE_API", "https://okcheese.com/api")
LEDGER_OPEN = "<!-- trace-sweep-ledger"
LEDGER_CLOSE = "trace-sweep-ledger -->"
DEEP_DIVE = "待深挖"
META_OPEN = "<!-- trace-sweep-meta"
META_CLOSE = "trace-sweep-meta -->"
DEEP_DIVE_LINES = 25  # about half a day at ~50 task lines a day
DEEP_DIVE_MAX_DAYS = 7
SEEN_HOURS = 48  # event ids kept for de-duplication; longer than any sweep window
NEW = "新"

# (kind, who should fix it, pattern). First match wins; the order matters.
KINDS = [
    ("工具参数类型不符", "平台", r"InputValidationError|failed schema validation|invalid_type|缺少参数"),
    ("未回复消息就调别的工具", "平台", r"has sent a message you have not answered"),
    (
        "凭据只管另一段对话",
        "平台",
        r"credential works another conversation|taskCredentialOtherConversation|只在自己那段对话里读写",
    ),
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

# Copied from backend/app/core/obs.py (scrub_secrets) because this script must run
# with the standard library alone. backend/tests/unit/test_trace_sweep_scrub_in_sync.py
# fails if the two copies drift apart, so change both together.
_SECRET_KEY_TAILS = (
    "token",
    "secret",
    "password",
    "passwd",
    "api_key",
    "apikey",
    "credential",
)
_SECRET_KEY = r"(?:[A-Za-z0-9]+_)*(?:" + "|".join(_SECRET_KEY_TAILS) + r")"
_OAUTH_CODE_RE = re.compile(r"([?&]code=)[^\s&#]+", re.IGNORECASE)
_SECRET_KV_RE = re.compile(
    r"\b(" + _SECRET_KEY + r")([\"']?\s*[=:]\s*)([\"']?)[^\"'\s,;&}\])>]+\3?",
    re.IGNORECASE,
)
_URL_CRED_RE = re.compile(r"(://[^\s:/@]+:)[^\s@/]+(@)")
_AUTH_SCHEME_RE = re.compile(r"\b(bearer|basic)(\s+)[A-Za-z0-9._~+/=-]{4,}", re.IGNORECASE)
_SECRET_VALUE_RE = re.compile(
    r"\b(?:"
    r"sk-[A-Za-z0-9_-]{8,}"
    r"|gh[pousr]_[A-Za-z0-9]{8,}"
    r"|github_pat_[A-Za-z0-9_]{8,}"
    r"|xox[abprs]-[A-Za-z0-9-]{8,}"
    r"|AKIA[0-9A-Z]{12,}"
    r"|eyJ[A-Za-z0-9_-]{6,}(?:\.[A-Za-z0-9_-]+){2}"
    r")"
)


def scrub_secrets(value: str) -> str:
    """Same passes, same order as backend/app/core/obs.py:scrub_secrets."""
    out = _AUTH_SCHEME_RE.sub(r"\1\2***", value)
    out = _SECRET_KV_RE.sub(r"\1\2\3***\3", out)
    out = _OAUTH_CODE_RE.sub(r"\1***", out)
    out = _URL_CRED_RE.sub(r"\1***\2", out)
    return _SECRET_VALUE_RE.sub("***", out)


def redact(text: str) -> str:
    text = scrub_secrets(text)
    # The credentials this script was given may appear verbatim in a recorded
    # command line, with no key name in front of them for the patterns to catch.
    for name in ("OKCHEESE_PASSWORD", "OKCHEESE_TOKEN"):
        secret = os.environ.get(name)
        if secret and len(secret) >= 6:
            text = text.replace(secret, "***")
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
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.load(resp)["data"]
    except urllib.error.HTTPError as err:
        if err.code in (401, 403):
            sys.exit(
                f"{method} {path}: HTTP {err.code}. The credentials were refused or lack access; "
                "set OKCHEESE_TOKEN to a fresh token, or OKCHEESE_USERNAME/OKCHEESE_PASSWORD."
            )
        raise


def token_from_env() -> str:
    if os.environ.get("OKCHEESE_TOKEN"):
        return os.environ["OKCHEESE_TOKEN"]
    user, password = os.environ.get("OKCHEESE_USERNAME"), os.environ.get("OKCHEESE_PASSWORD")
    if not (user and password):
        sys.exit("set OKCHEESE_TOKEN, or OKCHEESE_USERNAME and OKCHEESE_PASSWORD")
    data = _request("POST", "/users/auth/login", None, {"username": user, "password": password}) or {}
    if not data.get("accessToken"):
        sys.exit(
            "login returned no access token (the account may require a second factor); "
            "log in in a browser and set OKCHEESE_TOKEN instead"
        )
    return data["accessToken"]


def _tasks(channel: str, token: str, since: str) -> list[dict]:
    data = _request("GET", f"/topics/{channel}/tasks?limit=0", token)  # 0 = roster, no blocks
    tasks = data["data"] if isinstance(data, dict) else data
    return [t for t in tasks if (t.get("updated_at") or "") >= since]


def _command_of(meta: dict) -> str:
    """The command a tool call ran.

    A call that gave a description shows the description instead of the command,
    and the command moves to `detail`; a plain call has no `detail` and keeps it
    in `arg`. Reading only `detail` sees an empty command for every call of the
    second kind, which is most of them."""
    return str(meta.get("detail") or meta.get("arg") or "")


def _order_of(block: dict) -> tuple:
    """A block's own place in the transcript: its seq, then its full timestamp.

    ``at`` is truncated to the second, so it cannot tell apart the several calls
    one turn makes inside that second. Sorting by it alone leaves the tie to the
    rest of the key — enough for ``Bash`` to sort ahead of the edits it actually
    followed."""
    meta = block.get("meta") or {}
    seq = block.get("seq")
    return (seq if isinstance(seq, int) else -1, meta.get("at") or block.get("created_at") or "")


def _tool_calls(task: dict, token: str, since: str, until: str):
    """Yield (order, block id, at, tool, command, error) for every tool call inside the window."""
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
            tool, error = meta.get("tool"), meta.get("error")
            if (tool or error) and since <= at <= until:
                yield (
                    _order_of(block),
                    block.get("id") or f"{task['id']}:{at}",
                    at,
                    tool or "?",
                    _command_of(meta),
                    error if isinstance(error, str) and error else "",
                )
        before = page.get("oldest_id")
        if not page.get("has_more") or not before or (oldest and oldest < since):
            return


# Edits with no check run between them (round 9, method 3): the cheapest
# unambiguous signal that an agent is editing blind. A block counts once it
# reaches EDIT_BLOCK edits; the commands that end one are the repo's checks.
EDIT_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})
EDIT_BLOCK = 8
_CHECK_RE = re.compile(
    r"\b(pytest|vitest|jest|playwright|ruff|pyright|mypy|eslint|tsc|vue-tsc|pre-commit|"
    r"import-linter|check[-_][a-z-_]+|[a-z-]*ratchet[a-z-]*|frontend_grade|ci[:_-]fast|"
    r"task (test|guards|check)|npm (run )?test|pnpm (run )?test)\b",
    re.IGNORECASE,
)


def edit_blocks(calls: list[tuple[str, str, str]]) -> list[int]:
    """Sizes of the runs of at least EDIT_BLOCK edits with no check between them.

    ``calls`` is (at, tool, command) in time order."""
    blocks, run = [], 0
    for _, tool, command in calls:
        if tool in EDIT_TOOLS:
            run += 1
        elif tool == "Bash" and _CHECK_RE.search(command):
            if run >= EDIT_BLOCK:
                blocks.append(run)
            run = 0
    if run >= EDIT_BLOCK:
        blocks.append(run)
    return blocks


def _timeline(calls: list[tuple]) -> list[tuple[str, str, str]]:
    """(at, tool, command) in the transcript's own order, for ``edit_blocks``."""
    return [(at, tool, command) for _, _, at, tool, command, _ in sorted(calls, key=lambda c: c[0])]


def _label(task: dict) -> str:
    """A task's number as text; a task without one is told apart by its id."""
    number = task.get("number")
    return str(number) if number is not None else "?" + str(task.get("id", ""))[:6]


def _task_order(label: str) -> tuple:
    return (0, int(label), "") if label.isdigit() else (1, 0, label)


def _event_key(block_id: str) -> str:
    return hashlib.sha1(block_id.encode()).hexdigest()[:12]


def sweep(channel: str, token: str, since: str, until: str) -> dict:
    found: dict[str, dict] = {}
    tasks = _tasks(channel, token, since)
    calls = 0
    blind: dict[str, list[int]] = {}
    for task in tasks:
        task_calls = list(_tool_calls(task, token, since, until))
        for _order, block_id, at, tool, command, error in task_calls:
            if not error:
                continue
            calls += 1
            clean = redact(error)
            sig = signature(clean)
            kind, owner = classify(clean)
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
                    "sample": clean[:200],
                    "ids": {},
                },
            )
            row["count"] += 1
            row["ids"][block_id] = at
            row["tasks"].add(_label(task))
            row["first"], row["last"] = min(row["first"], at), max(row["last"], at)
        if blocks := edit_blocks(_timeline(task_calls)):
            blind[_label(task)] = blocks
    return {
        "since": since,
        "until": until,
        "tasks": len(tasks),
        "task_numbers": sorted((_label(t) for t in tasks), key=_task_order),
        "errors": calls,
        "rows": found,
        "edit_blocks": blind,
    }


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
        tasks = ", ".join(f"#{n}" for n in sorted(map(str, r["tasks"]), key=_task_order))
        lines.append(
            f"| {len(r['tasks'])} | {r['count']} | {r['owner']} | {r['kind']} | {sample} | {tasks} |"
        )
    blind = result.get("edit_blocks") or {}
    if blind:
        lines += [
            "",
            f"改了没跑检查（连续 {EDIT_BLOCK} 次以上编辑、中间没跑测试或检查）：",
            "",
            "| 任务 | 块数 | 最长一块 |",
            "|---|---|---|",
        ]
        for label, blocks in sorted(blind.items(), key=lambda kv: -max(kv[1])):
            lines.append(f"| #{label} | {len(blocks)} | {max(blocks)} |")
    return "\n".join(lines)


def merge_ledger(ledger: dict, result: dict, seen: dict | None = None) -> dict:
    """Fold a sweep into the ledger. ``seen`` maps an event key to its time and is
    shared across signatures, so an event already counted is never counted again
    however many times its window is swept."""
    seen = {} if seen is None else seen
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
        fresh = {_event_key(i): at for i, at in r["ids"].items() if _event_key(i) not in seen}
        seen.update(fresh)
        entry["tasks"] = sorted(set(map(str, entry["tasks"])) | set(map(str, r["tasks"])), key=_task_order)
        entry["first"], entry["last"] = min(entry["first"], r["first"]), max(entry["last"], r["last"])
        entry["count"] += len(fresh)
        if entry["status"] == NEW and len(entry["tasks"]) >= 2:
            entry["status"] = DEEP_DIVE
            entry["flagged"] = result["until"]
    return ledger


def deep_dive_due(meta: dict, ledger: dict, now: str, lines: int = DEEP_DIVE_LINES) -> str | None:
    """Why a deep dive is due now, or None. Three triggers, any one is enough."""
    last = meta.get("last_deep_dive", "")
    new_lines = len(meta.get("lines_since", []))
    if new_lines >= lines:
        return f"上次深挖后新增 {new_lines} 条任务线（阈值 {lines}）"
    flagged = [e for e in ledger.values() if e.get("status") == DEEP_DIVE and e.get("flagged", "") > last]
    if flagged:
        return f"{len(flagged)} 个候选在上次深挖后攒到 2 条以上的线"
    if not last or datetime.fromisoformat(now) - datetime.fromisoformat(last) >= timedelta(
        days=DEEP_DIVE_MAX_DAYS
    ):
        return f"距上次深挖已满 {DEEP_DIVE_MAX_DAYS} 天（兜底）"
    return None


def parse_meta(content: str) -> dict:
    start, end = content.find(META_OPEN), content.find(META_CLOSE)
    return json.loads(content[start + len(META_OPEN) : end]) if start >= 0 and end >= 0 else {}


def render_ledger(ledger: dict, swept: str, meta: dict | None = None) -> str:
    meta = meta or {}
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
    due = deep_dive_due(meta, ledger, swept)
    head = [
        f"上次深挖：{meta.get('last_deep_dive') or '还没有'}；之后新增任务线 {len(meta.get('lines_since', []))} 条。",
        f"该不该深挖：{'该，' + due if due else '还不用'}。",
        "",
    ]
    out = head + out
    out += ["", LEDGER_OPEN, json.dumps(ledger, ensure_ascii=False), LEDGER_CLOSE]
    out += [META_OPEN, json.dumps(meta, ensure_ascii=False), META_CLOSE]
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


def update_doc(doc: str, token: str, result: dict, mark_deep_dive: bool = False) -> str | None:
    """Merge into the ledger document; return why a deep dive is due, if it is."""
    current = _request("GET", f"/documents/{doc}", token) or {}
    content = current.get("content") or ""
    ledger = parse_ledger(content)
    meta = parse_meta(content)
    _apply_hand_statuses(ledger, content)
    seen = meta.get("seen", {})
    merge_ledger(ledger, result, seen)
    horizon = (datetime.fromisoformat(result["until"]) - timedelta(hours=SEEN_HOURS)).isoformat()
    meta["seen"] = {k: at for k, at in seen.items() if at >= horizon}
    meta["lines_since"] = sorted(
        set(map(str, meta.get("lines_since", []))) | set(result["task_numbers"]), key=_task_order
    )
    if mark_deep_dive:
        meta["last_deep_dive"], meta["lines_since"] = result["until"], []
    written = render_ledger(ledger, result["until"], meta)
    _request(
        "PUT",
        f"/documents/{doc}",
        token,
        {"content": written, "expected_version": current.get("doc_version", 0)},
    )
    # The document store may normalise markdown; the ledger lives in an HTML
    # comment so it should come back byte for byte. Check rather than assume.
    stored = (_request("GET", f"/documents/{doc}", token) or {}).get("content") or ""
    if parse_ledger(stored) != parse_ledger(written) or parse_meta(stored) != meta:
        sys.exit(f"document {doc} did not keep the ledger intact after writing; not trusting it")
    return deep_dive_due(meta, ledger, result["until"])


def self_test() -> int:
    failures = []

    def check(name, got, want):
        if got != want:
            failures.append(f"{name}: got {got!r}, want {want!r}")

    check("redacts bearer", redact("Authorization: Bearer abcdefghijklmnop"), "Authorization: Bearer ***")
    check("redacts basic", redact("Authorization: Basic dXNlcjpwdw=="), "Authorization: Basic ***")
    check("redacts password", redact('{"password":"hunter22"}'), '{"password":"***"}')
    check("redacts dsn", redact("postgresql://app:pw123@db:5432/x"), "postgresql://app:***@db:5432/x")
    check("redacts short ghp", redact("token ghp_abcdefgh1"), "token ***")
    check("redacts oauth code", redact("/cb?code=abc123&state=1"), "/cb?code=***&state=1")
    check("keeps status_code", redact("status_code=500"), "status_code=500")
    os.environ["OKCHEESE_PASSWORD"] = "s3cret-value!"
    check(
        "redacts a known secret anywhere",
        redact("login with s3cret-value! here"),
        "login with *** here",
    )
    del os.environ["OKCHEESE_PASSWORD"]
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
                "tasks": {"2", "10"},
                "first": "a",
                "last": "b",
                "count": 3,
                "ids": {"b1": "a", "b2": "a", "b3": "b"},
            }
        },
        "until": "z",
    }
    ledger = merge_ledger({}, result)
    edits = [("a", "Edit", "")] * 9
    check("a blind edit block is counted", edit_blocks(edits + [("b", "Bash", "uv run pytest -q")]), [9])
    check("a check ends the block", edit_blocks(edits[:4] + [("b", "Bash", "ruff check .")] + edits[:4]), [])
    check("other commands do not", edit_blocks(edits[:4] + [("b", "Bash", "git status")] + edits[:4]), [8])
    check(
        "a described command reads through detail",
        _command_of({"arg": "跑测试", "detail": "uv run pytest -q"}),
        "uv run pytest -q",
    )
    check("a plain command reads from arg", _command_of({"arg": "uv run pytest -q"}), "uv run pytest -q")
    check(
        "a check with no description still ends the block",
        edit_blocks(edits[:4] + [("b", "Bash", _command_of({"arg": "ruff check ."}))] + edits[:4]),
        [],
    )
    # The transcript a real turn leaves: one call described, one not. Read
    # through `_tool_calls`, because a command taken from the wrong field is
    # invisible to a test that calls `_command_of` itself.
    described = {
        "id": "b1",
        "meta": {"tool": "Bash", "at": "2026-10-12T11:00:04", "arg": "跑测试", "detail": "uv run pytest -q"},
    }
    plain = {"id": "b2", "meta": {"tool": "Bash", "at": "2026-10-12T11:00:05", "arg": "ruff check ."}}

    def calls_of(page: dict) -> list[tuple]:
        real_request = _request
        globals()["_request"] = lambda *a, **k: page
        try:
            return list(_tool_calls({"id": "t"}, "tok", "2026-10-12T00:00:00", "2026-10-12T23:00:00"))
        finally:
            globals()["_request"] = real_request

    calls = calls_of({"data": [described, plain], "has_more": False})
    check("a described call's command comes from detail", calls[0][4], "uv run pytest -q")
    check("a plain call's command comes from arg", calls[1][4], "ruff check .")

    # The same two commands through the signal they feed: a check ends the run of
    # edits, so four either side of one is not a block. Each command is the only
    # check between the eight in turn, so reading that one from the wrong field
    # stops it reading as a check and the eight join into one block.
    def edit(i: int) -> dict:
        return {"id": f"e{i}", "meta": {"tool": "Edit", "at": f"2026-10-12T11:00:{i:02d}", "arg": "改文件"}}

    for middle in (described, plain):
        calls = calls_of(
            {
                "data": [edit(i) for i in range(4)] + [middle] + [edit(i) for i in range(10, 14)],
                "has_more": False,
            }
        )
        check(
            f"{middle['id']}'s command ends the run of edits",
            edit_blocks(_timeline(calls)),
            [],
        )

    # One turn makes several calls inside the same second, so the truncated `at`
    # cannot order them: only the block's own `seq` can. Eight edits either side
    # of a check, all stamped 11:00:05, arriving check-last. Order by the second
    # and the tie falls to the tool name — Bash sorts before Edit, the check
    # lands first, and the eight edits join into one block of 8. Drop the sort
    # and the check stays last, which does the same. Either way the table this
    # sweep exists to print is wrong.
    def stamped(seq: int, tool: str, command: str) -> dict:
        return {"id": f"s{seq}", "seq": seq, "meta": {"tool": tool, "at": "2026-10-12T11:00:05", "arg": command}}

    tied = calls_of(
        {
            "data": [stamped(s, "Edit", "改文件") for s in (100, 101, 102, 103, 110, 111, 112, 113)]
            + [stamped(104, "Bash", "ruff check .")],
            "has_more": False,
        }
    )
    check("a check in the same second still ends the block", edit_blocks(_timeline(tied)), [])
    check(
        "ordering that second by tool name would not",
        edit_blocks([(c[2], c[3], c[4]) for c in tied]),
        [8],
    )
    check("tasks sort by number", ledger["s"]["tasks"], ["2", "10"])
    check("a task without a number keeps its id", _label({"number": None, "id": "abcdef12"}), "?abcdef")
    check("two tasks flag deep dive", ledger["s"]["status"], DEEP_DIVE)
    check("counts each event once", ledger["s"]["count"], 3)
    seen: dict = {}
    big = {"rows": {"w": dict(result["rows"]["s"], ids={f"e{i}": "a" for i in range(400)})}, "until": "z"}
    wide = merge_ledger({}, big, seen)
    merge_ledger(wide, big, seen)
    check("re-sweeping a window of 400 events adds nothing", wide["w"]["count"], 400)
    check(
        "signature of a redacted error hides the token",
        "ghp_" in signature(redact("bad ghp_abcdefgh123")),
        False,
    )
    ledger["s"]["status"] = "已开 #1"
    merge_ledger(ledger, result)
    check("hand status kept", ledger["s"]["status"], "已开 #1")
    check("ledger round-trips", parse_ledger(render_ledger(ledger, "z")), ledger)
    check("meta round-trips", parse_meta(render_ledger({}, "z", {"lines_since": [1]})), {"lines_since": [1]})
    now = "2026-10-12T12:00:00"
    recent = {"last_deep_dive": "2026-10-12T11:00:00", "lines_since": []}
    check("quiet ledger is not due", deep_dive_due(recent, {}, now), None)
    many = dict(recent, lines_since=list(range(25)))
    check("enough new lines is due", bool(deep_dive_due(many, {}, now)), True)
    fresh = {"s": {"status": DEEP_DIVE, "flagged": "2026-10-12T11:30:00"}}
    check("fresh candidate is due", bool(deep_dive_due(recent, fresh, now)), True)
    old = {"last_deep_dive": "2026-10-01T00:00:00", "lines_since": []}
    check("a week without one is due", bool(deep_dive_due(old, {}, now)), True)
    for f in failures:
        print("FAIL", f)
    print("self-test:", "ok" if not failures else f"{len(failures)} failed")
    return 1 if failures else 0


def retro(argv: list[str]) -> int:
    """`trace-sweep.py retro ...`: the dispatch retro's tables, in trace_retro.py."""
    spec = importlib.util.spec_from_file_location("trace_retro", Path(__file__).with_name("trace_retro.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # One implementation of the platform reads, so a refused token fails the
    # same way here as in the sweep.
    module._request, module.token_from_env = _request, token_from_env
    # `--room x` and `--room=x` are the same request to argparse; both need the
    # token armed, or the platform half silently runs without one.
    room = next((a for a in argv if a == "--room" or a.startswith("--room=")), None)
    if room is not None and "--self-test" not in argv:
        # The same credentials as the sweep, password login included.
        os.environ["OKCHEESE_TOKEN"] = token_from_env()
    return module.retro_main(argv)


def main() -> int:
    if sys.argv[1:2] == ["retro"]:
        return retro(sys.argv[2:])
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--channel", help="channel (room) topic id to sweep")
    parser.add_argument("--hours", type=float, default=3.0, help="window length ending now (default 3)")
    parser.add_argument("--since", help="window start, ISO UTC; overrides --hours")
    parser.add_argument("--ledger-doc", help="library document id to merge the result into")
    parser.add_argument(
        "--mark-deep-dive", action="store_true", help="record that a deep dive just ran (resets the counters)"
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    if not args.channel:
        parser.error("--channel is required")
    now = datetime.now(UTC)
    since = args.since or (now - timedelta(hours=args.hours)).strftime("%Y-%m-%dT%H:%M:%S")
    until = now.strftime("%Y-%m-%dT%H:%M:%S")
    if args.ledger_doc and since < (now - timedelta(hours=SEEN_HOURS)).strftime("%Y-%m-%dT%H:%M:%S"):
        # Events older than this have left the seen map; folding them in again would double their count.
        parser.error(f"--ledger-doc only takes windows within the last {SEEN_HOURS} hours")
    token = token_from_env()
    result = sweep(args.channel, token, since, until)
    print(summarize(result))
    if args.ledger_doc:
        due = update_doc(args.ledger_doc, token, result, args.mark_deep_dive)
        print(f"\n总账已更新：document {args.ledger_doc}")
        print(f"该不该深挖：{'该，' + due if due else '还不用'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
