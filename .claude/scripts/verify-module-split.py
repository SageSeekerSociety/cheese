#!/usr/bin/env python3
"""Compare a module against the package it was split into.

    .claude/scripts/verify-module-split.py \\
        --check-facade backend/app/domain/review/polling --base-ref origin/main
    .claude/scripts/verify-module-split.py \\
        --check-facade app.domain.review.polling --base-ref origin/main
    .claude/scripts/verify-module-split.py --self-test

    0 = nothing to report        1 = a difference a split introduced
    2 = cannot judge (not a package, the old file is not in the base ref, a
        file that does not parse) — never a pass

WHY. Turning `a/foo.py` into `a/foo/__init__.py` plus submodules is mechanical
until it isn't, and the three ways it goes wrong are all invisible to every
check the repo already runs:

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
in the note line so a reader can see what was set aside. That is the boundary
to know: a caller doing `from <old module> import X` where the old module had
merely imported `X` is not caught here. Two more names are set aside, each for
a reason that would otherwise red-light every split in this repository:
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

Only the standard library is used: no network, no third-party import, no write.
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import os
import subprocess
import sys
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
    for alias in node.names:
        if alias.name == "*":
            if node.module:
                facts.star_imports.append(internal or node.module)
            continue
        name = alias.asname or alias.name
        facts.imported.add(name)
        if relative:
            facts.relative_imports[name] = (node.module, alias.name)
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
    facts = ModuleFacts(path=path)
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
        modules[key] = read_source(
            path.read_text(encoding="utf-8"), str(path), inside, module_name
        )
    if "" not in modules:
        raise ValueError(f"{package_dir} has no __init__.py, so it is not a package")
    return Package(root=package_dir, modules=modules)


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
    return name in star_facts.defined or name in star_facts.imported


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
            + " — 不是它导出的东西, 未参与比对"
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
    if args.runtime_identity:
        check_runtime_identity(package, findings)

    print("== verify-module-split ==")
    print(f"old : {old_path} @ {base_ref}")
    print(f"new : {package_dir}")
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
            "Compare a module with the package it was split into: exported names, "
            "signatures, deferred imports."
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
        # Only ever on its own: a run that reports 13 passing tests and exits 0
        # is a green light for work that did not happen if a package name was
        # mistaken for this flag.
        if args.check_facade:
            parser.print_usage(sys.stderr)
            print(
                "cannot judge: --self-test runs the tests and checks no package; "
                "pass one or the other",
                file=sys.stderr,
            )
            return EXIT_CANNOT_JUDGE
        return run_self_test(["-v"] if args.verbose else [])
    if not args.check_facade:
        parser.print_usage(sys.stderr)
        print("cannot judge: pass --check-facade, or --self-test", file=sys.stderr)
        return EXIT_CANNOT_JUDGE
    try:
        return run_check(args)
    except BrokenPipeError:  # pragma: no cover - piping into head
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return EXIT_CANNOT_JUDGE


if __name__ == "__main__":
    raise SystemExit(main())
