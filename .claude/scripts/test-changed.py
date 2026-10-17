#!/usr/bin/env python3
"""Run only the tests this working tree's change can reach — the writing loop.

``task test:changed`` answers one question quickly: *did the edit I just made
break anything it reaches?* It is meant to run between edits, next to the editor
and next to the agent writing the test that covers the edit.

``task ci:fast`` answers the other question — *is this change safe to push* — and
pays for it: the static hooks are most of that tier's 180 s, and the type checks
and the tests sit behind them. No hook runs here. What is left
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

One of ``test_plan``'s inputs is not a default: ``conservative``, which ci-fast
computes as ``bool(unknown_paths) or not base_sha``. It is the point at which the
selection stops narrowing — a path the shared map does not name, or a change no
merge base can be attributed to, widens the frontend run to the whole suite and
the backend run to the full pure fallback. This script passes that same flag, so
its selection is ci-fast's and not a narrower echo of it: without it, a change on
an unmapped path plans the i18n guards alone (no changed ``frontend/src`` file
seeds the related graph) and greens having judged nothing. ``--strict`` refuses
the widened entries when a full sweep is not what the edit-time loop should spend.

The three suites this keeps are ``backend-pure``, ``frontend-unit`` and
``frontend-i18n``. The type checks and the hooks are dropped: a type error is a
push-time verdict, it costs tens of seconds, and `task ci:fast` already owns it.
The evidence rules are kept in full — an empty selection, a selected file that
never executed, or a related run whose seeds ran alone all read as unknown (exit
2) rather than as a pass. Speed must not come from believing an empty run.

No widening is added here that the shared map does not make. A change under
``.claude/scripts/`` or ``.github/scripts/`` that the map names nowhere but
``guards`` is a case where the answer is *nothing to judge*, not a case for one:
backend unit tests do cover those scripts (``tests/unit/test_*_sweep_*.py`` and
friends load them by file path) but the AST graph cannot see an edge that is a
path in a string, so the selection pinpoints none of them and the run exits
unknown (2) — the honest report, since running the always-on tree guards and
calling it green would be a verdict about a change none of them judges.
``plan_for`` drops exactly that shape; a helper that widened the scope instead
would only relabel the same empty plan. The map does name six ``.claude/scripts/``
files for the frontend (``frontend_grade.py``, the four ratchets and
``ratchet_report.py``), and a change to one of those runs the frontend suite
rather than exiting 2.

Exit 0 pass, 1 fail, 2 unknown (nothing to judge, empty selection, no merge base,
a suite that reported a pass without running what it selected), 3 blocked (not a
git repository, or a tool a suite needs is missing). The exit code is folded the
way ci-fast folds its own — see ``worst``. Unknown is a real answer here, not a
nuisance: it means this run has no opinion about the change.
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
    import ci_fast_backend as backend
    spec = importlib.util.spec_from_file_location("ci_fast_entry", entry)
    module = importlib.util.module_from_spec(spec)
    with contextlib.chdir(root):
        spec.loader.exec_module(module)
    # The unit-test files every backend selection carries whether or not it could
    # narrow: ``select_backend`` puts them in the entry's ``files`` alongside what
    # it pinpointed. That is the structural way to ask "would this entry run the
    # guards and nothing else" — the reason string next to it is prose, and two
    # very different selections both report an empty ``required``.
    module.GUARD_TESTS = frozenset(f"{backend.UNIT_PREFIX}{name}"
                                   for name in backend.TREE_GUARDS)
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


def plan_for(platform, root, changed, scope, frontend_scope, tmp, memory_mib,
             catalog_guard, *, conservative):
    """ci-fast's plan, cut down to the suites an edit-time run wants.

    ``conservative`` is forwarded, not assumed, and it is keyword-only so that a
    caller cannot omit it by accident: it is the flag that decides whether the
    plan may narrow at all (see the module docstring), and a caller that drops it
    silently gets a smaller selection than ci-fast's for exactly the changes
    nothing else can judge.

    Kept as the plan object rather than turned into a shell string: the evidence
    that decides pass/unknown lives in it, and dropping it would leave this
    entry point believing any exit code the tests happened to hand back.

    One entry is dropped rather than run: the backend suite with an empty
    ``required`` and no fallback behind it. That happens when the backend scope is
    on and no backend module changed — a change under ``.claude/scripts/``, or
    under ``deploy/metering-proxy/``, which the map routes to the backend suite
    because it can change what the tests do. What is left of that selection is the
    always-on tree guards, and running those and reporting green would say "this
    change is fine" about a change none of them judges. The exception is a
    frontend catalog edit, where those guards are exactly what the widening was
    for.

    A *full fallback* is not that case and is not dropped: ``required`` is empty
    there too, but the entry behind it is the whole pure suite, which is what
    ci-fast runs for the same change. It is kept so that ``--strict`` has
    something to refuse; dropping it here would silently turn "run the suite" into
    "no opinion" and leave that half of ``--strict`` unreachable.
    """
    plan, omissions = platform.test_plan(
        root, changed, scope, Path(tmp), conservative, memory_mib=memory_mib,
        frontend_scope=frontend_scope)
    kept = []
    for entry in plan:
        if entry["id"] not in SUITES:
            continue
        if (entry["id"] == "backend-pure" and not entry["evidence"]["required"]
                and not catalog_guard and only_guards(platform, entry)):
            omissions.append({"id": "backend-pure", "reason":
                              "没有后端单测静态够得着这次改动，选中的只有常跑的门禁用例"})
            continue
        kept.append(entry)
    return kept, omissions


def only_guards(platform, entry):
    """Whether a backend entry would run the always-on guards and nothing else.

    ``select_backend`` puts what it pinpointed in ``required`` and everything it
    will run in ``files``. An empty ``required`` has two meanings — the guards
    alone, or the whole pure suite once nothing could be pinpointed — and only the
    second is worth a run. ``files`` is what tells them apart, without reading the
    prose reason next to it: outside a fallback the selection is exactly the guards
    the plan builds from ``TREE_GUARDS``.
    """
    evidence = entry["evidence"]
    return not set(evidence["files"]) - platform.GUARD_TESTS


def pinpointed(platform, entry):
    """Whether this plan entry judges the change cheaply enough for ``--strict``.

    ``--strict`` refuses a sweep: the backend's fallback is every pure unit test,
    the frontend's is all 700-odd specs, and both are minutes. It is not there to
    refuse a cheap judgement, so an entry that runs only the always-on tree guards
    counts as pinpointed. That shape reaches ``--strict`` in exactly one case —
    a changed i18n catalog, where the guards are the reader of the file — and
    refusing it would leave the run judging nothing while refusing a job that
    costs seconds.

    For the backend, "cheap" is ``only_guards``: the full fallback is the one
    selection that names no ``required`` files *and* carries more than the guards
    in ``files``. For the frontend it is a ``related`` seed; ``full`` is the whole
    spec suite.
    """
    evidence = entry["evidence"]
    if entry["id"] == "backend-pure":
        return bool(evidence["required"]) or only_guards(platform, entry)
    if entry["id"] == "frontend-unit":
        return evidence.get("mode") == "related"
    return True


def worst(results):
    """Fold the per-suite verdicts the way ci-fast folds its own.

    Order matters and is ci-fast's, not severity-of-a-number: a red test outranks
    a suite that could not start, which outranks a suite that reported a pass
    without running what it selected. Folding by ``max`` would report a real test
    failure as unknown (2) as soon as any other suite was inconclusive, and would
    turn ``blocked`` into 2 rather than 3 — the two ways this entry point's exit
    code stops matching ci-fast's on the same change.
    """
    if "fail" in results:
        return FAIL
    if "blocked" in results:
        return BLOCKED
    if any(r not in ("pass", "not_applicable") for r in results) or "pass" not in results:
        return UNKNOWN
    return PASS


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
    # ci-fast's own answer to "can this be narrowed at all": an unmapped path or
    # a missing merge base means it cannot, and the selection must widen the way
    # ci-fast widens it rather than fall back to whatever the graph happens to
    # see. See the module docstring.
    conservative = bool(unknown) or not base_sha

    with tempfile.TemporaryDirectory(prefix="test-changed-") as tmp:
        plan, omissions = plan_for(platform, root, changed, scope, frontend_scope,
                                   tmp, args.memory_mib, catalog_guard,
                                   conservative=conservative)
        report = {"base": args.base, "merge_base": base_sha, "head": head,
                  "changed": changed, "unknown_paths": unknown,
                  "conservative": conservative,
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
            loose = [entry for entry in plan if not pinpointed(platform, entry)]
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
        verdicts = []
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
            verdicts.append(result["result"])
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        return worst(verdicts)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
