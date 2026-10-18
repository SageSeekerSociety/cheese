#!/usr/bin/env python3
"""回答一批主干红归谁、作者在不在修。

    python3 .claude/scripts/main-red-owner.py <主干部 run id>
    python3 .claude/scripts/main-red-owner.py <批 head sha>
    python3 .claude/scripts/main-red-owner.py --self-test

输入一条 `Required CI` 在 main 上的 run id，或者那一批的 head sha。输出一段可以直接
贴进 #1343 的中文：

1. 哪些 job 红了、日志里点了哪些测试用例；
2. 批内提交对撞后 culprits 是谁 —— 判据直接 import `landing_watch._blame`（#4255），
   不在这里重写；
3. culprit 的作者、来源 PR，以及提交里 `Link:` 记的任务；
4. 作者名下有没有改了同一批文件/同名修复分支的开放 PR；
5. 建议动作：作者在修就等；有任务就叫回任务；分不出或没人修再开热修。

只读：不重跑、不改 PR、不改任务。所有 GitHub 读都走 `gh api`，平台任务编号只在
`OKCHEESE_TOKEN` 或 `OKCHEESE_USER`/`OKCHEESE_PASS` 在环境里时才解析；没有凭据时
输出任务链接，不猜编号。`--json` 交结构化结果，给别的脚本消费。
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.parse
import urllib.request

REPO_ROOT = Path(__file__).resolve().parents[2]

#: A completed check that went red. `cancelled` is not one: a newer push to
#: main cancels the run it superseded (same set as landing_watch).
FAILED = frozenset({"failure", "timed_out", "startup_failure"})

#: Jobs that only restate another job's verdict.
AGGREGATORS = frozenset(
    {
        "CI required",
        "CI required (draft quick)",
        "scope",
        "Mark main green",
    }
)

#: `landing_forge`'s bounds for reading a batch. Kept equal on purpose: a
#: batch read here must be as complete as the one the platform blames from.
COMPARE_MAX_COMMITS = 250
COMMIT_MAX_FILES = 300
LOOK_BACK = 20

#: How many red jobs have their log read for test names. landing_watch reads
#: three; a diagnosis wants the shape of the whole failure, so five.
RED_LOGS = 5
MAX_CASES = 12

#: Stop after this many author PRs have had their files read. Each is one call.
MAX_PR_FILE_READS = 6

#: A commit message carries the task it came from:
#: `Link: https://okcheese.com/projects/<project>/topics/<channel>/tasks/<uuid>`.
TASK_LINK = re.compile(
    r"https://[^\s]+/tasks/"
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
)
PR_IN_SUBJECT = re.compile(r"\(#(\d+)\)\s*$")

#: A failed test line: pytest `FAILED tests/x.py::test_y - …`,
#: vitest/jest `FAIL tests/x.test.ts > …`.
_PYTEST_CASE = re.compile(r"\b(?:FAILED|ERROR)\s+([^\s]+::[^\s]+)")
_VITEST_CASE = re.compile(
    r"\bFAIL\s+([^\s]+\.(?:test|spec)\.[cm]?[jt]sx?)\s*>\s*(.+?)\s*$"
)

#: Stems too generic to name a fix branch or a PR title.
GENERIC_STEMS = frozenset(
    {
        "init",
        "main",
        "index",
        "utils",
        "util",
        "types",
        "type",
        "config",
        "test",
        "tests",
        "conftest",
        "helpers",
        "common",
        "base",
        "app",
    }
)


class ReadError(Exception):
    """GitHub would not answer, or answered with something unreadable."""


# ---------------------------------------------------------------------------
# The attribution criterion is imported, never copied.
# ---------------------------------------------------------------------------


def _load_attribution():
    """`_blame`, `_names`, `_stem`, `BatchCommits`, `CommitChange` and the
    log parser, from the modules that own them.

    They live behind the backend package, so run with its virtualenv. `python3`
    without it re-execs into `backend/.venv/bin/python` when that exists, rather
    than silently growing a second copy of the rule here.
    """
    backend = REPO_ROOT / "backend"
    if str(backend) not in sys.path:
        sys.path.insert(0, str(backend))
    try:
        from app.domain.review.landing_forge import (  # noqa: PLC0415
            BatchCommits,
            CommitChange,
            _failed_test_paths,
        )
        from app.domain.review.landing_watch import (  # noqa: PLC0415
            _blame,
            _names,
            _stem,
        )
    except ModuleNotFoundError:
        venv = backend / ".venv" / "bin" / "python"
        # Compare prefixes, not resolved paths: a virtualenv's interpreter is
        # usually a symlink to the system one, and resolving it would call the
        # venv identical to the interpreter that is missing its packages.
        if venv.exists() and Path(sys.prefix).resolve() != (backend / ".venv").resolve():
            os.execv(str(venv), [str(venv), str(Path(__file__).resolve()), *sys.argv[1:]])
        raise SystemExit(
            "main-red-owner imports the backend attribution rule; run it with "
            "backend/.venv/bin/python (backend dependencies are not installed here)"
        )
    return BatchCommits, CommitChange, _failed_test_paths, _blame, _names, _stem


(
    BatchCommits,
    CommitChange,
    failed_test_paths,
    blame_of,
    names_test,
    stem_of,
) = _load_attribution()


# ---------------------------------------------------------------------------
# GitHub reads
# ---------------------------------------------------------------------------


def _run(argv: list[str], *, timeout: int = 120) -> str:
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReadError(f"{argv[0]} failed: {exc}") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        raise ReadError(f"{' '.join(argv[:4])}: {detail[0][:240] if detail else 'exit ' + str(proc.returncode)}")
    return proc.stdout


def _json_of(text: str):
    """`gh api` prints bare JSON, some wrappers prepend a banner: start at the
    first `{`/`[` like trace_retro._gh does."""
    for ch in ("{", "["):
        at = text.find(ch)
        if at < 0:
            continue
        try:
            return json.loads(text[at:])
        except json.JSONDecodeError:
            continue
    raise ReadError("gh answered without JSON")


def _gh_json(argv: list[str], *, timeout: int = 120):
    return _json_of(_run(["gh", *argv], timeout=timeout))


def _api(repo: str, suffix: str, params: dict | None = None):
    path = f"/repos/{urllib.parse.quote(repo, safe='/')}{suffix}"
    if params:
        path += "?" + urllib.parse.urlencode(params)
    return _gh_json(["api", path])


def _repo_name(explicit: str | None) -> str:
    if explicit:
        return explicit
    env = os.environ.get("GH_REPO")
    if env:
        return env
    out = _run(["gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"])
    return out.strip()


def _default_branch(repo: str) -> str:
    return _api(repo, "")["default_branch"]


@dataclass(frozen=True, slots=True)
class Job:
    id: int
    name: str
    conclusion: str | None
    url: str


@dataclass(frozen=True, slots=True)
class RunFacts:
    id: int
    head: str
    branch: str
    conclusion: str | None
    url: str
    jobs: tuple[Job, ...]


def _run_of(raw: dict) -> RunFacts:
    return RunFacts(
        id=int(raw["id"]),
        head=str(raw.get("head_sha") or ""),
        branch=str(raw.get("head_branch") or ""),
        conclusion=raw.get("conclusion"),
        url=str(raw.get("html_url") or ""),
        jobs=(),
    )


def _jobs_of(raw: list[dict]) -> tuple[Job, ...]:
    return tuple(
        Job(
            id=int(job.get("id") or 0),
            name=str(job.get("name") or ""),
            conclusion=job.get("conclusion"),
            url=str(job.get("html_url") or ""),
        )
        for job in raw
        if job.get("id")
    )


def _is_gate_run(raw: dict) -> bool:
    return raw.get("name") == "Required CI" or str(raw.get("path") or "").endswith(
        "required-ci.yml"
    )


def load_run(repo: str, value: str) -> RunFacts:
    """The `Required CI` run on main, by id or by the head it reported on."""
    default = _default_branch(repo)
    if value.isdigit():
        raw = _api(repo, f"/actions/runs/{value}")
        if not _is_gate_run(raw):
            raise ReadError(
                f"run {value} is {raw.get('name')!r}, not a Required CI gate run"
            )
        run = _run_of(raw)
        jobs = _api(repo, f"/actions/runs/{value}/jobs", {"per_page": 100})["jobs"]
    else:
        full = value
        if len(value) < 40:
            full = str(_api(repo, f"/commits/{value}")["sha"])
        found = _api(
            repo,
            "/actions/runs",
            {"head_sha": full, "branch": default, "per_page": 100},
        ).get("workflow_runs") or []
        gate = [
            r
            for r in found
            if r.get("status") == "completed" and _is_gate_run(r)
        ]
        if not gate:
            raise ReadError(
                f"no completed Required CI run on {default} has head {full[:8]}"
            )
        raw = max(gate, key=lambda r: str(r.get("created_at") or ""))
        jobs = _api(repo, f"/actions/runs/{raw['id']}/jobs", {"per_page": 100})["jobs"]
        run = _run_of(raw)
    if run.branch and run.branch != default:
        raise ReadError(
            f"run {run.id} is on {run.branch}, not {default}; this answers main batches only"
        )
    return RunFacts(**{**asdict(run), "jobs": _jobs_of(jobs)})


def load_job_logs(repo: str, jobs: tuple[Job, ...]) -> dict[int, str]:
    """The tail of each red leaf job's log, up to `RED_LOGS` of them."""
    logs: dict[int, str] = {}
    for job in _red_leaf_jobs(jobs)[:RED_LOGS]:
        if not job.id:
            continue
        try:
            path = f"/repos/{urllib.parse.quote(repo, safe='/')}/actions/jobs/{job.id}/logs"
            logs[job.id] = _run(["gh", "api", path], timeout=120)[-512 * 1024 :]
        except ReadError:
            logs[job.id] = ""
    return logs


