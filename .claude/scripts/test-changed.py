#!/usr/bin/env python3
"""Run only the tests this working tree's change can reach — the writing loop.

``task test:changed`` answers one question quickly: *did the edit I just made
break anything it reaches?* It is meant to run between edits, next to the editor
and next to the agent writing the test that covers the edit.

``task ci:fast`` answers the other question — *is this change safe to push* — and
pays for it: the static hooks are most of that tier's 180 s, and
``PROVE_REGRESSION``/type checks sit behind them. No hook runs here. What is left
is the two product suites, selected exactly the way ci-fast selects them, so the
run is bounded by the tests rather than by a hook budget. That decoupling is the
whole point: a session that must wait for ruff and the ratchets before it learns
whether its own test passes will write the assert-and-pray version instead.

Selection is not a copy of ci-fast's; it *is* ci-fast's. This script imports
``ci-fast.py`` and calls its ``test_plan``, which is the one place the backend AST
reverse-import graph (``ci_fast_backend.select_backend``), the frontend
``vitest related`` seeding, the off-disk spec list, the narrow type project and
the evidence rules live. Re-deriving any of that here would drift, and the drift
would land as two answers to "which tests does this change reach".

The three suites this keeps are ``backend-pure``, ``frontend-unit`` and
``frontend-i18n``. The type checks and the hooks are dropped: a type error is a
push-time verdict, it costs tens of seconds, and `task ci:fast` already owns it.
The evidence rules are kept in full — an empty selection, a selected file that
never executed, or a related run whose seeds ran alone all read as unknown (exit
2) rather than as a pass. Speed must not come from believing an empty run.

One local widening the shared path map does not make: a change under
``.claude/scripts/`` or ``.github/scripts/`` turns the backend suite on. Those
scripts are covered by backend unit tests (``tests/unit/test_*_sweep_*.py`` and
friends load them by path), and the path map routes them to ``guards`` only, so
without this a change to a script would be judged by nothing at all. The graph
cannot see those edges — the tests load the script by file path, not by import —
so the selection pinpoints no unit test for them and the run says so instead of
reporting the tree guards as a pass.

Exit 0 pass, 1 fail, 2 unknown (nothing to judge, empty selection, no merge
base), 3 blocked (missing tool). Unknown is a real answer here, not a nuisance:
it means this run has no opinion about the change.
"""

import argparse
import contextlib
import fnmatch
import importlib.util
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import time

PASS, FAIL, UNKNOWN, BLOCKED = 0, 1, 2, 3

# The plan entries this entry point keeps, in the order they run. The rest of
# ci-fast's plan is the type checks, which belong to the push, not to the edit.
SUITES = ("backend-pure", "frontend-unit", "frontend-i18n")

# Script roots whose backend unit tests the import graph cannot see. See the
# module docstring: the map sends these to `guards`, the tests load them by
# path, and neither the map nor the graph is wrong — they answer other questions.
SCRIPT_ROOTS = (".claude/scripts/", ".github/scripts/")


def repo_root():
    """The worktree's top level, so this runs from the repo or any subdirectory."""
    try:
        proc = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                              capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return Path(proc.stdout.strip())


def load_platform(root):
    """Load ``ci-fast.py`` as a module, from a repo that may not be the cwd.

    Its module-level code runs ``git rev-parse`` for ``REPO_ROOT``, so the
    directory it was found in must be on ``sys.path`` first for its own
    ``from ci_fast_checks import ...`` to resolve — and that git call reads the
    *process's* cwd, which is why the import runs with the root as cwd: a caller
    that is inside the worktree but not at its top (a test, or a shell that just
    came back from ``backend/``) must not decide whether this can run at all.
    """
    scripts = root / ".github" / "scripts"
    entry = scripts / "ci-fast.py"
    if not entry.is_file():
        raise SystemExit(f"缺少 {entry}：这个入口要靠 ci-fast.py 的选测逻辑，不能在别处跑")
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    spec = importlib.util.spec_from_file_location("ci_fast_entry", entry)
    module = importlib.util.module_from_spec(spec)
    with contextlib.chdir(root):
        spec.loader.exec_module(module)
    return module


