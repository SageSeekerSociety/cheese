"""Conservative static Python dependency selection, without importing app code.

Follow reverse imports transitively, over the real AST — never over file-name
tokens. A literal ``importlib.import_module("x")`` is an edge like any other
import; a dynamic one (``import_module(name)``, ``__import__(expr)``) cannot be
resolved, so every module that contains one is treated as a consumer of every
module. That is the safe direction: app/main.py loads every route module that
way, so without it a change to a route would never reach the tests that build
the app through main. A large selection that runs out of budget is reported
unknown, never trimmed.

``select_backend`` returns three things:

* ``selected`` — every unit-test file to hand pytest (the targeted consumers,
  the always-on tree guards, plus every unit test when the graph cannot be
  trusted);
* ``reason`` — why, for the report;
* ``targets`` — the consumer files that specifically depend on a changed
  module. A green run must show those actually executed; running only the
  always-on guards does not prove the changed module has coverage. When the
  whole selection is a conservative fallback, ``targets`` is empty because no
  module was pinpointed.

Unmapped source changes, configuration edits and non-Python backend inputs widen
to all unit modules (pytest still applies the real fixture-derived ``pure``
marker, so nothing outside the layer runs on a machine with no database).
"""

import ast
from collections import defaultdict, deque
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
    "backend/pyproject.toml", "backend/uv.lock", "backend/openapi.json",
    "backend/tests/module_durations.json", "backend/tests/quarantine.txt",
    "backend/tests/slow_tests.txt", "backend/.importlinter",
    "backend/deferred-import-baseline.json", ".pre-commit-config.yaml",
}
UNIT_PREFIX = "tests/unit/"
DYNAMIC_IMPORT_NAMES = {"import_module", "__import__"}


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
    """Static import edges of one module, plus whether it has unresolved ones."""
    tree = ast.parse(path.read_text())
    refs, literals, dynamic = set(), set(), False
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            refs.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            prefix = node.module or ""
            if node.level:
                parents = package.split(".")
                prefix = ".".join(parents[:len(parents) - node.level + 1] + ([prefix] if prefix else []))
            for alias in node.names:
                child = f"{prefix}.{alias.name}"
                refs.add(child if child in modules else prefix)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            literals.add(node.value)
        elif _dynamic_import(node):
            dynamic = True
    # A literal string naming a module (or a repository-relative .py file) is a
    # dependency too: importlib.import_module("app.x") and open("app/x.py").
    for literal in literals:
        if literal in modules:
            refs.add(literal)
        normalized = literal.removeprefix("backend/")
        if normalized.endswith(".py"):
            name = module_name(normalized)
            if name in modules:
                refs.add(name)
    return refs, dynamic


def _rel(path, backend):
    return str(path.relative_to(backend))


def select_backend(root, changed, conservative=False):
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
    dynamic_consumers = set()
    try:
        for module, path in by_module.items():
            refs, dynamic = dependencies(path, module, modules)
            for dependency in refs:
                if dependency in modules:
                    reverse[dependency].add(module)
            if dynamic:
                dynamic_consumers.add(module)
    except (OSError, SyntaxError, UnicodeError) as exc:
        return all_tests, f"full pure fallback: cannot parse dependency graph ({type(exc).__name__})", []
    # A module whose import list is not fully known may consume anything; treat
    # it as a consumer of every module so nothing that reaches it is missed.
    for consumer in dynamic_consumers:
        for dependency in modules:
            reverse[dependency].add(consumer)
    if conservative or any(p in META or p.endswith("/conftest.py") or p.startswith(".github/") for p in changed):
        return all_tests, "full pure fallback: configuration/unknown path/no merge base", []
    targets = set()
    for changed_module in changed_modules:
        if changed_module in tests:  # a changed unit test proves itself
            targets.add(_rel(tests[changed_module], backend))
        seen = {changed_module}
        queue = deque([changed_module])
        while queue:
            for consumer in reverse[queue.popleft()]:
                if consumer not in seen:
                    seen.add(consumer)
                    queue.append(consumer)
        affected = seen & tests.keys()
        if not affected and not changed_module.startswith("tests."):
            return all_tests, f"full pure fallback: no static consumers for {changed_module}", []
        targets.update(_rel(tests[m], backend) for m in affected)
    relevant = [p for p in changed if p.startswith("backend/") and p not in META]
    if any(not p.endswith(".py") for p in relevant):
        return all_tests, "full pure fallback: non-Python backend input", []
    # Guards also run for frontend catalog inputs selected by the CI path map.
    selected = set(targets)
    selected.update(f"{UNIT_PREFIX}{name}" for name in TREE_GUARDS)
    return sorted(selected), "reverse transitive Python imports plus tree/i18n guards", sorted(targets)
