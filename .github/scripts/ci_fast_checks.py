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
    unknown (pytest 2/3/4/5, Vitest --noPassWithNoTests on an empty graph).
  * Frontend scope only falls back to the full Vitest run when the change
    cannot be narrowed — an edit to one of the frontend config files this
    module watches (FRONTEND_META: the two package manifests, the vite and
    tsconfig files, .pre-commit-config.yaml), a .github/ edit, a test-harness
    source or an unknown scope. A config outside that list does not widen by
    itself: beside a changed source root the related run keeps it narrowed,
    and alone it seeds no root and widens like any other runtime file. A style
    edit, a prose change, a types-only input or a deletion whose
    references went with it keeps the related run seeded by the surviving roots.
    A deletion something surviving still imports widens: the run is seeded
    from the changed files, so the spec of an unchanged file that imports the
    deleted one is never selected, and the typecheck is no fallback — the app
    declares `*.vue`, Vite declares `*.css` and every other kind it serves, so
    vue-tsc resolves an import of a file that is gone, and the narrow project
    holds only the changed files. A change under src/
    that seeds no root — a locale catalog, a ratchet baseline, an asset —
    widens: `related` is seeded from the changed source files, and a file a spec
    reads off disk rather than imports creates no edge to that spec
    (styles/docBlocks.spec.ts reads its stylesheet with readFileSync; main.ts and
    the doc blocks import it, that spec does not), so the guards alone would
    pass the tier with the test that answers for the edit never run — measured:
    6px -> 8px on the padding-left of .doc-status[data-status] left both i18n
    guards green and only docBlocks.spec.ts red. A seedless change that leaves
    the suite nothing to judge — prose, a declaration file, a types-only input
    like backend/openapi.json — runs the guards alone and reports frontend-unit
    as not run rather than expanding to it. A full run must show both i18n guard
    specs actually executed. Whether a change carries frontend runtime content
    is read from the paths the path map routed into frontend scope, not from
    every changed path: a backend change that regenerates backend/openapi.json
    carries frontend scope with it, and reading its own .py files as content
    this suite must execute would run the whole Vitest suite for a document no
    test can load — the sole reader of it writes types-only.
  * The read-off-disk spec gets the same treatment without widening: a narrowed
    run is seeded with the changed roots *and* every spec that judges a
    frontend file by reading it (OFF_DISK_SPECS), because those are the specs
    the graph cannot reach. The report must show each of them ran, so a
    selection that quietly drops one is unknown rather than green. That covers
    the read that a surviving root used to hide: a component whose template a
    spec reads, a stylesheet edited beside a changed component. A deletion is a
    read only where a spec reads it, and an import is not a read: the spec that
    imports the deleted file is reached by neither the seeds nor this list, so
    that case widens instead of being seeded here.
  * The frontend's parallelism is derived from the memory ceiling the caller
    passed (vitest_workers), not pinned: a run given a ceiling that backs
    several forks gets several, and the local default still gets one.
