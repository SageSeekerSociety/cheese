#!/usr/bin/env python3
"""回答一批主干红归谁、作者在不在修。

    python3 .claude/scripts/main-red-owner.py <主干 run id>
    python3 .claude/scripts/main-red-owner.py <批 head sha>
    python3 .claude/scripts/main-red-owner.py --self-test

输入一条 `Required CI` 在 main 上的 run id，或者那一批的 head sha。输出一段可以直接
贴进 #1343 的中文：

1. 哪些 job 红了、日志里点了哪些测试用例；
2. 批内提交对撞后 culprits 是谁 —— 判据在本文件下方的「the attribution rule」
   一节（`_blame`）；
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

#: Jobs that only restate another job's verdict.
AGGREGATORS = frozenset(
    {
        "CI required",
        "CI required (draft quick)",
        "scope",
        "Fail fast",
        "Mark main green",
    }
)

#: Bounds for reading a batch: GitHub lists at most 250 commits in one compare
#: and 300 files in one commit, and past that nothing is pinned on a commit.
COMPARE_MAX_COMMITS = 250
COMMIT_MAX_FILES = 300
LOOK_BACK = 20

#: How many red jobs have their log read for failing file names: a diagnosis
#: wants the shape of the whole failure.
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


#: How a CI log line is cleaned before it is read: the runner's timestamp and
#: ANSI colours go, a token-shaped string is masked, and the line is capped.
#: The same rules as `backend/app/domain/review/github_pr/failure_logs.py`.
_LOG_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T[\d:.]+Z ")
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_TOKEN_SHAPE = re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{20,})")
_LOG_LINE_MAX_CHARS = 200


def clean_log_line(raw: str) -> str:
    line = _LOG_TIMESTAMP.sub("", raw.rstrip("\r\n"))
    line = _ANSI.sub("", line)
    return _TOKEN_SHAPE.sub("<已脱敏>", line)[:_LOG_LINE_MAX_CHARS]


# --- the attribution rule -----------------------------------------------------
#
# This repository's own triage of a red main batch: which commit of the batch
# the failing tests and diagnostics point at. It is repository tooling, so the
# rule lives here (the platform watches nothing after a merge).

@dataclass(frozen=True, slots=True)
class CommitChange:
    """One commit a batch covers, and what it changed."""

    sha: str
    subject: str
    files: tuple[str, ...]
    #: False when the forge may have listed only part of what the commit
    #: changed (GitHub stops at 300 files), so nothing can be pinned on it.
    complete: bool = True


@dataclass(frozen=True, slots=True)
class BatchCommits:
    """The commits a batch covers, as far as the forge would list them."""

    #: How many commits the batch covers, whether or not each was read.
    total: int
    #: The commits whose changes were read: the newest ones, when the forge
    #: refused to list them all.
    changes: tuple[CommitChange, ...]
    #: False when some commits were not read, so no failure can be pinned on
    #: one commit.
    complete: bool

#: A failed check-run's log names its failing tests in one of these shapes:
#: pytest `FAILED tests/x.py::test_y - …`, vitest/jest `FAIL  src/x.test.ts > …`.
_FAILED_LINE = re.compile(r"\b(?:FAILED|ERROR|FAIL)\b|✕")
_TEST_PATH = re.compile(
    r"(?<![\w/.-])((?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+"
    r"\.(?:py|pyi|ts|tsx|js|jsx|vue|go|rs|java))"
)

#: Ruff's full/concise locations, Pyright, tsc/vue-tsc, and Go vet errors.
_SOURCE_FILE = r"[^\s:()]+\.(?:py|pyi|[cm]?[jt]sx?|vue|css|scss|go)"
_STATIC_DIAGNOSTIC = re.compile(
    rf"^\s*(?:-->\s+)?({_SOURCE_FILE})(?:"
    r":\d+:\d+(?:\s*$|:\s*[A-Z]+\d+\b|\s+-\s+error:)"
    r"|\(\d+,\d+\):\s*error TS\d+\b"
    r"|(?<=\.go):\d+:\d+:\s+\S"
    r"|:\d+:\d+\s{2,}\S|:\d+\s+\S"
    r"|:\s+(?:[B-Z]|A -> [B-Z])\s*$)"
)
#: Grouped diagnostics put the path above location/severity or issue-label rows.
_DIAGNOSTIC_FILE = re.compile(rf"^\s*({_SOURCE_FILE})\s*$")
_GROUPED_ERROR = re.compile(r"^\s+(?:\d+:\d+\s+error\s|\d+:\s+\[[^]]+\]\s)")
_TAGGED_DIAGNOSTIC = re.compile(r"^\s+(?:NEW|BASELINE GREW):\s+(.+)$")

def _failed_paths(log: str, *, tests_only: bool = False) -> list[str]:
    """Files named by test failures and static diagnostics in a job log.

    Test probes must use ``tests_only``: rerunning a test file cannot judge a
    lint or type error in it.
    """
    found: list[str] = []
    diagnostic_file: str | None = None
    failure_report = False
    for raw in log.splitlines():
        line = clean_log_line(raw)
        if _FAILED_LINE.search(line):
            found += [match.group(1) for match in _TEST_PATH.finditer(line)]
            failure_report = True
        elif not line.strip():
            failure_report = False
        if tests_only:
            continue
        diagnostic = _STATIC_DIAGNOSTIC.match(line)
        if diagnostic:
            found.append(diagnostic.group(1))
        tagged = _TAGGED_DIAGNOSTIC.match(line) if failure_report else None
        if tagged:
            found += [match.group(1) for match in _TEST_PATH.finditer(tagged.group(1))]
        header = _DIAGNOSTIC_FILE.fullmatch(line)
        if header:
            diagnostic_file = header.group(1)
            if diagnostic_file and diagnostic_file.endswith(".go"):
                # gofmt -l reports an unformatted file as a bare path.
                found.append(diagnostic_file)
        elif diagnostic_file and _GROUPED_ERROR.match(line):
            found.append(diagnostic_file)
        elif not re.match(r"^\s+\d+:\d+\s+warning\s", line):
            diagnostic_file = None
    return found

#: A completed check that went red. `cancelled` is not one: a newer push to
#: the branch cancels the run it superseded.
_FAILED = frozenset(
    {"failure", "timed_out", "startup_failure", "action_required", "stale"}
)

def _blame(tests: tuple[str, ...], commits: BatchCommits | None) -> str | None:
    """The one commit the failed tests point at, or None when they point at no
    commit, at more than one, or at files the forge did not list whole."""
    if not tests or commits is None:
        return None
    if not commits.complete or not all(c.complete for c in commits.changes):
        return None
    owners: set[str] | None = None
    for test in tests:
        matched = {c.sha for c in commits.changes if _names(test, c.files)}
        if len(matched) != 1:
            return None
        owners = matched if owners is None else owners & matched
        if not owners:
            return None
    return next(iter(owners)) if owners and len(owners) == 1 else None


#: Extensions that name the same kind of code. `test_foo.py` is about
#: `foo.py` in this monorepo; a `.test.ts` next to a `.py` of the same name is
#: a coincidence, and matching it would blame the wrong line.
_FAMILIES = (
    frozenset({"py", "pyi"}),
    frozenset({"ts", "tsx", "js", "jsx", "vue"}),
)


def _names(test_path: str, files: tuple[str, ...]) -> bool:
    """Whether a failing test file is one this commit's changes are about."""
    want = test_path.removeprefix("./")
    for path in files:
        changed = path.removeprefix("./")
        if (
            changed == want
            or changed.endswith("/" + want)
            or want.endswith("/" + changed)
        ):
            return True
    stem = _stem(want)
    if not stem:
        return False
    family = _family(want)
    return any(_family(path) == family and _stem(path) == stem for path in files)