def _red_leaf_jobs(jobs: tuple[Job, ...]) -> tuple[Job, ...]:
    red = [j for j in jobs if j.conclusion in FAILED]
    leaves = [j for j in red if j.name not in AGGREGATORS]
    return tuple(leaves or red)


# ---------------------------------------------------------------------------
# Batch, source PR, task, and the fix already in flight
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Batch:
    total: int
    commits: tuple[CommitChange, ...]
    complete: bool
    anchor: str | None
    tests: tuple[str, ...]
    cases: tuple[str, ...]
    blamed: str | None
    reason: str

    def subject(self, sha: str) -> str:
        for commit in self.commits:
            if commit.sha == sha:
                return commit.subject
        return ""


def _main_green(repo: str) -> str | None:
    try:
        return str(_api(repo, "/git/ref/heads/main-green")["object"]["sha"])
    except (ReadError, KeyError, TypeError):
        return None


def _compare(repo: str, base: str, head: str) -> dict | None:
    try:
        data = _api(repo, f"/compare/{base}...{head}")
    except ReadError:
        return None
    return data if data.get("status") in ("ahead", "identical") else None


def _batch_from_compare(compare: dict, changes: dict[str, dict]) -> BatchCommits:
    listed = [c for c in (compare.get("commits") or []) if isinstance(c, dict)]
    total = compare.get("total_commits")
    total = total if isinstance(total, int) else len(listed)
    complete = (
        len(listed) >= total
        and len(listed) < COMPARE_MAX_COMMITS
        and len(listed) <= LOOK_BACK
    )
    read: list[CommitChange] = []
    for commit in listed[-LOOK_BACK:]:
        sha = commit.get("sha")
        raw = changes.get(sha) if sha else None
        if not sha or raw is None:
            complete = False
            continue
        message = str((raw.get("commit") or {}).get("message") or "").strip()
        subject = message.splitlines()[0] if message else ""
        files_raw = [f for f in (raw.get("files") or []) if isinstance(f, dict)]
        files = tuple(f.get("filename") or "" for f in files_raw if f.get("filename"))
        read.append(
            CommitChange(
                sha=sha,
                subject=subject,
                files=files,
                complete=len(files_raw) < COMMIT_MAX_FILES,
            )
        )
    return BatchCommits(total=total, changes=tuple(read), complete=complete)


