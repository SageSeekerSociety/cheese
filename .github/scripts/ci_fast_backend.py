"""Conservative static Python dependency selection, without importing app code.

Follow reverse imports transitively, over the real AST — never over file-name
tokens. A literal ``importlib.import_module("x")`` is an edge like any other
import; a dynamic one (``import_module(name)``, ``__import__(expr)``) cannot be
resolved, so every module that contains one is treated as a consumer of every
module. That is the safe direction: app/main.py loads every route module that
way, so without it a change to a route would never reach the tests that build
the app through main. A large selection that runs out of budget is reported
unknown, never trimmed.

``select_backend`` returns four things:

* ``selected`` — every unit-test file to hand pytest (the targeted consumers,
  the always-on tree guards, plus every unit test when the graph cannot be
  trusted);
* ``reason`` — why, for the report;
* ``targets`` — the consumer files that specifically depend on a changed
  module. A green run must show those actually executed; running only the
  always-on guards does not prove the changed module has coverage. When the
  whole selection is a conservative fallback, ``targets`` is empty because no
  module was pinpointed.
* ``omitted`` — the selected files this environment cannot run because they
  need a pinned harness build (see below); empty when it has them.

The pinned Claude Code / Codex / pi builds come from the environment
(``CHEESE_TEST_*``), installed by the full CI and by dev-db.sh. A test that
reaches for one — through ``tests/pinned_claude.py`` or by reading a variable
itself — FAILS where the builds were not installed, it does not skip
(tests/pinned_claude.py: _pinned). A tier that cannot provide them must not turn
that into a verdict on the change, so the caller says so (``harness_gap``) and
those files are dropped from ``selected`` and named in ``omitted`` for the
report to mark not run. They stay in ``targets``: a run that did not execute its
pinpointed consumers did not judge the change, and the audit must still see it.

Unmapped source changes, configuration edits and non-Python backend inputs widen
to all unit modules (pytest still applies the real fixture-derived ``pure``
marker, so nothing outside the layer runs on a machine with no database).
"""

import ast
from collections import defaultdict, deque
import os
from pathlib import Path

# These inspect trees/catalogs rather than importing the files they constrain,
# so the import graph alone would not reach them; they always run.
TREE_GUARDS = (
    "test_domain_import_guard.py", "test_legacy_error_ratchet.py",
    "test_a_refusal_is_said_with_a_key.py", "test_sentences.py",
    "test_is_private_read_points.py", "test_harness_boundary.py",
    "test_actor_is_not_typed.py", "test_author_type_two_values.py",
    "test_nothing_reads_the_model_off_a_type_or_an_instance.py",
    "test_social_notifications_through_the_ledger.py",
    "test_remote_hub_answers_every_hub_call.py", "test_the_platform_never_starts_a_turn.py",
    "test_no_project_memory_pool.py", "test_one_roster_agents_included.py",
    "test_no_adhoc_auth_helpers.py", "test_local_fs_sharing_guard.py",
)
META = {
    "backend/pyproject.toml", "backend/uv.lock",
    # backend/openapi.json left the repository on 2026-10-14; the frontend
    # builds it on demand and what stays committed is its digest. Either way a
    # non-Python input under backend/ hits the rule above, which asks for the
    # same full run.
    "backend/openapi.sha256",
    "backend/tests/module_durations.json", "backend/tests/quarantine.txt",
    "backend/tests/slow_tests.txt", "backend/.importlinter",
    "backend/deferred-import-baseline.json", ".pre-commit-config.yaml",
}
UNIT_PREFIX = "tests/unit/"
DYNAMIC_IMPORT_NAMES = {"import_module", "__import__"}
# The pinned harness builds enter a test through this helper, or by the test
# reading one of the variables itself (test_execution_bootstrap.py does, with
# its own fallback to a local pin). Both are roots: their consumers cannot run
# without the builds.
HARNESS_HELPER = "tests.pinned_claude"
HARNESS_VARIABLES = frozenset({"CHEESE_TEST_CLAUDE", "CHEESE_TEST_CODEX", "CHEESE_TEST_PI"})


def pinned_harness_gap(environ=None):
    """Why this environment cannot run the pinned-harness tests, or "" if it can.

    Presence of the three builds, not their versions: the pin itself is checked
    by the tests that use one (pinned_claude.pi_binary compares ``--version``),
    and a planner that ran each binary would pay for that on every tier, for a
    verdict the failing test already gives.
    """
    env = os.environ if environ is None else environ
    missing = sorted(v for v in HARNESS_VARIABLES
                     if not (env.get(v) and Path(env[v]).is_file()))
    return "missing: " + ", ".join(missing) if missing else ""


