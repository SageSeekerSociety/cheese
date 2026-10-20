"""Which integration test files a change reaches, from what the tests executed.

Most integration cases reach the code through HTTP on the conftest's client, an
edge no import records, so an import graph alone misses them. The map records
the other direction: a scheduled run (coverage-map.yml) traces the whole
integration layer under coverage.py with one context per test, and the map
keeps, for every function under backend/app and backend/scripts, the test
files whose cases executed it. That is test impact analysis at function
granularity, the way pytest-testmon selects.

A pull request then runs the test files that executed a function it changed.
Code that runs only when its module is imported — a constant, a class body such
as a pydantic model's fields, a registration call — executes before any test
starts, so no test's context holds it. For those lines the selection follows
the names they define to the functions that read them (``settings.x`` reads
``x``), and for a statement that defines no name it falls back to every module
that imports this one, through the import graph, and the tests of each.

The static import selection (backend_imports.select_integration) judges the
rest: a change to a file every test shares, a conftest, a test file, or test
data. It does not add the tests that merely import a changed module: one that
never executes the change has nothing of it to check, and an import that
breaks fails Backend Test's collection of every test. Test files the map does
not cover (the traced run gave them no context) run every time.

Usage:
    coverage_map.py build COVERAGE_FILE --backend DIR --out SHARD.json
    coverage_map.py merge SHARD.json... --selection DIR --commit SHA --out MAP.json
"""

import argparse
import ast
import json
import sys
from collections import defaultdict
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent))
import backend_imports  # noqa: E402

#: The name a module's own top-level lines are recorded under.
MODULE = "<module>"
#: The trees a traced run measures, relative to backend/.
MEASURED = ("app/", "scripts/")
#: How many shards a complete traced run has (test.yml's integration matrix).
SHARDS = 5