def _cases_in(logs: dict[int, str]) -> tuple[str, ...]:
    found: list[str] = []
    for text in logs.values():
        for raw in text.splitlines():
            line = _clean(raw)
            match = _PYTEST_CASE.search(line)
            if match:
                found.append(match.group(1))
                continue
            match = _VITEST_CASE.search(line)
            if match:
                found.append(f"{match.group(1)} > {match.group(2)}")
    return tuple(_dedupe(found)[:MAX_CASES])


def _clean(raw: str) -> str:
    """Drop the Actions timestamp, the CR and ANSI colour, like landing_watch."""
    line = re.sub(r"^\d{4}-\d\d-\d\dT[\d:.]+Z\s?", "", raw.rstrip("\r\n"))
    return re.sub(r"\x1b\[[0-9;]*m", "", line)


def _dedupe(items) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _tests_in(logs: dict[int, str]) -> tuple[str, ...]:
    found: list[str] = []
    for text in logs.values():
        found += failed_test_paths(text)
    return tuple(sorted(set(found)))


def _reason(tests: tuple[str, ...], commits: BatchCommits) -> str:
    """Why `_blame` can or cannot name one commit. The matching itself is
    `landing_watch._names`; this only words the answer."""
    if not tests:
        return "日志里没有点名测试文件"
    if not commits.complete or any(not c.complete for c in commits.changes):
        return "批内提交没能读全，不能钉在单个提交上"
    matches = [(test, [c.sha for c in commits.changes if names_test(test, c.files)]) for test in tests]
    if any(not hit for _, hit in matches):
        return "有失败的测试文件不在任何批内提交的改动里"
    if any(len(hit) > 1 for _, hit in matches):
        return "同一个失败文件被批内多个提交改到（对撞）"
    common = set(matches[0][1])
    for _, hit in matches[1:]:
        common &= set(hit)
    if len(common) == 1:
        return "唯一 culprit"
    return "失败用例分别指向不同提交（对撞）"


def _batch(repo: str, head: str, logs: dict[int, str]) -> Batch:
    tests = _tests_in(logs)
    anchor = _main_green(repo)
    compare = _compare(repo, anchor, head) if anchor else None
    if compare is None:
        # Without main-green, the last green batch's head is the next best
        # anchor; a batch after no green at all cannot be bounded.
        try:
            runs = _api(
                repo,
                "/actions/workflows/required-ci.yml/runs",
                {"branch": "main", "status": "success", "per_page": 1},
            ).get("workflow_runs") or []
            base = str(runs[0]["head_sha"]) if runs else ""
            compare = _compare(repo, base, head) if base and base != head else None
            anchor = base or None
        except (ReadError, KeyError, IndexError):
            compare = None
    if compare is None:
        commits = BatchCommits(total=0, changes=(), complete=False)
    else:
        changes: dict[str, dict] = {}
        for commit in (compare.get("commits") or [])[-LOOK_BACK:]:
            sha = commit.get("sha") if isinstance(commit, dict) else None
            if not sha:
                continue
            try:
                changes[sha] = _api(repo, f"/commits/{sha}")
            except ReadError:
                changes.pop(sha, None)
        commits = _batch_from_compare(compare, changes)
    blamed = blame_of(tests, commits)
    return Batch(
        total=commits.total,
        commits=commits.changes,
        complete=commits.complete,
        anchor=anchor,
        tests=tests,
        cases=_cases_in(logs),
        blamed=blamed,
        reason=_reason(tests, commits),
    )


@dataclass(frozen=True, slots=True)
class Source:
    sha: str
    subject: str
    pr: int | None
    pr_title: str
    pr_author: str
    task_id: str | None
    task_link: str | None
    merged_at: str | None


