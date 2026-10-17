"""Check a Python pure move without importing either version.

Usage: move_only_verify.py --base-ref origin/main old.py facade.py parts.py ...
The destinations are the complete new file set; the FIRST is the public entry
point (the surviving old module or the new package's __init__.py). Paths are
repository-relative. The base is the exact commit, not its merge base.

Compare multisets of full top-level function/class and assignment ASTs, ignoring
locations/comments, plus the old/new entry point's exported bindings. Explicit
literal __all__ wins; otherwise all non-private bindings, including imports,
are public. Follow static re-exports only within the supplied destination set.
No functions, imports, or assignment RHSs from the inputs are executed.

Exit 0: these structural checks agree; 1: a difference; 2: unsupported/unreadable.
This is NOT a behaviour proof: import-time ordering, changed globals inside moved
functions, circular imports, and monkeypatch/logger identity still need review
and tests. Dynamic module statements, star imports, and dynamic __all__ cannot
be judged by this deliberately narrow check. verify-module-split.py separately
checks interface compatibility for refactors that are not pure moves.
"""

import argparse
import ast
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
import subprocess
import sys


class Unjudged(ValueError):
    pass


def dump(node):
    return ast.dump(node, include_attributes=False)


def target_names(node):
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, (ast.Tuple, ast.List)):
        return [name for item in node.elts for name in target_names(item)]
    raise Unjudged("module assignment to an attribute/subscript is not a pure declaration")


@dataclass
class Module:
    path: str
    definitions: Counter = field(default_factory=Counter)
    constants: Counter = field(default_factory=Counter)
    bindings: dict = field(default_factory=dict)
    exports: set | None = None

    @property
    def name(self):
        parts = tuple(Path(self.path).with_suffix("").parts)
        return parts[:-1] if parts[-1] == "__init__" else parts

    def bind(self, name, value):
        if name in self.bindings:
            raise Unjudged(f"{self.path}: repeated binding {name}")
        self.bindings[name] = value

    def public(self):
        return self.exports if self.exports is not None else {
            name for name in self.bindings if not name.startswith("_")}


def parse(path, source):
    module = Module(path)
    tree = ast.parse(source, filename=path)
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            continue  # Module docstrings may move; function/class docstrings may not.
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            key = (node.name, dump(node))
            module.definitions[key] += 1
            module.bind(node.name, ("local", key))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = [name for target in targets for name in target_names(target)]
            if "__all__" in names:
                if names != ["__all__"] or module.exports is not None:
                    raise Unjudged(f"{path}: repeated or chained __all__")
                try:
                    values = ast.literal_eval(node.value)
                except (ValueError, TypeError, SyntaxError) as exc:
                    raise Unjudged(f"{path}: dynamic __all__") from exc
                if not isinstance(values, (list, tuple)) or not all(isinstance(v, str) for v in values):
                    raise Unjudged(f"{path}: __all__ must be a literal list/tuple of names")
                module.exports = set(values)
                continue
            key = (", ".join(names), dump(node))
            module.constants[key] += 1
            for name in names:
                module.bind(name, ("local", key))
        elif isinstance(node, ast.ImportFrom):
            if node.module == "__future__" and node.level == 0:
                # These affect annotations and other semantics, unlike ordinary
                # plumbing imports. Preserve the feature set on every moved file.
                for alias in node.names:
                    module.bind(f"__future__:{alias.name}", ("future", alias.name))
                continue
            for alias in node.names:
                if alias.name == "*":
                    raise Unjudged(f"{path}: star import")
                module.bind(alias.asname or alias.name, ("from", node.level, node.module or "", alias.name))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                module.bind(alias.asname or alias.name.split(".")[0],
                            ("import", alias.name, bool(alias.asname)))
        else:
            raise Unjudged(f"{path}:{node.lineno}: unsupported module statement {type(node).__name__}")
    return module


def imported_module(module, level, name):
    if not level:
        return tuple(name.split(".")) if name else ()
    package = module.name if Path(module.path).stem == "__init__" else module.name[:-1]
    if level > len(package):
        raise Unjudged(f"{module.path}: relative import escapes the package")
    return package[:len(package) - level + 1] + (tuple(name.split(".")) if name else ())


