"""Scoped, bounded local evidence before push; never a substitute for full CI.

task ci:fast: quick tier, 180s, and a sampled 3072 MiB group RSS ceiling — both
are this machine's own limits; --budget/--memory-mib override them for a bigger
one, which is what Required CI's draft job does (see ci-fast.md).
Direct Python invocation defaults to the legacy static tier (no tests/types).
--tier extended gives the quick selection 900s without expanding its coverage.
--types additionally runs the full Pyright/frontend-typecheck manual hooks.
--pre-push is the entry point the platform's task-worktree pre-push hook calls
(one report per push, for a reader who did not choose to run this): it leaves
out the hooks that spawn the node toolchain — those get killed on a small box,
and a report that calls a killed check red is worse than no report — reports
what it left out as not run, and closes with the command that reproduces each
item that did not pass. It never blocks the push: the hook that calls it
swallows the exit code (a push refused here is a commit the platform has not
delivered, and the checkout it lives in does not survive long).
Use --title for a proposed PR title; otherwise read the actual PR title from
--pull-request FILE (a `gh api repos/OWNER/REPO/pulls/N` payload, or the event
JSON, which nests it under "pull_request"), falling back to `gh pr view`. A
DRAFT pull request carries the platform's placeholder title, which says nothing
about the change, so a draft's title is not checked at all — the same rule
check-commit-title.py already applies to a draft — and is reported as not run;
it is checked once the PR is marked ready for review.
A missing title is unverified, never replaced by a possibly unrelated HEAD title,
and a payload that cannot be read at all costs this run the title alone — the
rest of the checks still run and can still fail it.

Scope comes from Required CI's select()/path map, plus one local widening: a
path the map does not name could be any kind of code, so it turns both product
suites on — except for prose, which cannot hold the code a suite covers and is
evidence for the doc checks alone (DOC_GLOBS). Changes include committed,
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
    "guards": ["file-size", "repo-rules", "action-pins", "manual-anchors", "doc-references"],
    "backend": ["ruff", "ruff-format", "boundary-check", "deferred-imports", "migration-fork"],
    "frontend": ["eslint", "stylelint", "boundary-check-frontend", "scene-ratchet", "catalog-ratchet"],
}
TYPES_HOOKS = {"backend": ["pyright"], "frontend": ["frontend-typecheck"]}
# pre-commit hooks that spawn the JS toolchain (node/eslint/stylelint) rather
# than python, and are run after tests/types. On a small machine one started
# with too little budget left can be killed by the kernel before the sampled
# RSS sees it coming, which would otherwise turn into a hard "blocked" for a
# tier whose whole job is to report unknown when it cannot judge. The ratchets
# next to them are python scripts and cost seconds, so they are not guarded:
# their own per-check timeout reports them unknown if the budget really is gone.
JS_RUNTIME_HOOKS = {"eslint", "stylelint", "boundary-check-frontend"}
MERGE_BASE_HOOKS = {"file-size", "migration-fork"}
# Prose, in any of the forms this repo keeps it: contents that cannot be the
# code a product suite covers, so a diff of only these is unknown-path widening
# that proves nothing about backend or frontend. The shared path map already
# names `docs/**` for guards; what this adds is the doc paths it does not name —
# `README.md`, `AGENTS.md`, a skill's `SKILL.md` — which read as unknown and
# turned a docs-only draft run into the full suite, which cannot fit that tier's
# budget, so it answered unknown (reported 2026-10-12). Guards still run on
# every invocation, so the doc checks themselves are unaffected.
DOC_GLOBS = ("*.md", "docs/**")
# Group RSS the sampled ceiling may be set to. The default (3072 MiB) is the
# work machine's own session cap — what a local run can actually be backed by —
# so a local run keeps it. A CI runner has its own memory and no session cap:
# 9216 MiB is what the frontend's own rule (frontend/vite.config.ts: one fork
# per 3 GiB) gives that box — a third of the 4 vCPU / 16 GiB runner's RAM.
# Anything above is refused rather than clamped: past it the sampled ceiling
# stops describing what the machine can back.
MAX_MEMORY_MIB = 9216
# Floor for starting a JS-runtime hook after tests/types. These three take tens
# of seconds each (eslint ~67s in full CI), so below it the start is not worth
# the kill it would earn a second or two later.
JS_HOOK_MIN_SECONDS = 15


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
    parser.add_argument("--pull-request", metavar="FILE",
                        help="PR JSON to read the title (and draft flag) from"
                             " (gh api repos/O/R/pulls/N, or the event JSON)")
    parser.add_argument("--pre-push", action="store_true",
                        help="the platform's task-worktree pre-push entry point: leave out"
                             " the hooks that spawn the node toolchain, and close with how to"
                             " reproduce each item that did not pass")
    parser.add_argument("--report", default=str(Path(GIT_DIR, "ci-fast-report.json")))
    parser.add_argument("--timeout", type=float, default=900, help="per-check seconds")
    parser.add_argument("--budget", type=float, help="whole tier seconds (quick/static 180; extended 900)")
    parser.add_argument("--memory-mib", type=int, default=3072,
                        help=f"sampled process-group RSS ceiling, 1..{MAX_MEMORY_MIB}")
    args = parser.parse_args(argv)
    if args.budget is None:
        args.budget = 900 if args.tier == "extended" else 180
    if args.timeout <= 0 or args.budget <= 0 or not 0 < args.memory_mib <= MAX_MEMORY_MIB:
        parser.error(f"positive time limits and 1..{MAX_MEMORY_MIB} MiB are required")
    started = time.monotonic()
    report_path = Path(args.report)
    report = {"version": 2, "status": "unknown", "exit": UNKNOWN, "tier": args.tier,
              "pre_push": args.pre_push,
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
        if args.pre_push:
            # The reader did not ask for this run and is about to push anyway,
            # so the report has to be actionable as it stands: every item this
            # entry point did not judge names the command that runs it outside
            # the hook — the ones that failed, and the ones it left out.
            precommit = os.environ.get("CI_FAST_PRECOMMIT", "uvx pre-commit")
            flagged = [row for row in report["ran"] if row["result"] not in ("pass", "not_applicable")]
            # Only the pre-push filter can put a JS hook in not_run: it is the
            # one place that removes them from the selection before they run.
            dropped = [item for item in report["not_run"] if item["id"] in JS_RUNTIME_HOOKS]
            print("ci:fast: 推前自检只报告，不挡推送。")
            for row in flagged:
                print(f"  复现 {row['id']}（{row['result']}）:"
                      f" {precommit} run {row['id']} --all-files --hook-stage manual")
            for item in dropped:
                print(f"  推前没跑 {item['id']}（想知道就跑）:"
                      f" {precommit} run {item['id']} --all-files --hook-stage manual")
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
        rss_note = "sampled RSS未采到" if row.get("unmeasured") else f"sampled RSS {row['peak_rss_mib']} MiB"
        print(f"  {row['id']}: {row['result']} ({row['seconds']}s; {rss_note})", flush=True)
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
    # A path the shared map does not name could be any kind of code, so the tier
    # widens to both product suites; prose cannot be, so it is not an unknown
    # path (DOC_GLOBS). Everything else widens exactly as before.
    unknown = [
        p for p in changed
        if not any(fnmatch.fnmatchcase(p, pat) for patterns in paths_map.values() for pat in patterns)
        and not any(fnmatch.fnmatchcase(p, pat) for pat in DOC_GLOBS)
    ]
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
    # Which paths the map itself routed into frontend scope, for the planner:
    # a backend change reaches that scope through a regenerated
    # backend/openapi.json, and the planner must not read the same change's
    # .py files as frontend content the suite has to execute — it would run the
    # whole Vitest suite for a document no test can load.
    frontend_scope = [p for p in changed
                      if any(fnmatch.fnmatchcase(p, pat) for pat in paths_map["frontend"])]
    report["scope"] = scope
    print(f"ci:fast: tier={args.tier} base={base_sha or 'NONE'} head={head[:12]} scope={[s for s, v in scope.items() if v]}")
    if args.tier != "static":
        title = args.title
        source = "proposed --title"
        draft = False
        # Why the PR payload could not be read, when it could not. An unread
        # payload costs this run the title and nothing else: the checks below
        # judge the change, and the verdict is fail-first, so a real failure
        # still fails it. Returning here instead reported unknown having
        # executed nothing — a green for a change nothing looked at.
        unread = None
        if title is None and args.pull_request:
            source = "actual PR title"
            # The event JSON nests the PR under "pull_request"; `gh api
            # .../pulls/N` returns the PR object itself. Accept either, so the
            # caller can pass whichever it already has.
            try:
                payload = json.loads(Path(args.pull_request).read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                unread = f"cannot read --pull-request {args.pull_request}: {type(exc).__name__}"
            else:
                nested = payload.get("pull_request") if isinstance(payload, dict) else None
                pull = nested if isinstance(nested, dict) else payload
                if not isinstance(pull, dict):
                    # Valid JSON that is not a PR object (a scalar, a list, a
                    # `pull_request` that is not an object, a truncated write
                    # that still parses). `.get` on it raised, and an unhandled
                    # exception exits 1 — which the caller reads as "this change
                    # is red": a red for a payload nobody could read.
                    unread = (f"cannot read --pull-request {args.pull_request}: "
                              f"{type(payload).__name__}, not a PR object")
                else:
                    title, draft = pull.get("title"), bool(pull.get("draft"))
                    if not isinstance(title, str):
                        # Same again for the title itself: a non-string would
                        # reach check-commit-title.py as an argv element and
                        # raise TypeError out of the run. No readable title is
                        # no title.
                        title = None
        elif title is None:
            source = "actual PR title"
            # The draft flag is read in the same call. Whether a title is judged
            # is a property of the PR, not of which argv carried the title in:
            # the local entry point (`task ci:fast`) passes no --pull-request,
            # so on a branch whose PR is a draft this is the read that sees the
            # platform's placeholder — and judging it would red the
            # contributor's own run, before they pushed anything.
            result = execute(["gh", "pr", "view", "--json", "title,draft",
                              "--jq", "[(.draft | tostring), .title] | @tsv"],
                             REPO_ROOT, min(10, max(0, deadline - time.monotonic())),
                             args.memory_mib)
            # gh (and the platform's credential wrapper) can print a preamble
            # notice before the fields; they are one tab-separated line, so take
            # the last non-empty line rather than the whole merged
            # stdout+stderr blob. The first tab is the separator: @tsv escapes
            # any tab inside the title.
            lines = [line.strip() for line in result["output"].splitlines() if line.strip()]
            title, draft = None, False
            if result["result"] == "pass" and lines:
                flag, _, rest = lines[-1].partition("\t")
                draft = flag == "true"
                # A PR with no title is no title, not the string "null".
                title = rest or None
        if unread:
            # The payload said neither what the title is nor whether this is a
            # draft. One check with no input is unknown — the row below is what
            # the caller reads as unjudged — and the rest of the run is
            # unaffected, which is what keeps a real failure a failure.
            report["title"] = {"source": source, "value": None}
            report["ran"].append({"id": "commit-title", "result": "unknown",
                                  "reason": unread, "seconds": 0})
            report["reasons"].append(f"commit-title: unknown {unread}")
        elif draft:
            # A draft's title is the platform's placeholder for work in progress
            # ("WIP: <task title>"), which says nothing about the change: judging
            # it here would fail a draft for text it does not own.
            # check-commit-title.py skips a draft the same way, and pr-title.yml
            # re-runs the title check when the PR is marked ready for review.
            report["title"] = {"source": source, "value": None, "draft": True}
            report["not_run"].append({"id": "commit-title",
                                      "reason": "draft pull request: the placeholder title is not checked"})
        else:
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
    if args.pre_push:
        # Not a silent narrowing: what leaves the selection is named in
        # not_run, so a report that could not judge lint says so instead of
        # reading as a pass that covered it. The drift check above still sees
        # the whole set, so a config that lost one of these is still unknown.
        dropped = [h for h in hooks if h in JS_RUNTIME_HOOKS]
        hooks = [h for h in hooks if h not in JS_RUNTIME_HOOKS]
        report["not_run"].extend(
            {"id": h, "reason": "pre-push entry point: this one spawns the node toolchain, "
                               "which a small box kills"}
            for h in dropped
        )
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
            plan, omissions = test_plan(Path(REPO_ROOT), changed, scope, Path(tmp),
                                        bool(unknown) or not base_sha, args.memory_mib, frontend_scope)
            report["not_run"].extend(omissions)
            for check in plan:
                invoke(check["id"], check["command"], check["cwd"], check.get("evidence"), check.get("env"))
    for identifier in hooks:
        if identifier == "file-size":
            continue
        if identifier in JS_RUNTIME_HOOKS and deadline - time.monotonic() < JS_HOOK_MIN_SECONDS:
            # Tests/types already took what the tier had; a node hook started
            # now would spend its whole life being killed. Report it unknown —
            # this tier is allowed to say it could not check, and must never
            # report a red it did not actually observe.
            report["ran"].append({"id": identifier, "result": "unknown", "seconds": 0,
                                  "reason": "no budget left after tests/types"})
            report["not_run"].append({"id": identifier, "reason": "超时/预算耗尽"})
            continue
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
