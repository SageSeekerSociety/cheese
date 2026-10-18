#!/usr/bin/env python3
"""Who uses this? — the question an agent otherwise answers with ten greps.

    python3 .claude/scripts/where.py SYMBOL            # every use of a name
    python3 .claude/scripts/where.py app.domain.x      # who imports a module
    python3 .claude/scripts/where.py app/domain/x.py   # same, as a path
    python3 .claude/scripts/where.py app.domain.x.SYMBOL   # a name, in that module's reach only

The graph is ci-fast's own: ``ci_fast_backend.dependencies`` over the same
``backend/{app,scripts,tests}`` file set that ``select_backend`` walks, so this
and the test selection agree on who imports whom. Nothing is re-parsed by a
second rule for imports.

What it adds is the half the selector throws away: *where* in each file. A use is
an AST node — a ``Name``, an attribute access, an imported alias, a definition —
so a comment or a string that merely mentions the name is not a use. ``grep``
cannot make that distinction; that is the reason to run this instead.

Output is grouped by file, tests last (app/, scripts/, tests/ sort that way), one ``line: source`` row per use. A file is
tagged ``imports`` when it imports the module the symbol is defined in, which
separates the real callers from an unrelated function that shares the name.
"""

import argparse
import ast
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FOLDERS = ("app", "scripts", "tests")


def _load_backend_graph(root):
    path = root / ".github" / "scripts" / "ci_fast_backend.py"
    spec = importlib.util.spec_from_file_location("ci_fast_backend", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def backend_files(root):
    backend = root / "backend"
    return {p for folder in FOLDERS for p in (backend / folder).rglob("*.py")}


def uses_of(tree, name):
    """Line numbers where ``name`` is a real use: a Name, ``.name`` attribute,
    imported alias, def/class, keyword argument or ``global``-style binding."""
    hits = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == name:
            hits.add(node.lineno)
        elif isinstance(node, ast.Attribute) and node.attr == name:
            hits.add(node.end_lineno)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == name:
            hits.add(node.lineno)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.name.split(".")[-1] == name or alias.asname == name:
                    hits.add(node.lineno)
        elif isinstance(node, ast.keyword) and node.arg == name:
            hits.add(node.value.lineno)
        elif isinstance(node, ast.arg) and node.arg == name:
            hits.add(node.lineno)
    return hits


def classify(target, modules):
    """('module', dotted) when target names a module, else ('symbol', scope, name)."""
    dotted = target.removeprefix("backend/").removesuffix(".py").replace("/", ".")
    if dotted.endswith(".__init__"):
        dotted = dotted[: -len(".__init__")]
    if dotted in modules:
        return "module", dotted, None
    head, _, tail = dotted.rpartition(".")
    if head in modules:
        return "symbol", head, tail
    return "symbol", None, target


def find(root, target):
    """Return {relative path: {"tags": set, "lines": [(lineno, text)]}}."""
    graph = _load_backend_graph(root)
    backend = root / "backend"
    paths = backend_files(root)
    by_module = {graph.module_name(p.relative_to(backend)): p for p in paths}
    modules = set(by_module)
    kind, scope, name = classify(target, modules)
    needle = name or scope.rpartition(".")[2]
    result = {}
    seen = {}  # module -> (text, imports) for the files that mention the needle

    def add(path, tag, lines=()):
        entry = result.setdefault(str(path.relative_to(root)), {"tags": set(), "lines": []})
        entry["tags"].add(tag)
        entry["lines"].extend(lines)

    for module, path in by_module.items():
        try:
            text = path.read_text()
        except (OSError, UnicodeError):
            continue
        if needle not in text:
            continue
        try:
            _, imports, _, _ = graph.dependencies(path, module, modules)
        except (SyntaxError, UnicodeError):
            continue
        seen[module] = (text, imports)
    if kind == "module":
        for module, (text, imports) in seen.items():
            if scope in imports or any(i.startswith(scope + ".") for i in imports):
                path = by_module[module]
                src = text.splitlines()
                lines = sorted({n.lineno for n in ast.walk(ast.parse(text))
                                if isinstance(n, (ast.Import, ast.ImportFrom))
                                and _names_module(n, scope, module, path, modules)})
                add(path, "imports", [(n, src[n - 1].strip()) for n in lines])
        return result
    # A name defined in app/x/tokens.py is usually imported from the package
    # app/x that re-exports it, so the scope reaches every __init__ that imports it.
    reach = {scope} if scope is not None else None
    while reach is not None:
        grown = {m for m, (_, imports) in seen.items()
                 if by_module[m].name == "__init__.py" and imports & reach} - reach
        if not grown:
            break
        reach |= grown
    for module, (text, imports) in seen.items():
        path = by_module[module]
        hits = uses_of(ast.parse(text), name)
        if not hits:
            continue
        if reach is not None and module not in reach and not imports & reach:
            continue
        src = text.splitlines()
        tag = "defines" if module == scope else ("imports" if reach and imports & reach else "uses")
        add(path, tag, [(n, src[n - 1].strip()) for n in sorted(hits)])
    return result


def _names_module(node, scope, module, path, modules):
    """Whether this import statement is the edge to ``scope`` (not to a sibling)."""
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]
    if isinstance(node, ast.Import):
        return any(a.name == scope or a.name.startswith(scope + ".") for a in node.names)
    prefix = node.module or ""
    if node.level:
        parents = package.split(".")
        prefix = ".".join(parents[: len(parents) - node.level + 1] + ([prefix] if prefix else []))
    return prefix == scope or prefix.startswith(scope + ".") or any(
        f"{prefix}.{a.name}" == scope or f"{prefix}.{a.name}".startswith(scope + ".") for a in node.names)


def render(result):
    if not result:
        return "no uses found"
    ordered = sorted(result)  # app/ and scripts/ sort before tests/
    out = []
    for path in ordered:
        entry = result[path]
        out.append(f"{path}  [{','.join(sorted(entry['tags']))}]")
        out.extend(f"  {n}: {text}" for n, text in sorted(set(entry["lines"])))
    tests = sum(p.startswith("backend/tests/") for p in result)
    out.append(f"-- {len(result)} files ({tests} in tests)")
    return "\n".join(out)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("target", help="symbol, dotted module, module path, or module.symbol")
    parser.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    result = find(args.root, args.target)
    if not result and "." in args.target.removesuffix(".py"):
        print("note: nothing found; check that the module part of the dotted name exists", file=sys.stderr)
    print(render(result))


if __name__ == "__main__":
    main()
