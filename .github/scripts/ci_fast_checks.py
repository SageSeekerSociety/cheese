"""Which bounded checks the fast tier runs, and how their evidence is judged.

Test selection is deliberately honest about its edges. The scope comes from the
Required CI path map; this module turns "frontend" / "backend" into concrete
commands and turns each command's report into a verdict nobody can fake:

  * A green suite needs a readable report that shows the selected tests actually
    ran. An empty selection, a skipped test that does not name a capability this
    machine lacks, a missing report, a selected file with no executed case, or a
    process that died by signal is unknown (exit 2), never a pass. Running only the always-on guards never proves a changed
    module has coverage — its consumer files must appear in the report: the
    specs that read the tree off disk run whatever changed, so passing only them
    is an empty selection wearing their exit code.
  * The report decides pass/fail for suites; the exit code only widens it to
    unknown (pytest 2/3/4/5, Vitest --no-passWithNoTests on an empty graph).
    A file that failed before running a test — a collection/import error or a
    broken beforeAll — is a failure, not an empty selection. (An unhandled
    error that never reaches a file's report stays a process failure, which
    reads as unknown.) Vitest counts it in numFailedTestSuites while numFailedTests
    stays 0, so the "no tests ran" rule used to report a run of failing suites
    as nothing to judge (measured: 408 s of suites that could not load read as
    "empty/skipped/uncovered tests"). A worker crash is the one exception: it
    says nothing about the change and is unknown.
  * Frontend scope only falls back to the full Vitest run when the change
    cannot be narrowed — an edit to one of the frontend config files this
    module watches (FRONTEND_META: the two package manifests, the vite and
    tsconfig files, .pre-commit-config.yaml), a test-harness source or an
    unknown scope. A .github/ edit does not widen: no workflow or script under
    it is imported by the frontend graph, and a routine workflow change was
    spending the whole 850-second suite on an answer the full tier gives after
    landing. A config outside that list does not widen by itself: beside a
    changed source root the related run keeps it narrowed,
    and alone it seeds no root and widens like any other runtime file. A style
    edit, a prose change, a declaration file or a deletion whose
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
    the suite nothing to judge — prose, a declaration file — runs the guards
    alone and reports frontend-unit as not run rather than expanding to it. A
    full run must show both i18n guard specs actually executed. Whether a change
    carries frontend runtime content is read from the paths the path map routed
    into frontend scope, not from every changed path: a change that also
    touches the backend brings its .py files along, and those are not content
    this suite can execute.
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
  * The backend's parallelism is derived the same way (pytest_workers): the
    full suite runs ``-n auto``; this tier gets one worker per 1536 MiB, at
    most the CPUs, and the local default still runs one process. When two
    trees run at once the backend is told the wall clock it lost through
    CHEESE_TEST_CEILING_SCALE (ceiling_scale), which multiplies the layer
    ceilings in ``backend/tests/conftest.py``; full CI never sets it and keeps
    the strict numbers.
"""

