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
- **A signature gains or loses a parameter.** The name is still there, but a
  caller passing by keyword or a test patching the attribute fails, and no type
  check sees it because the function is still callable.
- **A deferred import appears.** `backend/scripts/check_deferred_imports.py`
  freezes function-body imports per file, which means a split resets the count
  for every file it creates: moving an import into a new module and adding one
  there is indistinguishable, from the baseline's side, from moving it alone.

WHAT IS COMPARED. The old side is the pre-split single file, read from git
(`git show <ref>:<path>`) so the working tree may already hold the package. The
new side is the package directory on disk. A file whose old path cannot be
derived gets `--old-file`.

A name the old module only *imported* is not required of the package. Every
module imports a few dozen names for its own use, and demanding they survive
would turn every run red. What the old module binds itself — `def`, `class`,
assignment, and anything named in `__all__` — is required; the rest is listed
in the note line so a reader can see what was set aside. That is the boundary
to know: a caller doing `from <old module> import X` where the old module had
merely imported `X` is not caught here.

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
    #: qualified name (`Widget.render`) -> signature
    signatures: dict[str, Signature] = field(default_factory=dict)
    deferred: list[DeferredImport] = field(default_factory=list)


def _signature(node: ast.AST, kind: str) -> Signature:
    args = node.args  # type: ignore[attr-defined]
    params = [a.arg for a in args.posonlyargs]
    params += [a.arg for a in args.args]
    if args.vararg is not None:
        params.append("*" + args.vararg.arg)
    elif args.kwonlyargs:
        # `def f(a, *, b)` and `def f(a, b)` must not compare equal: only the
        # first refuses a third positional argument.
        params.append("*")
    params += [a.arg for a in args.kwonlyargs]
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
    for node in ast.walk(test):
        if isinstance(node, ast.Name) and node.id == "TYPE_CHECKING":
            return True
        if isinstance(node, ast.Attribute) and node.attr == "TYPE_CHECKING":
            return True
    return False


def _collect_import_from(node: ast.ImportFrom, facts: ModuleFacts) -> None:
    if node.module == "__future__":
        return
    relative = bool(node.level) and bool(node.module)
    for alias in node.names:
        if alias.name == "*":
            if node.module:
                facts.star_imports.append(node.module)
            continue
        name = alias.asname or alias.name
        facts.imported.add(name)
        if relative:
            facts.relative_imports[name] = (node.module, alias.name)


def _collect_statements(body: list[ast.stmt], facts: ModuleFacts) -> None:
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
            _collect_import_from(node, facts)
        elif isinstance(node, ast.If):
            if _guarded_by_type_checking(node.test):
                continue
            _collect_statements(node.body, facts)
            _collect_statements(node.orelse, facts)
        elif isinstance(node, ast.Try):
            _collect_statements(node.body, facts)
            for handler in node.handlers:
                _collect_statements(handler.body, facts)
            _collect_statements(node.orelse, facts)
            _collect_statements(node.finalbody, facts)


def _literal_all(tree: ast.Module) -> list[str] | None:
    """`__all__` when it is a literal list or tuple of strings, else None."""
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
            return None
        names: list[str] = []
        for element in value.elts:
            if isinstance(element, ast.Constant) and isinstance(element.value, str):
                names.append(element.value)
            else:
                return None
        return names
    return None


def _deferred(source: str) -> list[DeferredImport]:
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
                found.append(DeferredImport(_deferred_target(child), child.lineno))
            walk(
                child,
                in_function
                or isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)),
            )

    walk(tree, False)
    return found


def _deferred_target(node: ast.Import | ast.ImportFrom) -> str:
    """What a deferred import depends on, with the binding's name left out.

    `import a as b` and `import a` are the same dependency; a comparison that
    noticed the alias would report a rename as a new import.
    """
    if isinstance(node, ast.Import):
        return "import " + ", ".join(alias.name for alias in node.names)
    module = "." * node.level + (node.module or "")
    return f"from {module} import " + ", ".join(alias.name for alias in node.names)


def read_source(source: str, path: str) -> ModuleFacts:
    facts = ModuleFacts(path=path)
    tree = ast.parse(source, filename=path)
    _collect_statements(tree.body, facts)
    facts.all_list = _literal_all(tree)
    facts.deferred = _deferred(source)
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