"""

import json
import os
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from ci_fast_backend import select_backend

I18N_SPECS = ("src/i18n/catalog.spec.ts", "src/i18n/english.spec.ts")
# Specs that judge a frontend file by reading it with `node:fs` — a component's
# markup, a stylesheet, a whole directory — instead of importing it. The import
# graph has no edge for a read, so `vitest related` never selects one for the
# file it answers for: measured, a broken rule in styles/docBlocks.css left both
# i18n guards green and only styles/docBlocks.spec.ts red, and `related` on
# components/settings/BranchProtectionSection.vue does not select the spec that
# reads that component's template. They run on every frontend change that
# reaches the suite, beside the related selection, and the report must show they
# did. This is every spec under frontend/src importing `node:fs` at the time of
# writing (catalog.spec.ts is already an i18n guard; collab/ has its own config);
# a new spec that reads the tree this way belongs in this tuple. Reading through
# `?raw` is the same kind of read — measured, `related` draws no edge for it
# either — so a spec that reaches a file that way, with no import edge to it,
# belongs here too. test_ci_fast.py holds this tuple against the tree.
OFF_DISK_SPECS = (
    "src/branchProtectionSection.spec.ts",
    "src/components/ChatPanel.mentionMenu.spec.ts",
    "src/components/CheeseAvatar.spec.ts",
    "src/components/admin/AdminGrid.spec.ts",
    "src/components/base/BaseButton.spec.ts",
    "src/components/base/SettingsRow.spec.ts",
    "src/githubSettingsSections.spec.ts",
    "src/offScreenSkipping.spec.ts",
    "src/styles/docBlocks.spec.ts",
    "src/vShowDisplayUtilities.spec.ts",
    "src/views/feedback/scroll.spec.ts",
    "src/views/home/Landing.spec.ts",
)
# Config whose change invalidates the narrow type project and the related graph.
FRONTEND_META = {
    "frontend/package.json", "frontend/pnpm-lock.yaml", "frontend/vite.config.ts",
    "frontend/tsconfig.json", "frontend/tsconfig.app.json", "frontend/tsconfig.node.json",
    ".pre-commit-config.yaml",
}
# Types-only inputs: nothing under frontend/ reads these at runtime — the sole
# reader of backend/openapi.json is scripts/gen-api-types.mjs, whose output
# (src/api/openapi.d.ts) the generator writes types-only. So they widen the type
# check, never the test run: a backend route change used to run all 700+ specs
# for a document no test can load.
FRONTEND_TYPES_META = {"backend/openapi.json"}
# What the dangling-import scan reads: every file kind that can hold a module
# specifier.
SCAN_SUFFIXES = (".ts", ".tsx", ".vue", ".js", ".jsx", ".mjs", ".cjs",
                 ".css", ".scss", ".sass", ".less", ".html")
# What a specifier written without an extension can resolve to: the module
# kinds this frontend imports, the same set Vite tries for it.
SPECIFIER_SUFFIXES = (".ts", ".tsx", ".vue", ".js", ".jsx", ".mjs", ".cjs",
                      ".css", ".scss", ".sass", ".less", ".json")
#: A quoted string, the only place a module specifier appears.
SPECIFIER = re.compile(r"""["']([^"'\n]+)["']""")
VITEST_FLAGS = ["--no-passWithNoTests", "--no-allowOnly"]
# Sampled group RSS one Vitest fork is allowed. frontend/vite.config.ts sizes
# its own pool the same way ("a fork peaks at 1.7-2.1 GB": happy-dom plus its
# own Vuetify/SCSS compile), so the pool a run gets is derived from the memory
# ceiling it was given rather than pinned to a single worker. 3072 MiB is the
# local quick tier's ceiling and the work machine's session cap; a CI runner
# that can back more gets more. It is deliberately generous, which is what
# leaves the Vitest parent — in the same sampled group — its room: the whole
# frontend suite at three forks (the 4 vCPU / 16 GiB runner's own pool) peaked
# at 3580 MiB on 2026-10-12, against a 9216 MiB ceiling and this budget per fork.
MIB_PER_VITEST_WORKER = 3072
TSC_CANNOT_JUDGE = 2


def _cores():
    """Affinity-aware CPU count, mirroring the frontend's own pool rule."""
    if hasattr(os, "process_cpu_count"):
        return os.process_cpu_count() or 1
    if hasattr(os, "sched_getaffinity"):
        return len(os.sched_getaffinity(0)) or 1
    return os.cpu_count() or 1