def module_name(path):
    parts = list(Path(path).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def is_unit_test(relative):
    return relative.startswith(UNIT_PREFIX) and Path(relative).name.startswith("test_")


def _dynamic_import(node):
    """A call that imports a module whose name is not a string literal."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    name = func.id if isinstance(func, ast.Name) else (
        func.attr if isinstance(func, ast.Attribute) else None
    )
    if name not in DYNAMIC_IMPORT_NAMES:
        return False
    if not node.args:
        return True
    first = node.args[0]
    return not (isinstance(first, ast.Constant) and isinstance(first.value, str))


def dependencies(path, module, modules):
    """Static edges of one module: what it depends on, what it imports, whether
    it has unresolved imports, and its string literals.

    The two edge sets differ. ``refs`` is "everything this module touches": the
    imports, plus a literal naming a module or a repository-relative .py file
    (importlib.import_module("app.x"), open("app/x.py")) — and a test that
    asserts over a file names it without importing it, which is a real
    dependency for selection. ``imports`` is the import statements alone, the
    only edges along which "needs what this module needs" travels.
    """
    tree = ast.parse(path.read_text())
    imports, literals, dynamic = set(), set(), False
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            prefix = node.module or ""
            if node.level:
                parents = package.split(".")
                prefix = ".".join(parents[:len(parents) - node.level + 1] + ([prefix] if prefix else []))
            for alias in node.names:
                child = f"{prefix}.{alias.name}"
                imports.add(child if child in modules else prefix)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            literals.add(node.value)
        elif _dynamic_import(node):
            dynamic = True
    # A literal string naming a module (or a repository-relative .py file) is a
    # dependency too: importlib.import_module("app.x") and open("app/x.py").
    refs = set(imports)
    for literal in literals:
        if literal in modules:
            refs.add(literal)
        normalized = literal.removeprefix("backend/")
        if normalized.endswith(".py"):
            name = module_name(normalized)
            if name in modules:
                refs.add(name)
    return refs, imports, dynamic, literals


def _transitive(seeds, reverse):
    """The seeds plus everything that imports one of them, directly or not."""
    seen, queue = set(seeds), deque(seeds)
    while queue:
        for consumer in reverse[queue.popleft()]:
            if consumer not in seen:
                seen.add(consumer)
                queue.append(consumer)
    return seen


def _rel(path, backend):
    return str(path.relative_to(backend))


def select_backend(root, changed, conservative=False, harness_gap=""):
    backend = root / "backend"
    # Include deleted modules in import resolution, so their consumers survive.
    changed_modules = {module_name(p.removeprefix("backend/")) for p in changed
                       if p.startswith("backend/") and p.endswith(".py")}
    paths = [p for folder in ("app", "scripts", "tests") for p in (backend / folder).rglob("*.py")]
    by_module = {module_name(p.relative_to(backend)): p for p in paths}
    tests = {m: p for m, p in by_module.items() if is_unit_test(_rel(p, backend))}
    all_tests = sorted(_rel(p, backend) for p in tests.values())
    modules = set(by_module) | changed_modules
    reverse = defaultdict(set)
    # Import statements only, for the harness question — see the docstring. Two
    # over-approximations of `reverse` must not carry it: the dynamic-import
    # penalty below (app.main loads its routes that way, which would make it a
    # consumer of the harness helper and every test that builds the app through
    # it a harness test — most of the suite, dropped from a tier that can run
    # it) and the literal-named files (the legacy-error ratchet names every file
    # it counts, which is not a reason to skip it).
    import_reverse = defaultdict(set)
    dynamic_consumers = set()
    harness_sources = set()

    def result(selected, reason, targets):
        """Every way out: drop what this environment cannot run, and name it.

        The dropped files stay in ``targets`` — the run did not judge the change
        if its pinpointed consumers never executed, and the audit must say so.
        """
        if not harness_gap:
            return selected, reason, targets, []
        needs_harness = _transitive(harness_sources, import_reverse) if harness_sources else set()
        omitted = [p for p in selected if module_name(p) in needs_harness]
        if not omitted:
            return selected, reason, targets, []
        keep = [p for p in selected if p not in set(omitted)]
        return (keep,
                f"{reason}; {len(omitted)} pinned-harness test files not run ({harness_gap})",
                targets, omitted)

    try:
        for module, path in by_module.items():
            refs, imports, dynamic, literals = dependencies(path, module, modules)
            for dependency in refs:
                if dependency in modules:
                    reverse[dependency].add(module)
            for dependency in imports:
                if dependency in modules:
                    import_reverse[dependency].add(module)
            if dynamic:
                dynamic_consumers.add(module)
            if module == HARNESS_HELPER or HARNESS_VARIABLES & literals:
                harness_sources.add(module)
    except (OSError, SyntaxError, UnicodeError) as exc:
        return result(all_tests,
                      f"full pure fallback: cannot parse dependency graph ({type(exc).__name__})", [])
    # A module whose import list is not fully known may consume anything; treat
    # it as a consumer of every module so nothing that reaches it is missed.
    for consumer in dynamic_consumers:
        for dependency in modules:
            reverse[dependency].add(consumer)
    if conservative or any(p in META or p.endswith("/conftest.py") or p.startswith(".github/") for p in changed):
        return result(all_tests, "full pure fallback: configuration/unknown path/no merge base", [])
    targets = set()
    for changed_module in changed_modules:
        if changed_module in tests:  # a changed unit test proves itself
            targets.add(_rel(tests[changed_module], backend))
        affected = _transitive({changed_module}, reverse) & tests.keys()
        if not affected and not changed_module.startswith("tests."):
            return result(all_tests, f"full pure fallback: no static consumers for {changed_module}", [])
        targets.update(_rel(tests[m], backend) for m in affected)
    relevant = [p for p in changed if p.startswith("backend/") and p not in META]
    if any(not p.endswith(".py") for p in relevant):
        return result(all_tests, "full pure fallback: non-Python backend input", [])
    # Guards also run for frontend catalog inputs selected by the CI path map.
    selected = set(targets)
    selected.update(f"{UNIT_PREFIX}{name}" for name in TREE_GUARDS)
    return result(sorted(selected), "reverse transitive Python imports plus tree/i18n guards", sorted(targets))