def resolve(module, name, modules, visiting=frozenset()):
    key = (module.path, name)
    if key in visiting:
        raise Unjudged(f"{module.path}: circular re-export of {name}")
    binding = module.bindings.get(name)
    if binding is None:
        raise Unjudged(f"{module.path}: exported name {name} has no static binding")
    if binding[0] != "from":
        return binding
    _, level, imported, original = binding
    target = imported_module(module, level, imported)
    # Absolute imports may omit a source root such as backend/. A suffix is
    # accepted only when it identifies one supplied module unambiguously.
    candidates = [m for m in modules if m.name == target or (
        not level and target and m.name[-len(target):] == target)]
    if len(candidates) > 1:
        raise Unjudged(f"{module.path}: ambiguous import {imported}")
    if candidates:
        return resolve(candidates[0], original, modules, visiting | {key})
    return ("external", target, original)


def compare(old, new):
    differences = []
    for attribute in ("definitions", "constants"):
        before = getattr(old, attribute)
        after = sum((getattr(m, attribute) for m in new), Counter())
        for label, items in (("missing/changed", before - after), ("added/changed", after - before)):
            for (name, _), count in sorted(items.items()):
                differences.append(f"{attribute} {label}: {name} (x{count})")
    before_exports, after_exports = old.public(), new[0].public()
    for label, names in (("missing", before_exports - after_exports), ("added", after_exports - before_exports)):
        if names:
            differences.append(f"exports {label}: {', '.join(sorted(names))}")
    # Comparing future imports per file avoids silently changing annotation
    # evaluation when a definition was otherwise moved byte-for-byte.
    features = {v for v in old.bindings.values() if v[0] == "future"}
    for module in new:
        if {v for v in module.bindings.values() if v[0] == "future"} != features:
            differences.append(f"future imports changed: {module.path}")
    unknown = []
    for name in sorted(before_exports & after_exports):
        try:
            if resolve(old, name, [old]) != resolve(new[0], name, new):
                differences.append(f"export binding changed: {name}")
        except Unjudged as exc:
            unknown.append(str(exc))
    return differences, unknown


def git(root, *args):
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, timeout=15)
    if result.returncode:
        raise Unjudged(result.stderr.decode(errors="replace").strip())
    return result.stdout


def relative_path(root, value):
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or path.suffix != ".py":
        raise Unjudged(f"expected a repository-relative .py path: {value}")
    if not (root / path).resolve().is_relative_to(root):
        raise Unjudged(f"path escapes repository: {value}")
    return path.as_posix()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-ref", default="origin/main")
    parser.add_argument("source", help="original Python file at base-ref")
    parser.add_argument("destinations", nargs="+", help="complete working-tree file list, public entry point first")
    args = parser.parse_args(argv)
    try:
        root = Path(git(Path.cwd(), "rev-parse", "--show-toplevel").decode().strip()).resolve()
        source = relative_path(root, args.source)
        paths = [relative_path(root, path) for path in args.destinations]
        if len(set(paths)) != len(paths):
            raise Unjudged("duplicate destination paths")
        sha = git(root, "rev-parse", "--verify", "--end-of-options", f"{args.base_ref}^{{commit}}").decode().strip()
        old = parse(source, git(root, "show", f"{sha}:{source}"))
        new = [parse(path, (root / path).read_bytes()) for path in paths]
        differences, unknown = compare(old, new)
    except (Unjudged, OSError, SyntaxError, UnicodeError, subprocess.TimeoutExpired) as exc:
        print(f"UNJUDGED: {exc}", file=sys.stderr)
        return 2
    for difference in differences:
        print(f"DIFFERENT: {difference}")
    for reason in unknown:
        print(f"UNJUDGED: {reason}")
    if differences:
        return 1
    if unknown:
        return 2
    print(f"PASS: AST declarations and entry-point exports conserved against {sha[:12]}; "
          "runtime behaviour unverified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