def spans(source):
    """``(functions, imports)`` of ``source``: ``(first, last, qualified name)``
    for every function and method, decorators included, and the line numbers of
    top-level import statements."""
    tree = ast.parse(source)
    found = []

    def walk(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = prefix + child.name
                first = min([d.lineno for d in child.decorator_list] + [child.lineno])
                found.append((first, child.end_lineno, name))
                walk(child, name + ".")
            elif isinstance(child, ast.ClassDef):
                walk(child, prefix + child.name + ".")

    walk(tree, "")
    imports = set()
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            imports.update(range(node.lineno, node.end_lineno + 1))
    return found, imports


def function_at(found, line):
    """The innermost function of ``found`` holding ``line``, or None."""
    inner = [f for f in found if f[0] <= line <= f[1]]
    return min(inner, key=lambda f: f[1] - f[0])[2] if inner else None


def build(coverage_file, backend, out):
    """One traced shard's map: ``{file: {function: [test files]}}``."""
    from coverage import CoverageData

    if not Path(coverage_file).is_file():
        raise SystemExit(f"::error::{coverage_file} does not exist: the traced run left no coverage data")
    data = CoverageData(str(coverage_file))
    data.read()
    root = str(backend.resolve()) + "/"
    result = {}
    for path in data.measured_files():
        if not path.startswith(root):
            continue
        rel = path[len(root):]
        if not rel.startswith(MEASURED):
            continue
        try:
            found, _ = spans(Path(path).read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            found = []
        functions = defaultdict(set)
        for line, contexts in data.contexts_by_lineno(path).items():
            name = function_at(found, line) or MODULE
            for context in contexts:
                if context:
                    functions[name].add(context.split("::", 1)[0])
        if functions:
            result[rel] = {name: sorted(tests) for name, tests in sorted(functions.items())}
    out.write_text(json.dumps(result, sort_keys=True))
    return result


def planned_files(selection_dir):
    """The test files the traced shards were assigned, from ci_shard's manifests."""
    files = set()
    for manifest in Path(selection_dir).rglob("selection-*.json"):
        for nodeid in json.loads(manifest.read_text())["assigned"]:
            files.add(nodeid.split("::", 1)[0])
    return files


def merge(shards, planned, commit):
    """The published map: every shard's functions, the commit it was traced
    at, and the planned test files no context names (``unmapped``)."""
    files = defaultdict(lambda: defaultdict(set))
    for shard in shards:
        for rel, functions in shard.items():
            for name, tests in functions.items():
                files[rel][name].update(tests)
    seen = {t for functions in files.values() for tests in functions.values() for t in tests}
    return {
        "commit": commit,
        "files": {rel: {n: sorted(t) for n, t in sorted(fs.items())} for rel, fs in sorted(files.items())},
        "unmapped": sorted(planned - seen),
    }


def _defined(source, lines):
    """``(names, bare)`` for the top-level statements ``lines`` touch outside
    any function: the names they bind (a class's own name and the names its
    body binds, an assignment's targets), and whether one of them binds
    nothing a reader could be found by (a call, a branch)."""
    tree = ast.parse(source)
    names, bare = set(), False

    def targets(node):
        for target in getattr(node, "targets", None) or [getattr(node, "target", None)]:
            for n in ast.walk(target) if target is not None else ():
                if isinstance(n, ast.Name):
                    names.add(n.id)

    for stmt in tree.body:
        stmt_lines = set(range(stmt.lineno, stmt.end_lineno + 1))
        if not stmt_lines & lines:
            continue
        if isinstance(stmt, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if isinstance(stmt, ast.ClassDef):
            names.add(stmt.name)
            for item in stmt.body:
                if isinstance(item, (ast.Assign, ast.AnnAssign, ast.AugAssign)) and \
                        set(range(item.lineno, item.end_lineno + 1)) & lines:
                    targets(item)
            continue
        if isinstance(stmt, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets(stmt)
        elif isinstance(stmt, ast.TypeAlias):
            names.add(stmt.name.id)
        elif isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
            continue  # a docstring
        else:
            bare = True
    return names, bare


def readers(backend, paths):
    """``{identifier: {(file, function)}}``: the functions under app/ and
    scripts/ whose body (decorators included) mentions the identifier as a name
    or an attribute."""
    index = defaultdict(set)
    for rel in paths.values():
        if not rel.startswith(MEASURED):
            continue
        try:
            tree = ast.parse((backend / rel).read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue

        def walk(node, prefix):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    name = prefix + child.name
                    for n in ast.walk(child):
                        if isinstance(n, ast.Name):
                            index[n.id].add((rel, name))
                        elif isinstance(n, ast.Attribute):
                            index[n.attr].add((rel, name))
                    walk(child, name + ".")
                elif isinstance(child, ast.ClassDef):
                    walk(child, prefix + child.name + ".")

        walk(tree, "")
    return index


def changed_lines(diff):
    """``{path: (old lines, new lines)}`` from a ``git diff -U0`` text; a
    deleted file is keyed by its old path."""
    out = {}
    old_path = new_path = None
    for line in diff.splitlines():
        if line.startswith("--- "):
            old_path = line[6:] if line.startswith("--- a/") else None
        elif line.startswith("+++ "):
            new_path = line[6:] if line.startswith("+++ b/") else None
        elif line.startswith("@@ "):
            old, new = line.split()[1:3]
            a, _, b = old[1:].partition(",")
            x, _, y = new[1:].partition(",")
            entry = out.setdefault(new_path or old_path, (set(), set()))
            entry[0].update(range(int(a), int(a) + int(b or 1)))
            entry[1].update(range(int(x), int(x) + int(y or 1)))
    return out


def select(cmap, backend, changed, lines, read_base, read_head):
    """``(files, reasons)``: the integration test files ``changed`` reaches.

    ``cmap`` is a merged map, ``changed`` repository paths, ``lines`` the
    :func:`changed_lines` of the diff, and ``read_base`` / ``read_head`` return
    a repository path's text at the diff's base and head (None when absent).
    ``files`` is None when the whole layer must run; the reasons say, per
    changed file, why each group was selected.
    """
    # The import half judges everything but the code the map covers: the
    # inputs every test shares, conftests, test files and test data. A test
    # that imports a changed module without executing it is not selected; an
    # import that breaks fails Backend Test's collection of every test.
    static, why = backend_imports.select_integration(backend, [
        path for path in changed
        if not (path.startswith(tuple(f"backend/{m}" for m in MEASURED)) and path.endswith(".py"))])
    if static is None:
        return None, [why]
    selected = set(static)
    reasons = [f"import graph: {why}"]
    mapped = cmap["files"]
    paths, importers = backend_imports.graph(backend)
    index = None
    closure_seeds = set()
    for path in changed:
        if not path.startswith("backend/"):
            continue
        rel = path[len("backend/"):]
        if rel.startswith("tests/"):
            if rel.endswith(".py"):
                selected.add(rel)
            continue
        if not rel.startswith(MEASURED):
            continue
        if not rel.endswith(".py"):
            # Data beside code (a prompt, a template) is read by the modules of
            # its folder: the tests of every function in them.
            folder = str(PurePosixPath(rel).parent)
            hit = {t for f, fs in mapped.items() if str(PurePosixPath(f).parent) == folder
                   for ts in fs.values() for t in ts}
            selected |= hit
            reasons.append(f"{rel}: data read by its folder's modules -> {len(hit)} files")
            continue
        head, base = read_head(path), read_base(path)
        if head is None:
            hit = {t for ts in mapped.get(rel, {}).values() for t in ts}
            selected |= hit
            reasons.append(f"{rel}: deleted, every test that ran it -> {len(hit)} files")
            continue
        old, new = lines.get(path, (set(), set()))
        functions, names, bare = set(), set(), base is None
        for text, touched in ((base, old), (head, new)):
            if text is None or not touched:
                continue
            try:
                found, imports = spans(text)
            except SyntaxError:
                bare = True
                continue
            module_lines = set()
            source_lines = text.splitlines()
            for line in touched:
                name = function_at(found, line)
                if name:
                    functions.add(name)
                    continue
                stripped = source_lines[line - 1].strip() if line <= len(source_lines) else ""
                if line not in imports and stripped and not stripped.startswith("#"):
                    module_lines.add(line)
            if module_lines:
                defined, unnamed = _defined(text, module_lines)
                names |= defined
                bare = bare or unnamed
        hit = {t for f in functions for t in mapped.get(rel, {}).get(f, ())}
        note = [f"functions {sorted(functions)[:5]}{'…' if len(functions) > 5 else ''}"] if functions else []
        if names:
            if index is None:
                index = readers(backend, paths)
            by_name = set()
            for name in names:
                for f, fn in index.get(name, ()):
                    by_name.update(mapped.get(f, {}).get(fn, ()))
                # The methods of a changed class read its body.
                for fn, tests in mapped.get(rel, {}).items():
                    if fn.split(".", 1)[0] == name:
                        by_name.update(tests)
            hit |= by_name
            note.append(f"names {sorted(names)[:5]}{'…' if len(names) > 5 else ''}")
        if bare:
            closure_seeds.add(backend_imports.module_name(rel))
            note.append("a top-level statement that binds no name")
        selected |= hit
        reasons.append(f"{rel}: {', '.join(note) or 'imports or comments only'} -> {len(hit)} files")
    if closure_seeds:
        reached = backend_imports.reached_by(closure_seeds, importers)
        hit = {t for m in reached for ts in mapped.get(paths.get(m, ""), {}).values() for t in ts}
        selected |= hit
        reasons.append(f"every module importing {sorted(closure_seeds)}: {len(reached)} modules -> {len(hit)} files")
    unmapped = set(cmap.get("unmapped", ()))
    if unmapped:
        selected |= unmapped
        reasons.append(f"{len(unmapped)} test files the map does not cover run every time")
    present = {rel for rel in selected
               if (backend / rel).is_file() and not rel.startswith(backend_imports.NOT_COLLECTED)}
    return sorted(present), reasons


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build")
    b.add_argument("coverage_file", type=Path)
    b.add_argument("--backend", type=Path, required=True)
    b.add_argument("--out", type=Path, required=True)
    m = sub.add_parser("merge")
    m.add_argument("shards", type=Path, nargs="+")
    m.add_argument("--selection", type=Path, required=True)
    m.add_argument("--commit", required=True)
    m.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "build":
        result = build(args.coverage_file, args.backend, args.out)
        print(f"{len(result)} source files mapped")
        return 0
    if len(args.shards) != SHARDS:
        print(f"::error::{len(args.shards)} traced shards, expected {SHARDS}: a missing shard "
              "leaves its tests out of the map, so it is not published")
        return 1
    result = merge([json.loads(p.read_text()) for p in args.shards],
                   planned_files(args.selection), args.commit)
    args.out.write_text(json.dumps(result, sort_keys=True))
    tests = {t for fs in result["files"].values() for ts in fs.values() for t in ts}
    print(f"map at {args.commit}: {len(result['files'])} source files, {len(tests)} test files, "
          f"{len(result['unmapped'])} planned test files without a context")
    return 0


if __name__ == "__main__":
    sys.exit(main())
