"""Local static fast check: pick pre-commit hooks by merge-diff scope and run them.

WHY THIS EXISTS: Required CI takes ~8 minutes of wall time before the first
useful signal, and obvious failures (a lint error, an over-cap file) only
surface there. This entry point gives an agent or a developer that signal
before a PR is opened. It is v1 and covers STATIC CHECKS ONLY — pytest,
vue-tsc/build and e2e are deliberately out of scope (they need Postgres or
OOM a small box); the summary always lists what did NOT run.

WHAT THIS REUSES (it is a hook-ID selector, not a second scheduler):
- The scope decision comes from required-ci.py's select() and
  required-ci-paths.json — the same single source the Required CI gate uses.
  CI selection semantics are not changed by this script.
- The check commands come from .pre-commit-config.yaml: hooks are invoked by
  ID through the pre-commit framework, so the command table lives in exactly
  one place.

DIFF COVERAGE: committed base..HEAD, staged, unstaged and untracked files,
deletions, and renames on both sides (--no-renames reports the old and the
new path). Untracked files are not only selected on — they are CHECKED: the
hooks here are full-tree commands (`ruff check .`, eslint, file-size) that
read the working tree, untracked content included (verified empirically:
an untracked file with a lint error fails the ruff hook). The report
records base, head, and a CONTENT fingerprint of the dirty worktree —
never just HEAD, and never just path names.

--base DIVERGENCE: --base changes selection only. The file-size and
migration-fork hooks take a --base flag, but the pre-commit command table
invokes them without one, so they always judge origin/main. Judging the
wrong base can invert a verdict (a file that shrank on main but grew
against the PR's real base reads as a shrink), so with a custom --base
these hooks are NOT run and the run exits 2 (cannot verify) — a warning
alone would not verify the base the user asked for.

SKIP IS NOT A PASS: pre-commit returns 0 for a hook skipped via the SKIP
environment variable — without running it. Each hook runs as its own
process here, so its status line is unambiguous: a "Skipped" result is
recorded as skipped (never pass) and makes the run exit 2; "(no files to
check)" is recorded as not_applicable in not_run — legitimate, but never
counted as an execution. If every selected hook ends up skipped, the run
exits 2: zero executed checks is not a pass. The invocation pins
--color never (the CLI flag beats PRE_COMMIT_COLOR=always) and ANSI
escapes are stripped before matching, so a color-forced SKIP — where
"Skipped" carries a trailing reset code — cannot blind the detection.

CONSERVATIVE FALLBACK: with no merge base (shallow clone, missing
origin/main), the selector falls back to selecting everything — but checks
that themselves need the merge base (file-size) then CANNOT judge, and the
run exits 2 ("cannot verify"), not 0. An unknown path (one no suite pattern
owns) widens the scope to backend+frontend; it never narrows to guards only.

EXIT CODES (machine-readable, mirrored in the JSON report's status):
  0 pass    — every selected static check ran and passed. NOT a full-CI pass.
  1 fail    — a selected check ran and failed.
  2 unknown — cannot verify: no merge base after fallback, a hook timed out,
              the run was interrupted, a mapped hook ID is missing from
              .pre-commit-config.yaml, a selected hook was SKIPPED (SKIP
              env), a custom --base cannot be honored by the base-judging
              hooks, or zero selected hooks actually executed.
  3 blocked — the environment cannot run the checks: pre-commit/uv missing,
              backend/.venv or frontend/node_modules absent. This script
              never installs anything; the message names the setup command.

REPORT: JSON at <gitdir>/ci-fast-report.json (worktree-aware; override with
--report). Any stale report is deleted before the first hook runs, so a
killed run leaves NO report — and no report means "not passed". Green
requires the report to hit disk: if the write fails, a passing run is
downgraded to 2 (cannot verify); a failing run keeps its original code.

OPTIONAL TYPE CHECKS (--types): adds pyright and vue-tsc via their manual-
stage hooks. They are off by default because of memory footprint and layer
(they are type-level, not lint-level), not because they are slow — vue-tsc
is actually faster than eslint here. Without them this layer misses
type-level regressions such as the missing-prop-type class fixed around
37b97d38; run `task ci:fast -- --types` when a change touches component
props or shared types.

OPTIONAL PRE-PUSH HOOK (documentation only — nothing is installed by
default): to run this before every push, `printf '#!/bin/sh\nexec task ci:fast\n' > .git/hooks/pre-push && chmod +x .git/hooks/pre-push`.
Agents: run `task ci:fast` before opening a PR; behavioral regressions are
still owned by targeted tests plus full CI.

ENVIRONMENT: each worktree keeps its own backend/.venv and
frontend/node_modules — only uv's and pnpm's CONTENT caches are shared, and
those are content-addressed. This script does no result caching: a green
line always means the check just ran. CI_FAST_PRECOMMIT overrides the
pre-commit invocation (default "uvx pre-commit") for tests.
"""