def vitest_workers(memory_mib):
    """How many Vitest forks a run with this memory ceiling may start.

    The ceiling is what the caller says this machine can back, so the pool is
    capped by it: cores minus one (leave a core for the runner), one worker per
    MIB_PER_VITEST_WORKER, at most 16. The local default (3072 MiB) still
    yields one worker, which is what the work machine's own session cap allows.
    """
    return max(1, min(16, _cores() - 1, memory_mib // MIB_PER_VITEST_WORKER))


def vitest_flags(memory_mib):
    """The Vitest arguments for a run given this memory ceiling.

    A single worker keeps the file-in-process form the tier had while its pool
    was pinned to one: that is the local default, the run under the work
    machine's own 3 GiB cap. Several workers means the ceiling backs a pool, so
    the serial-file pin goes and the pool size is what bounds memory.
    """
    workers = vitest_workers(memory_mib)
    serial = ["--no-file-parallelism"] if workers == 1 else []
    return [f"--maxWorkers={workers}", *serial, *VITEST_FLAGS]


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


def _resolves_to(specifier, importer, frontend):
    """The file a module specifier names, or None if it names no local file.

    Vite's own rules for the ones this repository uses: ``@/`` is frontend/src,
    a leading ``/`` is the frontend root, and ``./``/``../`` are relative to the
    importer. A bare name is a package — except in a stylesheet, where
    ``@import "theme.css"`` and ``url(icon.svg)`` are relative too.
    """
    specifier = specifier.split("?")[0].split("#")[0]
    if specifier.startswith("@/"):
        base, rest = frontend / "src", specifier[2:]
    elif specifier.startswith("/"):
        base, rest = frontend, specifier[1:]
    elif specifier.startswith("."):
        base, rest = importer.parent, specifier
    elif "/" not in specifier and Path(specifier).suffix in SPECIFIER_SUFFIXES:
        base, rest = importer.parent, specifier
    else:
        return None
    try:
        return (base / rest).resolve()
    except OSError:
        return None


def _candidates(target):
    """What an extension-less specifier can resolve to, plus the file itself."""
    yield target
    if target.suffix:
        return
    for suffix in SPECIFIER_SUFFIXES:
        yield Path(str(target) + suffix)
    for suffix in SPECIFIER_SUFFIXES:
        yield target / f"index{suffix}"


def _imports_a_deleted_path(root, deleted):
    """Whether a file that survived still imports one of the ``deleted`` paths.

    A narrowed run is seeded from the changed roots, so it selects the specs
    that reach a root — never the spec of an unchanged file that imports a
    deleted one. Nothing else judges that import either: the app declares
    ``*.vue`` and Vite declares ``*.css`` and every asset kind it serves, so a
    module that is gone still resolves for vue-tsc, and the changed-files
    project holds only what the change set names.

    Only a specifier that resolves to the deleted file counts. A basename
    somewhere in the text does not: this tree talks about its own files in
    comments constantly ("see ./index.ts", "plugins/index.ts"), and reading
    those as references would run the whole suite for a rename nobody left
    dangling. Deletions that took their references with them — a component
    deleted beside its spec, a rename — resolve to nothing here and keep the run
    narrowed. What this cannot see is a reference the graph itself cannot see:
    a computed specifier, or a file a glob picks up (`import.meta.glob` in
    commands/palette/sources.ts and views/demo/scenes/index.ts, neither of which
    matches a module kind either of them is handed).
    """
    if not deleted:
        return False
    frontend = root / "frontend"
    gone = {(root / p).resolve() for p in deleted}
    for directory, dirnames, filenames in os.walk(frontend):
        dirnames[:] = [d for d in dirnames if d != "node_modules"]
        for name in filenames:
            if not name.endswith(SCAN_SUFFIXES):
                continue
            importer = Path(directory) / name
            try:
                text = importer.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            # No substring pre-filter on the basename: the common specifier
            # drops the extension (`'@/stores/workspace'` for workspace.ts), so
            # the basename of a deleted module often appears in no importer at
            # all — filtering on it would miss exactly the imports this exists
            # to find.
            for specifier in SPECIFIER.findall(text):
                target = _resolves_to(specifier, importer, frontend)
                if target and any(candidate in gone for candidate in _candidates(target)):
                    return True
    return False


def test_plan(root, changed, scope, tmp, conservative=False, memory_mib=3072, frontend_scope=None):
    plan, omissions = [], []
    flags = vitest_flags(memory_mib)
    if scope.get("frontend"):
        frontend_paths = [p for p in changed if p.startswith("frontend/")]
        surviving = [p for p in frontend_paths if (root / p).is_file()]
        deleted = [p for p in frontend_paths if not (root / p).is_file()]
        # A declaration file seeds nothing: it compiles to no module, so the
        # related graph has no edge to follow from it. Passing one as a root
        # would select no test file at all and turn the run unknown; it still
        # counts for types, below.
        roots = [p.removeprefix("frontend/") for p in surviving
                 if p.startswith("frontend/src/") and p.endswith((".ts", ".tsx", ".vue", ".js", ".jsx"))
                 and not p.endswith(".d.ts")]
        # A frontend change with no surviving source root cannot be narrowed
        # when it carries runtime content: `related` is seeded from the changed
        # source files, and a file the suite reads off disk rather than imports
        # has no edge to the spec that reads it — catalog.spec.ts scans the
        # locale JSON with readFileSync, styles/docBlocks.spec.ts reads its
        # stylesheet the same way. The guards do not stand in for it either:
        # measured, a broken rule in docBlocks.css (padding-left 6px -> 8px on
        # .doc-status[data-status]) left both i18n guards green and only
        # docBlocks.spec.ts red. Prose is judged by the doc checks, and a
        # declaration file or a types-only input compiles to no runtime, so those
        # may leave the suite nothing to judge. Anything else is runtime content
        # wherever it sits: a path outside frontend/ can still be routed into
        # frontend scope (a site stylesheet, a ratchet script) and is still
        # content the suite would have to read.
        # Which paths carry that content is the caller's answer when it has one:
        # the paths the path map routed into frontend scope. A backend change
        # rides into this branch through a regenerated backend/openapi.json, and
        # its own .py files are not content this suite can execute — the sole
        # reader of the document writes types-only (FRONTEND_TYPES_META).
        carries_runtime = any(not p.endswith((".md", ".d.ts"))
                              and p not in FRONTEND_TYPES_META
                              for p in (changed if frontend_scope is None else frontend_scope))
        # Config, ambient hooks, test-harness sources and unknown scopes cannot
        # be narrowed: nothing tells the related graph what they affect.
        unjudgeable = (conservative
                       or any(p in FRONTEND_META for p in changed)
                       or any(p.startswith(".github/") for p in changed)
                       or any(p.startswith(("frontend/src/test/", "frontend/scripts/")) for p in frontend_paths)
                       or (carries_runtime and not roots)
                       # Last: the scan reads the frontend tree, so it runs only
                       # for a deletion nothing above has already widened.
                       or _imports_a_deleted_path(root, deleted))
        output = tmp / "vitest.json"
        i18n_output = tmp / "i18n.json"
        i18n_check = {"id": "frontend-i18n", "cwd": root / "frontend",
                      "command": ["pnpm", "exec", "vitest", "run", *I18N_SPECS, *flags,
                                  "--reporter=json", "--outputFile", str(i18n_output)],
                      "evidence": {"kind": "vitest", "path": str(i18n_output), "required_files": list(I18N_SPECS)}}
        if unjudgeable:
            # The full run must be shown to have executed the i18n guards, not
            # merely to have matched them: require both by name.
            plan.append({"id": "frontend-unit", "cwd": root / "frontend",
                         "command": ["pnpm", "exec", "vitest", "run", *flags,
                                     "--reporter=json", "--outputFile", str(output)],
                         "evidence": {"kind": "vitest", "path": str(output), "mode": "full",
                                      "required_files": list(I18N_SPECS)}})
        else:
            # A deletion, a locale/style edit or a prose change does not widen
            # the graph: a changed src root still seeds the related run, and
            # whatever cannot seed it runs nothing (frontend-unit is reported as
            # an omission) instead of the whole suite. Filesystem scans are not
            # edges in that graph, so the specs that judge a file by reading it
            # (OFF_DISK_SPECS) and the i18n guards run explicitly alongside the
            # related selection rather than being assumed into it.
            off_disk = [p for p in OFF_DISK_SPECS if (root / "frontend" / p).is_file()]
            if roots:
                plan.append({"id": "frontend-unit", "cwd": root / "frontend",
                             "command": ["pnpm", "exec", "vitest", "related", "--run", *roots, *off_disk, *flags,
                                         "--reporter=json", "--outputFile", str(output)],
                             "evidence": {"kind": "vitest", "path": str(output), "mode": "related",
                                          "required_files": off_disk,
                                          "roots": roots}})
            else:
                omissions.append({"id": "frontend-unit",
                                  "reason": "no changed frontend source seeds the related graph; i18n guards only"})
            plan.append(i18n_check)
        ts_files = [p for p in surviving if p.endswith((".ts", ".tsx", ".vue"))]
        # Only a deleted module can leave importers dangling project-wide; a
        # deleted spec or asset cannot, and the narrow project judges the rest.
        deleted_sources = [p for p in deleted
                           if p.endswith((".ts", ".tsx", ".vue", ".js", ".jsx"))
                           and ".spec." not in p and ".test." not in p]
        full_types = (conservative
                      or any(p in FRONTEND_META or p in FRONTEND_TYPES_META or p.endswith(".d.ts") for p in changed)
                      or any(p.startswith(".github/") for p in changed)
                      or bool(deleted_sources))
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
        nodes = tmp / "pytest-nodes.json"
        # The audit plugin sits beside the runner; pytest imports it by module
        # name, so its directory goes on PYTHONPATH for this one run only.
        scripts = str(root / ".github" / "scripts")
        pythonpath = os.pathsep.join(
            [scripts] + ([os.environ["PYTHONPATH"]] if os.environ.get("PYTHONPATH") else []))
        env = {"TEST_PG_BASE": "postgresql://cheese:cheese@127.0.0.1:1/cheese_ci_fast",
               "DATABASE_URL": "postgresql+asyncpg://cheese:cheese@127.0.0.1:1/cheese_ci_fast",
               "PYTHONPATH": pythonpath, "CI_FAST_NODES": str(nodes)}
        plan.append({"id": "backend-pure", "cwd": root / "backend", "env": env,
                     "command": ["uv", "run", "--no-sync", "pytest", *selected, "-m", "pure", "-q",
                                 "--ignore=tests/forgejo", "-k", "not kotlin",
                                 "-p", "ci_fast_pytest_plugin", "--junitxml", str(output)],
                     "evidence": {"kind": "junit", "path": str(output), "nodes": str(nodes),
                                  "files": selected, "required": targets, "selection": reason}})
    return plan, omissions


def _case_file(case):
    """The file-or-class a JUnit testcase names, for matching a required path.

    pytest 9 writes no ``file`` attribute: ``classname`` is the dotted module
    path, with the class appended for a class-based test
    (``tests.unit.test_x.TestC``). An explicit ``file`` wins when a runner
    provides one; otherwise keep the classname so its module prefix can still
    be matched against a required ``.py`` path.
    """
    if case.get("file"):
        return case.get("file")
    return case.get("classname") or ""


def _covers(name, required):
    """Whether a testcase file/classname is evidence that ``required`` ran."""
    if not name:
        return False
    if name == required or name.endswith("/" + required):
        return True
    module = (required[:-3] if required.endswith(".py") else required).replace("/", ".")
    return name == module or name.startswith(module + ".")


def _runs(cases, required):
    """required file paths that produced no matching testcase at all."""
    names = [_case_file(case) for case in cases]
    return [r for r in required if not any(_covers(name, r) for name in names)]


def _audit(evidence, details):
    """Verify the pure nodes the run selected actually ran.

    The sidecar records the selection the run itself made — its selected pure
    nodes, the nodes ``-m``/``-k`` removed with their layer, and every outcome —
    so this checks what a JUnit report alone cannot: that no pure node was
    hidden by a filter, and that every selected pure node executed. An excluded
    ``integration``/``contract`` node is the layer working as designed (recorded
    as not_run, owned by full CI), not treated as missing coverage; a required
    file with no pure node at all is the same, but a change whose modules have
    no pure consumer at all is unknown.
    """
    data = json.loads(Path(evidence["nodes"]).read_text())
    selected_pure = {item["node"] for item in data.get("selected", []) if item.get("layer") == "pure"}
    hidden = sorted(item["node"] for item in data.get("deselected", []) if item.get("layer") == "pure")
    executed = data.get("executed", [])
    ran = {e["node"] for e in executed if e.get("when") == "call" and e.get("outcome") == "passed"}
    failures = sum(e.get("outcome") in ("failed", "error") for e in executed)
    unrun = sorted(selected_pure - ran)
    pure_files = {node.split("::", 1)[0] for node in selected_pure}
    required = evidence.get("required", [])
    uncovered = [r for r in required if r not in pure_files]
    details.update(selected_pure=len(selected_pure), executed=len(ran),
                   hidden=hidden, unrun=unrun, not_run_pure=uncovered)
    if failures:
        return "fail", "the pure run contains errors/failures"
    if hidden:
        return "unknown", f"-m/-k deselected pure tests (hidden={hidden})"
    if unrun:
        return "unknown", f"pure tests selected but not executed (unrun={unrun})"
    if not selected_pure:
        return "unknown", "the pure selection is empty"
    if required and not any(r in pure_files for r in required):
        return "unknown", "no pure test reaches the changed modules"
    return "pass", ""


def validate_evidence(evidence):
    """Judge one report file alone. Types are decided by the exit code, in resolve()."""
    kind = evidence["kind"]
    details = {k: v for k, v in evidence.items() if k not in ("path", "nodes")}
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
            missing = _runs(cases, evidence.get("required", []))
    except (OSError, ValueError, KeyError, TypeError, ET.ParseError) as exc:
        return {"result": "unknown", "reason": f"unreadable {kind} report: {exc}", "evidence": details}
    details.update(passed=passed, skipped=skipped, errors=errors, missing=missing)
    if errors:
        return {"result": "fail", "reason": "test report contains errors/failures", "evidence": details}
    if evidence.get("nodes"):
        # The run left its own account of the selection; trust that over the
        # report file, which cannot show what a filter removed.
        try:
            outcome, reason = _audit(evidence, details)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return {"result": "unknown", "reason": f"unreadable node audit: {exc}", "evidence": details}
        if outcome != "pass":
            return {"result": outcome, "reason": reason, "evidence": details}
        return {"result": "pass", "evidence": details}
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
