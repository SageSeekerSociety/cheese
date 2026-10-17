#!/usr/bin/env python3
"""Compare a module against the package it was split into, or a directory of
modules against the same directory in the base ref.

    .claude/scripts/verify-module-split.py \\
        --check-facade backend/app/domain/review/polling --base-ref origin/main
    .claude/scripts/verify-module-split.py \\
        --check-facade app.domain.review.polling --base-ref origin/main
    .claude/scripts/verify-module-split.py \\
        --check-moves backend/app/domain/review --base-ref origin/main
    .claude/scripts/verify-module-split.py --self-test

    0 = nothing to report        1 = a difference the change introduced
    2 = cannot judge (not a package, the old file is not in the base ref, a
        file that does not parse, a second copy of the module importable
        somewhere) — never a pass

WHY. Turning `a/foo.py` into `a/foo/__init__.py` plus submodules is mechanical
until it isn't. The facade checks cover these three ways it goes wrong:

- **A public name stops being exported.** `from a.foo import Widget` worked
  before; after the split `Widget` lives in `a/foo/models.py` and `__init__.py`
  forgot to re-export it. Nothing in the tree imports `Widget` by that path
  except the callers, and they are the ones that break.
- **A callable's signature changes.** The name is still there, but a caller
  passing by keyword or a test patching the attribute fails, and no type check
  sees it because the function is still callable. `def` becoming `async def`
  counts, and so does a parameter losing its default or gaining a `/` in front
  of it: both keep the module importable and the tests green.
- **A deferred import appears.** `backend/scripts/check_deferred_imports.py`
  freezes function-body imports per file, which means a split resets the count
  for every file it creates: moving an import into a new module and adding one
  there is indistinguishable, from the baseline's side, from moving it alone.

MOVES. `--check-moves DIR` asks the other half of the same question. A split
creates a package where one file stood; a move leaves every file where it was
and takes a name out of one of them for a sibling — `ClaudeLaunch` out of
`session_launch.py` into `device_launch.py` — and the callers that imported it
from the module it left break at import where no local gate looks. So every
`.py` under DIR whose content differs from the base ref is compared with its
base-ref self: a name it no longer offers that a repository caller still
imports from it is a blocker, and where a sibling picked the name up the report
names that sibling so the reader sees the move rather than a deletion. A module
the change deletes outright counts the same way, unless a package of the same
name took its place — that is `--check-facade`'s question, and answering it
twice would read as two findings for one break.

WHAT IS COMPARED. The old side is the pre-split single file, read from git
(`git show <ref>:<path>`) so the working tree may already hold the package. The
new side is the package directory on disk. A file whose old path cannot be
derived gets `--old-file`.

The package's own facade is followed however it names its submodules, relative
or absolute: `from .models import Widget` and
`from app.x.y.models import Widget as Widget` are the same re-export, and
detecting only the first would report every split in this repository as having
dropped its whole surface.

A name the old module only *imported* is not required of the package. Every
module imports a few dozen names for its own use, and demanding they survive
would turn every run red. What the old module binds itself — `def`, `class`,
assignment, and anything named in `__all__` — is required; the rest is listed
in the note line so a reader can see what was set aside. The consumer check
separately requires those names (including private ones) when a repository
caller imports them. Two more names are set aside by the facade check:
`LOG = logging.getLogger(__name__)`, which each new module writes its own copy
of, and a private class member that became a module-level function taking the
instance — both reported as notes, not as lost names. A *public* constant is
required whatever it is built from, and `__all__` overrides both.

Two things are out of reach and each is said out loud instead of passed over in
silence. A module that assigns `__all__` to something other than a literal
(`sorted([...])`, a concatenation) declares names this cannot read: they fall
back to top-level declarations and the run says so in a note. A module-level
`__getattr__` (PEP 562) serves names no static read can enumerate; on the old
side that costs nothing, on the package side it turns a real export into
「顶层符号不再导出」— a false alarm, not a silent pass.

The deferred-import rule is `check_deferred_imports.py`'s: an `import` /
`from ... import` lexically inside a `def` or `async def`, at any depth, counted
once per statement. That file stays the authority on the rule; the self-test
compares this script's count against its `deferred_imports_in` on the same
source so the two cannot drift apart silently.

SHADOW COPIES. Comparing one module against one package means exactly one copy
of that module may be in reach. A pre-split file left beside the package, or a
copy of the module parked under the system temp directory, is a second module
under the same name: `--runtime-identity` imports whichever one it reaches
first, and the checks around this tool — eslint, a ratchet, test collection —
read the tree as it stands. Neither is a difference the change introduced, so
the run says which copy to delete and exits 2 instead of reporting on two
modules at once. Beside the package the name decides, since one directory
cannot hold both; under a temp directory the text does, because a file named
like the module but holding something else is somebody else's work, not a copy
of this one. The move check reads no such copy and does not look.

CONSUMERS. Git-tracked and unignored new Python files across the repository
(including cli/, scripts/, backend/scripts/ and .claude/scripts/) are scanned
for imports from the package and literal setattr / monkeypatch.setattr /
unittest.mock.patch targets. A facade alias does not preserve a moved function's
global lookups. Patches on emission methods of a named logging.getLogger object
are checked against the new modules' logger names; parent handlers remain valid.
Comments and single-line strings that say patch/replace/mock (or 替换/补丁) are
checked for package-qualified targets. The convention `tests replace this name
on <module>` is compared with the adjacent import's source, not interpreted as
an arbitrary natural-language claim about what tests actually do.

WHOLE-FILE READS. A caller can depend on a module without naming anything in
it: `Path(module.__file__).read_text()` ships the source of a module that runs
on another machine. It keeps working after `a/b.py` becomes `a/b/__init__.py`
and then answers with the package's `__init__.py` instead of the module — a
silent wrong file rather than an ImportError. The same holds for
`module.__spec__.origin`, and for `spec_from_file_location(name, location)`
when the location comes from either. A literal location, by contrast, still
loads the same file: the location argument, not the module name, decides what
is loaded, and the name only labels the spec. `--check-moves` scans the whole
repository for these shapes against every module the change turned into a
package, following `import a.b`, `from a import b`, `import a.b as c` and
`x = importlib.import_module("a.b")` to the module each names. A write to
`__file__` is not a read and is left alone.

This is an explicit CLI check, not an automatic CI gate. Dynamic patch
targets, external object attributes and arbitrary prose are outside its static
coverage. No application module is imported by the consumer check.

Only the standard library is used: no network, no third-party import, no write.
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import io
import json
import os
import re
import tokenize
import subprocess
import symtable
import sys
import tempfile
import textwrap
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

EXIT_OK, EXIT_DIFFERENT, EXIT_CANNOT_JUDGE = 0, 1, 2

DEFAULT_BASE_REF = "origin/main"

#: The module that owns the deferred-import rule; the self-test cross-checks
#: against it when it is present.
DEFERRED_RULE_SOURCE = Path("backend/scripts/check_deferred_imports.py")


def _public(name: str) -> bool:
    return bool(name) and not name.startswith("_")


# ---------------------------------------------------------------------------
# Reading one module
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DeferredImport:
    """One import statement lexically inside a function body."""

    target: str
    #: the modules it depends on, alias dropped and relative imports resolved to
    #: the module they name (`from .util` in `pkg/mod.py` -> `pkg.util`, so the
    #: same statement one directory deeper keeps its key)
    keys: tuple[str, ...]
    line: int


@dataclass(frozen=True)
class Signature:
    """A callable's parameter list, positionally."""

    kind: str
    params: tuple[str, ...]


@dataclass
class ModuleFacts:
    """What one `.py` file declares at its top level."""

    path: str
    module_name: str = ""
    #: public name -> the node that binds it
    defined: dict[str, ast.AST] = field(default_factory=dict)
    #: names a top-level `import` / `from ... import` brought in
    imported: set[str] = field(default_factory=set)
    #: name bound by `from .sub import name` -> (submodule key, name there)
    relative_imports: dict[str, tuple[str, str]] = field(default_factory=dict)
    #: `from .sub import *`
    star_imports: list[str] = field(default_factory=list)
    #: `__all__` when it is a literal list of strings, else None
    all_list: list[str] | None = None
    #: the file assigns `__all__` but not to something this can read
    all_unreadable: bool = False
    #: qualified name (`Widget.render`) -> signature
    signatures: dict[str, Signature] = field(default_factory=dict)
    deferred: list[DeferredImport] = field(default_factory=list)


def _param_text(arg: ast.arg, has_default: bool) -> str:
    """A parameter as a caller meets it: `b` or `b=`, meaning it may be omitted."""
    return arg.arg + ("=" if has_default else "")


def _signature(node: ast.AST, kind: str) -> Signature:
    """Everything about the parameter list a caller can trip over.

    Three things a name-only list would hide, each of them a break the caller
    sees and no type check does:

    - a bare `/` closes the positional-only group, so `f(a, /, b)` refuses
      `f(a=1)` where `f(a, b)` accepts it;
    - a trailing `=` marks a default, so `f(a, b)` refuses `f(a)` where
      `f(a, b=1)` accepts it;
    - `kind` carries `async`, so `async def run` is not `def run`.
    """
    args = node.args  # type: ignore[attr-defined]
    positional = [*args.posonlyargs, *args.args]
    first_default = len(positional) - len(args.defaults)
    params: list[str] = []
    for index, arg in enumerate(positional):
        params.append(_param_text(arg, index >= first_default))
        if args.posonlyargs and index + 1 == len(args.posonlyargs):
            params.append("/")
    if args.vararg is not None:
        params.append("*" + args.vararg.arg)
    elif args.kwonlyargs:
        # `def f(a, *, b)` and `def f(a, b)` must not compare equal: only the
        # first refuses a third positional argument.
        params.append("*")
    params += [
        _param_text(arg, default is not None)
        for arg, default in zip(args.kwonlyargs, args.kw_defaults, strict=True)
    ]
    if args.kwarg is not None:
        params.append("**" + args.kwarg.arg)
    return Signature(kind=kind, params=tuple(params))


