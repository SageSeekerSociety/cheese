"""删掉的死定义不许回来。

下面这些名字的能力早已换成别的写法，旧定义却在仓库里零引用地挂了很久：没有检查会
数「这个名字全仓还出现几次」，ruff 也没有「定义了却没人调用」的规则（ARG001–005
管的是未用参数）。它们一并删掉后，由这里按语法树数后端全部 ``.py`` 里的定义
（``def`` / ``async def`` / ``class``）和引用（名字、属性、``import``），两样都必须
是 0。

要把其中某个能力接回来，先删掉这里对应的那一行，再写新的。
"""

from __future__ import annotations

import ast
import re
from collections import Counter
from pathlib import Path

#: 导入根：``backend/``。这个文件在 ``backend/tests/unit/`` 里。
BACKEND = Path(__file__).resolve().parents[2]

#: 名字 → 删掉前它所在的模块，只用于失败时说清来历。
DEAD = {
    "multipart_upload": "app.core.storage",
    "presign_parts": "app.core.storage",
    "complete_multipart": "app.core.storage",
    "abort_multipart": "app.core.storage",
    "has_domain_role": "app.auth.core",
    "is_parent_or_equal": "app.auth.core",
    "attach_cookie": "app.api.proxy",
    "forwardable_request_headers": "app.api.proxy",
}

#: 语法树里出现的名字在源文本里必然也出现：先按文本筛，只解析筛中的文件，整个
#: 后端逐份解析要十几秒。
_MENTION = re.compile(r"\b(?:" + "|".join(DEAD) + r")\b")

_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _sightings(tree: ast.AST) -> Counter[tuple[str, str]]:
    """``(名字, "def" | "ref")`` → 这棵树里出现的次数，只数 ``DEAD`` 里的名字。"""
    seen: Counter[tuple[str, str]] = Counter()
    for node in ast.walk(tree):
        if isinstance(node, _DEFS) and node.name in DEAD:
            seen[node.name, "def"] += 1
        elif isinstance(node, ast.Name) and node.id in DEAD:
            seen[node.id, "ref"] += 1
        elif isinstance(node, ast.Attribute) and node.attr in DEAD:
            seen[node.attr, "ref"] += 1
        elif isinstance(node, ast.alias):
            for part in (node.name.split(".")[-1], node.asname):
                if part in DEAD:
                    seen[part, "ref"] += 1
    return seen


def _sources() -> list[Path]:
    return [
        path
        for path in sorted(BACKEND.rglob("*.py"))
        if not any(
            part.startswith(".") or part in {"__pycache__", "node_modules"}
            for part in path.relative_to(BACKEND).parts
        )
    ]


def test_removed_definitions_have_no_definition_or_reference() -> None:
    found: list[str] = []
    for path in _sources():
        text = path.read_text(encoding="utf-8")
        if not _MENTION.search(text):
            continue
        tree = ast.parse(text, filename=str(path))
        for (name, kind), count in sorted(_sightings(tree).items()):
            found.append(
                f"{path.relative_to(BACKEND)}: {name} ({kind} ×{count}；"
                f"原在 {DEAD[name]}，已删)"
            )
    assert not found, "这些死定义又出现了：\n" + "\n".join(found)


def test_the_scan_sees_files_at_all() -> None:
    # 扫描根找错时上面那条会空转变绿；按名字认出一份确实在后端里的文件。
    assert BACKEND / "app" / "core" / "storage.py" in _sources()


def test_a_redefinition_or_a_call_is_counted() -> None:
    # 故意违规：同名定义、调用、属性访问、导入各算一次，上面那条就会红。
    seen = _sightings(
        ast.parse(
            "from app.api.proxy import attach_cookie as ac\n"
            "async def presign_parts(): ...\n"
            "class has_domain_role: ...\n"
            "store.abort_multipart('k')\n"
            "is_parent_or_equal(1, 2)\n"
        )
    )
    assert seen == {
        ("attach_cookie", "ref"): 1,
        ("presign_parts", "def"): 1,
        ("has_domain_role", "def"): 1,
        ("abort_multipart", "ref"): 1,
        ("is_parent_or_equal", "ref"): 1,
    }
