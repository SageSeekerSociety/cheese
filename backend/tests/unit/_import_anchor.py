"""守卫测试共用的相对导入折算。

守卫测试要判一句 ``import`` 落在哪个模块上，才判得了那是不是门面；相对写法得按被
扫那份文件自己的 ``__package__`` 折算——``.`` 落在 ``__package__`` 上，``..`` 落到
它的父包，依此类推。

**别从 ``__name__`` 推。** 包的 ``__init__.py`` 上 ``__name__`` 和 ``__package__``
是同一个（``pkg/sub/__init__.py`` 两者都是 ``pkg.sub``），别的模块上 ``__name__``
比 ``__package__`` 长一级。拿 ``__name__`` 去掉最后一段当锚点，子包的
``__init__.py`` 就多折了一层：那里的 ``from .. import pkg`` 本该落到 ``pkg``，会落
到它的父包上。同一个错法能被抄进好几处而没人发现，才收口到这里。
"""

from __future__ import annotations

import ast
from pathlib import Path

#: 导入根：``backend/``。这个文件在 ``backend/tests/unit/`` 里。
BACKEND = Path(__file__).resolve().parents[2]


def package_of(path: Path) -> str | None:
    """这份文件的 ``__package__``——相对导入按它算，不按 ``__name__``。

    ``pkg/leaf.py`` 和 ``pkg/__init__.py`` 的 ``__package__`` 是同一个 ``pkg``，就是
    路径的目录部分；两类文件在这里落在同一个答案上。不在导入根底下、或者是根上的
    顶层模块（``__package__`` 是空串）时返回 ``None``。
    """
    try:
        relative = path.resolve().relative_to(BACKEND)
    except ValueError:
        return None
    return ".".join(relative.with_suffix("").parts[:-1]) or None


def package_at(package: str, level: int) -> str | None:
    """``level`` 个点开头的相对导入，从 ``package`` 往上折 ``level-1`` 级落在哪个包上。

    ``.`` 一级落在 ``package`` 自己身上，``..`` 两级落到它的父包。折出顶层之外时
    导入机器抛 ``ImportError``，这里返回 ``None``，让判据把那句当成看不见的。
    """
    if not package:
        return None
    parts = package.split(".")
    if level - 1 >= len(parts):
        return None
    return ".".join(parts[: len(parts) - (level - 1)])


def absolute_module(node: ast.ImportFrom, package: str | None) -> str | None:
    """一句 ``from … import`` 落在哪个模块上；相对的按 ``package`` 折算。"""
    if not node.level:
        return node.module
    if package is None:
        return None
    base = package_at(package, node.level)
    if base is None:
        return None
    return f"{base}.{node.module}" if node.module else base