def _collect_methods(cls: ast.ClassDef, prefix: str, out: dict[str, Signature]) -> None:
    for child in cls.body:
        if isinstance(child, ast.FunctionDef):
            out[f"{prefix}.{child.name}"] = _signature(child, "method")
        elif isinstance(child, ast.AsyncFunctionDef):
            out[f"{prefix}.{child.name}"] = _signature(child, "async method")
        elif isinstance(child, ast.ClassDef):
            _collect_methods(child, f"{prefix}.{child.name}", out)


def _bind(target: ast.AST, node: ast.AST, facts: ModuleFacts) -> None:
    if isinstance(target, ast.Name):
        facts.defined[target.id] = node
    elif isinstance(target, (ast.Tuple, ast.List)):
        for element in target.elts:
            _bind(element, node, facts)
    elif isinstance(target, ast.Starred):
        _bind(target.value, node, facts)


def _guarded_by_type_checking(test: ast.AST) -> bool:
    """Whether the `if` body only runs while type checking.

    `if TYPE_CHECKING:`: the body is for the type checker and the `else` branch
    is what runs — so the body is skipped and the `else` is read. A test that
    mentions the name in any other way could run either branch, and the body is
    then skipped as well, which is the conservative direction for a name the
    old module claimed to export.
    """
    for node in ast.walk(test):
        if isinstance(node, ast.Name) and node.id == "TYPE_CHECKING":
            return True
        if isinstance(node, ast.Attribute) and node.attr == "TYPE_CHECKING":
            return True
    return False


def _target_names(target: ast.AST) -> list[str]:
    """The names an assignment or `del` target covers."""
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)):
        return [name for element in target.elts for name in _target_names(element)]
    if isinstance(target, ast.Starred):
        return _target_names(target.value)
    return []


def _internal_submodule(node: ast.ImportFrom, package_name: str) -> str:
    """The submodule key an absolute import names, when it names this package.

    Splits here re-export a package's names from its own facade with absolute
    imports — `from app.x.y.advance import advance_pr_card as advance_pr_card`.
    A reader that followed only relative imports would call every one of those a
    name the package no longer exposes.
    """
    if node.level or not node.module or not package_name:
        return ""
    prefix = package_name + "."
    if not node.module.startswith(prefix):
        return ""
    return node.module[len(prefix) :]


def _collect_import_from(
    node: ast.ImportFrom, facts: ModuleFacts, package_name: str = ""
) -> None:
    if node.module == "__future__":
        return
    relative = bool(node.level) and bool(node.module)
    internal = _internal_submodule(node, package_name)
    submodule = node.module
    if node.level and package_name and facts.module_name:
        absolute = _absolute_module(facts.module_name, node.level, node.module)
        relative = absolute == package_name or absolute.startswith(package_name + ".")
        submodule = absolute[len(package_name) :].lstrip(".") if relative else absolute
    for alias in node.names:
        if alias.name == "*":
            if submodule is not None:
                facts.star_imports.append(internal or submodule)
            continue
        name = alias.asname or alias.name
        facts.imported.add(name)
        if relative:
            facts.relative_imports[name] = (submodule, alias.name)
        elif internal:
            facts.relative_imports[name] = (internal, alias.name)


def _collect_statements(
    body: list[ast.stmt], facts: ModuleFacts, package_name: str = ""
) -> None:
    """Bindings from a list of statements, recursing into `if` and `try`.

    A name defined under `if` / `try` is a name callers can import, so missing
    one would be a false alarm. A `TYPE_CHECKING` block is skipped whole: what
    it imports does not exist at run time on purpose.
    """
    for node in body:
        if isinstance(node, ast.FunctionDef):
            facts.defined[node.name] = node
            facts.signatures[node.name] = _signature(node, "def")
        elif isinstance(node, ast.AsyncFunctionDef):
            facts.defined[node.name] = node
            facts.signatures[node.name] = _signature(node, "async def")
        elif isinstance(node, ast.ClassDef):
            facts.defined[node.name] = node
            _collect_methods(node, node.name, facts.signatures)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                _bind(target, node, facts)
        elif isinstance(node, ast.AnnAssign):
            # `x: int` alone binds nothing at run time.
            if node.value is not None:
                _bind(node.target, node, facts)
        elif isinstance(node, ast.AugAssign):
            _bind(node.target, node, facts)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                facts.imported.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            _collect_import_from(node, facts, package_name)
        elif isinstance(node, ast.Delete):
            # `X = 1` then `del X`: the module offers no `X` to import, so
            # requiring one of the package would be a false alarm.
            for target in node.targets:
                for name in _target_names(target):
                    facts.defined.pop(name, None)
        elif isinstance(node, ast.If):
            if _guarded_by_type_checking(node.test):
                # The `else` branch is the one that runs at run time.
                _collect_statements(node.orelse, facts, package_name)
                continue
            _collect_statements(node.body, facts, package_name)
            _collect_statements(node.orelse, facts, package_name)
        elif isinstance(node, ast.Try):
            _collect_statements(node.body, facts, package_name)
            for handler in node.handlers:
                _collect_statements(handler.body, facts, package_name)
            _collect_statements(node.orelse, facts, package_name)
            _collect_statements(node.finalbody, facts, package_name)


def _literal_all(tree: ast.Module) -> list[str] | None:
    """`__all__` when it is a literal list or tuple of strings, else None."""
    return _read_all(tree)[0]


def _read_all(tree: ast.Module) -> tuple[list[str] | None, bool]:
    """`(names, present_but_unreadable)` for a module-level `__all__`.

    A module that computes its `__all__` — `sorted([...])`, a concatenation, a
    loop — declares it just as much as one that writes it out, and this cannot
    see through that. The caller says so in the report rather than treating the
    module as if it had none: the two are not the same answer.
    """
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign):
            targets, value = [node.target], node.value
        else:
            continue
        if not any(isinstance(t, ast.Name) and t.id == "__all__" for t in targets):
            continue
        if not isinstance(value, (ast.List, ast.Tuple)):
            return None, True
        names: list[str] = []
        for element in value.elts:
            if isinstance(element, ast.Constant) and isinstance(element.value, str):
                names.append(element.value)
            else:
                return None, True
        return names, False
    return None, False


def _deferred(source: str, module_name: str = "") -> list[DeferredImport]:
    """Every import statement lexically inside a function body, once each.

    Same rule as `backend/scripts/check_deferred_imports.py`: an import nested
    one function deeper is one import, not one per enclosing function, and an
    import in a class body or under a module-level `if` is not counted.
    """
    tree = ast.parse(source)
    found: list[DeferredImport] = []

    def walk(node: ast.AST, in_function: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if in_function and isinstance(child, (ast.Import, ast.ImportFrom)):
                found.append(
                    DeferredImport(
                        _deferred_target(child),
                        _deferred_keys(child, module_name),
                        child.lineno,
                    )
                )
            walk(
                child,
                in_function
                or isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)),
            )

    walk(tree, False)
    return found


def _deferred_target(node: ast.Import | ast.ImportFrom) -> str:
    """The statement as written, for the report only.

    Not what the comparison uses, because the same dependency can be written
    several ways: `import a, b` and `import b, a`, or one statement and two,
    name the same modules.
    """
    if isinstance(node, ast.Import):
        return "import " + ", ".join(alias.name for alias in node.names)
    module = "." * node.level + (node.module or "")
    return f"from {module} import " + ", ".join(alias.name for alias in node.names)


def _absolute_module(module_name: str, level: int, module: str | None) -> str:
    """`from ..x import y` in `<module_name>` as the module it actually names.

    A relative import is a dependency on a real module, and which module that
    is depends on where the statement sits. `from .util import helper` in
    `pkg/impl.py` still names `pkg.util` after the file moves to
    `pkg/impl/rendering.py` only if the dots were rewritten to `..`; comparing
    the written form would report the correct rewrite as a new import and the
    stale one as unchanged.
    """
    if not level:
        return module or ""
    parts = module_name.split(".") if module_name else []
    # A module's own package is everything above it; each extra level goes up
    # one from there.
    package = parts[:-1] if parts else []
    keep = len(package) - (level - 1)
    if keep < 0:
        return "." * level + (module or "")
    prefix = parts[:keep]
    if module:
        prefix = [*prefix, module]
    return ".".join(prefix)


def _deferred_keys(
    node: ast.Import | ast.ImportFrom, module_name: str
) -> tuple[str, ...]:
    """What a deferred import depends on: module names, aliases dropped.

    `import a as b` and `import a` are the same dependency, and `from a import
    b, c` is one dependency on `a`, not two. A comparison that noticed the
    aliases or the grouping would report a rename or a split statement as a new
    import.
    """
    if isinstance(node, ast.Import):
        return tuple(sorted(alias.name for alias in node.names))
    resolved = _absolute_module(module_name, node.level, node.module)
    return (resolved,) if resolved else ()


def read_source(
    source: str, path: str, module_name: str = "", package_name: str = ""
) -> ModuleFacts:
    facts = ModuleFacts(path=path, module_name=module_name)
    tree = ast.parse(source, filename=path)
    _collect_statements(tree.body, facts, package_name)
    facts.all_list, facts.all_unreadable = _read_all(tree)
    facts.deferred = _deferred(source, module_name)
    return facts


# ---------------------------------------------------------------------------
# Reading the package
# ---------------------------------------------------------------------------


@dataclass
class Package:
    root: Path
    #: "" is `__init__.py`; anything else is the dotted path inside the package
    modules: dict[str, ModuleFacts]
    #: Which entry is the module under inspection, when the table is a whole
    #: package. A caller's `from app.foo import x` walks down the table from
    #: there — read as "from the top" it would take the module's own directory
    #: siblings for submodules of it. Empty in facade mode, where the table is
    #: the package and the module under inspection is its `""`.
    key: str = ""
    #: The package's dotted name, for naming the modules the table holds.
    name: str = ""

    @property
    def facade(self) -> ModuleFacts:
        return self.modules[""]