def scope_for(changed, select, paths_map, base_sha, doc_globs):
    """Which product suites the change reaches, and which paths routed there.

    ci-fast's own computation, minus the always-on guards. A path the map does
    not name could be any kind of code, so it widens to both suites — except
    prose, which cannot hold the code a suite covers and is evidence for the doc
    checks alone. A missing merge base means nothing is attributable to the
    branch, so both suites run.
    """
    scope = dict(select(changed))
    if not base_sha:
        scope = {suite: True for suite in paths_map}
    named = [pat for patterns in paths_map.values() for pat in patterns]
    unknown = [p for p in changed
               if not any(fnmatch.fnmatchcase(p, pat) for pat in named)
               and not any(fnmatch.fnmatchcase(p, pat) for pat in doc_globs)]
    if unknown:
        scope["backend"] = scope["frontend"] = True
    if any(p.startswith(SCRIPT_ROOTS) and p.endswith(".py") for p in changed):
        scope["backend"] = True
    # A frontend catalog under src/i18n/messages/** is read back by the
    # backend's refusal/sentences tree guards, and the shared map names only
    # three catalogs, so any other one leaves backend scope off. Same widening
    # ci-fast makes, for the same guards — and here the guards are the whole
    # reason backend scope was turned on, so the caller must run them even
    # though the graph pinpoints no unit test for a catalog.
    catalog_guard = any(p.startswith("frontend/src/i18n/messages/") for p in changed)
    if catalog_guard:
        scope["backend"] = True
    frontend_scope = [p for p in changed
                      if any(fnmatch.fnmatchcase(p, pat) for pat in paths_map["frontend"])]
    return scope, frontend_scope, unknown, catalog_guard


def plan_for(platform, root, changed, scope, frontend_scope, tmp, memory_mib, catalog_guard):
    """ci-fast's plan, cut down to the suites an edit-time run wants.

    Kept as the plan object rather than turned into a shell string: the evidence
    that decides pass/unknown lives in it, and dropping it would leave this
    entry point believing any exit code the tests happened to hand back.

    One entry is dropped rather than run: a backend selection that pinpoints no
    unit test — ``required`` empty — happens when no backend module changed (a
    script root turned the suite on), and what is left is the always-on tree
    guards. Running them and reporting green would say "this change is fine"
    about a change none of them judges. The exception is a frontend catalog
    edit, where those guards are exactly what the widening was for.
    """
    plan, omissions = platform.test_plan(
        root, changed, scope, Path(tmp), memory_mib=memory_mib,
        frontend_scope=frontend_scope)
    kept = []
    for entry in plan:
        if entry["id"] not in SUITES:
            continue
        if entry["id"] == "backend-pure" and not entry["evidence"]["required"] and not catalog_guard:
            omissions.append({"id": "backend-pure", "reason":
                              "没有后端单测静态够得着这次改动，选中的只有常跑的门禁用例"})
            continue
        kept.append(entry)
    return kept, omissions


def pinpointed(entry):
    """Whether this plan entry judges the change, rather than the whole suite.

    A full-suite fallback is the honest answer to a change the graph cannot
    narrow, and it is far too big for an edit-time loop: the backend's fallback
    is every pure unit test, the frontend's is all 700-odd specs. ``--strict``
    refuses those instead of spending minutes on them.
    """
    evidence = entry["evidence"]
    if entry["id"] == "backend-pure":
        return not evidence["selection"].startswith("full pure fallback")
    if entry["id"] == "frontend-unit":
        return evidence.get("mode") == "related"
    return True


def describe(entry):
    return f"{entry['id']}: cd {entry['cwd']} && {shlex.join(str(a) for a in entry['command'])}"


