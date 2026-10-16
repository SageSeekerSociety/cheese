#!/usr/bin/env python3
"""Reject two AST patterns that silently weaken split/seam tests."""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path


HINTS = {
    "recursive-class-methods": "请只遍历 tree.body 顶层节点，不要递归收集 ClassDef 内的方法",
    "dir-difference": "请用 cls.__dict__ 检查子类自身定义，代替 set(dir(Child)) - set(dir(Parent))",
}


def _is_ast_type(node: ast.AST, names: set[str]) -> bool:
    if isinstance(node, (ast.Tuple, ast.List)):
        return any(_is_ast_type(element, names) for element in node.elts)
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "ast"
        and node.attr in names
    )


def _isinstance_of_function(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "isinstance"
        and len(node.args) >= 2
        and _is_ast_type(node.args[1], {"FunctionDef", "AsyncFunctionDef"})
    )


def _is_walk_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "walk"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "ast"
        and bool(node.args)
    )


def _target_names(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, (ast.Tuple, ast.List)):
        return set().union(*(_target_names(element) for element in node.elts))
    return set()


def _is_dir_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "dir"
    )


def _is_set_of_dir(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "set"
        and len(node.args) == 1
        and _is_dir_call(node.args[0])
    )


def _is_dir_difference(node: ast.AST) -> bool:
    if (
        isinstance(node, ast.BinOp)
        and isinstance(node.op, ast.Sub)
        and _is_set_of_dir(node.left)
        and _is_set_of_dir(node.right)
    ):
        return True
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "difference"
        and _is_set_of_dir(node.func.value)
        and len(node.args) == 1
        and _is_set_of_dir(node.args[0])
    )


def _findings(tree: ast.Module) -> list[tuple[int, str]]:
    findings: list[tuple[int, str]] = []

    # Walking a module to collect functions also collects class methods as if
    # they were module-level functions. Handle loops and comprehensions alike.
    for node in ast.walk(tree):
        if isinstance(node, (ast.For, ast.AsyncFor)) and _is_walk_call(node.iter):
            names = _target_names(node.target)
            if names and any(
                _isinstance_of_function(child)
                for child in ast.walk(node)
                if child is not node
            ):
                findings.append((node.lineno, "recursive-class-methods"))
        elif isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            for generator in node.generators:
                if not _is_walk_call(generator.iter):
                    continue
                names = _target_names(generator.target)
                value_expressions = [node.key, node.value] if isinstance(node, ast.DictComp) else [node.elt]
                expressions = [*generator.ifs, *value_expressions]
                if names and any(
                    _isinstance_of_function(child)
                    for expression in expressions
                    for child in ast.walk(expression)
                ):
                    findings.append((node.lineno, "recursive-class-methods"))

    assigned_differences: dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and _is_dir_difference(node.value):
            for target in node.targets:
                assigned_differences.update((name, node.lineno) for name in _target_names(target))
        elif isinstance(node, ast.AnnAssign) and node.value and _is_dir_difference(node.value):
            assigned_differences.update((name, node.lineno) for name in _target_names(node.target))

    for node in ast.walk(tree):
        if not isinstance(node, ast.Assert):
            continue
        inline_difference = any(_is_dir_difference(child) for child in ast.walk(node.test))
        assigned_name = next(
            (
                child.id
                for child in ast.walk(node.test)
                if isinstance(child, ast.Name) and child.id in assigned_differences
            ),
            None,
        )
        if inline_difference or assigned_name:
            line = node.lineno
            if not inline_difference:
                line = min(assigned_differences[child.id] for child in ast.walk(node.test) if isinstance(child, ast.Name) and child.id in assigned_differences)
            findings.append((line, "dir-difference"))

    return findings


def _test_files(root: Path) -> list[Path]:
    unit = root / "backend" / "tests" / "unit"
    if not unit.is_dir():
        return []
    return sorted(
        path
        for path in unit.glob("test_*.py")
        if ("_split" in path.name or "_seam" in path.name)
    )


def check(root: Path) -> int:
    errors = 0
    for path in _test_files(root):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError, UnicodeError) as exc:
            print(f"{path}:{getattr(exc, 'lineno', 1)}: 无法解析测试文件: {exc}", file=sys.stderr)
            errors += 1
            continue
        for line, kind in _findings(tree):
            print(f"{path}:{line}: {HINTS[kind]}", file=sys.stderr)
            errors += 1
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    errors = check(args.root.resolve())
    if errors:
        print(f"接缝测试守卫发现 {errors} 个问题", file=sys.stderr)
        return 1
    print("接缝测试守卫通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