import json
import os
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from ci_fast_backend import (
    TREE_GUARDS,
    is_migration_path,
    pinned_harness_gap,
    select_backend,
)

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
# a new spec that reads the tree this way belongs in this tuple. `?raw` is left
# out: it was measured once as drawing no `related` edge either, and the run
# that would confirm it cannot run here — it is killed on the work machine's
# 1.5 GiB. The two mistakes are not equal: seeding a spec that does have an edge
# turns an empty selection green, while leaving one out only ever reads unknown,
# so this tuple errs that way. test_ci_fast.py holds it against the tree.
OFF_DISK_SPECS = (
    "src/branchProtectionSection.spec.ts",
    "src/components/ChatPanel.mentionMenu.spec.ts",
    "src/components/CheeseAvatar.spec.ts",
    "src/components/admin/AdminGrid.spec.ts",
    "src/components/base/BaseButton.spec.ts",
    "src/components/base/SettingsRow.spec.ts",
    # Reads BottomAppBar.vue off disk to hold the overlay fix in place. Added in
    # #3521 (be7a1d36, 2026-10-12 16:38) without a tuple entry; that commit's
    # cifast check was skipped, so this list's own test was the first to notice.
    "src/components/common/Navigation/BottomAppBar.spec.ts",
    "src/offScreenSkipping.spec.ts",
    "src/styles/docBlocks.spec.ts",
    "src/vShowDisplayUtilities.spec.ts",
    "src/views/demo/DemoTokensPage.spec.ts",
    "src/views/demo/demoTokens.spec.ts",
    "src/views/demo/demoTransitions.spec.ts",
    "src/views/feedback/scroll.spec.ts",
    "src/views/home/Landing.spec.ts",
    # Asserts four deleted modules are not back. It reads the tree with
    # existsSync rather than importing what it judges, so no changed root can
    # seed it; without an entry here a change that restored one of the four
    # would select no test file that notices.
    "src/retiredModules.spec.ts",
)
# Config whose change invalidates the narrow type project and the related graph.
FRONTEND_META = {
    "frontend/package.json", "frontend/pnpm-lock.yaml", "frontend/vite.config.ts",
    "frontend/tsconfig.json", "frontend/tsconfig.app.json", "frontend/tsconfig.node.json",
    ".pre-commit-config.yaml",
}
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
# Sampled group RSS one pytest-xdist worker is allowed. A single-process pure
# run peaked at 1.1 GiB across the 12 CI runs measured on 2026-10-19, and the
# workers share one sampled ceiling. 1536 MiB leaves that peak ~35% headroom
# while keeping the work machine's 3072 MiB ceiling at two workers.
MIB_PER_PYTEST_WORKER = 1536
TSC_CANNOT_JUDGE = 2
# A Vitest pool/worker failure, not a test failure: the worker died (killed,
# out of memory) before the suite could report. Vitest puts one of these in the
# file's `message`; it says nothing about the change, so it is unknown rather
# than a red check. Both spellings are from vitest 5.0.3's pool code.
WORKER_CRASH = re.compile(
    r"Worker exited unexpectedly|\[vitest-pool\]: Worker .* emitted error")


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