def read_package(package_dir: Path, module_name: str = "") -> Package:
    modules: dict[str, ModuleFacts] = {}
    for path in sorted(package_dir.rglob("*.py")):
        relative = path.relative_to(package_dir)
        if any(
            part == "__pycache__" or part.startswith(".") for part in relative.parts
        ):
            continue
        if relative.name == "__init__.py":
            key = ".".join(relative.parts[:-1])
        else:
            key = ".".join(relative.parts[:-1] + (relative.stem,))
        inside = f"{module_name}.{key}" if key else module_name
        if relative.name == "__init__.py" and inside:
            inside += ".__init__"
        modules[key] = read_source(
            path.read_text(encoding="utf-8"), str(path), inside, module_name
        )
    if "" not in modules:
        raise ValueError(f"{package_dir} has no __init__.py, so it is not a package")
    return Package(root=package_dir, modules=modules, name=module_name)


def _resolve(
    package: Package,
    module_key: str,
    name: str,
    seen: frozenset = frozenset(),
):
    """Which submodule the package-level `name` really comes from.

    Follows `from .x import name` and `from .x import *` so a facade of facades
    resolves too. Returns `(module_key, name_there)`, or None.
    """
    if (module_key, name) in seen:
        return None
    facts = package.modules.get(module_key)
    if facts is None:
        return None
    if name in facts.defined:
        return (module_key, name)
    seen = seen | {(module_key, name)}
    if name in facts.relative_imports:
        sub, original = facts.relative_imports[name]
        return _resolve(package, sub, original, seen)
    for star in facts.star_imports:
        star_facts = package.modules.get(star)
        if star_facts is None or (star, name) in seen:
            continue
        if not _reachable_through_star(star_facts, name):
            # `from .x import *` brings in `x.__all__` when it has one, and
            # otherwise every public name — a name outside it is not reachable
            # this way, however it got defined in `x`.
            continue
        if name in star_facts.defined or name in star_facts.imported:
            return (star, name)
        hit = _resolve(package, star, name, seen)
        if hit is not None:
            return hit
    return None


def _reachable_through_star(star_facts: ModuleFacts, name: str) -> bool:
    """What `from <star_facts> import *` actually binds."""
    if star_facts.all_list is not None:
        return name in star_facts.all_list
    return _public(name) and (name in star_facts.defined or name in star_facts.imported)


def facade_exports(package: Package) -> set[str]:
    """The public names `from <package> import X` can reach."""
    facade = package.facade
    names = {n for n in set(facade.defined) | facade.imported if _public(n)}
    names |= {n for n in facade.all_list or [] if _public(n)}
    names |= {n for n in facade.relative_imports if _public(n)}
    for star in facade.star_imports:
        star_facts = package.modules.get(star)
        if star_facts is None:
            continue
        # `from .x import *` binds `x.__all__` when `x` declares one, and every
        # public name it has otherwise — counting the rest would report a name
        # as reachable that `import package` never binds.
        if star_facts.all_list is not None:
            reached = set(star_facts.all_list)
        else:
            reached = set(star_facts.defined) | star_facts.imported
        names |= {n for n in reached if _public(n)}
    return names


def facade_signatures(package: Package) -> dict[str, Signature]:
    """Signatures keyed the way the package exposes them.

    `Widget.render`, not `models.Widget.render`.
    """
    out = dict(package.facade.signatures)
    for name in facade_exports(package):
        if name in out:
            continue
        hit = _resolve(package, "", name)
        if hit is None or hit[0] == "":
            continue
        module_key, original = hit
        for key, signature in package.modules[module_key].signatures.items():
            if key == original or key.startswith(original + "."):
                out.setdefault(name + key[len(original) :], signature)
    return out


def signature_locations(package: Package) -> dict[str, set[str]]:
    """Qualified name -> the submodules that define it.

    Tells `Foo.bar` having moved somewhere the facade does not expose apart
    from `Foo.bar` having disappeared, which are different findings.
    """
    index: dict[str, set[str]] = {}
    for key, facts in package.modules.items():
        for name in facts.signatures:
            index.setdefault(name, set()).add(key or "__init__.py")
    return index


def _shape_params(signature: Signature) -> tuple[str, ...]:
    """The parameter names a lift has to reproduce.

    `Signature.params` is written for callers, and two of its entries are not
    names: the `/` that closes the positional-only group, and the `*args` or
    `**kwargs` marker, which carries a name no caller can use. Renaming a vararg
    leaves every caller alone, so a lift may do it; a `/` cannot survive losing
    the parameter it guards.

    A marker keeps its kind. `*` and `*args` look alike once the name is gone,
    but they are not alike: the first accepts no positional and the second any
    number, so a namesake carrying the wrong one is not this method's lift.
    """
    names: list[str] = []
    for param in signature.params:
        if param == "/":
            continue
        if param.startswith("**"):
            names.append("**...")
        elif param == "*":
            names.append("*")
        elif param.startswith("*"):
            names.append("*...")
        else:
            names.append(param)
    return tuple(names)


def _lifted_shape(original: Signature, function: Signature) -> bool:
    """Is `function` the old method's parameter list with the instance handled?

    A lift writes the instance one of two ways: it keeps it as a parameter —
    `_room(self, project, topic, agent)` stays four wide and `_require_screen`
    spells the same slot `hub` — or the method was spending it on its owner and
    the function does without, `_fan_out_screen_data(self, screen, raw)` becoming
    `_fan_out_screen_data(screen, raw)`.

    The instance slot is pinned by what follows it, or by a list that came out
    unchanged: a tail that matches ties the two lists together, and so does
    keeping every name the method had, `self` included. With neither — a method
    that took only `self`, and a function that renamed it — `def _reset(value)`
    and `def _reset()` match on the name and the arity and nothing else, which
    is nothing to go on, so neither is read as a lift. A real lift of such a
    method that renamed the instance reads the same way and gets a red where the
    other reading gets a note; the class it left is where a person settles which
    one it was.

    A member that takes no instance at all — a `@staticmethod` — has no slot to
    lose, so `def _unavailable()` moving out as `def _unavailable()` is compared
    as it stands and matches.
    """
    if original.kind.startswith("async") != function.kind.startswith("async"):
        return False
    before, after = _shape_params(original), _shape_params(function)
    if before == after:
        # Nothing was dropped, so there is no instance slot to explain: the
        # function has every name the member had. A `@staticmethod` moving out
        # unchanged lands here, empty parameter list and all, and an empty list
        # is also the namesake both readings share.
        return True
    if len(after) == len(before):
        # Equal width and the lists differ, so the first slot is the one that
        # changed. A tail under it that matches pins that slot to the instance;
        # with no tail below it there is only the arity, and a namesake has that
        # too.
        return after[1:] == before[1:] and len(before) > 1
    if len(after) == len(before) - 1:
        return len(before) > 1 and after == before[1:]
    return False


def lifted_to_module_level(old: ModuleFacts, package: Package, key: str) -> str | None:
    """The module holding a class member that is now a plain function.

    Splitting a long class lifts a private helper out to a function. The name is
    still in the package and the class no longer has the member, so calling it a
    lost symbol would red-light the repo's own splits.

    What this reads is the parameter list, not the name and not the call sites: a
    module-level function named `_x` whose parameters are the old method's with
    `self` renamed or dropped is the lift. A name alone proves nothing —
    `DeviceHub._require_screen` sitting next to an unrelated `_require_screen` is
    how a method that really went missing gets reported as a note and the whole
    run as clean.

    Call sites are deliberately not read. They go wrong in both directions:
    `svc._x(3)` is a method call on an object that merely shares a name with a
    module, while `partial(_x, 1)`, `map(_x, ...)` and `@_x` are a lift whose
    callers never call the function at all. Neither tells a lift from a namesake;
    the parameter list does.

    Two unrelated functions can still share a name and the whole parameter list:
    `def _paint(width)` is the lift of `_paint(self, width)` and a namesake alike
    to anything readable here. That way round the cost is a note a person can
    check; the other way round the cost is a red run on a real lift.
    """
    head, _, member = key.rpartition(".")
    if not head or not member.startswith("_"):
        return None
    original = old.signatures.get(key)
    if original is None:
        return None
    for module_key, facts in package.modules.items():
        node = facts.defined.get(member)
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        function = facts.signatures.get(member)
        if function is not None and _lifted_shape(original, function):
            return module_key or "__init__.py"
    return None


#: Names bound by a call the package must not be asked to repeat. This is the
#: one such factory in the repository: `logger = logging.getLogger(__name__)`
#: sits at the top of nearly every module, and each module a split creates
#: writes its own. A constant built by *any other* call — `frozenset({...})`,
#: `sorted([...])` — is something the old module made, and it is still required.
_SCAFFOLD_FACTORIES = frozenset({"logging.getLogger"})


def _scaffolding(node: ast.AST) -> bool:
    if not isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
        return False
    value = node.value
    if not isinstance(value, ast.Call):
        return False
    try:
        return ast.unparse(value.func) in _SCAFFOLD_FACTORIES
    except Exception:  # pragma: no cover - unparse is total on a parsed tree
        return False


def scaffolding_names(facts: ModuleFacts) -> set[str]:
    """The names `_scaffolding` sets aside, for the report to list."""
    return {n for n, node in facts.defined.items() if _public(n) and _scaffolding(node)}


def declared_public(facts: ModuleFacts) -> set[str]:
    """The names the old module offered: what it made, plus `__all__`."""
    return {
        n for n, node in facts.defined.items() if _public(n) and not _scaffolding(node)
    } | {n for n in facts.all_list or [] if _public(n)}


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------


class Findings:
    def __init__(self) -> None:
        self.blockers: list[str] = []
        self.notes: list[str] = []
        self.summary: list[str] = []

    def block(self, message: str) -> None:
        self.blockers.append(message)

    def note(self, message: str) -> None:
        self.notes.append(message)

    def render(self) -> str:
        lines = [f"[note] {m}" for m in self.notes]
        lines += [f"[FAIL] {m}" for m in self.blockers]
        return "\n".join(lines)


