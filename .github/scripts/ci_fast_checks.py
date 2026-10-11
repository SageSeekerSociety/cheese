"""Which bounded checks the fast tier runs, and how their evidence is judged.

Test selection is deliberately honest about its edges. The scope comes from the
Required CI path map; this module turns "frontend" / "backend" into concrete
commands and turns each command's report into a verdict nobody can fake:

  * A green suite needs a readable report that shows the selected tests actually
    ran. An empty selection, a skipped test, a missing report, a selected file
    with no executed case, or a process that died by signal is unknown (exit 2),
    never a pass. Running only the always-on guards never proves a changed
    module has coverage — its consumer files must appear in the report.
  * The report decides pass/fail for suites; the exit code only widens it to
    unknown (pytest 2/3/4/5, Vitest --no-passWithNoTests on an empty graph).
  * Frontend scope with no changed source root, a deleted/config/.github edit or
    an unknown path falls back to the full Vitest run; the related graph is only
    trusted when it has real roots to seed it, and the full run must show both
    i18n guard specs actually executed.
"""

import json
from pathlib import Path
import xml.etree.ElementTree as ET

from ci_fast_backend import select_backend

I18N_SPECS = ("src/i18n/catalog.spec.ts", "src/i18n/english.spec.ts")
# Config whose change invalidates the narrow type project and the related graph.
FRONTEND_META = {
    "frontend/package.json", "frontend/pnpm-lock.yaml", "frontend/vite.config.ts",
    "frontend/tsconfig.json", "frontend/tsconfig.app.json", "frontend/tsconfig.node.json",
    "backend/openapi.json", ".pre-commit-config.yaml",
}
VITEST_FLAGS = ["--maxWorkers=1", "--no-file-parallelism", "--no-passWithNoTests", "--no-allowOnly"]
TSC_CANNOT_JUDGE = 2


def _narrow_config(root, tmp, ts_files):
    """A project that type-checks only the changed files, inheriting the real one.

    ``types: [...]`` and ``paths`` in frontend/tsconfig.json resolve relative to
    the config that declares them. In a temp dir that means the compiler cannot
    find ``vitest/globals`` / ``vue/jsx`` / ``vuetify`` (no node_modules) and
    ``@/*`` paths point nowhere, so vue-tsc exits 2 without judging anything.
    Bridge node_modules with a symlink and pin the absolute baseUrl/paths.
    """
    config = tmp / "tsconfig.json"
    (tmp / "node_modules").symlink_to(root / "frontend" / "node_modules")
    config.write_text(json.dumps({
        "extends": str(root / "frontend/tsconfig.json"),
        "files": [str(root / p) for p in ts_files],
        "include": [str(root / "frontend/env.d.ts"), str(root / "frontend/src/**/*.d.ts")],
        "exclude": [], "references": [],
        "compilerOptions": {
            "noEmit": True,
            "baseUrl": str(root / "frontend"),
            "paths": {"@/*": ["src/*"]},
        },
    }))
    return config


def test_plan(root, changed, scope, tmp, conservative=False):
    plan, omissions = [], []
    if scope.get("frontend"):
        frontend_paths = [p for p in changed if p.startswith("frontend/")]
        roots = [p.removeprefix("frontend/") for p in frontend_paths
                 if p.startswith("frontend/src/") and p.endswith((".ts", ".tsx", ".vue", ".js", ".jsx"))
                 and (root / p).is_file()]
        deleted = [p for p in frontend_paths if not (root / p).is_file()]
        # A narrowed run is trusted only when a changed source root seeds the
        # related graph. Anything else — no root, a meta/config/.github edit, a
        # deleted path, an unknown/no-base scope — runs everything.
        full = (conservative
                or any(p in FRONTEND_META for p in changed)
                or any(p.startswith(".github/") for p in changed)
                or bool(deleted)
                or any(p.startswith(("frontend/src/test/", "frontend/scripts/")) for p in frontend_paths)
                or not roots)
        output = tmp / "vitest.json"
        command = ["pnpm", "exec", "vitest"]
        command += ["run"] if full else ["related", "--run", *roots]
        command += [*VITEST_FLAGS, "--reporter=json", "--outputFile", str(output)]
        evidence = {"kind": "vitest", "path": str(output), "mode": "full" if full else "related", "roots": roots}
        if full:
            # The full run must be shown to have executed the i18n guards, not
            # merely to have matched them: require both by name.
            evidence["required_files"] = list(I18N_SPECS)
        plan.append({"id": "frontend-unit", "command": command, "cwd": root / "frontend", "evidence": evidence})
        if not full:
            # Filesystem scans are not edges in Vitest's related graph, so run
            # the i18n guard specs explicitly instead of assuming related has them.
            i18n_output = tmp / "i18n.json"
            plan.append({"id": "frontend-i18n", "cwd": root / "frontend",
                         "command": ["pnpm", "exec", "vitest", "run", *I18N_SPECS, *VITEST_FLAGS,
                                     "--reporter=json", "--outputFile", str(i18n_output)],
                         "evidence": {"kind": "vitest", "path": str(i18n_output), "required_files": list(I18N_SPECS)}})
        ts_files = [p for p in frontend_paths if p.endswith((".ts", ".tsx", ".vue")) and (root / p).is_file()]
        full_types = (conservative
                      or any(p in FRONTEND_META or p.endswith(".d.ts") for p in changed)
                      or bool(deleted)
                      or any(p.startswith(".github/") for p in changed))
        if full_types:
            # Config/ambient/deletion cannot be judged by a project built from a
            # file list, so fall back to the repository's own typecheck.
            plan.append({"id": "frontend-typecheck-full", "command": ["pnpm", "run", "typecheck"],
                         "cwd": root / "frontend", "evidence": {"kind": "types", "mode": "full-config-fallback"}})
        elif ts_files:
            config = _narrow_config(root, tmp, ts_files)
            plan.append({"id": "frontend-typecheck-changed", "cwd": root / "frontend",
                         "command": ["node", "scripts/tsc-ratchet.mjs", "--project", str(config), "--json"],
                         "evidence": {"kind": "types", "files": ts_files,
                                      "limitation": "changed roots and imports, not all downstream consumers"}})
        else:
            omissions.append({"id": "frontend-typecheck-changed",
                              "reason": "no changed TS/Vue files; types not applicable"})
    if scope.get("backend"):
        selected, reason, targets = select_backend(root, changed, conservative)
        output = tmp / "pytest.xml"
        env = {"TEST_PG_BASE": "postgresql://cheese:cheese@127.0.0.1:1/cheese_ci_fast",
               "DATABASE_URL": "postgresql+asyncpg://cheese:cheese@127.0.0.1:1/cheese_ci_fast"}
        plan.append({"id": "backend-pure", "cwd": root / "backend", "env": env,
                     "command": ["uv", "run", "--no-sync", "pytest", *selected, "-m", "pure", "-q",
                                 "--ignore=tests/forgejo", "-k", "not kotlin", "--junitxml", str(output)],
                     "evidence": {"kind": "junit", "path": str(output), "files": selected,
                                  "required": targets, "selection": reason}})
    return plan, omissions