def _source(repo: str, sha: str, raw_commit: dict) -> Source:
    message = str((raw_commit.get("commit") or {}).get("message") or "").strip()
    subject = message.splitlines()[0] if message else ""
    pr_raw = None
    match = PR_IN_SUBJECT.search(subject)
    if match:
        try:
            candidate = _api(repo, f"/pulls/{match.group(1)}")
            if candidate.get("merged_at") and str(candidate.get("merge_commit_sha") or sha) == sha:
                pr_raw = candidate
        except ReadError:
            pr_raw = None
    if pr_raw is None:
        try:
            rows = _api(repo, f"/commits/{sha}/pulls")
            merged = [r for r in rows if isinstance(r, dict) and r.get("merged_at")]
            merged.sort(key=lambda r: str(r.get("merged_at") or ""), reverse=True)
            pr_raw = merged[0] if merged else None
        except ReadError:
            pr_raw = None
    body = str((pr_raw or {}).get("body") or "")
    link = TASK_LINK.search(body) or TASK_LINK.search(message)
    task_id = link.group(1) if link else None
    task_link = None
    if link:
        task_link = str(link.group(0))
    return Source(
        sha=sha,
        subject=subject,
        pr=int(pr_raw["number"]) if pr_raw and pr_raw.get("number") else None,
        pr_title=str((pr_raw or {}).get("title") or ""),
        pr_author=str(((pr_raw or {}).get("user") or {}).get("login") or ""),
        task_id=task_id,
        task_link=task_link,
        merged_at=str((pr_raw or {}).get("merged_at") or "") or None,
    )


@dataclass(frozen=True, slots=True)
class TaskRef:
    id: str
    number: int | None
    owner: str | None
    title: str | None
    link: str | None


def _platform_token() -> str | None:
    token = os.environ.get("OKCHEESE_TOKEN")
    if token:
        return token
    user = os.environ.get("OKCHEESE_USER")
    password = os.environ.get("OKCHEESE_PASS")
    if not (user and password):
        return None
    try:
        answer = _platform_call(
            "POST", "/users/auth/login", {"username": user, "password": password}
        )
        return str(_unwrap(answer)["accessToken"])
    except Exception:
        return None