def check_exports(old: ModuleFacts, package: Package, findings: Findings) -> None:
    declared = declared_public(old)
    reachable = facade_exports(package)
    missing = sorted(declared - reachable)
    for name in missing:
        hit = _resolve(package, "", name)
        where = f"现在在 {hit[0]}，包顶层看不到" if hit and hit[0] else "包里找不到"
        findings.block(f"顶层符号不再导出: {name}（{where}）")

    if old.all_unreadable:
        findings.note(
            f"{old.path} 的 __all__ 不是字面量, 读不出它声明了哪些名字 — "
            "这一批名字只用「顶层赋了值」来算, 改名或丢失可能漏报"
        )

    if package.facade.all_unreadable:
        findings.note(
            f"{package.facade.path} 的 __all__ 不是字面量, 读不出内容 — "
            "「__all__ 多声明了名字」这一项没查"
        )

    # A name is worth checking only when the facade declares it public: every
    # real facade imports `logging` or `dataclasses`, and reporting those as
    # "new public names" is how a check stops being read.
    if package.facade.all_list is not None:
        extra = sorted(set(package.facade.all_list) - declared - set(old.imported))
        for name in extra:
            findings.block(f"__all__ 声明了旧模块没有的公开名: {name}")

    scaffolding = sorted(scaffolding_names(old) - set(old.all_list or []))
    if scaffolding:
        findings.note(
            "旧模块顶层还有 "
            + ", ".join(scaffolding)
            + " — 每个模块各自一份, 未要求包再导出"
        )

    incidental = sorted({n for n in old.imported if _public(n)} - declared)
    findings.summary.append(
        f"顶层公开符号: 旧 {len(declared)} 个, 包顶层可达 {len(reachable)} 个, "
        f"缺 {len(missing)} 个"
    )
    if incidental:
        findings.note(
            "旧模块顶层还 import 了 "
            + ", ".join(incidental[:8])
            + ("" if len(incidental) <= 8 else f" 等 {len(incidental)} 个")
            + " — 门面比对不要求保留；仓内调用方另查"
        )


def check_signatures(old: ModuleFacts, package: Package, findings: Findings) -> None:
    tops = declared_public(old)
    old_signatures = {
        k: v for k, v in old.signatures.items() if k.split(".")[0] in tops
    }
    new_signatures = facade_signatures(package)
    shared = sorted(set(old_signatures) & set(new_signatures))

    drifted = 0
    for key in shared:
        before, after = old_signatures[key], new_signatures[key]
        if before.kind != after.kind:
            drifted += 1
            findings.block(f"变了同步/异步: {key} 旧 {before.kind} → 包 {after.kind}")
        elif before.params != after.params:
            drifted += 1
            findings.block(
                f"形参变了: {key} 旧 ({', '.join(before.params)}) "
                f"→ 包 ({', '.join(after.params)})"
            )

    missing = sorted(set(old_signatures) - set(new_signatures))
    locations = signature_locations(package)
    lost = 0
    for key in missing:
        where = locations.get(key)
        if where:
            lost += 1
            findings.block(
                f"定义搬走但包顶层没暴露: {key} 现在只在 {', '.join(sorted(where))} 里"
            )
            continue
        lifted = lifted_to_module_level(old, package, key)
        if lifted:
            findings.note(
                f"方法被提到模块级: {key} 现在是 {lifted} 里的普通函数, 类上没有了"
            )
            continue
        lost += 1
        findings.block(f"符号没了: {key} 整个包里都找不到")

    findings.summary.append(
        f"函数签名: 比对 {len(shared)} 个, 形参变了 {drifted} 个, "
        f"未暴露或丢失 {lost} 个"
    )


def check_deferred(old: ModuleFacts, package: Package, findings: Findings) -> None:
    new_entries = [
        (key, item) for key, facts in package.modules.items() for item in facts.deferred
    ]
    old_keys = Counter(key for item in old.deferred for key in item.keys)
    new_keys = Counter(key for _, item in new_entries for key in item.keys)

    for dependency in sorted(new_keys):
        was = old_keys.get(dependency, 0)
        if was == 0:
            where = _deferred_sites(new_entries, dependency)
            findings.block(f"多出函数内延迟 import: {dependency}（{where}）")
        elif new_keys[dependency] > was:
            where = _deferred_sites(new_entries, dependency)
            findings.block(
                f"函数内延迟 import 变多了: {dependency} 旧 {was} 处 → "
                f"包 {new_keys[dependency]} 处（{where}）"
            )

    findings.summary.append(
        f"函数内延迟 import: 旧 {len(old.deferred)} 条 / 包 {len(new_entries)} 条, "
        f"依赖 旧 {len(old_keys)} 种 / 包 {len(new_keys)} 种"
    )


def _deferred_sites(new_entries, dependency: str) -> str:
    """Where the package imports `dependency` inside a function."""
    return ", ".join(
        sorted(
            f"{key or '__init__.py'}:{item.line}"
            for key, item in new_entries
            if dependency in item.keys
        )
    )


# ---------------------------------------------------------------------------
# Consumers: imports, patch lookups and the reasons that name those lookups
# ---------------------------------------------------------------------------


def _has_binding(package: Package, key: str, name: str, seen=frozenset()) -> bool:
    """Include private/incidental imports, but follow internal re-exports to a binding."""
    if (key, name) in seen or key not in package.modules:
        return False
    facts = package.modules[key]
    seen = seen | {(key, name)}
    if ".".join(filter(None, (key, name))) in package.modules:
        return True  # from a package import an actual submodule
    if name in facts.relative_imports:
        sub, original = facts.relative_imports[name]
        return _has_binding(package, sub, original, seen)
    if name in facts.defined or name in facts.imported:
        return True
    return any(
        _reachable_through_star(package.modules[sub], name)
        and _has_binding(package, sub, name, seen)
        for sub in facts.star_imports
        if sub in package.modules
    )


def _target_exists(package: Package, dotted: str, target: str) -> bool:
    if not target.startswith(dotted + "."):
        return True
    parts = target[len(dotted) + 1 :].split(".")
    key = package.key
    while parts and ".".join(filter(None, (key, parts[0]))) in package.modules:
        key = ".".join(filter(None, (key, parts.pop(0))))
    if not parts:
        return True
    if not _has_binding(package, key, parts[0]):
        return False
    hit = _resolve(package, key, parts[0])
    if len(parts) > 1 and hit:
        node = package.modules[hit[0]].defined.get(hit[1])
        if isinstance(node, ast.ClassDef) and not node.bases:
            members = ModuleFacts(path="<class>")
            _collect_statements(node.body, members)
            if "__getattr__" not in members.defined:
                return parts[1] in members.defined or parts[1] in members.imported
    return True  # external objects cannot be enumerated without importing them


def _global_reads(node: ast.AST) -> set[str]:
    tables = list(
        symtable.symtable(ast.unparse(node), "<definition>", "exec").get_children()
    )
    names: set[str] = set()
    while tables:
        table = tables.pop()
        names.update(
            s.get_name()
            for s in table.get_symbols()
            if s.is_global() and s.is_referenced()
        )
        tables.extend(table.get_children())
    return names


def _moved_lookup(old: ModuleFacts, package: Package, name: str) -> str | None:
    """A re-export preserves object identity, not the function's globals dictionary."""
    for public, before in old.defined.items():
        if not isinstance(
            before, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            continue
        hit = _resolve(package, package.key, public)
        if not hit or hit[0] == package.key:
            continue
        after = package.modules[hit[0]].defined.get(hit[1])
        if after and name in _global_reads(before) & _global_reads(after):
            return public
    return None


class ConsumerChecks(ast.NodeVisitor):
    def __init__(self, old, package, dotted, module, path, findings, logger_names):
        self.old, self.package, self.dotted = old, package, dotted
        self.module, self.path, self.findings = module, path, findings
        self.aliases: dict[str, str] = {}
        self.logger_names = logger_names

    def name(self, node: ast.AST) -> str:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name):
            return self.aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            parent = self.name(node.value)
            return parent + "." + node.attr if parent else ""
        if (
            isinstance(node, ast.Call)
            and self.name(node.func) == "logging.getLogger"
            and node.args
        ):
            if isinstance(node.args[0], ast.Constant) and isinstance(
                node.args[0].value, str
            ):
                return "@logger:" + node.args[0].value
        return ""

    def visit_Assign(self, node: ast.Assign | ast.AnnAssign) -> None:
        self.generic_visit(node)
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        value = (
            self.name(node.value)
            if node.value and not isinstance(node.value, ast.Constant)
            else ""
        )
        for target in targets:
            for name in _target_names(target):
                self.aliases[name] = value if isinstance(target, ast.Name) else ""

    visit_AnnAssign = visit_Assign

    def block(self, node: ast.AST, message: str) -> None:
        self.findings.block(f"{self.path}:{node.lineno}: {message}")

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.aliases[alias.asname or alias.name.split(".")[0]] = (
                alias.name if alias.asname else alias.name.split(".")[0]
            )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = _absolute_module(self.module, node.level, node.module)
        for alias in node.names:
            target = module + "." + alias.name
            if alias.name != "*" and not _target_exists(
                self.package, self.dotted, target
            ):
                self.block(node, f"调用方导入断裂: {target}")
            self.aliases[alias.asname or alias.name] = target

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        previous, self.aliases = self.aliases, self.aliases.copy()
        for arg in (
            *node.args.posonlyargs,
            *node.args.args,
            *node.args.kwonlyargs,
            *([node.args.vararg] if node.args.vararg else []),
            *([node.args.kwarg] if node.args.kwarg else []),
        ):
            self.aliases[arg.arg] = "monkeypatch" if arg.arg == "monkeypatch" else ""
        self.generic_visit(node)
        self.aliases = previous

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        previous, self.aliases = self.aliases, self.aliases.copy()
        self.generic_visit(node)
        self.aliases = previous

    def check_patch(self, node: ast.AST, target: str, reason: bool = False) -> None:
        label = "理由句失指" if reason else "补丁目标断裂"
        if not _target_exists(self.package, self.dotted, target):
            self.block(node, f"{label}: {target}")
        elif target.startswith(self.dotted + "."):
            name = target[len(self.dotted) + 1 :]
            if "." not in name and (
                moved := _moved_lookup(self.old, self.package, name)
            ):
                self.block(
                    node,
                    f"{label if reason else '补丁不再作用于搬走的定义'}: {target} → {moved}",
                )

    def visit_Call(self, node: ast.Call) -> None:
        function = self.name(node.func)
        target = ""
        if (
            function in {"setattr", "monkeypatch.setattr", "unittest.mock.patch.object"}
            and len(node.args) >= 2
        ):
            obj, attribute = node.args[:2]
            if self.name(obj).startswith("@logger:"):
                logger = self.name(obj)[len("@logger:") :]
                if (
                    logger == self.dotted
                    and logger not in self.logger_names
                    and self.logger_names
                    # Child records still traverse the parent's handlers and
                    # propagation settings, but never its emission methods.
                    and isinstance(attribute, ast.Constant)
                    and attribute.value
                    in {
                        "debug",
                        "info",
                        "warning",
                        "warn",
                        "error",
                        "exception",
                        "critical",
                        "fatal",
                        "log",
                        "_log",
                        "handle",
                        "makeRecord",
                    }
                ):
                    self.block(
                        node,
                        f"日志名称已迁移: {logger} → {', '.join(sorted(self.logger_names))}",
                    )
            elif (
                isinstance(attribute, ast.Constant)
                and isinstance(attribute.value, str)
                and (len(node.args) >= 3 or function == "unittest.mock.patch.object")
            ):
                target = self.name(obj) + "." + attribute.value
            elif isinstance(obj, ast.Constant) and isinstance(obj.value, str):
                target = obj.value
        elif function == "unittest.mock.patch" and node.args:
            target = self.name(node.args[0])
        if target:
            self.check_patch(node, target)
        self.generic_visit(node)