import fnmatch
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time

REPO_ROOT = subprocess.check_output(
    ["git", "rev-parse", "--show-toplevel"], text=True
).strip()
# In a linked worktree .git is a FILE (a gitdir pointer), so the report cannot
# live at <root>/.git — ask git for the real per-worktree git dir.
GIT_DIR = subprocess.check_output(
    ["git", "rev-parse", "--git-dir"], text=True, cwd=REPO_ROOT
).strip()
if not os.path.isabs(GIT_DIR):
    GIT_DIR = str(Path(REPO_ROOT, GIT_DIR))

PASS, FAIL, UNKNOWN, BLOCKED = 0, 1, 2, 3

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

# suite -> pre-commit hook IDs, static layer only. The commands behind these
# IDs live in .pre-commit-config.yaml; this map is selection, not definition.
FAST_HOOKS = {
    "backend": ["ruff", "ruff-format", "boundary-check", "deferred-imports", "migration-fork"],
    "frontend": ["eslint", "stylelint", "boundary-check-frontend", "scene-ratchet"],
    "guards": ["repo-rules", "action-pins", "manual-anchors", "file-size"],
}
TYPES_HOOKS = {"backend": ["pyright"], "frontend": ["frontend-typecheck"]}

# Checks that need the merge base to judge at all. Both scripts accept a
# --base flag, but the pre-commit command table never passes one, so they
# always judge origin/main: with no merge base they cannot judge, and with
# a custom --base they would judge the WRONG base — either way the run
# cannot verify (exit 2), it must not pass on their say-so.
MERGE_BASE_HOOKS = {"file-size", "migration-fork"}

# What the static layer never runs, with the honest next step. Always listed
# in the summary: v1 does not run these no matter what was selected.
NOT_RUN = [
    ("pytest", "cd backend && uv run pytest tests/ -n 4 -q  # 或 -k 定向"),
    ("typecheck", "task ci:fast -- --types  # 或 pnpm run typecheck / uv run pyright"),
    ("build", "cd frontend && pnpm run build"),
    ("e2e", "task e2e:test  # 需要完整环境"),
]
TIMEOUT_NOTE = "pytest/typecheck/build/e2e 不在静态层；0 只代表所选静态检查真实完成，不代表完整 CI 通过"


def sh(args, **kw):
    return subprocess.run(args, capture_output=True, text=True, cwd=REPO_ROOT, **kw)


def git_lines(*args):
    proc = subprocess.run(
        ["git", *args, "-z"], cwd=REPO_ROOT, capture_output=True, text=True
    )
    if proc.returncode != 0:
        return []
    return [p for p in proc.stdout.split("\0") if p]