def _platform_call(method: str, path: str, body: dict | None, token: str | None = None):
    api = os.environ.get("OKCHEESE_API", "https://okcheese.com/api")
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(api + path, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def _unwrap(value):
    while isinstance(value, dict) and "data" in value:
        value = value["data"]
    return value


def _task(source: Source) -> TaskRef | None:
    if not source.task_id:
        return None
    task = TaskRef(
        id=source.task_id,
        number=None,
        owner=None,
        title=None,
        link=source.task_link,
    )
    token = _platform_token()
    if not token:
        return task
    try:
        row = _unwrap(
            _platform_call("GET", f"/topics/{source.task_id}/task", None, token)
        )
    except Exception:
        return task
    if not isinstance(row, dict):
        return task
    return TaskRef(
        id=source.task_id,
        number=row.get("number"),
        owner=row.get("owner_handle"),
        title=row.get("title"),
        link=source.task_link,
    )


@dataclass(frozen=True, slots=True)
class Fix:
    underway: bool
    strong: bool
    prs: tuple[int, ...]
    branches: tuple[str, ...]
    detail: str


def _keywords(tests: tuple[str, ...], files: tuple[str, ...]) -> tuple[str, ...]:
    stems = {stem_of(path).lower() for path in (*tests, *files)}
    return tuple(sorted(s for s in stems if len(s) >= 5 and s not in GENERIC_STEMS))


def _prs(repo: str, author: str, search: str | None) -> list[dict]:
    argv = [
        "pr",
        "list",
        "--repo",
        repo,
        "--state",
        "open",
        "--author",
        author,
        "--limit",
        "50",
        "--json",
        "number,title,headRefName,url,isDraft,updatedAt,author",
    ]
    if search:
        argv += ["--search", search]
    return _gh_json(argv)


def _pr_files(repo: str, number: int) -> tuple[str, ...]:
    found: list[str] = []
    for page in (1, 2, 3):
        rows = _api(repo, f"/pulls/{number}/files", {"per_page": 100, "page": page})
        found += [str(r["filename"]) for r in rows if isinstance(r, dict) and r.get("filename")]
        if len(rows) < 100:
            break
    return tuple(found)


def _branches(keywords: tuple[str, ...]) -> tuple[str, ...]:
    try:
        text = _run(["git", "ls-remote", "--heads", "origin"], timeout=30)
    except ReadError:
        return ()
    names = [
        line.split("refs/heads/", 1)[1]
        for line in text.splitlines()
        if "refs/heads/" in line
    ]
    return tuple(
        name
        for name in names
        if any(keyword in name.lower() for keyword in keywords)
    )[:5]


def _fix(
    repo: str,
    author: str,
    tests: tuple[str, ...],
    files: tuple[str, ...],
) -> Fix:
    if not author:
        return Fix(False, False, (), (), "没有可查的作者")
    keywords = _keywords(tests, files)
    rows: list[dict] = []
    try:
        rows += _prs(repo, author, None)
    except ReadError as exc:
        return Fix(False, False, (), (), f"查询作者开放 PR 失败：{exc}")
    if keywords:
        try:
            rows += _prs(repo, author, " OR ".join(keywords[:2]))
        except ReadError:
            # The listing above already answered; a search API refusal must
            # not throw away what was read.
            pass
    by_number: dict[int, dict] = {}
    for row in rows:
        number = row.get("number")
        if number:
            by_number[int(number)] = row
    ordered = sorted(
        by_number.values(),
        key=lambda row: str(row.get("updatedAt") or ""),
        reverse=True,
    )
    named = [
        row
        for row in ordered
        if any(
            keyword in (str(row.get("title") or "") + " " + str(row.get("headRefName") or "")).lower()
            for keyword in keywords
        )
    ]
    candidates = (named or ordered)[:MAX_PR_FILE_READS]
    strong: list[int] = []
    medium: list[int] = []
    for row in candidates:
        number = int(row["number"])
        try:
            changed = _pr_files(repo, number)
        except ReadError:
            continue
        if any(names_test(test, changed) for test in tests):
            strong.append(number)
        elif any(names_test(path, changed) for path in files):
            medium.append(number)
    branches = _branches(keywords)
    picked = tuple(sorted(set(strong + medium)))
    if strong:
        detail = "开放 PR {} 改了失败的测试文件".format(
            "、".join(f"#{n}" for n in strong)
        )
    elif medium:
        detail = "开放 PR {} 改了 culprit 的同一批文件".format(
            "、".join(f"#{n}" for n in medium)
        )
    elif branches:
        detail = "同名分支 {}".format("、".join(branches))
    else:
        detail = "作者名下没有改了同一批文件的开放 PR 或同名分支"
    return Fix(
        underway=bool(strong or medium or branches),
        strong=bool(strong),
        prs=picked,
        branches=branches,
        detail=detail,
    )


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Report:
    run_id: int
    run_url: str
    head: str
    conclusion: str | None
    total: int
    complete: bool
    failed_jobs: tuple[dict, ...]
    cases: tuple[str, ...]
    tests: tuple[str, ...]
    blamed: str | None
    blame_reason: str
    source: dict | None
    task: dict | None
    fix: dict | None
    action: str
    paragraph: str


def _action(
    blamed: str | None,
    reason: str,
    source: Source | None,
    task: TaskRef | None,
    fix: Fix | None,
) -> str:
    if blamed is None:
        if "没有点名" in reason:
            return "日志没点名用例：叫回批内全部任务逐条自证，并要求失败 job 补打测试名"
        if "读不全" in reason or "读不到" in reason:
            return "批内提交没读全：人工分诊，不指定单一 culprit"
        return "对撞不出：叫回批内全部任务逐条自证，不把红算在单个任务头上"
    if fix and fix.underway:
        where = "、".join(f"#{n}" for n in fix.prs) or "、".join(fix.branches)
        if fix.strong:
            return f"作者在修，等 {where} 落地；不另开热修"
        if fix.prs:
            return f"作者疑似在修（{where} 改了同一批文件）；先确认，再决定是否开热修"
        return f"作者疑似在修（同名分支 {where}）；先确认，再决定是否开热修"
    if task and task.number:
        return f"叫回任务 #{task.number}（owner {task.owner or '未知'}）修；不另开热修"
    if source and source.pr:
        return f"叫回来源 PR #{source.pr} 的任务修；未解析到任务编号，不另开热修"
    return "叫回该提交对应的任务修；未解析到任务编号，不另开热修"


def _paragraph(report: Report, batch: Batch, source: Source | None) -> str:
    lines: list[str] = []
    head = report.head[:8]
    lines.append(
        f"主干红批 run {report.run_id}（head {head}，"
        f"批内 {report.total} 个提交，结论 {report.conclusion or '未完成'}）"
    )
    if report.failed_jobs:
        shown = "、".join(job["name"] for job in report.failed_jobs[:6])
        more = " 等" if len(report.failed_jobs) > 6 else ""
        lines.append(f"失败 job：{shown}{more}（{len(report.failed_jobs)} 个）")
    if report.cases:
        shown = "；".join(report.cases[:6])
        more = f" 等 {len(report.cases)} 个" if len(report.cases) > 6 else ""
        lines.append(f"失败用例：{shown}{more}")
    elif report.tests:
        lines.append("失败用例文件：" + "、".join(report.tests))
    lines.append(f"归因：{report.blame_reason}")
    if report.blamed:
        subject = batch.subject(report.blamed)
        files = next((c.files for c in batch.commits if c.sha == report.blamed), ())
        lines.append(
            f"culprit：{report.blamed[:8]} {subject}"
            + (f"（改了 {len(files)} 个文件）" if files else "")
        )
        if source:
            who = f"PR #{source.pr}" if source.pr else "未找到来源 PR"
            if source.pr_author:
                who += f"（作者 {source.pr_author}）"
            if report.task and report.task.get("number"):
                who += f" · 任务 #{report.task['number']}"
                if report.task.get("owner"):
                    who += f"（owner {report.task['owner']}）"
            elif report.task and report.task.get("link"):
                who += f" · 任务 {report.task['link']}"
            lines.append(f"来源：{who}")
        if report.fix:
            lines.append(f"修复中：{report.fix['detail']}")
    else:
        shown = "、".join(c.sha[:8] for c in batch.commits[:10])
        suffix = " 等" if len(batch.commits) > 10 else ""
        lines.append(f"来源：无单一 culprit；批内提交 {shown}{suffix}")
    lines.append(f"建议：{report.action}")
    return "\n".join(lines)


def build_report(
    run: RunFacts,
    batch: Batch,
    source: Source | None,
    task: TaskRef | None,
    fix: Fix | None,
    *,
    fixing_override: bool | None = None,
) -> Report:
    """Turn already-read facts into the paragraph. `fixing_override` exists
    for the self-test's deliberate violation: it shows what the verdict would
    be if the author-is-fixing rule were deleted."""
    red = _red_leaf_jobs(run.jobs)
    fix_for_action = fix
    if fix is not None and fixing_override is not None:
        fix_for_action = Fix(
            underway=fixing_override,
            strong=fix.strong,
            prs=fix.prs,
            branches=fix.branches,
            detail=fix.detail,
        )
    action = _action(batch.blamed, batch.reason, source, task, fix_for_action)
    report = Report(
        run_id=run.id,
        run_url=run.url,
        head=run.head,
        conclusion=run.conclusion,
        total=batch.total,
        complete=batch.complete,
        failed_jobs=tuple(
            {"name": job.name, "conclusion": job.conclusion, "url": job.url}
            for job in red
        ),
        cases=batch.cases,
        tests=batch.tests,
        blamed=batch.blamed,
        blame_reason=batch.reason,
        source=asdict(source) if source else None,
        task=asdict(task) if task else None,
        fix=asdict(fix) if fix else None,
        action=action,
        paragraph="",
    )
    return Report(**{**asdict(report), "paragraph": _paragraph(report, batch, source)})


def collect(repo: str, value: str) -> Report:
    run = load_run(repo, value)
    if run.conclusion not in FAILED:
        batch = Batch(0, (), True, None, (), (), None, "这批没有红")
        report = build_report(run, batch, None, None, None)
        return Report(
            **{
                **asdict(report),
                "action": "这批没有红，无需归因",
                "paragraph": (
                    f"run {run.id}（head {run.head[:8]}）结论 {run.conclusion}，"
                    "没有要归的责"
                ),
            }
        )
    logs = load_job_logs(repo, run.jobs)
    batch = _batch(repo, run.head, logs)
    source = None
    task = None
    fix = None
    if batch.blamed:
        commit = next((c for c in batch.commits if c.sha == batch.blamed), None)
        raw = _api(repo, f"/commits/{batch.blamed}")
        source = _source(repo, batch.blamed, raw)
        task = _task(source)
        fix = _fix(
            repo,
            source.pr_author or str((raw.get("author") or {}).get("login") or ""),
            batch.tests,
            commit.files if commit else (),
        )
    return build_report(run, batch, source, task, fix)


# ---------------------------------------------------------------------------
# Self-test: recorded run JSON, three scenarios, one deliberate violation
# ---------------------------------------------------------------------------


def _recorded(
    *,
    run_id: int,
    head: str,
    gate_red: bool,
    jobs: list[dict],
    logs: dict[str, str],
    anchor: str,
    commits: list[dict],
    changes: dict[str, dict],
    pulls: dict[str, list[dict]],
    open_prs: list[dict],
    pr_files: dict[str, list[str]],
    branches: list[str],
    task: dict | None,
) -> dict:
    return {
        "run": {
            "id": run_id,
            "head_sha": head,
            "head_branch": "main",
            "conclusion": "failure" if gate_red else "success",
            "html_url": f"https://github.com/o/r/actions/runs/{run_id}",
            "name": "Required CI",
        },
        "jobs": jobs,
        "logs": logs,
        "anchor": anchor,
        "compare": {
            "status": "ahead",
            "total_commits": len(commits),
            "commits": commits,
        },
        "changes": changes,
        "pulls": pulls,
        "open_prs": open_prs,
        "pr_files": pr_files,
        "branches": branches,
        "task": task,
    }


CULPRIT = "1111111111111111111111111111111111111111"
OTHER = "2222222222222222222222222222222222222222"
HEAD = "3333333333333333333333333333333333333333"
ANCHOR = "0000000000000000000000000000000000000000"
TASK_ID = "b0b7fd34-5ab9-4662-aa8d-8bf391fea741"
TASK_LINK_TEXT = (
    "https://okcheese.com/projects/de808b13-ffd2-4b8a-9d1d-fba7babe389f"
    f"/topics/31095c05-3888-49a6-a448-2de40d1df78a/tasks/{TASK_ID}"
)


def _fixture_unique(fixing: bool) -> dict:
    """The widget batch: one commit changed the failing test's module, a later
    docs commit did not. `fixing` decides whether an open fix PR exists."""
    return _recorded(
        run_id=900001,
        head=HEAD,
        gate_red=True,
        jobs=[
            {"id": 11, "name": "backend / pure (1/2)", "conclusion": "failure", "html_url": "https://x/11"},
            {"id": 12, "name": "backend / pure (2/2)", "conclusion": "success", "html_url": "https://x/12"},
            {"id": 13, "name": "CI required", "conclusion": "failure", "html_url": "https://x/13"},
            {"id": 14, "name": "scope", "conclusion": "success", "html_url": "https://x/14"},
        ],
        logs={
            "11": (
                "2026-10-18T22:00:00.0000000Z =========================== short test summary info ============================\n"
                "2026-10-18T22:00:01.0000000Z FAILED tests/unit/test_widget.py::test_it_works - AssertionError: nope\n"
                "2026-10-18T22:00:02.0000000Z 1 failed, 3 passed in 1.2s\n"
            )
        },
        anchor=ANCHOR,
        commits=[
            {"sha": CULPRIT, "commit": {"message": "feat(widget): make the widget work (#42)"}},
            {"sha": HEAD, "commit": {"message": "docs: explain the widget (#43)"}},
        ],
        changes={
            CULPRIT: {
                "commit": {"message": f"feat(widget): make the widget work (#42)\n\nLink: {TASK_LINK_TEXT}\n"},
                "files": [
                    {"filename": "backend/app/domain/widget.py"},
                    {"filename": "backend/tests/unit/test_widget.py"},
                ],
                "author": {"login": "WangChangxin0809"},
            },
            HEAD: {
                "commit": {"message": "docs: explain the widget (#43)"},
                "files": [{"filename": "docs/widget.md"}],
                "author": {"login": "WangChangxin0809"},
            },
        },
        pulls={
            CULPRIT: [
                {
                    "number": 42,
                    "title": "feat(widget): make the widget work",
                    "user": {"login": "WangChangxin0809"},
                    "merged_at": "2026-10-18T20:00:00Z",
                    "merge_commit_sha": CULPRIT,
                    "head": {"ref": "task/aaaaaaaa"},
                    "body": f"Link: {TASK_LINK_TEXT}\n",
                }
            ]
        },
        open_prs=(
            [
                {
                    "number": 44,
                    "title": "fix(widget): keep the widget working",
                    "headRefName": "task/bbbbbbbb",
                    "user": {"login": "WangChangxin0809"},
                    "url": "https://github.com/o/r/pull/44",
                    "isDraft": False,
                    "updatedAt": "2026-10-18T22:30:00Z",
                }
            ]
            if fixing
            else []
        ),
        pr_files={"44": ["backend/app/domain/widget.py", "backend/tests/unit/test_widget.py"]} if fixing else {},
        branches=[],
        task={
            "data": {
                "number": 1977,
                "owner_handle": "wangchangxin",
                "agent_handle": "cheese-111111111111",
                "title": "修 widget",
                "status": "open",
            }
        },
    )


def _fixture_collision() -> dict:
    """Both commits touch the same module stem, so no one commit owns the red."""
    return _recorded(
        run_id=900003,
        head=OTHER,
        gate_red=True,
        jobs=[
            {"id": 31, "name": "backend / unit (1/2)", "conclusion": "failure", "html_url": "https://x/31"},
            {"id": 32, "name": "CI required", "conclusion": "failure", "html_url": "https://x/32"},
        ],
        logs={
            "31": (
                "2026-10-18T22:10:00.0000000Z FAILED tests/unit/test_ratchet.py::test_it_holds\n"
            )
        },
        anchor=ANCHOR,
        commits=[
            {"sha": CULPRIT, "commit": {"message": "test(ratchet): pin the rule (#51)"}},
            {"sha": OTHER, "commit": {"message": "refactor(ratchet): move the rule (#52)"}},
        ],
        changes={
            CULPRIT: {
                "commit": {"message": "test(ratchet): pin the rule (#51)"},
                "files": [{"filename": "backend/tests/unit/test_ratchet.py"}],
                "author": {"login": "WangChangxin0809"},
            },
            OTHER: {
                "commit": {"message": "refactor(ratchet): move the rule (#52)"},
                "files": [{"filename": "backend/app/domain/ratchet.py"}],
                "author": {"login": "WangChangxin0809"},
            },
        },
        pulls={},
        open_prs=[],
        pr_files={},
        branches=[],
        task=None,
    )


def _facts_from_fixture(fixture: dict) -> tuple[RunFacts, Batch, Source | None, TaskRef | None, Fix | None]:
    run = _run_of(fixture["run"])
    run = RunFacts(**{**asdict(run), "jobs": _jobs_of(fixture["jobs"])})
    logs = {int(key): text for key, text in fixture["logs"].items()}
    commits = _batch_from_compare(fixture["compare"], fixture["changes"])
    tests = _tests_in(logs)
    blamed = blame_of(tests, commits)
    batch = Batch(
        total=commits.total,
        commits=commits.changes,
        complete=commits.complete,
        anchor=fixture["anchor"],
        tests=tests,
        cases=_cases_in(logs),
        blamed=blamed,
        reason=_reason(tests, commits),
    )
    source = None
    task = None
    fix = None
    if blamed:
        raw = fixture["changes"][blamed]
        source = _source_from_raw(blamed, raw, fixture["pulls"].get(blamed, []))
        task = _task_from_raw(fixture["task"])
        commit = next(c for c in commits.changes if c.sha == blamed)
        fix = _fix_from_raw(
            fixture["open_prs"],
            fixture["pr_files"],
            fixture["branches"],
            tests,
            commit.files,
        )
    return run, batch, source, task, fix


def _source_from_raw(sha: str, raw: dict, pulls: list[dict]) -> Source:
    message = str((raw.get("commit") or {}).get("message") or "").strip()
    subject = message.splitlines()[0] if message else ""
    pr_raw = None
    if pulls:
        pr_raw = pulls[0]
    body = str((pr_raw or {}).get("body") or "")
    link = TASK_LINK.search(body) or TASK_LINK.search(message)
    return Source(
        sha=sha,
        subject=subject,
        pr=int(pr_raw["number"]) if pr_raw else None,
        pr_title=str((pr_raw or {}).get("title") or ""),
        pr_author=str(((pr_raw or {}).get("user") or {}).get("login") or ""),
        task_id=link.group(1) if link else None,
        task_link=link.group(0) if link else None,
        merged_at=str((pr_raw or {}).get("merged_at") or "") or None,
    )


def _task_from_raw(raw: dict | None) -> TaskRef | None:
    if not raw:
        return None
    row = _unwrap(raw)
    return TaskRef(
        id=str(row.get("id") or TASK_ID),
        number=row.get("number"),
        owner=row.get("owner_handle"),
        title=row.get("title"),
        link=None,
    )


def _fix_from_raw(
    open_prs: list[dict],
    pr_files: dict[str, list[str]],
    branches: list[str],
    tests: tuple[str, ...],
    files: tuple[str, ...],
) -> Fix:
    keywords = _keywords(tests, files)
    ordered = sorted(
        open_prs,
        key=lambda row: str(row.get("updatedAt") or ""),
        reverse=True,
    )
    named = [
        row
        for row in ordered
        if any(
            keyword
            in (str(row.get("title") or "") + " " + str(row.get("headRefName") or "")).lower()
            for keyword in keywords
        )
    ]
    candidates = (named or ordered)[:MAX_PR_FILE_READS]
    strong: list[int] = []
    medium: list[int] = []
    for row in candidates:
        number = int(row["number"])
        changed = tuple(pr_files.get(str(number), ()))
        if any(names_test(test, changed) for test in tests):
            strong.append(number)
        elif any(names_test(path, changed) for path in files):
            medium.append(number)
    picked = tuple(sorted(set(strong + medium)))
    if strong:
        detail = "开放 PR {} 改了失败的测试文件".format("、".join(f"#{n}" for n in strong))
    elif medium:
        detail = "开放 PR {} 改了 culprit 的同一批文件".format("、".join(f"#{n}" for n in medium))
    elif branches:
        detail = "同名分支 {}".format("、".join(branches))
    else:
        detail = "作者名下没有改了同一批文件的开放 PR 或同名分支"
    return Fix(
        underway=bool(strong or medium or branches),
        strong=bool(strong),
        prs=picked,
        branches=tuple(branches),
        detail=detail,
    )


def self_test() -> int:
    failures: list[str] = []
    cases = 0

    run_a, batch_a, source_a, task_a, fix_a = _facts_from_fixture(_fixture_unique(fixing=True))
    report_a = build_report(run_a, batch_a, source_a, task_a, fix_a)
    cases += 1
    if batch_a.blamed != CULPRIT:
        failures.append(f"唯一 culprit：得到 {batch_a.blamed}，应为 {CULPRIT}")
    if "作者在修" not in report_a.action or "#44" not in report_a.action:
        failures.append(f"作者在修：建议是 {report_a.action!r}")
    if "#1977" not in report_a.paragraph:
        failures.append("作者在修：段落里没有任务 #1977")
    if "test_widget.py::test_it_works" not in report_a.paragraph:
        failures.append("作者在修：段落里没有点名的用例")

    run_b, batch_b, source_b, task_b, fix_b = _facts_from_fixture(_fixture_unique(fixing=False))
    report_b = build_report(run_b, batch_b, source_b, task_b, fix_b)
    cases += 1
    if batch_b.blamed != CULPRIT:
        failures.append(f"无人修：culprit 得到 {batch_b.blamed}")
    if "叫回任务 #1977" not in report_b.action:
        failures.append(f"无人修：建议是 {report_b.action!r}")

    run_c, batch_c, source_c, task_c, fix_c = _facts_from_fixture(_fixture_collision())
    report_c = build_report(run_c, batch_c, source_c, task_c, fix_c)
    cases += 1
    if batch_c.blamed is not None:
        failures.append(f"对撞：culprit 应读不出来，得到 {batch_c.blamed}")
    if "对撞" not in report_c.blame_reason:
        failures.append(f"对撞：归因是 {report_c.blame_reason!r}")
    if "批内全部任务" not in report_c.action:
        failures.append(f"对撞：建议是 {report_c.action!r}")

    # Deliberate violation: deleting the author-is-fixing rule must change the
    # first scenario's verdict. If both readings come out the same, the rule is
    # not load-bearing and this self-test would stay green after it was removed.
    cases += 1
    violated = build_report(run_a, batch_a, source_a, task_a, fix_a, fixing_override=False)
    if violated.action == report_a.action:
        failures.append(
            "故意违规：「作者在修」判定去掉后结论没变，说明它没有被自测钉住"
        )
    if "叫回任务 #1977" not in violated.action:
        failures.append(f"故意违规：去掉判定后应落到叫回任务，得到 {violated.action!r}")

    # Attribution and its wording must agree: whenever `_blame` names a commit,
    # the stated reason has to say so, and the other way round.
    cases += 1
    if (batch_a.blamed is not None) != (batch_a.reason == "唯一 culprit"):
        failures.append("归因与理由不一致：唯一 culprit 的判定和措辞对不上")

    if failures:
        for failure in failures:
            print(f"FAIL {failure}", file=sys.stderr)
        return 1
    print(f"PASS: main-red-owner self-test ({cases} cases, offline)")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Who owns a red main batch, and whether a fix is underway."
    )
    parser.add_argument("target", nargs="?", help="main Required CI run id, or its head sha")
    parser.add_argument("--repo", help="owner/name (default: the checked-out repository)")
    parser.add_argument("--json", action="store_true", help="print the structured report")
    parser.add_argument("--self-test", action="store_true", help="run the offline self-test")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    if not args.target:
        parser.error("a run id or batch head sha is required (or --self-test)")
    try:
        repo = _repo_name(args.repo)
        report = collect(repo, args.target)
    except ReadError as exc:
        print(f"main-red-owner: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(asdict(report), ensure_ascii=False, indent=2))
    else:
        print(report.paragraph)
    return 0


if __name__ == "__main__":
    sys.exit(main())
