import ast
from pathlib import Path

import pytest


APP_ROOT = Path(__file__).resolve().parents[2] / "app"
_KNOWN = {
    ("domain/room_cleanup/retire.py", "_sweep_once"),
    ("domain/room_task/closing.py", "close_overdue"),
    ("domain/delivery/agent.py", "dispatch_pending"),
    ("domain/routine/service/settlement.py", "_settle_open_runs"),
    ("domain/routine/service/settlement.py", "_announce_finished"),
    ("domain/review/pr_publish.py", "retarget_completed_dependencies"),
}
_KNOWN_COUNTS = {
    ("domain/room_cleanup/retire.py", "_sweep_once"): 2,
    ("domain/room_task/closing.py", "close_overdue"): 1,
    ("domain/delivery/agent.py", "dispatch_pending"): 4,
    ("domain/routine/service/settlement.py", "_settle_open_runs"): 1,
    ("domain/routine/service/settlement.py", "_announce_finished"): 2,
    ("domain/review/pr_publish.py", "retarget_completed_dependencies"): 4,
}


class _LoopGetVisitor(ast.NodeVisitor):
    def __init__(self, path: str) -> None:
        self.path = path
        self.function_name = "<module>"
        self.loop_depth = 0
        self.reads: list[tuple[str, str, int, str]] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        old_name, old_depth = self.function_name, self.loop_depth
        self.function_name, self.loop_depth = f"<class {node.name}>", 0
        for statement in node.body:
            self.visit(statement)
        self.function_name, self.loop_depth = old_name, old_depth

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        old_name, old_depth = self.function_name, self.loop_depth
        self.function_name, self.loop_depth = node.name, 0
        for statement in node.body:
            self.visit(statement)
        self.function_name, self.loop_depth = old_name, old_depth

    def visit_For(self, node: ast.For) -> None:
        self._visit_loop(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self._visit_loop(node)

    def _visit_loop(self, node: ast.For | ast.AsyncFor) -> None:
        self.visit(node.iter)
        self.visit(node.target)
        self.loop_depth += 1
        for statement in node.body:
            self.visit(statement)
        self.loop_depth -= 1
        for statement in node.orelse:
            self.visit(statement)

    def visit_Call(self, node: ast.Call) -> None:
        function = node.func
        if (
            self.loop_depth
            and isinstance(function, ast.Attribute)
            and function.attr == "get"
            and isinstance(function.value, ast.Name)
            and function.value.id in {"session", "db"}
        ):
            self.reads.append(
                (self.path, self.function_name, node.lineno, ast.unparse(node))
            )
        self.generic_visit(node)


def _find_row_reads(app_root: Path) -> list[tuple[str, str, int, str]]:
    reads = []
    for path in sorted(app_root.rglob("*.py")):
        relative_path = path.relative_to(app_root).as_posix()
        visitor = _LoopGetVisitor(relative_path)
        visitor.visit(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        reads.extend(visitor.reads)
    return reads


def _assert_no_unknown_reads(
    app_root: Path,
    known: set[tuple[str, str]] | frozenset[tuple[str, str]],
    known_counts: dict[tuple[str, str], int],
) -> None:
    reads = _find_row_reads(app_root)
    actual = {(path, function) for path, function, _line, _expression in reads}
    unexpected = actual - known
    counts: dict[tuple[str, str], int] = {}
    for path, function, _line, _expression in reads:
        key = (path, function)
        counts[key] = counts.get(key, 0) + 1
    unexpected.update(
        key
        for key in known_counts.keys() | counts.keys()
        if counts.get(key, 0) != known_counts.get(key, 0)
    )
    if unexpected:
        details = "\n".join(
            f"{path}:{line}: {expression} (in {function})"
            for path, function, line, expression in reads
            if (path, function) in unexpected
        )
        raise AssertionError(f"Unexpected ORM primary-key reads in loops:\n{details}")


def test_no_row_reads_in_loops() -> None:
    _assert_no_unknown_reads(APP_ROOT, _KNOWN, _KNOWN_COUNTS)


def test_loop_primary_key_reads_are_reported_and_can_be_baselined(
    tmp_path: Path,
) -> None:
    app_root = tmp_path / "app"
    app_root.mkdir()
    (app_root / "sample.py").write_text(
        "async def process(rows, session):\n"
        "    for row in rows:\n"
        "        await session.get(Topic, row.id)\n",
        encoding="utf-8",
    )

    with pytest.raises(
        AssertionError,
        match=r"sample\.py:3: session\.get\(Topic, row\.id\)",
    ):
        _assert_no_unknown_reads(app_root, set(), {})

    key = ("sample.py", "process")
    _assert_no_unknown_reads(app_root, {key}, {key: 1})

    (app_root / "sample.py").write_text(
        "async def process(rows, session):\n"
        "    for row in rows:\n"
        "        await session.get(Topic, row.id)\n"
        "        await session.get(Topic, row.parent_id)\n",
        encoding="utf-8",
    )
    with pytest.raises(
        AssertionError,
        match=r"sample\.py:4: session\.get\(Topic, row\.parent_id\)",
    ):
        _assert_no_unknown_reads(app_root, {key}, {key: 1})