def main(argv):
    parser = argparse.ArgumentParser(
        prog="test-changed.py",
        description="只跑这次改动够得着的后端/前端单测（写代码时的秒级反馈）。")
    parser.add_argument("--base", default="origin/main",
                        help="比对的基线，默认 origin/main")
    parser.add_argument("--budget", type=float, default=900,
                        help="整个入口的总秒数上限，默认 900（不是 ci:fast 的钩子预算）")
    parser.add_argument("--timeout", type=float, default=900, help="单个套件的秒数上限")
    parser.add_argument("--memory-mib", type=int, default=3072, help="被测进程组的 RSS 上限")
    parser.add_argument("--strict", action="store_true",
                        help="选测落回整个套件时（后端全部纯单测 / 前端全部 spec）不跑，直接以 2 退出")
    parser.add_argument("--plan", action="store_true", help="只打印要跑什么，不跑")
    parser.add_argument("--json", action="store_true", help="以 JSON 打印结果")
    args = parser.parse_args(argv)

    root = repo_root()
    if root is None:
        print("test:changed 要在项目的 git 仓库里跑（这里不是仓库）", file=sys.stderr)
        return BLOCKED
    platform = load_platform(root)
    changed, base_sha, head, staged, unstaged, untracked = platform.collect_changes(args.base)
    select, paths_map = platform.load_select()
    scope, frontend_scope, unknown, catalog_guard = scope_for(
        changed, select, paths_map, base_sha, platform.DOC_GLOBS)

    with tempfile.TemporaryDirectory(prefix="test-changed-") as tmp:
        plan, omissions = plan_for(platform, root, changed, scope, frontend_scope, tmp,
                                   args.memory_mib, catalog_guard)
        report = {"base": args.base, "merge_base": base_sha, "head": head,
                  "changed": changed, "unknown_paths": unknown,
                  "dirty": sorted(set(staged + unstaged + untracked)),
                  "plan": [describe(entry) for entry in plan],
                  "omissions": omissions, "ran": []}
        if not args.json:
            suites = [name for name in ("backend", "frontend") if scope.get(name)]
            print(f"test:changed base={base_sha or 'NONE'} head={head[:12]} "
                  f"changed={len(changed)} suites={suites}")
            for entry in plan:
                print(f"  {describe(entry)}")
            for omitted in omissions:
                print(f"  {omitted['id']}: 没跑 ({omitted['reason']})")
        if not plan:
            report["nothing_to_run"] = "这次改动落在不归后端/前端单测管的路径上"
            if args.json:
                print(json.dumps(report, ensure_ascii=False, indent=2))
            else:
                print(f"  {report['nothing_to_run']}")
            return UNKNOWN

        if args.strict:
            loose = [entry for entry in plan if not pinpointed(entry)]
            if loose:
                for entry in loose:
                    reason = (entry["evidence"].get("selection")
                              or f"mode={entry['evidence'].get('mode')}")
                    print(f"  {entry['id']}: 不能定点判定这次改动，--strict 下不跑 ({reason})")
                report["strict_refused"] = [entry["id"] for entry in loose]
                if args.json:
                    print(json.dumps(report, ensure_ascii=False, indent=2))
                return UNKNOWN

        if args.plan:
            if args.json:
                print(json.dumps(report, ensure_ascii=False, indent=2))
            return PASS

        started = time.monotonic()
        outcome = PASS
        for entry in plan:
            left = max(0.0, args.budget - (time.monotonic() - started))
            process = platform.execute(entry["command"], entry["cwd"],
                                       min(args.timeout, left), args.memory_mib,
                                       entry.get("env"))
            result = platform.resolve(process, entry["evidence"])
            row = {"id": entry["id"], "result": result["result"],
                   "seconds": result.get("seconds"),
                   "reason": result.get("reason", "")}
            report["ran"].append(row)
            if result["result"] != "pass":
                # Without this the writer sees a red line and no failing assert,
                # and the next thing they learn is at push time.
                out = platform.ANSI_RE.sub("", process.get("output", "")).strip()
                if out:
                    row["tail"] = "\n".join(out.splitlines()[-25:])
            if not args.json:
                tail = f" ({row['reason']})" if row["reason"] else ""
                print(f"  {row['id']}: {row['result']} {row['seconds']}s{tail}")
                if row.get("tail"):
                    print(row["tail"])
            outcome = max(outcome, {"pass": PASS, "fail": FAIL}.get(result["result"], UNKNOWN))
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        return outcome


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