def _repository_python_files(repo: Path) -> list[str]:
    """Every tracked or new, unignored `.py` file in the repository."""
    proc = subprocess.run(
        [
            "git",
            "ls-files",
            "-z",
            "--cached",
            "--others",
            "--exclude-standard",
            "--",
            "*.py",
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return sorted(set(proc.stdout.split("\0")) - {""})


def check_consumers(
    repo: Path, old: ModuleFacts, package: Package, dotted: str, findings: Findings
) -> None:
    """Read tracked and new Python callers across the repo; never import their code."""
    logger_names: set[str] = set()
    # The table can be a whole package, so a module in it is named from the
    # package rather than from the module this check is about.
    owner = package.name or dotted
    for key, facts in package.modules.items():
        for node in facts.defined.values():
            if _scaffolding(node) and node.value.args:
                arg = node.value.args[0]
                if isinstance(arg, ast.Name) and arg.id == "__name__":
                    logger_names.add(owner + ("." + key if key else ""))
                elif isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    logger_names.add(arg.value)
    scanned = 0
    for relative in _repository_python_files(repo):
        path = repo / relative
        if not path.is_file():
            continue  # a tracked pre-split file was removed in the working tree
        source = path.read_text(encoding="utf-8")
        # Package-relative imports need not spell the package's name at all.
        if (
            not path.is_relative_to(package.root)
            and dotted.split(".")[-1] not in source
        ):
            continue
        try:
            tree = ast.parse(source, filename=relative)
        except SyntaxError as exc:
            raise ValueError(f"{relative}:{exc.lineno}: {exc.msg}") from exc
        root = repo / "backend" if relative.startswith("backend/") else repo
        module = _dotted_name(path, root)
        if path.name == "__init__.py":
            module += ".__init__"  # _absolute_module expects a file, not a package
        checker = ConsumerChecks(
            old, package, dotted, module, relative, findings, logger_names
        )
        checker.visit(tree)
        comments = [
            (tok.start[0], tok.string)
            for tok in tokenize.generate_tokens(io.StringIO(source).readline)
            if tok.type == tokenize.COMMENT
        ]
        strings = [
            (node.lineno, node.value)
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and "\n" not in node.value
        ]
        imports = [node for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        for line, text in comments + strings:
            if not re.search(
                r"\b(?:patch|patches|replace|replaces|mock)\b|替换|补丁", text
            ):
                continue
            for target in re.findall(
                r"(?<![\w.])" + re.escape(dotted) + r"(?:\.[A-Za-z_]\w*)*", text
            ):
                anchor = ast.Constant(value=text, lineno=line)
                if target != dotted:
                    checker.check_patch(anchor, target, reason=True)
                elif "this name on" in text:
                    for node in imports:
                        if node.lineno in (line, line + 1):
                            actual = _absolute_module(module, node.level, node.module)
                            if actual != target:
                                checker.block(
                                    anchor,
                                    f"理由句失指: {target}，导入实际来自 {actual}",
                                )
        scanned += 1
    findings.summary.append(
        f"仓内 Python 调用方（{dotted}）: 检查 {scanned} 个文件（含包外脚本）"
    )


# ---------------------------------------------------------------------------
# Names that moved between the files of one directory
# ---------------------------------------------------------------------------


def offered_names(facts: ModuleFacts | None) -> set[str]:
    """Every name `from <this module> import X` could bind.

    `defined` is what the file made itself; `imported` covers the name it only
    forwarded, which a caller reaching through the module saw just the same.
    Comparing two sides with this is what turns "the file lost a name" into a
    statement about callers instead of about the file's text.
    """
    if facts is None:
        return set()
    return set(facts.defined) | facts.imported


def changed_module_paths(repo_root: Path, base_ref: str, directory: str) -> list[str]:
    """Repo-relative `.py` paths under `directory` that differ from `base_ref`.

    An edited module and a deleted one both lost something, so both are listed;
    so is an untracked new file, which is where a move's destination shows up
    before anyone has committed it. `--no-renames` keeps a move reading as one
    deletion and one addition rather than as a rename this would have to undo.
    """

    def run(subcommand: str, *args: str) -> list[str]:
        proc = subprocess.run(
            ["git", subcommand, "-z", *args, "--", directory],
            cwd=repo_root,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise ValueError(f"git {subcommand} failed: {proc.stderr.strip()}")
        return proc.stdout.split("\0")

    found = run("diff", "--name-only", "--no-renames", base_ref) + run(
        "ls-files", "--others", "--exclude-standard"
    )
    return sorted({path for path in found if path and path.endswith(".py")})


def _containing_package(path: Path, root: Path) -> Path | None:
    """The outermost package directory `path` sits in, or None if it sits in none."""
    top: Path | None = None
    here = path.parent
    while here != root and (here / "__init__.py").is_file():
        top = here
        here = here.parent
    return top


def module_package(
    module_path: Path,
    root: Path,
    dotted: str,
    facts: ModuleFacts | None,
    cache: dict[Path, Package],
) -> Package:
    """`_target_exists`'s view of one module: the package it sits in, and where
    the module is inside it.

    The table is the whole package's, because that is what a caller's import
    walks down — `from app.foo import sub` and `from app.foo.sub import name`
    both need `app/foo/sub.py` in the table — and because a name this module
    re-exports has to be followed to the file it really comes from, which only
    the package's own reader can place. The price is that the walk has to be
    told which entry is the module under inspection: read from the top, it
    would take the module's own directory siblings for its submodules and pass
    a caller that imports a name the module no longer binds.
    """
    if facts is None:
        return Package(root=module_path.parent, modules={})
    package_dir = _containing_package(module_path, root)
    if package_dir is None:
        return Package(root=module_path.parent, modules={"": facts})
    package_name = _dotted_name(package_dir, root)
    if package_dir not in cache:
        cache[package_dir] = read_package(package_dir, package_name)
    inside = dotted[len(package_name) + 1 :] if package_name else dotted
    if inside not in cache[package_dir].modules:
        return Package(root=module_path.parent, modules={"": facts})
    return Package(
        root=module_path.parent,
        modules=cache[package_dir].modules,
        key=inside,
        name=package_name,
    )


class ModuleFileReads(ast.NodeVisitor):
    """Callers that locate a module's file instead of naming something in it.

    `Path(module.__file__).read_text()` ships or loads the module's source as
    one piece, and `Path(module.__file__).parent` finds the directory the
    module's resources sit beside. After `a/b.py` becomes `a/b/__init__.py`
    neither call fails: `__file__` is still a path that exists, and it now
    names the package's `__init__.py` — often nearly empty — or the directory
    `a/b` instead of `a`. Nothing in the export, signature or import checks
    sees either, because no name stopped resolving.

    Every read of the attribute is reported, not only `.read_text()`: the path
    itself is what changed meaning, and a caller doing `.parent` on it is as
    wrong as one reading the file. A *write* (`module.__file__ = ...`) is not a
    read and is left alone.

    `spec_from_file_location(name, location)` is caught through its location
    argument, which is checked as an ordinary read: the location, not the
    name, is what decides which file is loaded — the name only ends up on the
    spec's `name` — so a literal location, or no location at all, does not
    turn a split into a wrong read, and matching on the name alone would
    report a caller that still loads exactly what it meant to.

    The read is matched syntactically: a path carried through `getattr(mod,
    "__file__")`, `mod.__dict__`, a function parameter or `sys.modules` is
    outside what this sees, as with any other static check here.
    """

    def __init__(
        self,
        split: dict[str, str],
        path: str,
        module: str,
        findings: Findings,
    ) -> None:
        self.split = split
        self.path = path
        self.module = module
        self.findings = findings
        self.aliases: dict[str, str] = {}
        self.reported: set[int] = set()

    def resolved(self, node: ast.AST) -> str:
        """The dotted module an expression denotes, or "" when it names none."""
        if isinstance(node, ast.Name):
            return self.aliases.get(node.id, "")
        if isinstance(node, ast.Attribute):
            parent = self.resolved(node.value)
            return f"{parent}.{node.attr}" if parent else ""
        return ""

    def hit(self, node: ast.AST, dotted: str, detail: str) -> None:
        line = getattr(node, "lineno", 0)
        if line in self.reported:
            return
        self.reported.add(line)
        self.findings.block(
            f"{self.path}:{line}: 模块文件路径已改指: {dotted}（{detail}）"
        )

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            head = alias.name.split(".")[0]
            self.aliases[alias.asname or head] = alias.name if alias.asname else head

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = _absolute_module(self.module, node.level, node.module)
        for alias in node.names:
            if alias.name == "*":
                continue
            target = f"{module}.{alias.name}" if module else alias.name
            self.aliases[alias.asname or alias.name] = target

    def _alias_value(self, node: ast.AST) -> str:
        """What `x = ...` binds, when the value names a module this can follow.

        `x = importlib.import_module("a.b")` binds the module the string names,
        which is how a spec reader gets its argument without importing `a.b`
        at the top of the file.
        """
        if isinstance(node, ast.Call) and node.args:
            if self.resolved(node.func).rsplit(".", 1)[-1] == "import_module":
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    return first.value
        return self.resolved(node)

    def visit_Assign(self, node: ast.Assign | ast.AnnAssign) -> None:
        self.generic_visit(node)
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        value = (
            self._alias_value(node.value)
            if node.value and not isinstance(node.value, ast.Constant)
            else ""
        )
        for target in targets:
            for name in _target_names(target):
                self.aliases[name] = value if isinstance(target, ast.Name) else ""

    visit_AnnAssign = visit_Assign

    def _shadow(self, names: list[str]) -> dict[str, str]:
        """Bind `names` to something other than a module; returns the old map.

        A local of the same name is not the module: `from app import foo` next
        to `def read(foo): return foo.__file__` reads the argument. Missing
        that would fail a split on a caller the split cannot have broken.
        """
        previous = self.aliases
        self.aliases = self.aliases.copy()
        for name in names:
            self.aliases.pop(name, None)
        return previous

    @staticmethod
    def _argument_names(arguments: ast.arguments) -> list[str]:
        names = [
            argument.arg
            for argument in arguments.posonlyargs + arguments.args + arguments.kwonlyargs
        ]
        for argument in (arguments.vararg, arguments.kwarg):
            if argument is not None:
                names.append(argument.arg)
        return names

    @staticmethod
    def _defaults(arguments: ast.arguments) -> list[ast.AST]:
        return list(arguments.defaults) + [
            default for default in arguments.kw_defaults if default is not None
        ]

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        # Decorators, defaults and annotations are evaluated where the `def`
        # is, not in the function's own scope.
        for decorator in node.decorator_list:
            self.visit(decorator)
        for default in self._defaults(node.args):
            self.visit(default)
        for argument in node.args.posonlyargs + node.args.args + node.args.kwonlyargs:
            if argument.annotation is not None:
                self.visit(argument.annotation)
        if node.returns is not None:
            self.visit(node.returns)
        previous = self._shadow(self._argument_names(node.args))
        for statement in node.body:
            self.visit(statement)
        self.aliases = previous

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node: ast.Lambda) -> None:
        for default in self._defaults(node.args):
            self.visit(default)
        previous = self._shadow(self._argument_names(node.args))
        self.visit(node.body)
        self.aliases = previous

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        previous, self.aliases = self.aliases, self.aliases.copy()
        self.generic_visit(node)
        self.aliases = previous

    # `for`, `with` and `except` do not open a scope of their own: a binding
    # made in the body stays bound afterwards, and an import there is visible
    # below. So these shadow the target and do not put the whole map back.
    # The target itself is not restored either: a name a statement bound is
    # not the module again. When such a statement sits under an `if` and does
    # not run, the module is still bound and a read of it is missed — the
    # direction this check errs in, since a false report fails a split that
    # broke nothing, while a miss leaves the reader as it was before.
    def visit_For(self, node: ast.For | ast.AsyncFor) -> None:
        # The target is bound to an item, and the loop body may bind it to
        # something else again — an import inside the loop, say. Neither is
        # the module it may have been before, so the old binding is not put
        # back: a loop over an empty iterable leaves the module bound, and
        # that read is then missed rather than guessed at. Everything else
        # bound in the body stays bound, as Python leaves it.
        self.visit(node.iter)
        self._shadow(_target_names(node.target))
        for statement in node.body + node.orelse:
            self.visit(statement)

    visit_AsyncFor = visit_For

    def visit_With(self, node: ast.With | ast.AsyncWith) -> None:
        for item in node.items:
            self.visit(item.context_expr)
            if item.optional_vars is not None:
                # The target holds whatever the manager returned, always.
                self._shadow(_target_names(item.optional_vars))
        for statement in node.body:
            self.visit(statement)

    visit_AsyncWith = visit_With

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.type is not None:
            self.visit(node.type)
        if node.name:
            # Python deletes the exception name when the handler ends.
            self._shadow([node.name])
        for statement in node.body:
            self.visit(statement)

    def _visit_comprehension(self, generators: list[ast.comprehension]) -> None:
        """Bind each generator's target before the expressions it guards."""
        for generator in generators:
            self.visit(generator.iter)
            self._shadow(_target_names(generator.target))
            for condition in generator.ifs:
                self.visit(condition)

    def visit_ListComp(self, node: ast.ListComp) -> None:
        previous = self.aliases
        self._visit_comprehension(node.generators)
        self.visit(node.elt)
        self.aliases = previous

    visit_SetComp = visit_ListComp
    visit_GeneratorExp = visit_ListComp

    def visit_DictComp(self, node: ast.DictComp) -> None:
        previous = self.aliases
        self._visit_comprehension(node.generators)
        self.visit(node.key)
        self.visit(node.value)
        self.aliases = previous

    def visit_Attribute(self, node: ast.Attribute) -> None:
        # A write (`module.__file__ = ...`) sets the attribute and still works
        # after the split; only a read sees the new path.
        if not isinstance(node.ctx, ast.Load):
            self.generic_visit(node)
            return
        dotted = self.resolved(node.value)
        detail = ""
        if node.attr == "__file__":
            detail = "__file__ 现在指向 __init__.py"
        elif node.attr == "origin" and dotted.endswith(".__spec__"):
            # `mod.__spec__.origin` is the same answer as `mod.__file__`, and
            # it is what a `spec_from_file_location` call is given as its
            # location — the name argument only labels the spec.
            dotted = dotted[: -len(".__spec__")]
            detail = "__spec__.origin 现在是包的 __init__.py"
        if detail and dotted in self.split:
            self.hit(node, dotted, detail)
        self.generic_visit(node)


def check_split_readers(
    repo: Path, split: dict[str, str], findings: Findings
) -> None:
    """Scan every repository caller for a whole-file read of a now-package module.

    The reader did not change and cannot have: the split is what turned its
    `__file__` or its spec name into a different file. So this scans the whole
    repository rather than the files the change touched, and it is the one
    check here whose finding is about the split side, not about a name.
    """
    leaves = {dotted.rsplit(".", 1)[-1] for dotted in split}
    scanned = 0
    found = 0
    for relative in _repository_python_files(repo):
        path = repo / relative
        if not path.is_file():
            continue  # a tracked pre-split file was removed in the working tree
        source = path.read_text(encoding="utf-8")
        # Cheap sieve: a caller that names the module (or aliases it) and asks
        # for the file. Relative imports need not spell the package at all, so
        # the leaf name is what is looked for.
        if not any(leaf in source for leaf in leaves):
            continue
        try:
            tree = ast.parse(source, filename=relative)
        except SyntaxError as exc:
            raise ValueError(f"{relative}:{exc.lineno}: {exc.msg}") from exc
        root = repo / "backend" if relative.startswith("backend/") else repo
        module = _dotted_name(path, root)
        if path.name == "__init__.py":
            module += ".__init__"  # _absolute_module expects a file, not a package
        before = len(findings.blockers)
        ModuleFileReads(split, relative, module, findings).visit(tree)
        found += len(findings.blockers) - before
        scanned += 1
    findings.summary.append(
        f"整份读取: 拆分 {len(split)} 个模块, 检查 {scanned} 个文件, 命中 {found} 处"
    )


def check_moves(
    repo_root: Path, directory: Path, base_ref: str, findings: Findings
) -> None:
    """Names every changed file under `directory` gave up, and who still wants them.

    The consumer check does the deciding, module by module: it is the same
    question as for a split — is the name a caller imports still bound where
    the caller imports it from — and the same answer set, so a name that left
    for a sibling, a name deleted outright and a name moved to another package
    are all reported the same way, with the destination named where one file
    under `directory` took it.
    """
    before: dict[str, ModuleFacts | None] = {}
    after: dict[str, ModuleFacts | None] = {}
    where: dict[str, Path] = {}
    roots: dict[str, Path] = {}
    packages: dict[Path, Package] = {}
    squashed: dict[str, str] = {}  # module -> the package's __init__.py path
    for relative in changed_module_paths(repo_root, base_ref, str(directory)):
        on_disk = repo_root / relative
        init = repo_root / relative[: -len(".py")] / "__init__.py"
        if not on_disk.is_file() and init.is_file():
            # A package stands where the file did: that is the facade check's
            # question, and a second answer here would read as a second break.
            # The one thing that is this check's question is the caller that
            # reads the file whole, because there is no name to go missing.
            root = _import_root(on_disk.parent)
            try:
                squashed[_dotted_name(on_disk, root)] = str(init)
            except ValueError:
                pass
            continue
        # Per file, not per directory: `--check-moves backend` has to name the
        # same modules `app.foo` that `--check-moves backend/app` does, or a
        # caller's import matches nothing and the run passes over the break.
        root = _import_root(on_disk.parent)
        try:
            dotted = _dotted_name(on_disk, root)
        except ValueError:
            continue  # outside the import root: no caller can spell its name
        roots[dotted] = root
        old_source = show_in_ref(base_ref, relative, repo_root)
        new_source = (
            on_disk.read_text(encoding="utf-8") if on_disk.is_file() else None
        )
        before[dotted] = (
            read_source(old_source, f"{base_ref}:{relative}", dotted)
            if old_source is not None
            else None
        )
        after[dotted] = (
            read_source(new_source, relative, dotted)
            if new_source is not None
            else None
        )
        where[dotted] = on_disk

    lost = {d: offered_names(before[d]) - offered_names(after[d]) for d in before}
    gained = {d: offered_names(after[d]) - offered_names(before[d]) for d in before}
    moved = 0
    for dotted in sorted(before):
        for name in sorted(lost[dotted]):
            destination = next(
                (
                    other
                    for other in sorted(gained)
                    if other != dotted and name in gained[other]
                ),
                "",
            )
            if destination:
                moved += 1
                findings.note(f"符号搬移: {dotted}.{name} → {destination}")

    checked = 0
    for dotted in sorted(before):
        if not lost[dotted] or before[dotted] is None:
            continue
        facts = after[dotted]
        check_consumers(
            repo_root,
            before[dotted],
            module_package(
                where[dotted], roots[dotted], dotted, facts, packages
            ),
            dotted,
            findings,
        )
        checked += 1

    if squashed:
        check_split_readers(repo_root, squashed, findings)

    findings.summary.append(
        f"包内搬移: 对比 {len(before)} 个改动模块, 搬移 {moved} 个符号, "
        f"查调用方 {checked} 个模块"
    )


# ---------------------------------------------------------------------------
# Shadow copies: a second module under the same name
# ---------------------------------------------------------------------------

#: Where a copy of a module lands when someone parks one to work against it:
#: `<root>/<name>.py` and `<root>/<something>/<name>.py` are what a scratch
#: harness leaves behind. Deeper is another checkout's tree, not a copy. The
#: machine's own temp directory is read through `tempfile`, so a TMPDIR that
#: is not `/tmp` is looked in too rather than silently passed over.
SCRATCH_ROOTS = tuple(dict.fromkeys((Path("/tmp"), Path(tempfile.gettempdir()))))
SCRATCH_DEPTH = 2


def _shadow_in(directory: Path, name: str, depth: int) -> list[Path]:
    """`<name>.py` or a `__init__.py`-bearing `<name>/` under `directory`."""
    if depth < 1:
        return []
    found: list[Path] = []
    try:
        entries = sorted(directory.iterdir())
    except OSError:
        return []
    for entry in entries:
        if entry.name.startswith(".") or entry.name == "__pycache__":
            continue
        if entry.is_dir():
            if entry.name == name and (entry / "__init__.py").is_file():
                found.append(entry)
            elif not entry.is_symlink():
                found.extend(_shadow_in(entry, name, depth - 1))
        elif entry.suffix == ".py" and entry.stem == name:
            found.append(entry)
    return found


def shadow_beside(package_dir: Path) -> list[Path]:
    """The pre-split file someone put back where the package now stands.

    `<package>.py` beside `<package>/` is this module and no other — one
    directory cannot hold two — so the name is the whole question, and the
    check can run before anything is read.
    """
    beside = package_dir.with_suffix(".py")
    return [beside] if beside.is_file() else []


def _text_of(entry: Path) -> str | None:
    """The source of a `__init__.py`-bearing directory or of a file, if readable."""
    path = entry / "__init__.py" if entry.is_dir() else entry
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def package_texts(package_dir: Path) -> set[str]:
    """Every text the package holds, so a copy of any of them is recognisable.

    Empty files are left out. A package's blank `__init__.py` is the commonest
    file there is, and its text is no evidence at all: matched on it, any
    unrelated blank `/tmp/<name>.py` would refuse the run.
    """
    texts: set[str] = set()
    for path in sorted(package_dir.rglob("*.py")):
        text = _text_of(path)
        if text is not None and text.strip():
            texts.add(text)
    return texts


def shadow_copies(package_dir: Path, texts: set[str]) -> list[Path]:
    """Copies of this module parked under a scratch root, recognised by content.

    The name alone is not the question here: `/tmp/foo.py` is an ordinary name
    for an ordinary file, and a run that refused on it would refuse whenever
    anybody else had one — the self-test of this tool lost 62 of 68 cases to a
    single unrelated `/tmp/foo.py` left by another agent on 2026-10-16. What
    the check is about is the copy the change was worked against, so a
    candidate counts when its text is one of this module's own: the package's
    files, or the pre-split file it was split from.
    """
    found: list[Path] = []
    for root in SCRATCH_ROOTS:
        for candidate in _shadow_in(root, package_dir.name, SCRATCH_DEPTH):
            text = _text_of(candidate)
            if text is not None and text.strip() and text in texts:
                found.append(candidate)
    return found


def refuse_shadow(shadows: list[Path]) -> int:
    """Say which copy is in the way and refuse to compare two modules at once."""
    print(
        "cannot judge: duplicate module shadow in sys.path: "
        + ", ".join(str(path) for path in shadows)
        + " — the package is the copy that counts; delete the other and run again",
        file=sys.stderr,
    )
    return EXIT_CANNOT_JUDGE


# ---------------------------------------------------------------------------
# Runtime identity (opt-in)
# ---------------------------------------------------------------------------

#: A package whose `__init__` blocks would hang the identity check forever.
IDENTITY_TIMEOUT_SECONDS = 60

_IDENTITY_PROGRAM = textwrap.dedent(
    """
    import importlib, json, sys
    root, dotted, pairs = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])
    sys.path.insert(0, root)
    out = []
    try:
        package = importlib.import_module(dotted)
    except Exception as exc:
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}))
        raise SystemExit(0)
    for name, module_key, original in pairs:
        try:
            module = importlib.import_module(dotted + "." + module_key)
        except Exception as exc:
            out.append([name, False, f"submodule import failed: {type(exc).__name__}"])
            continue
        left, right = getattr(package, name, None), getattr(module, original, None)
        detail = "" if left is right else f"{left!r} is not {right!r}"
        out.append([name, left is right, detail])
    print(json.dumps({"results": out}))
    """
)


def dotted_name_of(package_dir: Path) -> tuple[str, Path] | None:
    """`(dotted module name, directory sys.path needs)`, or None if not a package."""
    if not (package_dir / "__init__.py").is_file():
        return None
    parts, current = [package_dir.name], package_dir
    while (current.parent / "__init__.py").is_file():
        current = current.parent
        parts.append(current.name)
    return ".".join(reversed(parts)), current.parent


def check_runtime_identity(package: Package, findings: Findings) -> None:
    resolved = dotted_name_of(package.root)
    if resolved is None:
        findings.note("运行时同一性: 包不能作为模块导入, 跳过")
        return
    dotted, sys_root = resolved

    pairs = [
        [name, hit[0], hit[1]]
        for name in sorted(facade_exports(package))
        if (hit := _resolve(package, "", name)) is not None and hit[0] != ""
    ]
    if not pairs:
        findings.note("运行时同一性: 门面没有来自子模块的再导出, 跳过")
        return

    try:
        proc = subprocess.run(
            [
                sys.executable,
                "-c",
                _IDENTITY_PROGRAM,
                str(sys_root),
                dotted,
                json.dumps(pairs),
            ],
            capture_output=True,
            text=True,
            cwd=str(sys_root),
            timeout=IDENTITY_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        # A package that blocks on import would otherwise hang the whole check,
        # with no verdict at all. It is a skip, like every other way this
        # opt-in step cannot answer.
        findings.note(f"运行时同一性: 包导入超过 {IDENTITY_TIMEOUT_SECONDS} 秒, 跳过")
        return
    line = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
    try:
        payload = json.loads(line)
    except ValueError:
        findings.note(
            "运行时同一性: 子进程没有给出可解析的结果, "
            f"跳过（退出码 {proc.returncode}）"
        )
        return
    if "error" in payload:
        findings.note(f"运行时同一性: 包不能导入, 跳过（{payload['error']}）")
        return
    broken = 0
    for name, ok, detail in payload["results"]:
        if not ok:
            broken += 1
            findings.block(
                f"再导出不是同一个对象: {dotted}.{name} 与子模块里的 {detail}"
            )
    findings.summary.append(
        f"运行时同一性: 校验 {len(payload['results'])} 个再导出, 不成立 {broken} 个"
    )


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


def repo_root_of(start: Path | None = None) -> Path | None:
    proc = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=str(start or Path.cwd()),
        capture_output=True,
        text=True,
    )
    return Path(proc.stdout.strip()) if proc.returncode == 0 else None


def locate(target: str, repo_root: Path) -> tuple[Path, str | None]:
    """`--check-facade` -> (package directory, where the old file was).

    The old path is inferred from where the package sits, not from the way the
    target was written, so the directory form and the dotted form agree. It is
    None when the package is outside the repository, where nothing can be
    inferred — the caller falls back to `--old-file`.
    """
    as_path = "/" in target or target.endswith(".py") or Path(target).exists()
    if as_path:
        candidate = Path(target)
        if not candidate.is_absolute():
            for base in (Path.cwd(), repo_root):
                if (base / candidate).exists():
                    candidate = base / candidate
                    break
        candidate = candidate.resolve()
        if candidate.is_dir():
            package_dir = candidate
        elif candidate.is_file() and candidate.name == "__init__.py":
            package_dir = candidate.parent
        else:
            raise ValueError(
                f"{target} is neither a package directory nor an __init__.py"
            )
        return package_dir, _path_within(package_dir, repo_root)

    try:
        spec = importlib.util.find_spec(target)
    except Exception as exc:
        # `find_spec` imports the parent packages, so anything the parent does
        # can come back out of here: ModuleNotFoundError for a name that is not
        # importable, whatever the parent raised otherwise.
        raise ValueError(
            f"{target} cannot be imported as a module ({type(exc).__name__}: {exc})"
        ) from None
    if spec is None:
        raise ValueError(f"{target} is not an importable package")
    locations = list(spec.submodule_search_locations or [])
    if locations:
        package_dir = Path(locations[0]).resolve()
    elif spec.origin and spec.origin.endswith("__init__.py"):
        package_dir = Path(spec.origin).resolve().parent
    else:
        raise ValueError(f"{target} is not an importable package")
    return package_dir, _path_within(package_dir, repo_root)


def _path_within(package_dir: Path, repo_root: Path) -> str | None:
    """The package's pre-split file, as a path inside the repository."""
    try:
        relative = package_dir.relative_to(repo_root.resolve())
    except ValueError:
        return None
    return f"{relative.as_posix()}.py"


def _module_name_of(old_path: str) -> str:
    """`a/b/c.py` -> `a.b.c`, as a path spells it.

    Used when no import root is known; `_dotted_name` is the one that matters,
    and this is its fallback.
    """
    if not old_path.endswith(".py"):
        return ""
    parts = [
        part for part in old_path[: -len(".py")].split("/") if part not in ("", ".")
    ]
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _import_root(package_dir: Path) -> Path:
    """The directory a dotted name for this package starts below.

    Python's own boundary: walk up while the parent holds an `__init__.py`.
    `backend/app/x/y` is `app.x.y`, not `backend.app.x.y` — and an absolute
    `from app.x.y.z import n` written inside the package is only recognisable as
    an internal import against the name Python itself would use.
    """
    top = package_dir.resolve()
    while (top.parent / "__init__.py").is_file():
        top = top.parent
    return top.parent


def _dotted_name(path: Path, root: Path) -> str:
    """`<root>/a/b/c.py` -> `a.b.c`; `__init__.py` names its directory."""
    parts = list(path.resolve().relative_to(root).parts)
    if not parts:
        return ""
    if parts[-1] == "__init__.py":
        parts.pop()
    else:
        parts[-1] = Path(parts[-1]).stem
    return ".".join(parts)


def show_in_ref(ref: str, path: str, repo_root: Path) -> str | None:
    proc = subprocess.run(
        ["git", "show", f"{ref}:{path}"],
        cwd=str(repo_root),
        capture_output=True,
    )
    if proc.returncode != 0:
        return None
    try:
        return proc.stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        # Not a difference: a file this cannot read is a file it cannot judge,
        # and exit 1 would read as "the split changed something".
        raise ValueError(f"{ref}:{path} is not UTF-8 ({exc})") from None


def report(findings: Findings, header: list[str]) -> int:
    """Print one run and turn it into an exit code. Nothing verdicts as a pass
    unless the checks that could not run have already returned their own code:
    reaching here with an empty finding list means they all ran and agreed."""
    print("== verify-module-split ==")
    for line in header:
        print(line)
    print("-" * 68)
    rendered = findings.render()
    if rendered:
        print(rendered)
    if findings.summary:
        print("-" * 68)
        for line in findings.summary:
            print(f"      {line}")
    print("-" * 68)
    if findings.blockers:
        print(f"结论: 发现 {len(findings.blockers)} 处差异")
        return EXIT_DIFFERENT
    print("结论: 通过")
    return EXIT_OK


def run_check(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo_root).resolve() if args.repo_root else repo_root_of()
    if repo_root is None:
        print(
            "cannot judge: not inside a git repository (see --repo-root)",
            file=sys.stderr,
        )
        return EXIT_CANNOT_JUDGE

    base_ref = args.base_ref or DEFAULT_BASE_REF
    try:
        package_dir, derived_old = locate(args.check_facade, repo_root)
    except ValueError as exc:
        print(f"cannot judge: {exc}", file=sys.stderr)
        return EXIT_CANNOT_JUDGE

    if shadows := shadow_beside(package_dir):
        return refuse_shadow(shadows)

    old_path = args.old_file or derived_old
    if old_path is None:
        print(
            f"cannot judge: {package_dir} is outside the repository {repo_root}, "
            "so the pre-split file cannot be found; name it with --old-file",
            file=sys.stderr,
        )
        return EXIT_CANNOT_JUDGE
    try:
        old_source = show_in_ref(base_ref, old_path, repo_root)
    except ValueError as exc:
        print(f"cannot judge: {exc}", file=sys.stderr)
        return EXIT_CANNOT_JUDGE
    if old_source is None:
        print(
            f"cannot judge: {base_ref} has no {old_path} "
            "(point --old-file at the pre-split file)",
            file=sys.stderr,
        )
        return EXIT_CANNOT_JUDGE
    if shadows := shadow_copies(
        package_dir,
        package_texts(package_dir)
        | ({old_source} if old_source.strip() else set()),
    ):
        return refuse_shadow(shadows)

    try:
        root = _import_root(package_dir)
        try:
            package_dir_name = _dotted_name(package_dir, root)
            old_module_name = _dotted_name(repo_root / old_path, root)
        except ValueError:
            # The package or its old file sits outside the import root: fall
            # back to the path, which names the two sides consistently even if
            # it is not the name an import statement would use.
            package_dir_name = _module_name_of(
                _path_within(package_dir, repo_root) or ""
            )
            old_module_name = _module_name_of(old_path)
        package = read_package(package_dir, package_dir_name)
        old = read_source(old_source, f"{base_ref}:{old_path}", old_module_name)
    except (ValueError, SyntaxError, UnicodeDecodeError) as exc:
        print(f"cannot judge: {exc}", file=sys.stderr)
        return EXIT_CANNOT_JUDGE

    findings = Findings()
    check_exports(old, package, findings)
    check_signatures(old, package, findings)
    check_deferred(old, package, findings)
    try:
        check_consumers(repo_root, old, package, package_dir_name, findings)
    except (OSError, ValueError, SyntaxError, subprocess.CalledProcessError) as exc:
        print(f"cannot judge: {exc}", file=sys.stderr)
        return EXIT_CANNOT_JUDGE
    if args.runtime_identity:
        check_runtime_identity(package, findings)

    return report(findings, [f"old : {old_path} @ {base_ref}", f"new : {package_dir}"])


def run_check_moves(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo_root).resolve() if args.repo_root else repo_root_of()
    if repo_root is None:
        print(
            "cannot judge: not inside a git repository (see --repo-root)",
            file=sys.stderr,
        )
        return EXIT_CANNOT_JUDGE

    base_ref = args.base_ref or DEFAULT_BASE_REF
    directory = Path(args.check_moves)
    if not directory.is_absolute():
        # Relative to where the run was typed first: `--check-moves app/x` from
        # inside the package is the same intent as the repo-relative spelling.
        for base in (Path.cwd(), repo_root):
            if (base / directory).is_dir():
                directory = base / directory
                break
    directory = directory.resolve()
    if not directory.is_dir():
        print(f"cannot judge: {directory} is not a directory", file=sys.stderr)
        return EXIT_CANNOT_JUDGE

    findings = Findings()
    try:
        check_moves(repo_root, directory, base_ref, findings)
    except (
        OSError,
        ValueError,
        SyntaxError,
        UnicodeDecodeError,
        subprocess.CalledProcessError,
    ) as exc:
        print(f"cannot judge: {exc}", file=sys.stderr)
        return EXIT_CANNOT_JUDGE

    return report(findings, [f"dir : {directory}", f"base : {base_ref}"])


def run_self_test(argv: list[str]) -> int:
    here = Path(__file__).resolve().parent
    test_file = here / "test-verify-module-split.py"
    if not test_file.is_file():
        print(
            f"cannot judge: no self-test beside this script at {test_file}",
            file=sys.stderr,
        )
        return EXIT_CANNOT_JUDGE
    spec = importlib.util.spec_from_file_location(
        "_verify_module_split_self_test", test_file
    )
    if spec is None or spec.loader is None:
        print(f"cannot judge: {test_file} cannot be loaded", file=sys.stderr)
        return EXIT_CANNOT_JUDGE
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return int(module.main(argv))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="verify-module-split.py",
        description=(
            "Compare a module with the package it was split into, or a directory "
            "of modules with its base-ref self: exported names, signatures, "
            "deferred imports, names that moved to a sibling."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--check-facade",
        metavar="PACKAGE",
        help="the new package: a directory, its __init__.py, or a dotted module",
    )
    parser.add_argument(
        "--check-moves",
        metavar="DIR",
        help=(
            "a directory of modules: report names that left one of its files "
            "while a caller still imports them from the file they left"
        ),
    )
    parser.add_argument(
        "--base-ref",
        metavar="REF",
        default=None,
        help=f"the ref holding the pre-split file (default {DEFAULT_BASE_REF})",
    )
    parser.add_argument(
        "--old-file",
        metavar="PATH",
        help="the pre-split file's path in --base-ref (default: derived from PACKAGE)",
    )
    parser.add_argument(
        "--runtime-identity",
        action="store_true",
        help="also assert getattr(package, name) is getattr(submodule, name)",
    )
    parser.add_argument(
        "--self-test", action="store_true", help="run the offline self-test"
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="with --self-test, name each test as it runs",
    )
    parser.add_argument(
        "--repo-root", metavar="DIR", help="repository root (default: detected)"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    if args.self_test:
        # Only ever on its own: a run that reports the self-test green and
        # exits 0 is a green light for work that did not happen if a package
        # name was mistaken for this flag. The same goes for a mode: two modes
        # in one run would have to pick which answer the exit code carries.
        if args.check_facade or args.check_moves:
            parser.print_usage(sys.stderr)
            print(
                "cannot judge: --self-test runs the tests and checks nothing "
                "else; pass one or the other",
                file=sys.stderr,
            )
            return EXIT_CANNOT_JUDGE
        return run_self_test(["-v"] if args.verbose else [])
    if bool(args.check_facade) == bool(args.check_moves):
        parser.print_usage(sys.stderr)
        print(
            "cannot judge: pass exactly one of --check-facade, --check-moves, "
            "or --self-test",
            file=sys.stderr,
        )
        return EXIT_CANNOT_JUDGE
    try:
        if args.check_moves:
            return run_check_moves(args)
        return run_check(args)
    except BrokenPipeError:  # pragma: no cover - piping into head
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return EXIT_CANNOT_JUDGE


if __name__ == "__main__":
    raise SystemExit(main())