def read_package(package_dir: Path) -> Package:
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
        modules[key] = read_source(path.read_text(encoding="utf-8"), str(path))
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
        if name in star_facts.defined or name in star_facts.imported:
            return (star, name)
        hit = _resolve(package, star, name, seen)
        if hit is not None:
            return hit
    return None


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
        reached = set(star_facts.defined) | star_facts.imported
        names |= {n for n in reached | set(star_facts.all_list or []) if _public(n)}
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


def declared_public(facts: ModuleFacts) -> set[str]:
    """The names the old module offered: what it defined, plus `__all__`."""
    return {n for n in facts.defined if _public(n)} | {
        n for n in facts.all_list or [] if _public(n)
    }


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

    # A name is worth checking only when the facade declares it public: every
    # real facade imports `logging` or `dataclasses`, and reporting those as
    # "new public names" is how a check stops being read.
    if package.facade.all_list is not None:
        extra = sorted(set(package.facade.all_list) - declared - set(old.imported))
        for name in extra:
            findings.block(f"__all__ 声明了旧模块没有的公开名: {name}")

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
        if before.params != after.params:
            drifted += 1
            findings.block(
                f"形参变了: {key} 旧 ({', '.join(before.params)}) "
                f"→ 包 ({', '.join(after.params)})"
            )

    missing = sorted(set(old_signatures) - set(new_signatures))
    locations = signature_locations(package)
    for key in missing:
        where = locations.get(key)
        if where:
            findings.block(
                f"定义搬走但包顶层没暴露: {key} 现在只在 {', '.join(sorted(where))} 里"
            )
        else:
            findings.block(f"符号没了: {key} 整个包里都找不到")

    findings.summary.append(
        f"函数签名: 比对 {len(shared)} 个, 形参变了 {drifted} 个, "
        f"未暴露或丢失 {len(missing)} 个"
    )


def check_deferred(old: ModuleFacts, package: Package, findings: Findings) -> None:
    new_entries = [
        (key, item) for key, facts in package.modules.items() for item in facts.deferred
    ]
    old_targets = [item.target for item in old.deferred]
    new_targets = [item.target for _, item in new_entries]

    introduced = sorted(set(new_targets) - set(old_targets))
    for target in introduced:
        where = sorted(
            f"{key or '__init__.py'}:{item.line}"
            for key, item in new_entries
            if item.target == target
        )
        findings.block(f"多出函数内延迟 import: {target}（{', '.join(where)}）")

    if not introduced and len(new_targets) > len(old_targets):
        findings.block(
            f"函数内延迟 import 变多了: 旧 {len(old_targets)} 处 → "
            f"包 {len(new_targets)} 处, 依赖没变只是重复了"
        )

    findings.summary.append(
        f"函数内延迟 import: 旧 {len(old_targets)} 处 / 包 {len(new_targets)} 处"
    )


# ---------------------------------------------------------------------------
# Runtime identity (opt-in)
# ---------------------------------------------------------------------------

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
    )
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


def locate(target: str, repo_root: Path) -> tuple[Path, str]:
    """`--check-facade` -> (package directory, where the old file was)."""
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
        try:
            relative = package_dir.relative_to(repo_root.resolve())
        except ValueError:
            raise ValueError(
                f"{package_dir} is outside the repository {repo_root}; "
                "name the old file with --old-file"
            ) from None
        return package_dir, f"{relative.as_posix()}.py"

    spec = importlib.util.find_spec(target)
    locations = list(spec.submodule_search_locations or []) if spec else []
    if locations:
        return Path(locations[0]).resolve(), target.replace(".", "/") + ".py"
    if spec and spec.origin and spec.origin.endswith("__init__.py"):
        return Path(spec.origin).resolve().parent, target.replace(".", "/") + ".py"
    raise ValueError(f"{target} is not an importable package")


def show_in_ref(ref: str, path: str, repo_root: Path) -> str | None:
    proc = subprocess.run(
        ["git", "show", f"{ref}:{path}"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
    )
    return proc.stdout if proc.returncode == 0 else None


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
    old_source = show_in_ref(base_ref, old_path, repo_root)
    if old_source is None:
        print(
            f"cannot judge: {base_ref} has no {old_path} "
            "(point --old-file at the pre-split file)",
            file=sys.stderr,
        )
        return EXIT_CANNOT_JUDGE

    try:
        package = read_package(package_dir)
        old = read_source(old_source, f"{base_ref}:{old_path}")
    except (ValueError, SyntaxError) as exc:
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
        "--repo-root", metavar="DIR", help="repository root (default: detected)"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--self-test" in argv:
        return run_self_test([a for a in argv if a != "--self-test"])
    parser = build_parser()
    args = parser.parse_args(argv)
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
