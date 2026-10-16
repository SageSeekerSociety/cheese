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


def _findings(tree: ast.Module) -> list[tuple[int, str]]:
    findings: list[tuple[int, str]] = []

    # Walking the whole module to collect functions also collects class methods
    # as if they were module-level functions.
    for node in ast.walk(tree):
        if not isinstance(node, (ast.For, ast.AsyncFor)):
            continue
        if not isinstance(node.iter, ast.Call):
            continue
        if not isinstance(node.iter.func, ast.Attribute) or node.iter.func.attr != "walk":
            continue
        if not node.iter.args or not isinstance(node.iter.args[0], ast.Name):
            continue
        if any(
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Name)
            and child.func.id == "isinstance"
            and any(
                isinstance(arg, ast.Attribute)
                and isinstance(arg.value, ast.Name)
                and arg.value.id == "ast"
                and arg.attr in {"FunctionDef", "AsyncFunctionDef"}
                for arg in child.args[1:]
            )
            for child in ast.walk(node)
        ):
            findings.append((node.lineno, "recursive-class-methods"))
            break

    for node in ast.walk(tree):
        if not isinstance(node, ast.Assert):
            continue
        if any(
            isinstance(child, ast.BinOp)
            and isinstance(child.op, ast.Sub)
            and _is_set_of_dir(child.left)
            and _is_set_of_dir(child.right)
            for child in ast.walk(node.test)
        ):
            findings.append((node.lineno, "dir-difference"))
            break

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