def _family(path: str) -> frozenset[str]:
    extension = path.rsplit(".", 1)[-1].lower()
    for family in _FAMILIES:
        if extension in family:
            return family
    return frozenset({extension})


def _stem(path: str) -> str:
    """`backend/tests/unit/test_pr_poll.py` and
    `backend/app/domain/review/pr_poll.py` share `pr_poll`: a test file
    failing is about the module it tests."""
    name = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    name = name.removeprefix("test_").removesuffix("_test")
    return name.removesuffix(".test")


failed_paths = _failed_paths
FAILED = _FAILED
blame_of = _blame
names_path = _names
stem_of = _stem


# ---------------------------------------------------------------------------
# GitHub reads
# ---------------------------------------------------------------------------


def _run_real(
    argv: list[str], *, timeout: int = 120, cwd: Path | None = None
) -> str:
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            check=False,
            cwd=cwd,
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


def _gh_json_real(argv: list[str], *, timeout: int = 120):
    return _json_of(_run_real(["gh", *argv], timeout=timeout))


def _api_real(repo: str, suffix: str, params: dict | None = None):
    path = f"/repos/{urllib.parse.quote(repo, safe='/')}{suffix}"
    if params:
        path += "?" + urllib.parse.urlencode(params)
    return _json_of(_run_real(["gh", "api", path]))