def load_select():
    spec = importlib.util.spec_from_file_location(
        "required_ci", Path(__file__).with_name("required-ci.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.select, module.PATHS


def configured_hook_ids():
    text = Path(REPO_ROOT, ".pre-commit-config.yaml").read_text()
    return set(re.findall(r"-\s+id:\s+(\S+)", text))


def collect_changes(base_ref):
    """Return (changed, base_sha, head, staged, unstaged, untracked, merge_base_ok)."""
    head = sh(["git", "rev-parse", "HEAD"]).stdout.strip()
    merge_base = sh(["git", "merge-base", base_ref, "HEAD"])
    base_sha = merge_base.stdout.strip() if merge_base.returncode == 0 else None
    committed = git_lines("diff", "--name-only", "--no-renames", base_sha, "HEAD") if base_sha else []
    staged = git_lines("diff", "--cached", "--name-only", "--no-renames")
    unstaged = git_lines("diff", "--name-only", "--no-renames")
    untracked = git_lines("ls-files", "--others", "--exclude-standard")
    changed = sorted(set(committed) | set(staged) | set(unstaged) | set(untracked))
    return changed, base_sha, head, staged, unstaged, untracked, base_sha is not None


def dirty_fingerprint(staged, unstaged, untracked):
    """Content fingerprint of the dirty worktree.

    A path list cannot bind the verified version: the same path with new
    content must produce a new fingerprint. Staged paths contribute their
    index blob id; every dirty path contributes its worktree content hash,
    or a deletion marker when the file is gone.
    """
    index_oids = {}
    if staged:
        out = subprocess.run(
            ["git", "ls-files", "-s", "-z", "--", *staged],
            cwd=REPO_ROOT, capture_output=True, text=True,
        ).stdout
        for rec in out.split("\0"):
            if rec:
                meta, path = rec.split("\t", 1)
                index_oids[path] = meta.split()[1]
    entries = []
    for path in sorted(set(staged + unstaged + untracked)):
        if path in staged:
            entries.append(f"{path}|idx:{index_oids.get(path, '?')}")
        worktree = Path(REPO_ROOT, path)
        if worktree.is_file():
            digest = hashlib.sha256(worktree.read_bytes()).hexdigest()[:16]
            entries.append(f"{path}|wt:{digest}")
        else:
            entries.append(f"{path}|deleted")
    return hashlib.sha256("\n".join(entries).encode()).hexdigest()[:12]


def main(argv):
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default="origin/main", help="base ref (default origin/main)")
    parser.add_argument("--types", action="store_true", help="also run pyright / vue-tsc (manual-stage hooks)")
    parser.add_argument("--report", default=str(Path(GIT_DIR, "ci-fast-report.json")))
    parser.add_argument("--timeout", type=int, default=900, help="per-hook seconds (default 900)")
    args = parser.parse_args(argv)

    started = time.time()
    report_path = Path(args.report)
    if report_path.exists():
        report_path.unlink()  # no report means "not passed"; never leave a stale one

    report = {
        "version": 1,
        "status": "unknown",
        "exit": UNKNOWN,
        "reasons": [],
        "fallback": False,
        "unknown_paths": [],
        "ran": [],
        "not_run": [],
    }

    def finish(status, code):
        report["status"] = status
        report["exit"] = code
        report["seconds"] = round(time.time() - started, 1)
        try:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        except OSError as exc:
            # No report on disk means "not passed" — a green exit without a
            # report would be exactly the false green this rule exists to
            # prevent. Failing runs keep their original (already non-zero) code.
            print(f"ci:fast: 报告写入失败（{exc}）", file=sys.stderr)
            report["reasons"].append(f"report write failed: {exc}")
            if code == PASS:
                print("ci:fast: 无法验证 — 检查通过但报告未落盘，按未通过处理", file=sys.stderr)
                return UNKNOWN
        return code

    try:
        return run(args, report, finish)
    except KeyboardInterrupt:
        report["reasons"].append("interrupted")
        print("\nci:fast: 被中断（无法验证）", flush=True)
        finish("unknown", UNKNOWN)
        sys.exit(130)
    except OSError as exc:
        report["reasons"].append(f"os error: {exc}")
        print(f"\nci:fast: blocked — {exc}", flush=True)
        return finish("blocked", BLOCKED)


def run(args, report, finish):
    select, paths_map = load_select()

    precommit = shlex.split(os.environ.get("CI_FAST_PRECOMMIT", "uvx pre-commit"))
    try:
        available = sh([*precommit, "--version"]).returncode == 0
    except OSError:
        available = False
    if not available:
        print("ci:fast: blocked — pre-commit 不可用（需要 uvx 或 pre-commit）")
        report["reasons"].append("pre-commit unavailable")
        return finish("blocked", BLOCKED)

    changed, base_sha, head, staged, unstaged, untracked, merge_base_ok = collect_changes(args.base)
    dirty = sorted(set(staged + unstaged + untracked))
    fp = dirty_fingerprint(staged, unstaged, untracked)
    report.update(
        base={"ref": args.base, "merge_base": base_sha},
        head=head,
        dirty={"count": len(dirty), "fingerprint": fp, "files": dirty},
    )
    custom_base = args.base != "origin/main"
    if custom_base:
        # file-size and migration-fork accept --base but the pre-commit
        # command table invokes them without one: they always judge
        # origin/main while the selector used args.base. Judging the wrong
        # base can invert the verdict (a file that shrank on main but grew
        # against the PR's real base would look like a shrink), so these
        # hooks are skipped below and the run cannot verify (exit 2).
        divergence = (f"--base={args.base} 只影响选测；file-size/migration-fork "
                      "恒按 origin/main 判定（pre-commit 命令表不传参），本次跳过这两项，"
                      "整体无法验证")
        print(f"ci:fast: 注意 — {divergence}")
        report["reasons"].append(divergence)
    if not merge_base_ok:
        report["fallback"] = True
        report["reasons"].append(f"no merge base with {args.base}; selector fell back to all suites")

    scope = select(changed) if changed else {suite: False for suite in paths_map}
    if not merge_base_ok:
        scope = {suite: True for suite in paths_map}

    known = set()
    for suite, patterns in paths_map.items():
        known.update(p for p in changed for pat in patterns if fnmatch.fnmatchcase(p, pat))
    unknown_paths = [p for p in changed if p not in known]
    if unknown_paths:
        report["unknown_paths"] = unknown_paths
        scope["backend"] = scope["frontend"] = True

    scope["guards"] = True  # same as CI: guards always run
    report["scope"] = scope

    hook_ids = []
    for suite, ids in FAST_HOOKS.items():
        if scope.get(suite):
            hook_ids.extend(ids)
    if args.types:
        for suite, ids in TYPES_HOOKS.items():
            if scope.get(suite):
                hook_ids.extend(ids)

    configured = configured_hook_ids()
    missing = [i for i in hook_ids if i not in configured]
    if missing:
        print(f"ci:fast: 无法验证 — hook id 不在 .pre-commit-config.yaml: {', '.join(missing)}")
        report["reasons"].append(f"hook ids missing from config: {missing}")
        return finish("unknown", UNKNOWN)

    blocked = []
    if scope.get("backend") and not Path(REPO_ROOT, "backend/.venv").is_dir():
        blocked.append("backend/.venv 不存在 — 先 task be:deps:sync（无 task 时 (cd backend && uv sync)）")
    if scope.get("frontend") and not Path(REPO_ROOT, "frontend/node_modules").is_dir():
        blocked.append("frontend/node_modules 不存在 — 先 task fe:install（无 task 时 (cd frontend && pnpm install --frozen-lockfile)）")
    if blocked:
        for line in blocked:
            print(f"ci:fast: blocked — {line}")
        report["reasons"].extend(blocked)
        return finish("blocked", BLOCKED)

    runnable, skipped_unknown = [], []
    for hook_id in hook_ids:
        if hook_id in MERGE_BASE_HOOKS:
            if not merge_base_ok:
                skipped_unknown.append((hook_id, f"no merge base — git fetch origin {args.base}"))
            elif custom_base:
                skipped_unknown.append((hook_id, f"hook 恒按 origin/main 判定，无法验证 --base={args.base}"))
            else:
                runnable.append(hook_id)
        else:
            runnable.append(hook_id)

    failures, timed_out, skipped, not_applicable = [], [], [], []
    print(f"ci:fast: base={base_sha or 'NONE'} head={head[:12]} "
          f"dirty={len(dirty)}(fp {fp}) scope={[s for s, v in scope.items() if v]}")
    if unknown_paths:
        print(f"ci:fast: 未知路径已扩大范围: {', '.join(unknown_paths)}")

    for hook_id in runnable:
        t0 = time.time()
        try:
            proc = subprocess.run(
                # --hook-stage manual so manual-stage hooks (pyright,
                # frontend-typecheck under --types) actually run; hooks with
                # no `stages:` restriction run at every stage including this.
                # --color never beats PRE_COMMIT_COLOR=always: a colored
                # "Skipped" carries a trailing ANSI reset that must never
                # blind the skip detection below.
                [*precommit, "run", hook_id, "--all-files", "--hook-stage", "manual",
                 "--color", "never"],
                capture_output=True, text=True, cwd=REPO_ROOT, timeout=args.timeout,
            )
        except subprocess.TimeoutExpired:
            timed_out.append(hook_id)
            report["ran"].append({"id": hook_id, "result": "timeout", "seconds": args.timeout})
            print(f"  ✗ {hook_id}: 超时（>{args.timeout}s）")
            continue
        seconds = round(time.time() - t0, 1)
        # Strip ANSI in depth (--color never is already pinned): a leftover
        # reset code after "Skipped" must not turn a skip into a pass.
        out = ANSI_RE.sub("", proc.stdout + proc.stderr)
        # pre-commit returns 0 for a hook it did NOT run: "Skipped" via the
        # SKIP env, or "(no files to check)Skipped" when the file filter
        # matched nothing. One hook runs per process here, so the status
        # line (dotted leader ending in Skipped) unambiguously names it.
        if proc.returncode == 0 and re.search(r"\.{4}[^\n]*Skipped[ \t]*$", out, re.M):
            if "no files to check" in out:
                # legitimate — but it is not an execution and must not be
                # counted as one
                report["not_run"].append({"id": hook_id, "reason": "无相关文件（pre-commit: no files to check）"})
                not_applicable.append(hook_id)
                print(f"  - {hook_id}: 无相关文件，未执行")
            else:
                why = "SKIP 环境变量" if hook_id in os.environ.get("SKIP", "").split(",") else "pre-commit 跳过"
                report["ran"].append({"id": hook_id, "result": "skipped", "seconds": seconds})
                skipped.append(hook_id)
                print(f"  ? {hook_id}: 被跳过（{why}）— 不算执行")
            continue
        ok = proc.returncode == 0
        report["ran"].append({"id": hook_id, "result": "pass" if ok else "fail", "seconds": seconds})
        print(f"  {'✓' if ok else '✗'} {hook_id} ({seconds}s)")
        if not ok:
            failures.append(hook_id)
            tail = (proc.stdout + proc.stderr).strip().splitlines()[-15:]
            print("\n".join("    " + line for line in tail))

    for hook_id, reason in skipped_unknown:
        report["not_run"].append({"id": hook_id, "reason": reason})
        print(f"  ? {hook_id}: 无法验证（{reason}）")

    for name, suggestion in NOT_RUN:
        if args.types and name == "typecheck":
            continue
        report["not_run"].append({"id": name, "reason": "静态层之外", "suggestion": suggestion})
    not_run_text = "; ".join(f"{n['id']}（{n.get('suggestion') or n['reason']}）" for n in report["not_run"])

    if failures:
        print(f"\nci:fast: 已执行静态检查 失败 — {', '.join(failures)}")
        print(f"未执行: {not_run_text}" if not_run_text else "")
        print(TIMEOUT_NOTE)
        report["reasons"].append(f"failed hooks: {failures}")
        return finish("fail", FAIL)
    if timed_out or skipped or skipped_unknown:
        why = []
        if timed_out:
            why.append(f"超时: {', '.join(timed_out)}")
        if skipped:
            why.append(f"被跳过（不算执行）: {', '.join(skipped)}")
        if skipped_unknown:
            why.append("部分检查无法判断（无 merge base 或自定义 --base）")
        print(f"\nci:fast: 已执行静态检查 无法验证 — {'；'.join(why)}")
        print(f"未执行: {not_run_text}" if not_run_text else "")
        report["reasons"].extend(why)
        return finish("unknown", UNKNOWN)
    executed = [r["id"] for r in report["ran"] if r["result"] == "pass"]
    if not executed:
        # Zero executed checks is not a pass, whatever pre-commit returned.
        print("\nci:fast: 无法验证 — 选中的 hook 均未实际执行（全部被跳过或无相关文件）")
        print(f"未执行: {not_run_text}" if not_run_text else "")
        report["reasons"].append("no selected hook actually executed")
        return finish("unknown", UNKNOWN)
    print(f"\nci:fast: 已执行静态检查 通过（{len(executed)} 项）")
    print(f"未执行: {not_run_text}" if not_run_text else "")
    print(TIMEOUT_NOTE)
    return finish("pass", PASS)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
