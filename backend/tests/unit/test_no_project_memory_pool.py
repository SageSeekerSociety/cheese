"""Shared project facts belong to documents; the retired scope cannot be used.

Historical migrations name the old database value directly. Runtime code and
tests must use the remaining instance-owned pools.
"""

import ast
import pathlib

from app.domain.memory.models import MemoryScope

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]

SCANNED = "backend/app"

RETIRED = "project"


def _sources() -> list[tuple[pathlib.Path, str]]:
    root = REPO_ROOT / SCANNED
    assert root.is_dir(), f"扫描根已经不存在了：{SCANNED}"
    return [
        (p, p.relative_to(REPO_ROOT).as_posix()) for p in sorted(root.rglob("*.py"))
    ]


def _reads_the_retired_pool(tree: ast.AST) -> list[int]:
    """Find attribute accesses to the retired scope."""
    return sorted(
        {
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and node.attr == RETIRED
            and isinstance(node.value, ast.Name)
            and node.value.id == "MemoryScope"
        }
    )


def test_the_project_pool_is_not_a_memory_scope() -> None:
    assert RETIRED not in {member.value for member in MemoryScope}


def test_no_product_code_reads_or_writes_the_project_pool() -> None:
    offenders: list[str] = []
    for path, rel in _sources():
        for lineno in _reads_the_retired_pool(ast.parse(path.read_text())):
            offenders.append(f"{rel}:{lineno}")

    assert not offenders, (
        "这些地方还在读写项目记忆池：\n  "
        + "\n  ".join(offenders)
        + "\n\n没有项目记忆池（结论 7）。所有人都该看见的事实写进项目总览的实况"
        "文档（`cheese_remember` 带 `everyone`，落到根房间的 doc 块）；一个实例自己"
        "学到的写进它自己的池（`memory_pool`）。"
    )