def _case_file(case):
    """The repository-relative file a pytest JUnit testcase came from."""
    if case.get("file"):
        return case.get("file")
    classname = case.get("classname")  # e.g. tests.unit.test_x
    return classname.replace(".", "/") + ".py" if classname else ""


def _runs(files, required):
    """required file paths that produced no testcase at all."""
    return [r for r in required if not any(f == r or f.endswith("/" + r) for f in files)]


def validate_evidence(evidence):
    """Judge one report file alone. Types are decided by the exit code, in resolve()."""
    kind = evidence["kind"]
    details = {k: v for k, v in evidence.items() if k != "path"}
    try:
        if kind == "vitest":
            data = json.loads(Path(evidence["path"]).read_text())
            passed = int(data.get("numPassedTests", 0))
            skipped = int(data.get("numPendingTests", 0)) + int(data.get("numTodoTests", 0))
            errors = int(data.get("numFailedTests", 0))
            files = {row["name"] for row in data.get("testResults", [])}
            missing = [p for p in evidence.get("required_files", [])
                       if not any(f.endswith("/" + p) for f in files)]
        else:
            xml = ET.parse(evidence["path"]).getroot()
            cases = list(xml.iter("testcase"))
            skipped = sum(case.find("skipped") is not None for case in cases)
            errors = sum(case.find("error") is not None or case.find("failure") is not None for case in cases)
            passed = len(cases) - skipped - errors
            missing = _runs({_case_file(case) for case in cases}, evidence.get("required", []))
    except (OSError, ValueError, KeyError, TypeError, ET.ParseError) as exc:
        return {"result": "unknown", "reason": f"unreadable {kind} report: {exc}", "evidence": details}
    details.update(passed=passed, skipped=skipped, errors=errors, missing=missing)
    if errors:
        return {"result": "fail", "reason": "test report contains errors/failures", "evidence": details}
    if not passed or skipped or missing:
        return {"result": "unknown",
                "reason": f"empty/skipped/uncovered tests (missing={missing})", "evidence": details}
    return {"result": "pass", "evidence": details}


def resolve(process, evidence):
    """Fold a bounded process run and its report into one verdict.

    The report decides pass/fail for suites; a non-zero exit that is not a test
    failure (pytest 2 interrupted / 3 internal / 4 usage / 5 no tests, Vitest
    --no-passWithNoTests on an empty graph, a signal) is unknown, never a red
    check and never a pass. A report that claims success while the process
    failed is distrusted. Hard process failures (timeout/memory/budget/blocked)
    pass through unchanged.
    """
    if process["result"] in ("timeout", "memory", "budget", "blocked"):
        return process
    kind = evidence["kind"]
    rc = process.get("returncode")
    if kind == "types":
        detail = {"evidence": {k: v for k, v in evidence.items() if k != "path"}}
        if process["result"] == "pass":
            return {**process, **detail}
        if rc == 1:
            return {**process, "result": "fail",
                    "reason": "new type errors over the ratchet baseline", **detail}
        if rc == TSC_CANNOT_JUDGE:
            return {**process, "result": "blocked", "reason": "vue-tsc could not judge (exit 2)", **detail}
        return {**process, "result": "unknown", "reason": f"type check exited {rc}", **detail}
    verdict = validate_evidence(evidence)
    outcome = verdict.pop("result")
    if outcome in ("fail", "unknown"):
        return {**process, "result": outcome, **verdict}
    if process["result"] == "pass":
        return {**process, **verdict}
    return {**process, "result": "unknown", "reason": f"report passed but the process exited {rc}", **verdict}