class _LiveReads:
    """Every read the report makes, behind one object.

    The self-test swaps a recorded implementation in for this one and calls
    `collect` for real: a mutation in the live path then shows up as a red
    self-test, instead of being hidden behind a hand-written copy of it.
    """

    def api(self, repo: str, suffix: str, params: dict | None = None):
        return _api_real(repo, suffix, params)

    def gh_json(self, argv: list[str], *, timeout: int = 120):
        return _gh_json_real(argv, timeout=timeout)

    def run(
        self, argv: list[str], *, timeout: int = 120, cwd: Path | None = None
    ) -> str:
        return _run_real(argv, timeout=timeout, cwd=cwd)

    def platform_token(self) -> str | None:
        return _platform_token_real()

    def platform_call(self, method: str, path: str, body: dict | None, token: str | None = None):
        return _platform_call_real(method, path, body, token)


READ = _LiveReads()


def _run(argv: list[str], *, timeout: int = 120, cwd: Path | None = None) -> str:
    return READ.run(argv, timeout=timeout, cwd=cwd)


def _gh_json(argv: list[str], *, timeout: int = 120):
    return READ.gh_json(argv, timeout=timeout)


def _api(repo: str, suffix: str, params: dict | None = None):
    return READ.api(repo, suffix, params)


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
    status: str | None
    conclusion: str | None
    url: str
    jobs: tuple[Job, ...]
    created_at: str = ""