def pytest_workers(memory_mib):
    """How many pytest-xdist workers a run with this memory ceiling may start.

    The full backend suite runs ``-n auto``; the fast tier ran one process and
    spent its whole budget waiting. The ceiling that bounds the run is the same
    sampled group RSS the caller passes, so the pool is capped by it as well:
    one worker per MIB_PER_PYTEST_WORKER, at most the CPUs, and at least one.
    The work machine's 3072 MiB cap still backs two; the 9216 MiB a CI draft
    gets backs the runner's four.
    """
    return max(1, min(_cores(), memory_mib // MIB_PER_PYTEST_WORKER))


def ceiling_scale(plan, memory_mib, jobs):
    """The wall-clock allowance for the checks this tier starts at once.

    The layer ceilings in ``backend/tests/conftest.py`` were measured on a run
    that had the machine to itself. When two trees run together, every test in
    the backend still takes the same number of CPU cycles but gets
    ``demand / cores`` times the wall clock, and the pure layer's 10 s ceiling
    would fail it for the machine sharing rather than for a wait. The factor
    covers a second tree's demand, not the backend pool itself: a dedicated CI
    runner ran the whole pure selection at ``-n 4`` with no test over its
    ceiling, while an overloaded shared box pushed tree guards to 17.7 s. This
    returns the factor the backend run multiplies its ceilings by — only when
    the plan really has another tree to share with, and only above one. Full
    CI never asks: it runs one tree at a time and keeps the strict numbers.
    """
    if jobs <= 1 or len({str(check["cwd"]) for check in plan}) <= 1:
        return 1.0
    ids = {check["id"] for check in plan}
    demand = 0
    if "backend-pure" in ids:
        demand += pytest_workers(memory_mib)
    if ids & {"frontend-unit", "frontend-i18n"}:
        # The Vitest parent runs beside its forks and is part of the load.
        demand += vitest_workers(memory_mib) + 1
    if not demand:
        return 1.0
    return max(1.0, demand / max(1, _cores()))


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
    importer. A bare name is a package, with one exception: a bare name carrying
    one of the module suffixes this frontend imports is read as relative to the
    importer, because that is the shape a stylesheet writes a local file in
    (``@import "theme.css"``). An extension-less bare name is left unresolved:
    no stylesheet here writes ``@import "theme"``, and taking any bare word as a
    path would read package CSS as an importer of a deleted file.
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
    """What a specifier can resolve to, plus the file it names itself.

    A specifier that ends in a module kind this frontend imports is that file.
    One that ends in anything else — no extension at all, or an extension the
    resolver does not know — still gets the known kinds appended, because a
    dot in a specifier says nothing about where the file name ends:
    ``./x.spec.helper`` names ``x.spec.helper.ts``, and reading ``.helper`` as
    the whole name matched no file at all.
    """
    yield target
    if target.suffix in SPECIFIER_SUFFIXES:
        return
    for suffix in SPECIFIER_SUFFIXES:
        yield Path(str(target) + suffix)
    for suffix in SPECIFIER_SUFFIXES:
        yield target / f"index{suffix}"


SPEC_SUFFIXES = (".spec.ts", ".spec.tsx", ".spec.js", ".spec.jsx",
                 ".test.ts", ".test.tsx", ".test.js", ".test.jsx")


def _direct_specs(root, roots):
    """The specs that name a changed module themselves.

    The quick tier used Vitest's ``related``, which follows the import graph
    transitively: a change to a core module selected most of the suite
    (measured 856 s on PR #4448, near the per-check timeout, and the report
    came back empty). One layer is what a fast first judgment needs — the
    specs that name the changed file directly, plus a changed spec itself.
    Deeper consumers are left to full CI, and the off-disk specs still run
    with them.
    """
    frontend = root / "frontend"
    seeds = set()
    for path in roots:
        seeds.update(_candidates((root / "frontend" / path).resolve()))
    selected = set()
    for directory, dirnames, filenames in os.walk(frontend):
        dirnames[:] = [d for d in dirnames if d != "node_modules"]
        for name in filenames:
            if not name.endswith(SPEC_SUFFIXES):
                continue
            spec = Path(directory) / name
            rel = spec.relative_to(frontend).as_posix()
            if rel in roots:
                selected.add(rel)
                continue
            try:
                text = spec.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for specifier in SPECIFIER.findall(text):
                target = _resolves_to(specifier, spec, frontend)
                if target and any(candidate in seeds for candidate in _candidates(target)):
                    selected.add(rel)
                    break
    return sorted(selected)


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
        # A demo scene is data its loader globs in (`scenes/index.ts`); the
        # static graph has no edge from the JSON to the specs that read the
        # scenes, so seed the loader. Without this a one-line scene edit
        # widened to the whole suite (measured 900 s, the per-check timeout).
        # A deleted scene seeds it too: the loader's glob still names the set.
        if any(p.startswith("frontend/src/views/demo/scenes/") and p.endswith(".json")
               for p in frontend_paths):
            loader = "src/views/demo/scenes/index.ts"
            if (root / "frontend" / loader).is_file() and loader not in roots:
                roots.append(loader)
        # A frontend change with no surviving source root cannot be narrowed
        # when it carries runtime content: `related` is seeded from the changed
        # source files, and a file the suite reads off disk rather than imports
        # has no edge to the spec that reads it — catalog.spec.ts scans the
        # locale JSON with readFileSync, styles/docBlocks.spec.ts reads its
        # stylesheet the same way. The guards do not stand in for it either:
        # measured, a broken rule in docBlocks.css (padding-left 6px -> 8px on
        # .doc-status[data-status]) left both i18n guards green and only
        # docBlocks.spec.ts red. Prose is judged by the doc checks, and a
        # declaration file compiles to no runtime, so those
        # may leave the suite nothing to judge. Anything else is runtime content
        # wherever it sits: a path outside frontend/ can still be routed into
        # frontend scope (a site stylesheet, a ratchet script) and is still
        # content the suite would have to read.
        # Which paths carry that content is the caller's answer when it has one:
        # the paths the path map routed into frontend scope. A change that also
        # touches the backend brings its .py files along, and those are not
        # content this suite can execute. Nor is anything under .github/: the
        # map routes a workflow into frontend scope because it configures the
        # suite, not because Vitest imports it. Counting it as content made a
        # workflow-only edit (or a workflow beside one changed component) fall
        # back to the full 850-second run, which the full tier answers after
        # landing anyway.
        runtime_paths = changed if frontend_scope is None else frontend_scope
        carries_runtime = any(
            not p.startswith(".github/") and not p.endswith((".md", ".d.ts"))
            for p in runtime_paths)
        # Config, ambient hooks, test-harness sources and unknown scopes cannot
        # be narrowed: nothing tells the related graph what they affect.
        # A .github/ edit is not one of them: no workflow or ci script is
        # imported by the frontend graph, so it leaves the related run seeded
        # by the surviving roots and, on its own, runs no unit test at all
        # (frontend-unit is reported as an omission) rather than the full suite.
        unjudgeable = (conservative
                       or any(p in FRONTEND_META for p in changed)
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
            # One layer, not the transitive graph: the specs that name a
            # changed file themselves, plus a changed spec. Following imports
            # all the way from a core module selected most of the suite
            # (856 s on PR #4448); full CI owns the deeper consumers. A file a
            # spec reads off disk has no import edge at all, so those specs
            # (OFF_DISK_SPECS) and the i18n guards run explicitly beside it.
            off_disk = [p for p in OFF_DISK_SPECS if (root / "frontend" / p).is_file()]
            if roots:
                specs = sorted(set(_direct_specs(root, roots)) | set(off_disk))
                plan.append({"id": "frontend-unit", "cwd": root / "frontend",
                             "command": ["pnpm", "exec", "vitest", "run", *specs, *flags,
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
        # A spec is judged by the name it ends with, not by the substring: a
        # helper module called x.spec.helper.ts is a module other files import,
        # and reading it as a spec would skip the one check that sees them.
        deleted_sources = [p for p in deleted
                           if p.endswith((".ts", ".tsx", ".vue", ".js", ".jsx"))
                           and not re.search(r"\.(spec|test)\.[cm]?[jt]sx?$", p)]
        # A .github/ edit does not change the compiler project either: the
        # narrow project inherits frontend/tsconfig.json and is judged by the
        # changed files, and no CI script is part of that program. It used to
        # force the repository's whole typecheck (87s) for the same reason the
        # full test run was forced — a rule about files that configure the
        # suite, applied to files the suite never reads.
        full_types = (conservative
                      or any(p in FRONTEND_META or p.endswith(".d.ts") for p in changed)
                      or bool(deleted_sources))
        if full_types:
            # Config/ambient/deletion cannot be judged by a project built from a
            # file list, so fall back to the repository's own typecheck.
            plan.append({"id": "frontend-typecheck-full", "command": ["pnpm", "run", "typecheck"],
                         "cwd": root / "frontend", "evidence": {"kind": "types", "mode": "full-config-fallback"}})
        elif ts_files:
            config = _narrow_config(root, tmp, ts_files)
            # Both branches go through scripts/typecheck.mjs, which builds the
            # backend's OpenAPI document and generates src/api/openapi.d.ts from
            # it before judging: neither half is committed, and the narrow
            # project reaches the types through src/cx_types.ts like the full
            # one does.
            plan.append({"id": "frontend-typecheck-changed", "cwd": root / "frontend",
                         "command": ["node", "scripts/typecheck.mjs", "--project", str(config), "--json"],
                         "evidence": {"kind": "types", "files": ts_files,
                                      "limitation": "changed roots and imports, not all downstream consumers"}})
        else:
            omissions.append({"id": "frontend-typecheck-changed",
                              "reason": "no changed TS/Vue files; types not applicable"})
    if scope.get("backend"):
        # A tier that has no pinned harness builds must not judge the tests that
        # need one: without them they fail rather than skip. They are named in
        # not_run and left in required, so a green run still means the change's
        # pinpointed consumers ran.
        selected, reason, targets, omitted = select_backend(
            root, changed, conservative, pinned_harness_gap())
        if omitted:
            omissions.append({"id": "backend-pure:pinned-harness",
                              "reason": f"{len(omitted)} test files need the pinned Claude Code/"
                                        f"Codex/pi builds, which this tier does not install: "
                                        + ", ".join(omitted)})
        if any(is_migration_path(p) for p in changed):
            omissions.append({"id": "backend-pure:alembic",
                              "reason": "migration files run in full CI's integration/e2e, "
                                        "not the pure layer"})
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
        # The full suite's own parallelism, bounded by what this machine was
        # said to back rather than by `auto` (which ignores the ceiling). One
        # worker keeps the single-process form the tier had, so the work
        # machine's 3072 MiB cap still runs the suite exactly as before.
        workers = pytest_workers(memory_mib)
        parallel = ["-n", str(workers)] if workers > 1 else []
        plan.append({"id": "backend-pure", "cwd": root / "backend", "env": env,
                     "command": ["uv", "run", "--no-sync", "pytest", *selected, "-m", "pure", "-q",
                                 "--ignore=tests/forgejo", "-k", "not kotlin",
                                 "-p", "ci_fast_pytest_plugin", *parallel,
                                 "--junitxml", str(output)],
                     "evidence": {"kind": "junit", "path": str(output), "nodes": str(nodes),
                                  "files": selected, "required": targets, "selection": reason}})
    if any(p.startswith("docs/") or p.startswith("frontend/src/views/demo/scenes/")
           for p in changed):
        # The docs build checks links, anchors and the demo scene/fence step
        # titles. Full CI runs it only after merge, so an edit that breaks it
        # lands red on main (2026-10-19: turn.md step 4 did). It is cheap:
        # three npm packages, about ten seconds, and OUT keeps it out of the
        # checkout.
        site = root / "docs" / "site"
        plan.append({"id": "docs-deps", "cwd": site,
                     "evidence": {"kind": "install",
                                  "strict": any(p.startswith("docs/site/package") for p in changed)},
                     "command": ["npm", "ci", "--no-audit", "--no-fund"]})
        plan.append({"id": "docs-build", "cwd": site, "needs": "docs-deps",
                     "env": {"OUT": str(tmp / "docs")},
                     "command": ["npm", "run", "build"]})
    return plan, omissions


def tree_guard_check(root):
    """The whole-tree guards as one pytest run, for the tier that has no test plan.

    These tests read the source tree instead of importing what they constrain,
    so they have no database and no pinned harness; ``-m pure`` is still passed
    so a test that does need one is left out rather than failing on a machine
    that has none. The same list the quick tier always adds to its selection.
    """
    env = {"TEST_PG_BASE": "postgresql://cheese:cheese@127.0.0.1:1/cheese_ci_fast",
           "DATABASE_URL": "postgresql+asyncpg://cheese:cheese@127.0.0.1:1/cheese_ci_fast"}
    return {"id": "tree-guards", "cwd": root / "backend", "env": env,
            "command": ["uv", "run", "--no-sync", "pytest",
                        *(f"tests/unit/{name}" for name in TREE_GUARDS),
                        "-m", "pure", "-q", "-p", "no:cacheprovider"]}


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


#: Capabilities the hosted runner deliberately lacks, as the skip reasons name
#: them. Match the capability, not one phrasing of it: the tmux tests skip with
#: "needs a real tmux >= 3.0", and the ripgrep ones with "this machine has no
#: ripgrep". Any other skip may hide a real failure.
_CAPABILITY_SKIPS = ("bubblewrap", "sandbox-exec", "ripgrep", "tmux")


def _declares_missing_capability(entry):
    """Whether a skip says the machine lacks what the test needs.

    pytest prints the reason as ``Skipped: <reason>`` in the longrepr half of
    the report. Anything else — a plain ``@pytest.mark.skip``, a feature-flag
    ``skipif``, an xfail (the plugin records it as ``xfail: ...``) — is not
    this case and must keep the node unrun.
    """
    if entry.get("outcome") != "skipped":
        return False
    reason = str(entry.get("reason") or "")
    return reason.startswith("Skipped: ") and any(cap in reason for cap in _CAPABILITY_SKIPS)


def _audit(evidence, details):
    """Verify the pure nodes the run selected actually ran.

    The sidecar records the selection the run itself made — its selected pure
    nodes, the nodes ``-m``/``-k`` removed with their layer, and every outcome —
    so this checks what a JUnit report alone cannot: that no pure node was
    hidden by a filter, and that every selected pure node was reached. A node
    the machine skips because it lacks a named capability (bubblewrap,
    sandbox-exec, ripgrep, tmux) counts as reached; any other skip, and a node
    with no report at all, does not. An excluded ``integration``/``contract``
    node is the layer working as designed (recorded as not_run, owned by full
    CI), not treated as missing coverage; a required file with no pure node at
    all is the same, but a change whose modules have no pure consumer at all is
    unknown.
    """
    data = json.loads(Path(evidence["nodes"]).read_text())
    selected_pure = {item["node"] for item in data.get("selected", []) if item.get("layer") == "pure"}
    hidden = sorted(item["node"] for item in data.get("deselected", []) if item.get("layer") == "pure")
    executed = data.get("executed", [])
    # The hosted runner deliberately lacks bubblewrap, sandbox-exec, ripgrep
    # and tmux, so those skips are the environment declining the test, not a
    # node the run missed. Every other skip stays unrun.
    ran = {e["node"] for e in executed
           if (e.get("when") == "call" and e.get("outcome") == "passed")
           or _declares_missing_capability(e)}
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


def _picked_for_the_change(evidence, files):
    """Whether the narrowed run selected a test file for the changed roots.

    A related run is seeded with the specs that read the tree off disk
    (`required_files`) as well as with the changed roots, and a seed runs
    whatever changed. A report holding nothing but seeded files is therefore an
    empty selection carrying the seeds' exit code — the pass main never gave it.
    A seeded file the change itself names is its own selection: a changed spec
    is reached by running it.
    """
    seeds = evidence.get("required_files", [])
    roots = evidence.get("roots", [])
    for name in files:
        if any(name.endswith("/" + p) for p in roots):
            return True
        if not any(name.endswith("/" + p) for p in seeds):
            return True
    return False


def validate_evidence(evidence):
    """Judge one report file alone. Types are decided by the exit code, in resolve()."""
    kind = evidence["kind"]
    details = {k: v for k, v in evidence.items() if k not in ("path", "nodes")}
    failed_suites, failed = 0, []
    try:
        if kind == "vitest":
            data = json.loads(Path(evidence["path"]).read_text())
            passed = int(data.get("numPassedTests", 0))
            skipped = int(data.get("numPendingTests", 0)) + int(data.get("numTodoTests", 0))
            errors = int(data.get("numFailedTests", 0))
            rows = [row for row in data.get("testResults", []) if isinstance(row, dict)]
            files = {row["name"] for row in rows if row.get("name")}
            # A file that failed before running a test is counted in
            # numFailedTestSuites, not numFailedTests. Without this, a run of
            # suites that could not load (an import/collection error, a broken
            # beforeAll) has passed=0, errors=0 and is called an empty
            # selection below — it read "no tests ran" after 408 s of suites
            # that never started. An unhandled error that never reaches the
            # file's report is a process failure, which resolve() reads as
            # unknown.
            failed = [row for row in rows
                      if row.get("status") == "failed" or row.get("message")]
            failed_suites = max(int(data.get("numFailedTestSuites", 0)), len(failed))
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
    details.update(passed=passed, skipped=skipped, errors=errors,
                   failed_suites=failed_suites, missing=missing)
    if errors:
        reason = "test report contains errors/failures"
        if kind == "vitest":
            failures = [
                f"{row.get('name', '')}::{case.get('fullName', '')}: "
                + str((case.get("failureMessages") or [""])[0]).split("\n", 1)[0][:200]
                for row in rows
                for case in row.get("assertionResults", [])
                if case.get("status") == "failed"
            ]
            if failures:
                reason += ": " + "; ".join(failures[:3])
        return {"result": "fail", "reason": reason, "evidence": details}
    if failed_suites:
        crashed = [row for row in failed
                   if WORKER_CRASH.search(str(row.get("message") or ""))]
        if failed and len(crashed) == len(failed):
            # The worker died before the suite could report; that is the
            # environment, not a verdict on the change.
            return {"result": "unknown",
                    "reason": f"vitest workers crashed before reporting ({len(crashed)} files)",
                    "evidence": details}
        reason = f"{failed_suites} test suite(s) failed before running a test"
        failures = [
            f"{row.get('name', '')}: "
            + str(row.get("message") or "").split("\n", 1)[0][:200]
            for row in failed
        ]
        if failures:
            reason += ": " + "; ".join(failures[:3])
        return {"result": "fail", "reason": reason, "evidence": details}
    if kind == "vitest" and evidence.get("mode") == "related" and not _picked_for_the_change(evidence, files):
        return {"result": "unknown",
                "reason": "the changed roots selected no test file; the always-on seeds ran alone",
                "evidence": details}
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
    if kind == "install":
        # An install that fails is the machine (or the registry) missing a
        # dependency, not a verdict on the change. Exit 3 says exactly that;
        # a timeout, memory stop or signal keeps its own result above. The one
        # exception is a change that edits docs/site's own package files: there
        # the install is judging the change, so its failure stays a failure.
        if process["result"] == "fail":
            if evidence.get("strict"):
                return {**process, "result": "fail",
                        "reason": f"the docs dependencies the change edits do not install (exit {rc})"}
            return {**process, "result": "blocked",
                    "reason": f"could not install the docs dependencies (exit {rc})"}
        return process
    if kind == "types":
        detail = {"evidence": {k: v for k, v in evidence.items() if k != "path"}}
        if process["result"] == "pass":
            return {**process, **detail}
        if rc == 1:
            return {**process, "result": "fail",
                    "reason": "type errors", **detail}
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
