"""「没有分母就没有比率」和「摘要怎么截」各只写一份。

``feature_stats`` 的两个功能页都要算比率，``docs_site`` 的搜索结果和问芝士
工具都要截摘要。规则各自抄过两份，改一份不会改另一份；这里钉住两件事：
用到它的模块拿到的是同一个对象，包里也没有谁再定义一个自己的。
"""

import ast
from pathlib import Path

import pytest

from app.domain.docs_site import excerpt as excerpt_module
from app.domain.docs_site import library, tools
from app.domain.docs_site.excerpt import excerpt
from app.domain.feature_stats import stats
from app.domain.feature_stats.features import docs_assistant, task_naming

DOMAIN = Path(__file__).resolve().parents[2] / "app" / "domain"

# 规则的唯一住处，和不许在别处再定义的函数名（带不带下划线都算）。
HOMES = {
    "feature_stats": (DOMAIN / "feature_stats" / "stats.py", {"ratio", "_ratio"}),
    "docs_site": (DOMAIN / "docs_site" / "excerpt.py", {"excerpt", "_excerpt"}),
}


def test_every_caller_holds_the_same_ratio():
    assert docs_assistant.stats.ratio is stats.ratio
    assert task_naming.ratio is stats.ratio


def test_every_caller_holds_the_same_excerpt():
    assert library.excerpt is excerpt_module.excerpt
    assert tools.excerpt is excerpt_module.excerpt


@pytest.mark.parametrize("package", sorted(HOMES))
def test_no_module_writes_its_own_copy(package):
    home, names = HOMES[package]
    copies = []
    for path in sorted((DOMAIN / package).rglob("*.py")):
        if path == home:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            defines = isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            if defines and node.name in names:
                copies.append(f"{path.relative_to(DOMAIN)}:{node.lineno} {node.name}")
    assert not copies, f"从 {home.relative_to(DOMAIN)} 导入，别再写一份：{copies}"


@pytest.mark.parametrize(
    ("numerator", "denominator", "expected"),
    [(None, 5, None), (1, None, None), (1, 0, None), (1, 4, 0.25)],
)
def test_ratio_has_no_value_without_a_base(numerator, denominator, expected):
    assert stats.ratio(numerator, denominator) == expected


def test_excerpt_keeps_text_exactly_at_the_limit():
    assert excerpt("abcde", 5) == "abcde"


def test_excerpt_cuts_longer_text_and_marks_the_cut():
    assert excerpt("abcd efgh", 5) == "abcd…"


def test_excerpt_collapses_whitespace_before_measuring():
    assert excerpt("  a\n\n b\tc  ", 5) == "a b c"