def _run_of(raw: dict) -> RunFacts:
    return RunFacts(
        id=int(raw["id"]),
        head=str(raw.get("head_sha") or ""),
        branch=str(raw.get("head_branch") or ""),
        status=raw.get("status"),
        conclusion=raw.get("conclusion"),
        url=str(raw.get("html_url") or ""),
        jobs=(),
        created_at=str(raw.get("created_at") or ""),
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
    """Each red leaf job's log, bounded to the platform's 8 MiB download limit."""
    logs: dict[int, str] = {}
    for job in _red_leaf_jobs(jobs)[:RED_LOGS]:
        if not job.id:
            continue
        try:
            path = f"/repos/{urllib.parse.quote(repo, safe='/')}/actions/jobs/{job.id}/logs"
            logs[job.id] = _run(["gh", "api", path], timeout=120)[:8 * 1024 * 1024]
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
    paths: tuple[str, ...]
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
            line = clean_log_line(raw)
            match = _PYTEST_CASE.search(line)
            if match:
                found.append(match.group(1))
                continue
            match = _VITEST_CASE.search(line)
            if match:
                found.append(f"{match.group(1)} > {match.group(2)}")
    return tuple(_dedupe(found)[:MAX_CASES])


def _dedupe(items) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _paths_in(logs: dict[int, str]) -> tuple[str, ...]:
    found: list[str] = []
    for text in logs.values():
        found += failed_paths(text)
    return tuple(sorted(set(found)))


#: `_reason`'s answers. `_action` switches on these names, never on a
#: substring of the sentence: it did once, and `没能读全` did not contain
#: `读不全`, so an unread batch was reported to the channel as a collision.
NO_PATHS = "日志里没有点名失败文件"
UNREAD = "批内提交没能读全，不能钉在单个提交上"
UNMATCHED = "有失败的文件不在任何批内提交的改动里"
SHARED = "同一个失败文件被批内多个提交改到（对撞）"
SPLIT = "失败文件分别指向不同提交（对撞）"
UNIQUE = "唯一 culprit"


def _reason(paths: tuple[str, ...], commits: BatchCommits) -> str:
    """Why `_blame` can or cannot name one commit. The matching itself is
    `_names`; this only names the answer."""
    if not paths:
        return NO_PATHS
    if not commits.complete or any(not c.complete for c in commits.changes):
        return UNREAD
    matches = [(path, [c.sha for c in commits.changes if names_path(path, c.files)]) for path in paths]
    if any(not hit for _, hit in matches):
        return UNMATCHED
    if any(len(hit) > 1 for _, hit in matches):
        return SHARED
    common = set(matches[0][1])
    for _, hit in matches[1:]:
        common &= set(hit)
    if len(common) == 1:
        return UNIQUE
    return SPLIT


def _batch(repo: str, head: str, logs: dict[int, str], *, before: str = "") -> Batch:
    paths = _paths_in(logs)
    anchor = _main_green(repo)
    compare = _compare(repo, anchor, head) if anchor else None
    if compare is None:
        # main-green may have advanced past a historical run. Use a green
        # run from before this one rather than today's newest green run.
        try:
            runs = _api(
                repo,
                "/actions/workflows/required-ci.yml/runs",
                {
                    "branch": _default_branch(repo),
                    "status": "success",
                    "per_page": 1,
                    **({"created": f"<{before}"} if before else {}),
                },
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
    blamed = blame_of(paths, commits)
    return Batch(
        total=commits.total,
        commits=commits.changes,
        complete=commits.complete,
        anchor=anchor,
        paths=paths,
        cases=_cases_in(logs),
        blamed=blamed,
        reason=_reason(paths, commits),
    )


@dataclass(frozen=True, slots=True)
class Source:
    sha: str
    subject: str
    pr: int | None
    pr_title: str
    pr_author: str
    commit_author: str
    pr_head: str | None
    task_id: str | None
    task_link: str | None
    merged_at: str | None


def _commit_author(raw_commit: dict) -> str:
    """The GitHub login when the commit has one, else the git author name."""
    login = (raw_commit.get("author") or {}).get("login")
    if login:
        return str(login)
    return str(((raw_commit.get("commit") or {}).get("author") or {}).get("name") or "")


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
        commit_author=_commit_author(raw_commit),
        pr_head=str(((pr_raw or {}).get("head") or {}).get("ref") or "") or None,
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


def _platform_token_real() -> str | None:
    token = os.environ.get("OKCHEESE_TOKEN")
    if token:
        return token
    user = os.environ.get("OKCHEESE_USER")
    password = os.environ.get("OKCHEESE_PASS")
    if not (user and password):
        return None
    try:
        answer = _platform_call_real(
            "POST", "/users/auth/login", {"username": user, "password": password}
        )
        return str(_unwrap(answer)["accessToken"])
    except Exception:
        return None


def _platform_call_real(method: str, path: str, body: dict | None, token: str | None = None):
    api = os.environ.get("OKCHEESE_API", "https://okcheese.com/api")
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(api + path, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def _platform_token() -> str | None:
    return READ.platform_token()


def _platform_call(method: str, path: str, body: dict | None, token: str | None = None):
    return READ.platform_call(method, path, body, token)


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


def _keywords(paths: tuple[str, ...], files: tuple[str, ...]) -> tuple[str, ...]:
    stems = {stem_of(path).lower() for path in (*paths, *files)}
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


def _branches(
    keywords: tuple[str, ...], exclude: tuple[str, ...] = ()
) -> tuple[str, ...]:
    try:
        text = _run(
            ["git", "ls-remote", "--heads", "origin"],
            timeout=30,
            cwd=REPO_ROOT,
        )
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
        if name not in exclude
        and any(keyword in name.lower() for keyword in keywords)
    )[:5]


def _fix(
    repo: str,
    author: str,
    paths: tuple[str, ...],
    files: tuple[str, ...],
    pr_head: str | None = None,
) -> Fix:
    if not author:
        return Fix(False, False, (), (), "没有可查的作者")
    keywords = _keywords(paths, files)
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
        if any(names_path(path, changed) for path in paths):
            strong.append(number)
        elif any(names_path(path, changed) for path in files):
            medium.append(number)
    branches = _branches(keywords, (pr_head,) if pr_head else ())
    picked = tuple(sorted(set(strong + medium)))
    if strong:
        detail = "开放 PR {} 改到了失败文件对应的模块".format(
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
    paths: tuple[str, ...]
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
        if reason == NO_PATHS:
            return "日志没点名失败文件：叫回批内全部任务逐条自证，并要求失败 job 补打诊断文件路径"
        if reason == UNREAD:
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
    batch_text = (
        f"批内 {report.total} 个提交" if report.total else "批内提交数未读出"
    )
    lines.append(
        f"主干红批 run {report.run_id}（head {head}，"
        f"{batch_text}，结论 {report.conclusion or '未完成'}）"
    )
    if report.failed_jobs:
        shown = "、".join(job["name"] for job in report.failed_jobs[:6])
        more = " 等" if len(report.failed_jobs) > 6 else ""
        lines.append(f"失败 job：{shown}{more}（{len(report.failed_jobs)} 个）")
    if report.cases:
        shown = "；".join(report.cases[:6])
        more = f" 等 {len(report.cases)} 个" if len(report.cases) > 6 else ""
        lines.append(f"失败用例：{shown}{more}")
    elif report.paths:
        lines.append("失败文件：" + "、".join(report.paths))
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
            authors = []
            if source.commit_author:
                authors.append(f"提交作者 {source.commit_author}")
            if source.pr_author and source.pr_author != source.commit_author:
                authors.append(f"PR 作者 {source.pr_author}")
            if authors:
                who += "（" + "，".join(authors) + "）"
            if report.task and report.task.get("number"):
                who += f" · 任务 #{report.task['number']}"
                if report.task.get("owner"):
                    who += f"（owner {report.task['owner']}）"
            elif report.task and report.task.get("link"):
                who += f" · 任务 {report.task['link']}"
            lines.append(f"来源：{who}")
        if report.fix:
            lines.append(f"修复线索：{report.fix['detail']}")
    else:
        shown = "、".join(c.sha[:8] for c in batch.commits[:10])
        suffix = " 等" if len(batch.commits) > 10 else ""
        if shown:
            lines.append(f"来源：无单一 culprit；批内提交 {shown}{suffix}")
        else:
            lines.append("来源：无单一 culprit；批内提交没读出来")
    lines.append(f"建议：{report.action}")
    return "\n".join(lines)


def build_report(
    run: RunFacts,
    batch: Batch,
    source: Source | None,
    task: TaskRef | None,
    fix: Fix | None,
) -> Report:
    """Turn already-read facts into the paragraph."""
    red = _red_leaf_jobs(run.jobs)
    action = _action(batch.blamed, batch.reason, source, task, fix)
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
        paths=batch.paths,
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
    if run.status != "completed":
        red = _red_leaf_jobs(run.jobs)
        shown = "、".join(job.name for job in red) or "还没有红的 job"
        action = f"这批还没跑完（{run.status or '未知状态'}）：等它出结论再归因"
        return Report(
            run_id=run.id,
            run_url=run.url,
            head=run.head,
            conclusion=run.conclusion,
            total=0,
            complete=False,
            failed_jobs=tuple(
                {"name": job.name, "conclusion": job.conclusion, "url": job.url}
                for job in red
            ),
            cases=(),
            paths=(),
            blamed=None,
            blame_reason="这批还没跑完",
            source=None,
            task=None,
            fix=None,
            action=action,
            paragraph=(
                f"run {run.id}（head {run.head[:8]}）还没跑完"
                f"（{run.status or '未知状态'}）；已红 job：{shown}\n"
                f"建议：{action}"
            ),
        )
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
    batch = _batch(repo, run.head, logs, before=run.created_at)
    source = None
    task = None
    fix = None
    if batch.blamed:
        commit = next((c for c in batch.commits if c.sha == batch.blamed), None)
        try:
            raw = _api(repo, f"/commits/{batch.blamed}")
        except ReadError:
            raw = None
        if raw is not None:
            source = _source(repo, batch.blamed, raw)
            task = _task(source)
            fix = _fix(
                repo,
                source.pr_author or source.commit_author,
                batch.paths,
                commit.files if commit else (),
                source.pr_head,
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
            "status": "completed",
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


class _RecordedReads:
    """The fixture's answers to every read the live path makes.

    Same seam as `_LiveReads`, so `collect` itself runs against it: the
    self-test exercises `load_run`, `_batch`, `_source`, `_task` and `_fix`,
    and a change to any of them is a change to what the self-test asserts.
    """

    def __init__(self, fixture: dict):
        self.f = fixture

    def api(self, repo: str, suffix: str, params: dict | None = None):
        f = self.f
        if suffix == "":
            return {"default_branch": "main"}
        if suffix == f"/actions/runs/{f['run']['id']}":
            return f["run"]
        if suffix == f"/actions/runs/{f['run']['id']}/jobs":
            return {"jobs": f["jobs"]}
        if suffix == "/git/ref/heads/main-green":
            return {"object": {"sha": f.get("current_green", f["anchor"])}}
        if suffix == "/actions/workflows/required-ci.yml/runs":
            if params and params.get("created") == f"<{f['run'].get('created_at')}":
                return {"workflow_runs": [{"head_sha": f["anchor"]}]}
            return {"workflow_runs": [{"head_sha": f.get("current_green", f["anchor"])}]}
        if suffix.startswith("/compare/"):
            if f.get("current_green") and suffix.startswith(f"/compare/{f['current_green']}..."):
                return {"status": "behind", "commits": [], "total_commits": 0}
            return f["compare"]
        if suffix.startswith("/commits/"):
            rest = suffix[len("/commits/") :]
            if rest.endswith("/pulls"):
                return f["pulls"].get(rest[: -len("/pulls")], [])
            return f["changes"].get(rest)
        if suffix.startswith("/pulls/"):
            rest = suffix[len("/pulls/") :]
            if rest.endswith("/files"):
                number = rest[: -len("/files")]
                return [
                    {"filename": name} for name in f["pr_files"].get(number, [])
                ]
            for pulls in f["pulls"].values():
                for pull in pulls:
                    if str(pull["number"]) == rest:
                        return pull
            return None
        raise ReadError(f"fixture has no answer for {suffix}")

    def gh_json(self, argv: list[str], *, timeout: int = 120):
        if argv[:2] == ["pr", "list"]:
            return self.f["open_prs"]
        raise ReadError(f"fixture has no answer for gh {' '.join(argv)}")

    def run(
        self, argv: list[str], *, timeout: int = 120, cwd: Path | None = None
    ) -> str:
        if argv[:2] == ["gh", "api"]:
            path = argv[2]
            if "/actions/jobs/" in path:
                job = path.split("/actions/jobs/", 1)[1].split("/", 1)[0]
                return self.f["logs"].get(job, "")
        if argv[:2] == ["git", "ls-remote"]:
            return "".join(
                f"{OTHER}\trefs/heads/{name}\n" for name in self.f["branches"]
            )
        raise ReadError(f"fixture has no answer for {' '.join(argv)}")

    def platform_token(self) -> str | None:
        return "self-test"

    def platform_call(self, method: str, path: str, body: dict | None, token: str | None = None):
        return self.f["task"]


def _collect_from_fixture(fixture: dict) -> Report:
    """Run the live collector against a recorded fixture. No network."""
    global READ
    was = READ
    READ = _RecordedReads(fixture)
    try:
        return collect("o/r", str(fixture["run"]["id"]))
    finally:
        READ = was


#: The log parser's cases: one per tool whose failure names a file.
_PARSER_CASES = (
    (
        "ruff-full",
        "2026-10-19T02:53:15.7Z F401 [*] `uuid` imported but unused\n"
        "2026-10-19T02:53:15.8Z   --> tests/integration/test_app_sign_in.py:15:8\n",
        ["tests/integration/test_app_sign_in.py"],
    ),
    (
        "ruff-concise",
        "2026-10-19T02:53:25.1Z tests/integration/test_app_sign_in.py:15:24: "
        "F401 [*] `sqlalchemy.select` imported but unused\n",
        ["tests/integration/test_app_sign_in.py"],
    ),
    (
        "pyright",
        "2026-10-19T02:53:25.1Z /w/backend/app/main.py\n"
        "2026-10-19T02:53:25.2Z   /w/backend/app/main.py:15:24"
        ' - \x1b[31merror\x1b[0m: "missing" is not defined (reportUndefinedVariable)\n',
        ["/w/backend/app/main.py"],
    ),
    (
        "vue-tsc",
        "2026-10-19T02:53:25.1Z src/views/SignIn.vue(28,12): error TS2339: "
        "Property 'missing' does not exist on type '{}'.\n",
        ["src/views/SignIn.vue"],
    ),
    (
        "go-vet",
        "2026-10-19T02:53:25.1Z # command-line-arguments\n"
        "2026-10-19T02:53:25.2Z ./go-diagnostic.go:5:28: fmt.Printf format %d "
        'has arg "not a number" of wrong type string\n',
        ["./go-diagnostic.go"],
    ),
    (
        "gofmt",
        "2026-10-19T02:53:25.1Z   internal/device/device.go\n"
        "2026-10-19T02:53:25.2Z ##[error]gofmt -w the files above\n",
        ["internal/device/device.go"],
    ),
    (
        "eslint-stylish",
        "2026-10-19T02:53:25.1Z /w/frontend/src/SignIn.vue\n"
        "2026-10-19T02:53:25.2Z   10:5  error  'unused' is assigned a value but never used"
        "  @typescript-eslint/no-unused-vars\n"
        "2026-10-19T02:53:25.4Z \n"
        "2026-10-19T02:53:25.5Z /w/frontend/src/Warning.vue\n"
        "2026-10-19T02:53:25.6Z   12:3  warning  Missing prop  vue/require-default-prop\n",
        ["/w/frontend/src/SignIn.vue"],
    ),
    (
        "i18n-grouped",
        "2026-10-19T02:53:25.1Z src/views/SignIn.vue\n"
        "2026-10-19T02:53:25.2Z   2: [Chinese outside the catalog] <span>确定</span>\n"
        "2026-10-19T02:53:25.3Z \n",
        ["src/views/SignIn.vue"],
    ),
    (
        "tagged",
        "2026-10-19T02:53:25.1Z FAIL: new debt cannot be frozen.\n"
        "2026-10-19T02:53:25.2Z     NEW: src/views/SignIn.vue\n"
        "2026-10-19T02:53:25.3Z     BASELINE GREW: src/views/SignIn.vue -> src/views/Form.vue\n",
        ["src/views/Form.vue", "src/views/SignIn.vue"],
    ),
    (
        "warnings-and-commands",
        "2026-10-19T02:53:25.1Z   /repo/backend/app/main.py:15:24 - warning: unused\n"
        "2026-10-19T02:53:25.2Z Run ruff check backend/app/main.py\n",
        [],
    ),
)


def self_test() -> int:
    failures: list[str] = []
    cases = 0

    report_a = _collect_from_fixture(_fixture_unique(fixing=True))
    cases += 1
    if report_a.blamed != CULPRIT:
        failures.append(f"唯一 culprit：得到 {report_a.blamed}，应为 {CULPRIT}")
    if "作者在修" not in report_a.action or "#44" not in report_a.action:
        failures.append(f"作者在修：建议是 {report_a.action!r}")
    if "#1977" not in report_a.paragraph:
        failures.append("作者在修：段落里没有任务 #1977")
    if "test_widget.py::test_it_works" not in report_a.paragraph:
        failures.append("作者在修：段落里没有点名的用例")
    # The live `_fix` is what put `underway` in the report: removing that rule
    # turns the assertion above red, which is this case's link to the code.
    if not report_a.fix or report_a.fix.get("underway") is not True:
        failures.append("故意违规钉不住：live _fix 没把「作者在修」收进报告")

    report_b = _collect_from_fixture(_fixture_unique(fixing=False))
    cases += 1
    if report_b.blamed != CULPRIT:
        failures.append(f"无人修：culprit 得到 {report_b.blamed}")
    if "叫回任务 #1977" not in report_b.action:
        failures.append(f"无人修：建议是 {report_b.action!r}")

    # A static failure can precede a long collection listing, and today's
    # green mark can be newer than the run being diagnosed.
    cases += 1
    historical = _fixture_unique(fixing=False)
    historical["run"]["created_at"] = "2026-10-18T22:00:00Z"
    historical["current_green"] = "4444444444444444444444444444444444444444"
    historical["logs"]["11"] = (
        "2026-10-18T22:00:01Z F401 [*] `uuid` imported but unused\n"
        "2026-10-18T22:00:02Z   --> tests/unit/test_widget.py:15:8\n"
        + "2026-10-18T22:00:03Z tests/unit/test_other.py::test_ok\n" * 12000
    )
    report_historical = _collect_from_fixture(historical)
    if report_historical.blamed != CULPRIT or report_historical.total != 2:
        failures.append(f"历史静态失败：culprit={report_historical.blamed}，批内={report_historical.total}")
    if report_historical.paths != ("tests/unit/test_widget.py",):
        failures.append(f"历史静态失败：路径={report_historical.paths}")

    report_c = _collect_from_fixture(_fixture_collision())
    cases += 1
    if report_c.blamed is not None:
        failures.append(f"对撞：culprit 应读不出来，得到 {report_c.blamed}")
    if "对撞" not in report_c.blame_reason:
        failures.append(f"对撞：归因是 {report_c.blame_reason!r}")
    if "批内全部任务" not in report_c.action:
        failures.append(f"对撞：建议是 {report_c.action!r}")

    # A batch nobody could read whole is manual triage, not a collision: the
    # reason and the action are separate names so they cannot drift apart.
    cases += 1
    unread = _action(None, UNREAD, None, None, None)
    if "人工分诊" not in unread:
        failures.append(f"读不全：建议是 {unread!r}")

    # A run still going has `conclusion=None`; it must not be reported as
    # "nothing to blame" while a job is already red.
    cases += 1
    running = _fixture_unique(fixing=True)
    running["run"]["status"] = "in_progress"
    running["run"]["conclusion"] = None
    report_running = _collect_from_fixture(running)
    if "还没跑完" not in report_running.action:
        failures.append(f"还在跑：建议是 {report_running.action!r}")
    if "没有要归的责" in report_running.paragraph:
        failures.append("还在跑：段落说成了没有要归的责")

    # The log parser, case by case: what each tool's failure names.
    for name, log, expected in _PARSER_CASES:
        cases += 1
        found = sorted(set(failed_paths(log)))
        if found != expected:
            failures.append(f"解析 {name}：得到 {found}，应为 {expected}")
        if failed_paths(log, tests_only=True):
            failures.append(f"解析 {name}：静态诊断被当成了测试失败")

    # Attribution: a batch not read whole names nobody, however plain the match.
    cases += 1
    partial = BatchCommits(
        total=30,
        changes=(CommitChange("b", "feat: b", ("backend/a.py",)),),
        complete=False,
    )
    if blame_of(("backend/tests/test_a.py",), partial) is not None:
        failures.append("没读全的一批：仍然点了名")
    cases += 1
    whole = BatchCommits(
        total=2,
        changes=(
            CommitChange("b", "feat: b", ("backend/a.py",)),
            CommitChange("c", "feat: c", ("backend/c.py",)),
        ),
        complete=True,
    )
    if blame_of(("backend/tests/test_a.py",), whole) != "b":
        failures.append("读全的一批：没点出唯一改了它的提交")
    cases += 1
    if not names_path("src/views/SignIn.vue", ("frontend/src/views/SignIn.vue",)):
        failures.append("前端诊断：仓库相对路径对不上")
    if not names_path("internal/device/device.go", ("cli/internal/device/device.go",)):
        failures.append("CLI 诊断：仓库相对路径对不上")

    # Every red conclusion counts, not only `failure`; a cancelled job does not.
    cases += 1
    red = _red_leaf_jobs(
        tuple(
            Job(i, f"job {conclusion}", conclusion, "")
            for i, conclusion in enumerate(
                ("failure", "timed_out", "startup_failure", "cancelled", "success"),
                start=1,
            )
        )
    )
    if sorted(job.conclusion for job in red) != ["failure", "startup_failure", "timed_out"]:
        failures.append(f"红的 job：得到 {[job.conclusion for job in red]}")

    # Attribution and its wording must agree: whenever `_blame` names a commit,
    # the stated reason has to say so, and the other way round.
    cases += 1
    if (report_a.blamed is not None) != (report_a.blame_reason == UNIQUE):
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
