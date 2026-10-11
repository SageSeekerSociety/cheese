"""Scoped, bounded local evidence before push; never a substitute for full CI.

task ci:fast: quick tier, 180s, serial checks and sampled 3072 MiB group RSS.
Direct Python invocation defaults to the legacy static tier (no tests/types).
--tier extended gives the quick selection 900s without expanding its coverage.
--types additionally runs the full Pyright/frontend-typecheck manual hooks.
Use --title for a proposed PR title; otherwise read the actual PR title with gh.
A missing title is unverified, never replaced by a possibly unrelated HEAD title.

Scope comes from Required CI's select()/path map. Changes include committed,
staged, unstaged, untracked, deleted and both renamed paths. Hook commands
remain in pre-commit config; tests reuse existing pytest/Vitest/type-ratchet
entry points. No installs, no result cache, no hook or compiler config changes.

Exit 0: all selected checks actually passed, NOT full CI. Exit 1: check failed.
Exit 2: unknown (budget, signal, skip, empty tests, unavailable merge base).
Exit 3: blocked (missing dependency/tool). Report binds head, base and dirty
content, is worktree-aware, and must be written before returning green.
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
import tempfile
import time

from ci_fast_checks import resolve, test_plan
from ci_fast_process import dependencies, execute

REPO_ROOT = subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip()
GIT_DIR = subprocess.check_output(["git", "rev-parse", "--git-dir"], text=True, cwd=REPO_ROOT).strip()
if not os.path.isabs(GIT_DIR):
    GIT_DIR = str(Path(REPO_ROOT, GIT_DIR))
PASS, FAIL, UNKNOWN, BLOCKED = 0, 1, 2, 3
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
FAST_HOOKS = {
    "guards": ["file-size", "repo-rules", "action-pins", "manual-anchors"],
    "backend": ["ruff", "ruff-format", "boundary-check", "deferred-imports", "migration-fork"],
    "frontend": ["eslint", "stylelint", "boundary-check-frontend", "scene-ratchet", "catalog-ratchet"],
}
TYPES_HOOKS = {"backend": ["pyright"], "frontend": ["frontend-typecheck"]}
MERGE_BASE_HOOKS = {"file-size", "migration-fork"}


def sh(args):
    proc = subprocess.run(args, capture_output=True, text=True, cwd=REPO_ROOT, timeout=10)
    return proc


def git_lines(*args):
    proc = sh(["git", *args, "-z"])
    if proc.returncode:
        raise OSError(f"git selection failed: {proc.stderr.strip()}")
    return [p for p in proc.stdout.split("\0") if p]


def load_select():
    spec = importlib.util.spec_from_file_location("required_ci", Path(__file__).with_name("required-ci.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.select, module.PATHS


def configured_hook_ids():
    return set(re.findall(r"-\s+id:\s+(\S+)", Path(REPO_ROOT, ".pre-commit-config.yaml").read_text()))


def collect_changes(base_ref):
    head = sh(["git", "rev-parse", "HEAD"])
    if head.returncode:
        raise OSError(head.stderr.strip())
    merge_base = sh(["git", "merge-base", base_ref, "HEAD"])
    base_sha = merge_base.stdout.strip() if merge_base.returncode == 0 else None
    committed = git_lines("diff", "--name-only", "--no-renames", base_sha, "HEAD") if base_sha else []
    staged = git_lines("diff", "--cached", "--name-only", "--no-renames")
    unstaged = git_lines("diff", "--name-only", "--no-renames")
    untracked = git_lines("ls-files", "--others", "--exclude-standard")
    changed = sorted(set(committed + staged + unstaged + untracked))
    return changed, base_sha, head.stdout.strip(), staged, unstaged, untracked


def dirty_fingerprint(staged, unstaged, untracked):
    index_oids = {}
    if staged:
        for rec in git_lines("ls-files", "-s", "--", *staged):
            meta, path = rec.split("\t", 1)
            index_oids[path] = meta.split()[1]
    entries = []
    for path in sorted(set(staged + unstaged + untracked)):
        if path in staged:
            entries.append(f"{path}|idx:{index_oids.get(path, '?')}")
        worktree = Path(REPO_ROOT, path)
        digest = hashlib.sha256(worktree.read_bytes()).hexdigest()[:16] if worktree.is_file() else "deleted"
        entries.append(f"{path}|wt:{digest}")
    return hashlib.sha256("\n".join(entries).encode()).hexdigest()[:12]


def main(argv):
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--tier", choices=("quick", "static", "extended"), default="static")
    parser.add_argument("--types", action="store_true", help="also run full type hooks")
    parser.add_argument("--title", help="proposed PR title; otherwise fetch the actual PR title")
    parser.add_argument("--report", default=str(Path(GIT_DIR, "ci-fast-report.json")))
    parser.add_argument("--timeout", type=float, default=900, help="per-check seconds")
    parser.add_argument("--budget", type=float, help="whole tier seconds (quick/static 180; extended 900)")
    parser.add_argument("--memory-mib", type=int, default=3072, help="sampled process-group RSS ceiling")
    args = parser.parse_args(argv)
    if args.budget is None:
        args.budget = 900 if args.tier == "extended" else 180
    if args.timeout <= 0 or args.budget <= 0 or not 0 < args.memory_mib <= 3072:
        parser.error("positive time limits and 1..3072 MiB are required")
    started = time.monotonic()
    report_path = Path(args.report)
    report = {"version": 2, "status": "unknown", "exit": UNKNOWN, "tier": args.tier,
              "budget_seconds": args.budget, "memory_limit_mib": args.memory_mib,
              "memory_measurement": "sampled process-group RSS, not address-space enforcement",
              "reasons": [], "fallback": False, "unknown_paths": [], "ran": [], "not_run": []}

    def finish(status, code):
        report.update(status=status, exit=code, seconds=round(time.monotonic() - started, 2))
        try:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        except OSError as exc:
            print(f"ci:fast: 报告写入失败: {exc}", file=sys.stderr)
            if code == PASS:
                return UNKNOWN
        label = {"pass": "通过", "fail": "失败", "unknown": "无法验证", "blocked": "blocked"}[status]
        prefix = "已执行静态检查 " if args.tier == "static" else ""
        print(f"ci:fast: {prefix}{label} ({report['seconds']}s); 不代表完整 CI 通过")
        for reason in report["reasons"]:
            print(f"  {reason}")
        for item in report["not_run"]:
            print(f"  未执行 {item['id']}: {item['reason']}")
        return code

    try:
        try:
            report_path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            report["reasons"].append(f"cannot remove stale report: {exc}")
            return finish("unknown", UNKNOWN)
        return run(args, report, finish, started)
    except KeyboardInterrupt:
        report["reasons"].append("interrupted")
        finish("unknown", UNKNOWN)
        return 130
    except subprocess.TimeoutExpired:
        report["reasons"].append("selection/setup timed out")
        return finish("unknown", UNKNOWN)
    except (OSError, ValueError) as exc:
        report["reasons"].append(str(exc))
        print(f"ci:fast: blocked — {exc}")
        return finish("blocked", BLOCKED)


def run(args, report, finish, started):
    deadline = started + args.budget

    def invoke(identifier, command, cwd=REPO_ROOT, evidence=None, env=None):
        result = execute(command, cwd, min(args.timeout, max(0, deadline - time.monotonic())), args.memory_mib, env)
        if evidence:
            result = resolve(result, evidence)
        out = ANSI_RE.sub("", result.get("output", ""))
        if identifier.startswith("hook:") and result["result"] == "pass" and re.search(r"\.{4}[^\n]*Skipped[ \t]*$", out, re.M):
            result["result"] = "not_applicable" if "no files to check" in out else "skipped"
        row = {"id": identifier.removeprefix("hook:"), **{k: v for k, v in result.items() if k != "output"}}
        if row["result"] in ("budget", "not_applicable"):
            report["not_run"].append({"id": row["id"], "reason": "无相关文件" if row["result"] == "not_applicable" else "超时/预算耗尽"})
        if row["result"] != "not_applicable":
            report["ran"].append(row)
        print(f"  {row['id']}: {row['result']} ({row['seconds']}s; sampled RSS {row['peak_rss_mib']} MiB)", flush=True)
        if row["result"] not in ("pass", "not_applicable"):
            print("\n".join(out.strip().splitlines()[-15:]))
            label = {"timeout": "超时", "skipped": "被跳过"}.get(row["result"], row["result"])
            report["reasons"].append(f"{row['id']}: {label} {row.get('reason', '')}".strip())
        return result

    changed, base_sha, head, staged, unstaged, untracked = collect_changes(args.base)
    report.update(base={"ref": args.base, "merge_base": base_sha}, head=head,
                  dirty={"count": len(set(staged + unstaged + untracked)),
                         "fingerprint": dirty_fingerprint(staged, unstaged, untracked),
                         "files": sorted(set(staged + unstaged + untracked))}, changed=changed)
    select, paths_map = load_select()
    scope = select(changed)
    if not base_sha:
        scope = {suite: True for suite in paths_map}
        report["fallback"] = True
    unknown = [p for p in changed if not any(fnmatch.fnmatchcase(p, pat) for patterns in paths_map.values() for pat in patterns)]
    if unknown:
        scope["backend"] = scope["frontend"] = True
        report["unknown_paths"] = unknown
    scope["guards"] = True
    # A frontend catalog edit under src/i18n/messages/** is read back by the
    # backend's refusal/sentences tree guards, but the shared CI path map names
    # only three catalogs, so any other one leaves backend scope off. Widen it
    # for this tier only, so those guards still run here; the shared map (and
    # what Required CI runs) is left untouched.
    if any(p.startswith("frontend/src/i18n/messages/") for p in changed):
        report["i18n_backend_guard"] = not scope.get("backend")
        scope["backend"] = True
    report["scope"] = scope
    print(f"ci:fast: tier={args.tier} base={base_sha or 'NONE'} head={head[:12]} scope={[s for s, v in scope.items() if v]}")
    if args.tier != "static":
        title = args.title
        source = "proposed --title"
        if title is None:
            source = "actual PR title"
            result = execute(["gh", "pr", "view", "--json", "title", "--jq", ".title"], REPO_ROOT,
                             min(10, max(0, deadline - time.monotonic())), args.memory_mib)
            # gh (and the platform's credential wrapper) can print a preamble
            # notice before the title; a PR title is one line, so take the last
            # non-empty line rather than the whole merged stdout+stderr blob.
            lines = [line.strip() for line in result["output"].splitlines() if line.strip()]
            title = lines[-1] if result["result"] == "pass" and lines else None
        report["title"] = {"source": source, "value": title}
        if title is None:
            report["ran"].append({"id": "commit-title", "result": "unknown", "reason": "no PR title; provide proposed --title", "seconds": 0})
        else:
            invoke("commit-title", [sys.executable, str(Path(__file__).with_name("check-commit-title.py")), title])

    precommit = shlex.split(os.environ.get("CI_FAST_PRECOMMIT", "uvx pre-commit"))
    ready = execute([*precommit, "--version"], REPO_ROOT,
                    min(10, max(0, deadline - time.monotonic())), args.memory_mib)
    if ready["result"] != "pass":
        report["reasons"].append(f"pre-commit unavailable: {ready['result']}")
        return finish("unknown" if ready["result"] in ("timeout", "budget", "memory", "unknown") else "blocked",
                      UNKNOWN if ready["result"] in ("timeout", "budget", "memory", "unknown") else BLOCKED)
    hooks = [h for suite, ids in FAST_HOOKS.items() if scope.get(suite) for h in ids]
    if args.types:
        hooks += [h for suite, ids in TYPES_HOOKS.items() if scope.get(suite) for h in ids]
    missing = sorted(set(hooks) - configured_hook_ids())
    if missing:
        report["reasons"].append(f"hook ids missing from config: {missing}")
        return finish("unknown", UNKNOWN)
    missing_deps = dependencies(REPO_ROOT, scope)
    if missing_deps:
        report["reasons"].extend(missing_deps)
        print("\n".join(missing_deps))
        return finish("blocked", BLOCKED)

    def hook(identifier):
        if identifier in MERGE_BASE_HOOKS and (not base_sha or args.base != "origin/main"):
            report["ran"].append({"id": identifier, "result": "unknown", "seconds": 0})
            report["not_run"].append({"id": identifier, "reason": "no merge base or custom --base cannot be honored by hook"})
            return
        invoke(f"hook:{identifier}", [*precommit, "run", identifier, "--all-files", "--hook-stage", "manual", "--color", "never"])

    # Cheap high-frequency metadata first; tests/types before full-tree lint.
    hook("file-size")
    if args.tier != "static":
        with tempfile.TemporaryDirectory(prefix="ci-fast-") as tmp:
            plan, omissions = test_plan(Path(REPO_ROOT), changed, scope, Path(tmp), bool(unknown) or not base_sha)
            report["not_run"].extend(omissions)
            for check in plan:
                invoke(check["id"], check["command"], check["cwd"], check.get("evidence"), check.get("env"))
    for identifier in hooks:
        if identifier != "file-size":
            hook(identifier)
    if args.tier == "static":
        report["not_run"].extend([
            {"id": "pytest", "reason": "static tier only; run task ci:fast for selected pure tests"},
            {"id": "typecheck", "reason": "static tier only; run task ci:fast for changed-file types"},
            {"id": "build", "reason": "outside this local tier; run full CI"},
            {"id": "e2e", "reason": "outside this local tier; run full CI"},
        ])
    else:
        report["not_run"].extend([
            {"id": "integration/contract/build/e2e/remote", "reason": "outside this local tier; run full CI"},
            {"id": "full frontend consumer types", "reason": "narrow types check changed roots/imports, not all consumers; --types adds full hooks"},
        ])
    results = [row["result"] for row in report["ran"]]
    if "fail" in results:
        return finish("fail", FAIL)
    if "blocked" in results:
        return finish("blocked", BLOCKED)
    if any(r not in ("pass", "not_applicable") for r in results) or "pass" not in results:
        return finish("unknown", UNKNOWN)
    # A changing worktree invalidates the content-bound verdict.
    now_changed, now_base, now_head, now_staged, now_unstaged, now_untracked = collect_changes(args.base)
    if (now_changed != changed or now_base != base_sha or now_head != head
            or dirty_fingerprint(now_staged, now_unstaged, now_untracked) != report["dirty"]["fingerprint"]):
        report["reasons"].append("worktree changed during checks")
        return finish("unknown", UNKNOWN)
    return finish("pass", PASS)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
