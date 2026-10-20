"""The backend's static import graph, and the test files a change imports.

Read from the source with ``ast``, never by importing app code: the graph has to
be built by a job with nothing but python3 (Required CI's ``scope``), and an
import would run the module.

An edge ``a -> b`` means module ``a`` imports ``b`` — ``import b``, ``from b
import x``, ``from . import b``, or ``importlib.import_module("b")`` with a
literal name. Importing ``app.x.y`` is also an edge to ``app.x`` and ``app``:
Python runs every parent package's ``__init__`` on the way down.

:func:`select_integration` is the import half of a pull request's integration
selection (coverage_map.py holds the rest): every test file that reaches a
changed module through imports, or None — the whole layer — when the change is
to something every test shares that neither imports nor a trace can see. It
alone misses most of the layer: an integration case reaches the code through
HTTP on the conftest's client, an edge no import records. A ``conftest.py`` is
not followed as a consumer: every test sits under one, and following it would
select everything for every change.
"""

import ast
from collections import defaultdict, deque
from pathlib import PurePosixPath

#: The folders under backend/ the graph is built from.
FOLDERS = ("app", "scripts", "tests")
#: The contract layer is a layer of its own and always runs whole.
CONTRACT_PREFIX = "tests/contract/"

#: Backend inputs every integration test depends on without importing them: the
#: dependencies, the schema the database is built from, and the sandbox scripts
#: and skills the test platform loads by path. A change to one runs every
#: integration test.
SHARED_INPUTS = ("pyproject.toml", "uv.lock", "alembic.ini")
SHARED_PREFIXES = ("alembic/", "sandbox/")
#: Under a shared prefix, files no test reads: the images built from it.
NOT_READ = ("sandbox/Dockerfile",)
#: Test data integration tests read by path. Data elsewhere under tests/ (the
#: contract layer's route index, the duration and quarantine lists) belongs to
#: suites that run whole.
TEST_DATA_PREFIXES = ("tests/integration/", "tests/support/", "tests/fixtures/")


def module_name(path):
    """``app/x/y.py`` -> ``app.x.y``; a package's ``__init__.py`` is the package."""
    parts = list(PurePosixPath(path).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _parents(name):
    pieces = name.split(".")
    return [".".join(pieces[:i]) for i in range(1, len(pieces))]


def imports_of(source, module, is_package, modules, parents=True):
    """The modules of ``modules`` that ``source`` (the text of ``module``) imports.

    With ``parents`` (the default), importing ``a.b.c`` also counts ``a.b`` and
    ``a``, whose ``__init__`` runs on the way; without, only the module named.
    """
    tree = ast.parse(source)
    package = module if is_package else module.rpartition(".")[0]
    out = set()

    def add(name):
        for candidate in (name, *(_parents(name) if parents else ())):
            if candidate in modules:
                out.add(candidate)
                if not parents:
                    return

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            prefix = node.module or ""
            if node.level:
                base = package.split(".")
                base = base[: len(base) - node.level + 1]
                prefix = ".".join(base + ([prefix] if prefix else []))
            add(prefix)
            for alias in node.names:
                child = f"{prefix}.{alias.name}"
                if child in modules:
                    out.add(child)
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else (
                func.attr if isinstance(func, ast.Attribute) else None)
            if name in ("import_module", "__import__") and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    add(first.value)
    out.discard(module)
    return out


def graph(backend):
    """``(paths, importers)`` for the backend tree at ``backend`` (a Path).

    ``paths`` maps each module to its file relative to backend/; ``importers``
    maps each module to the modules that import it directly.
    """
    files = [p for folder in FOLDERS for p in (backend / folder).rglob("*.py")]
    paths = {module_name(p.relative_to(backend).as_posix()): p.relative_to(backend).as_posix()
             for p in files}
    modules = set(paths)
    importers = defaultdict(set)
    for module, rel in paths.items():
        source = (backend / rel).read_text(encoding="utf-8")
        for target in imports_of(source, module, rel.endswith("__init__.py"), modules):
            importers[target].add(module)
    return paths, importers


def data_readers(backend, paths, filename):
    """The modules whose source names ``filename``: who reads a data file by path."""
    return {module for module, rel in paths.items()
            if filename in (backend / rel).read_text(encoding="utf-8")}


def is_conftest(module):
    return module == "conftest" or module.endswith(".conftest")


def reached_by(seeds, importers):
    """``seeds`` and every module that imports one of them, directly or not,
    except through a conftest."""
    seen, queue = set(seeds), deque(seeds)
    while queue:
        for consumer in importers.get(queue.popleft(), ()):
            if consumer not in seen and not is_conftest(consumer):
                seen.add(consumer)
                queue.append(consumer)
    return seen


def select_integration(backend, changed):
    """``(files, reason)``: the test files ``changed`` reaches, whose
    integration-layer cases must run.

    ``changed`` holds repository paths. ``files`` is ``None`` when every
    integration test must run, else a sorted list of paths relative to
    backend/ (possibly empty). A path outside backend/ is the caller's to judge
    and is ignored here.
    """
    paths = None
    seeds = set()
    for path in changed:
        if not path.startswith("backend/"):
            continue
        rel = path[len("backend/"):]
        top = rel.split("/", 1)[0]
        if rel.startswith(NOT_READ):
            continue
        if rel in SHARED_INPUTS or rel.startswith(SHARED_PREFIXES):
            return None, f"{path} is shared by every test"
        if PurePosixPath(rel).name == "conftest.py":
            return None, f"{path} is a conftest every test below it loads"
        if top not in FOLDERS:
            # A baseline, a Dockerfile, a note: checked by static jobs, not
            # read by an integration test.
            continue
        if rel.endswith(".py"):
            seeds.add(module_name(rel))
            continue
        if rel.startswith(TEST_DATA_PREFIXES):
            # Read by path: by the modules that name the file.
            if paths is None:
                paths, importers = graph(backend)
            readers = data_readers(backend, paths, PurePosixPath(rel).name)
            if not readers or any(is_conftest(m) for m in readers):
                return None, f"{path} is test data {'a conftest reads' if readers else 'no module names'}"
            seeds.update(readers)
            continue
        if top == "tests":
            continue
        # A data file beside code (a prompt, a template) is read by the modules
        # of its folder.
        if paths is None:
            paths, importers = graph(backend)
        folder = str(PurePosixPath(rel).parent).replace("/", ".")
        seeds.update(m for m in paths if m == folder or m.rpartition(".")[0] == folder)
    if not seeds:
        return [], "no backend module changed"
    if paths is None:
        paths, importers = graph(backend)
    reached = reached_by(seeds, importers)
    files = sorted(
        rel for module, rel in paths.items()
        if module in reached and rel.startswith("tests/") and not rel.startswith(CONTRACT_PREFIX)
        and PurePosixPath(rel).name.startswith("test_")
    )
    return files, f"{len(files)} test files import a changed module"
