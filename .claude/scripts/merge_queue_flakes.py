#!/usr/bin/env python3
"""合并队列里哪些用例在偶发判红 —— 一条命令出两张榜。

`merge_group` 一次把几条 PR 合到一起跑，同一个红灯其实有两个来源：一条用例自
己偶发（时序、共享资源），和几条 PR 的改动合起来把某个接口弄坏。两者的处置完全
相反 —— 前者记下来重跑，后者是这次合入的真故障，得把涉及的 PR 挡住 —— 但它们在
CI 上都只是「一个红的作业」。混在一张榜里，真故障会被当成又一次偶发忽略掉，所以
这个脚本存在的唯一理由是**把这两类红分开**。

分法是「同一个错误签名命中多少条不同用例」。一条用例零星报某个错，是偶发；一批
互不相干的用例在**同一批运行里**同时报同一个错（改了个接口、缺了个关键字参数、
导入坏掉），几乎不可能是巧合，那是组合出来的真故障。跨用例数达到阈值的签名在榜上
标 `[疑似合并冲突]`，提醒看榜的人先去看它，而不是去重跑那些用例。

    python3 .claude/scripts/merge_queue_flakes.py [--repo R] [--runs N] [--top K]
        [--conflict-min-tests M]
    python3 .claude/scripts/merge_queue_flakes.py --json-dir DIR ...   # 离线样本
    python3 .claude/scripts/merge_queue_flakes.py --self-test     # 自测：内嵌样本 + 读盘

取数走 `gh api`（凭据由平台注入，本脚本不读也不打印 token）：先按 `event=merge_group`
分页拿运行列表，只要 `conclusion == failure` 的运行，再逐个拿作业，再逐个拿失败作业
的日志。`--json-dir` 换成从磁盘读同样三步的原始返回，用来在没网时对着抓下来的样本
跑；目录布局：

    DIR/runs*.json             gh api repos/{repo}/actions/runs?event=merge_group
                               的分页原文，每页 {"total_count":..., "workflow_runs":[...]}
    DIR/jobs/<run_id>.json     gh api repos/{repo}/actions/runs/<run_id>/jobs 的原文
    DIR/logs/job-<job_id>.log  失败作业的原始日志

分母是**最近 N 次 merge_group 运行的总数**，不只是失败的：偶发的比例要拿全部运行
当分母，否则「4 次红」到底是 4/26 还是 4/300 就分不清了。

阈值的道理：一条偶发只脏一条用例，于是把阈值定在「好几条」之上 —— 默认 5 条不同
用例共用一个签名才算组合出来的真故障，低于它的仍是零星红。调这个数要用
`--conflict-min-tests`，别改这里的默认值：自测按默认值断言那条跨用例签名被标出来。

只读：不写任何文件，也不跑 git。
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import contextlib
from dataclasses import dataclass, field
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile

# 运行的红：只有 conclusion == failure 才算这条 merge_group 运行失败。
FAILED_RUN = frozenset({"failure"})
# 作业的红：比运行宽松一档，超时和 action_required 同样是「这个作业没绿」。
FAILED_JOB = frozenset({"failure", "timed_out", "action_required"})

#: 跨用例数达到几条才算组合出来的真故障。一条偶发只脏一条用例；真故障（缺关键字
#: 参数、导入坏掉、接口改了）会一次点亮一批互不相干的用例。5 是「明显不止一条」
#: 的下限，又不至于把两条用例撞同一个常见断言算进来。
DEFAULT_CONFLICT_MIN_TESTS = 5
DEFAULT_TOP = 20
DEFAULT_RUNS = 300

#: pytest 短摘要里的 `FAILED <节点> - <错误>`。前面的时间戳前缀、xdist 的 `[gw2]`
#: 用什么搜都行，因为锚点是 `FAILED` 这个词本身；节点里必须含 `::`，否则是别的
#: 输出里碰巧出现的 `FAILED`，不是一条用例。`##[error]` 那行用的是破折号 `—`
#: 而不是 ` - `，天然不匹配，不会重复计一次。
_FAILED_LINE = re.compile(r"FAILED\s+(\S+)\s+-\s+(.*)$")
_SIG_WIDTH = 80


@dataclass
class Job:
    """一个作业：判没判红，以及（判红时）它的原始日志。"""

    id: int
    conclusion: str | None
    log: str


@dataclass
class Run:
    """一条 merge_group 运行：结论，和它的作业（只有失败的才带日志）。"""

    id: int
    conclusion: str | None
    created_at: str
    jobs: list[Job] = field(default_factory=list)


def extract_failures(log: str) -> list[tuple[str, str]]:
    """从一份日志里抽出 (用例 id, 错误签名)，按日志内去重。

    同一份日志里同一条用例可能不止打一次 `FAILED`（`-x` 之前、重试、短摘要），
    去重是为了让「次数」是「几条运行红过」的量级，而不是日志行数。
    """
    found: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for line in log.splitlines():
        match = _FAILED_LINE.search(line)
        if not match:
            continue
        node, signature = match.group(1).strip(), match.group(2).strip()
        if "::" not in node:
            continue
        key = (node, signature)
        if key in seen:
            continue
        seen.add(key)
        found.append(key)
    return found


def analyze(runs: list[Run], top: int, min_tests: int) -> dict:
    """把运行聚合成两张榜。纯函数：不碰网络也不碰磁盘，好让自测直接喂假样本。"""
    failed_runs = [run for run in runs if run.conclusion in FAILED_RUN]

    test_runs: dict[str, set[int]] = defaultdict(set)
    test_sig: dict[str, Counter[str]] = defaultdict(Counter)
    sig_tests: dict[str, set[str]] = defaultdict(set)
    sig_runs: dict[str, set[int]] = defaultdict(set)
    sig_hits: Counter[str] = Counter()
    failed_jobs = 0

    for run in failed_runs:
        for job in run.jobs:
            if job.conclusion not in FAILED_JOB:
                continue
            failed_jobs += 1
            for node, signature in extract_failures(job.log):
                test_runs[node].add(run.id)
                test_sig[node][signature] += 1
                sig_tests[signature].add(node)
                sig_runs[signature].add(run.id)
                sig_hits[signature] += 1

    # 跨用例数是这个脚本的分界线：达到阈值的签名，它的红灯更像合出来的真故障。
    flagged = {sig for sig in sig_hits if len(sig_tests[sig]) >= min_tests}

    signature_rows = [
        {
            "signature": sig,
            "hits": sig_hits[sig],
            "tests": len(sig_tests[sig]),
            "runs": len(sig_runs[sig]),
            "flagged": sig in flagged,
        }
        for sig in sorted(sig_hits, key=lambda s: (-sig_hits[s], s))
    ]

    failed_count = len(failed_runs)
    total_count = len(runs)
    test_rows = []
    for node in sorted(test_runs, key=lambda t: (-len(test_runs[t]), t)):
        top_sig = test_sig[node].most_common(1)[0][0]
        test_rows.append(
            {
                "test": node,
                "runs": len(test_runs[node]),
                # 分母是全部运行，不只是失败的：这个占比就是这个用例对整体判红率
                # 的贡献，治理的目标（把判红率压下去）才读得出来。
                "share": (len(test_runs[node]) / total_count) if total_count else 0.0,
                "signature": top_sig,
                "flagged": top_sig in flagged,
            }
        )

    return {
        "runs_total": len(runs),
        "runs_failed": failed_count,
        "jobs_failed": failed_jobs,
        "flagged_signatures": sorted(flagged),
        "signatures": signature_rows[:top],
        "tests": test_rows[:top],
    }


def _clip(text: str, width: int = _SIG_WIDTH) -> str:
    """榜单要能并排看，长错误签名截到宽度。聚合按完整签名做，只有显示才截。"""
    return text if len(text) <= width else text[: width - 1] + "…"


def render(report: dict) -> str:
    lines: list[str] = []
    lines.append(
        f"merge_group runs: {report['runs_total']} total, "
        f"{report['runs_failed']} failed, "
        f"{report['jobs_failed']} failed jobs read"
    )
    if not report["runs_failed"]:
        lines.append("no failed merge_group runs in the window: nothing to rank")
        return "\n".join(lines)

    lines.append("")
    lines.append(
        f"[错误签名榜]  {len(report['signatures'])} shown, ranked by occurrences "
        f"over {report['runs_failed']} failed runs"
    )
    lines.append(f"  {'次数':>6} {'用例':>6} {'运行':>5}  错误签名")
    for row in report["signatures"]:
        mark = "[疑似合并冲突] " if row["flagged"] else ""
        lines.append(
            f"  {row['hits']:>6} {row['tests']:>6} {row['runs']:>5}  "
            f"{mark}{_clip(row['signature'])}"
        )

    lines.append("")
    lines.append(
        f"[用例复发榜]  {len(report['tests'])} shown, share of all "
        f"{report['runs_total']} runs"
    )
    lines.append(f"  {'次数':>6} {'占比':>7}  用例 / 最常同现的错误签名")
    for row in report["tests"]:
        mark = "  [疑似合并冲突]" if row["flagged"] else ""
        lines.append(
            f"  {row['runs']:>6} {row['share'] * 100:>6.1f}%  {row['test']}{mark}"
        )
        lines.append(f"          ↳ {_clip(row['signature'], 70)}")
    return "\n".join(lines)


def _gh(args: list[str]) -> str:
    """跑一次 `gh`，把 stdout 原样返回。凭据在环境里，脚本不碰 token。"""
    # 按字节抓再替换着解：一份作业日志里有一个坏字节，不该让整张榜读不出来，
    # 而文件那条路本来就是这么读的，两条路得一样能扛。
    proc = subprocess.run(["gh", *args], capture_output=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(
            f"gh {' '.join(args)} failed ({proc.returncode}): "
            f"{proc.stderr.decode('utf-8', errors='replace').strip()[:300]}"
        )
    return proc.stdout.decode("utf-8", errors="replace")


def _gh_json(endpoint: str) -> object:
    return json.loads(_gh(["api", endpoint]))


def _unique(payloads: list[dict]) -> list[dict]:
    """按 id 去重，保留第一次出现的那条（gh 最新的运行在前）。

    分页之间新运行会挤动位置，同一条运行可能在两页里各出现一次。不去重就会把
    一次运行算两遍：真运行少拿一条，签名的命中数翻倍而它的运行数不动。
    """
    seen: set[int] = set()
    unique: list[dict] = []
    for raw in payloads:
        if raw["id"] in seen:
            continue
        seen.add(raw["id"])
        unique.append(raw)
    return unique


def fetch_jobs(repo: str, run_id: int) -> list[dict]:
    jobs: list[dict] = []
    for page in range(1, 101):
        data = _gh_json(
            f"repos/{repo}/actions/runs/{run_id}/jobs?per_page=100&page={page}"
        )
        items = data["jobs"]
        jobs.extend(items)
        if len(items) < 100:
            break
    return jobs


def fetch_online(repo: str, limit: int) -> list[Run]:
    """gh api 走三步取数：运行列表 → 失败运行的作业 → 失败作业的日志。"""
    payloads: list[dict] = []
    for page in range(1, (limit + 99) // 100 + 1):
        data = _gh_json(
            f"repos/{repo}/actions/runs?event=merge_group&per_page=100&page={page}"
        )
        payloads.extend(data["workflow_runs"])
        if len(data["workflow_runs"]) < 100:
            break

    runs: list[Run] = []
    for raw in _unique(payloads):
        if len(runs) >= limit:
            break
        run_id = raw["id"]
        if raw.get("conclusion") not in FAILED_RUN:
            runs.append(Run(run_id, raw.get("conclusion"), raw.get("created_at", "")))
            continue
        jobs = [
            Job(
                job["id"],
                job.get("conclusion"),
                # 只失败作业才下日志：成功作业的日志对这张榜没有信息。
                _gh(["api", f"repos/{repo}/actions/jobs/{job['id']}/logs"])
                if job.get("conclusion") in FAILED_JOB
                else "",
            )
            for job in fetch_jobs(repo, run_id)
        ]
        runs.append(Run(run_id, raw.get("conclusion"), raw.get("created_at", ""), jobs))
    return runs


def load_offline(root: Path, limit: int) -> list[Run]:
    """从 `--json-dir` 读样本；布局见模块 docstring。取最近 limit 条运行。"""
    paths = sorted(root.glob("runs*.json"))
    if not paths:
        # 目录写错、或样本还没抓下来，读出来同样是「一条都没失败」；那和队列
        # 真干净分不出来，所以在这里就报错，不往下渲染一张空榜。
        raise ValueError(f"no runs*.json under {root}")
    payloads: list[dict] = []
    for path in paths:
        payloads.extend(json.loads(path.read_text())["workflow_runs"])

    ordered = sorted(
        _unique(payloads),
        key=lambda r: (r.get("created_at") or "", r["id"]),
        reverse=True,
    )

    runs: list[Run] = []
    for raw in ordered[:limit]:
        run_id = raw["id"]
        jobs: list[Job] = []
        jobs_file = root / "jobs" / f"{run_id}.json"
        if raw.get("conclusion") in FAILED_RUN and jobs_file.exists():
            for job in json.loads(jobs_file.read_text())["jobs"]:
                log_file = root / "logs" / f"job-{job['id']}.log"
                log = log_file.read_text(errors="replace") if log_file.exists() else ""
                jobs.append(Job(job["id"], job.get("conclusion"), log))
        runs.append(Run(run_id, raw.get("conclusion"), raw.get("created_at", ""), jobs))
    return runs


def _self_test_offline() -> None:
    """`--json-dir` 那条路也真跑一遍：把样本写进临时目录，再读回来。

    造四样东西：翻页边界上重复出现的一条运行（不能算两遍）、一份带非 UTF-8
    字节的日志（一个坏字节不该让整张榜读不出来）、一个空目录、一份文件在但
    没有运行的样本（后两者都要报错，不能渲染成「队列很干净」）。
    """
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)

        def page(name: str, workflow_runs: list[dict]) -> None:
            (root / name).write_text(json.dumps({"workflow_runs": workflow_runs}))

        def raw_run(run_id: int, conclusion: str, created_at: str) -> dict:
            return {"id": run_id, "conclusion": conclusion, "created_at": created_at}

        # 第二页把第一页的最后一条又带了一次：gh 分页之间位置会被新运行挤动。
        repeated = raw_run(3, "success", "2026-10-16T09:00:00Z")
        page(
            "runs-page1.json",
            [
                raw_run(1, "failure", "2026-10-15T05:00:00Z"),
                raw_run(2, "failure", "2026-10-16T08:00:00Z"),
                repeated,
            ],
        )
        page("runs-page2.json", [repeated])

        (root / "jobs").mkdir()
        for run_id, job_id in ((1, 11), (2, 21)):
            (root / "jobs" / f"{run_id}.json").write_text(
                json.dumps({"jobs": [{"id": job_id, "conclusion": "failure"}]})
            )
        (root / "logs").mkdir()
        # 一个坏字节混在同一行日志里：整行还有效，榜不该整张读不出来。
        (root / "logs" / "job-11.log").write_bytes(
            b"2026-10-15T05:48:30.3537274Z FAILED tests/b.py::test_flaky - "
            b"AssertionError: x \xff\xfe\n"
        )
        (root / "logs" / "job-21.log").write_text(
            "2026-10-16T08:12:41.1140393Z FAILED tests/b.py::test_flaky - "
            "AssertionError: x\n"
        )

        runs = load_offline(root, limit=300)
        # 3 条而不是 4 条：重复的那条被去掉了；最近的在最前。
        assert [run.id for run in runs] == [3, 2, 1], [run.id for run in runs]
        report = analyze(runs, top=DEFAULT_TOP, min_tests=DEFAULT_CONFLICT_MIN_TESTS)
        assert report["runs_total"] == 3 and report["runs_failed"] == 2, report
        top_test = report["tests"][0]
        assert top_test["test"] == "tests/b.py::test_flaky" and top_test["runs"] == 2, (
            top_test
        )
        # 分母是 3（含那条绿运行），不是 2 —— 和内存里那条路同一套算法。
        assert abs(top_test["share"] - 2 / 3) < 1e-9, top_test

        # 目录写错和目录里没有数据：都要退 2，并把原因说出来。
        (root / "blank").mkdir()
        page("blank/runs-page1.json", [])
        for missing in (root / "empty", root / "nope", root / "blank"):
            missing.mkdir(exist_ok=True)
            said = io.StringIO()
            with contextlib.redirect_stderr(said):
                code = main(["--json-dir", str(missing)])
            assert code == 2, (missing, code)
            assert "merge_group runs" in said.getvalue(), (missing, said.getvalue())


def _self_test_gh_bytes() -> None:
    """`gh` 那条路也真跑一遍：换掉 subprocess，看 `_gh` 怎么读回 stdout。

    gh 是按字节吐作业日志的，一个坏字节就落在这一行上。按文本模式抓，Python
    当场抛 UnicodeDecodeError，整张榜跟着读不出来。这里让替身照真实的样子演
    两种模式：文本模式抛，字节模式把带坏字节的输出交回来，等 `_gh` 自己替换着
    解。非零退出那条路也走一遍：报错里要带着 gh 说的话，而不是一声不响。
    """
    real_run = subprocess.run
    seen: list[list[str]] = []

    class Proc:
        def __init__(self, out: bytes, err: bytes, code: int) -> None:
            self.stdout, self.stderr, self.returncode = out, err, code

    def text_mode(kwargs: dict) -> bool:
        return bool(kwargs.get("text") or kwargs.get("encoding"))

    def fake_run(args, **kwargs):
        seen.append(args)
        if text_mode(kwargs):
            raise UnicodeDecodeError("utf-8", b"\xff\xfe", 0, 1, "invalid start byte")
        return Proc(b"assert 'closed' == 'open' \xff\xfe\n", b"", 0)

    subprocess.run = fake_run
    try:
        out = _gh(["api", "x"])
    finally:
        subprocess.run = real_run

    assert seen == [["gh", "api", "x"]], seen
    assert out.startswith("assert 'closed' == 'open' "), repr(out)
    assert "�" in out and "\xff" not in out, repr(out)

    def failing(args, **kwargs):
        if text_mode(kwargs):
            raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
        return Proc(b"", b"HTTP 404 \xff\n", 1)

    subprocess.run = failing
    try:
        try:
            _gh(["api", "y"])
        except RuntimeError as exc:
            said = str(exc)
        else:
            raise AssertionError("a gh that exits non-zero must not look like success")
    finally:
        subprocess.run = real_run
    assert "failed (1)" in said and "�" in said, said


def self_test() -> int:
    """离线核对整条链路：抽取 → 聚合 → 渲染，外加取数和两个 loader 各走一遍。

    假样本刻意造出两类红：一个签名跨 5 条用例（组合出来的真故障，要标出来），
    一条用例在两个运行里各红一次（偶发，不标）。抽取那步再混进 `[gw2]` 前缀，
    确认锚在 `FAILED` 上、不靠时间戳位置。只读自己写进临时目录的样本，不碰网络。
    """
    common = (
        "2026-10-15T05:48:30.3537274Z [gw2] FAILED tests/a.py::test_one - "
        "TypeError: boom\n"
        "2026-10-15T05:48:30.4000000Z FAILED tests/a.py::test_two - TypeError: boom\n"
        "2026-10-15T05:48:30.4100000Z FAILED tests/a.py::test_three - TypeError: boom\n"
        "2026-10-15T05:48:30.4200000Z FAILED tests/a.py::test_four - TypeError: boom\n"
        "2026-10-15T05:48:30.4300000Z FAILED tests/a.py::test_five - TypeError: boom\n"
        "2026-10-15T05:48:30.4400000Z FAILED tests/b.py::test_flaky - AssertionError: x\n"
        "2026-10-15T05:48:31.0000000Z ##[error]tests/a.py::test_one — TypeError: boom "
        "| rerun: cd backend && uv run pytest 'tests/a.py::test_one'\n"
    )
    flaky_only = "2026-10-16T08:12:41.1140393Z FAILED tests/b.py::test_flaky - AssertionError: x\n"
    runs = [
        Run(1, "failure", "2026-10-15T05:00:00Z", [Job(11, "failure", common)]),
        Run(2, "failure", "2026-10-16T08:00:00Z", [Job(21, "failure", flaky_only)]),
        Run(3, "success", "2026-10-16T09:00:00Z"),
        Run(4, "cancelled", "2026-10-16T10:00:00Z"),
    ]

    report = analyze(runs, top=DEFAULT_TOP, min_tests=DEFAULT_CONFLICT_MIN_TESTS)

    assert report["runs_total"] == 4, report
    assert report["runs_failed"] == 2, report
    # `##[error]` 那行（破折号）不能算成第二条记录：5 条用例，每条命中一次。
    assert report["jobs_failed"] == 2, report

    top_sig = report["signatures"][0]
    assert top_sig["signature"].startswith("TypeError: boom"), top_sig
    assert top_sig["hits"] == 5 and top_sig["tests"] == 5 and top_sig["runs"] == 1, (
        top_sig
    )
    assert top_sig["flagged"], "a signature across 5 tests must be flagged"

    flaky_sig = next(
        r for r in report["signatures"] if "AssertionError" in r["signature"]
    )
    assert flaky_sig["tests"] == 1 and not flaky_sig["flagged"], flaky_sig

    top_test = report["tests"][0]
    assert top_test["test"] == "tests/b.py::test_flaky", top_test
    assert top_test["runs"] == 2, top_test
    # 分母是 4（含两次没失败的运行），不是 2：占比要能读成对判红率的贡献。
    assert abs(top_test["share"] - 0.5) < 1e-9, top_test
    assert not top_test["flagged"], "a lone flake must not be flagged"

    cluster_test = next(
        r for r in report["tests"] if r["test"] == "tests/a.py::test_one"
    )
    assert cluster_test["runs"] == 1 and cluster_test["flagged"], cluster_test

    # 渲染也要真跑一遍：断言标出来的行确实带着标记，而不是只信聚合的结果。
    text = render(report)
    assert "[疑似合并冲突]" in text, text
    assert "tests/b.py::test_flaky" in text, text

    _self_test_gh_bytes()
    _self_test_offline()

    print(
        "self-test ok: "
        f"{report['runs_total']} runs, {report['runs_failed']} failed, "
        f"flagged={report['flagged_signatures']}; gh decode ok; offline loader ok"
    )
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Rank which merge_group cases flake. Two boards: cases by occurrences, "
            "and error signatures by occurrences/tests/runs. A signature spanning "
            ">= --conflict-min-tests distinct cases is marked [疑似合并冲突] because "
            "a merge-combination break is not a flake. --json-dir reads a saved "
            "sample instead of the network; layout: DIR/runs*.json (gh pages of "
            "actions/runs?event=merge_group), DIR/jobs/<run_id>.json "
            "(actions/runs/<id>/jobs), DIR/logs/job-<job_id>.log (job logs)."
        )
    )
    parser.add_argument("--repo", default="SageSeekerSociety/cheese")
    parser.add_argument(
        "--runs",
        type=int,
        default=DEFAULT_RUNS,
        help="how many recent merge_group runs (default 300)",
    )
    parser.add_argument(
        "--top", type=int, default=DEFAULT_TOP, help="rows per board (default 20)"
    )
    parser.add_argument(
        "--json-dir", type=Path, help="offline sample directory; see module docstring"
    )
    parser.add_argument(
        "--conflict-min-tests",
        type=int,
        default=DEFAULT_CONFLICT_MIN_TESTS,
        help=(
            "distinct cases sharing one signature before it is marked "
            "[疑似合并冲突] (default 5: one flake dirties one case)"
        ),
    )
    parser.add_argument(
        "--self-test", action="store_true", help="run the offline self-test"
    )
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()

    try:
        if args.json_dir is not None:
            runs = load_offline(args.json_dir, args.runs)
        else:
            runs = fetch_online(args.repo, args.runs)
    except (OSError, KeyError, ValueError, RuntimeError) as exc:
        print(f"could not gather merge_group runs: {exc}", file=sys.stderr)
        return 2

    if not runs:
        # 一条运行也没读到（目录写错、样本是空的）。这和「队列真干净」读起来一样，
        # 所以报错退出，不渲染一张「没什么可排的」的榜。
        where = args.json_dir if args.json_dir is not None else args.repo
        print(f"no merge_group runs to rank in {where}", file=sys.stderr)
        return 2

    print(render(analyze(runs, args.top, args.conflict_min_tests)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
