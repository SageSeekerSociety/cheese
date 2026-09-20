"""架构守卫：行为声明表上版本，功能矩阵没有空格，能力位有真读者。

结论 48 的三条判据，一条一条落在这里：

1. **表上版本。** 每个骨架的适配层只有一个 pin，声明引用它；``verified_against``
   是人手写的那一个——升级 pin 而没有重新读过声明，这里红。
2. **矩阵没有空格**（不变量 I6）。一格填不出「怎么关的」，就得填一条
   ``Difference``；``matrix()`` 自己就拒绝画出一张有空格的表。
3. **能力位有真读者**（不变量 I7）。一个能力位在它的实现目录之外零读者，就是一
   个没人消费的洞；两个以上读点而不共用一个判定函数，就是两个地方各自解释同一
   个布尔。
"""

import ast
import functools
from dataclasses import fields
from pathlib import Path

import pytest

from app.domain.agent.capability import (
    BUILT_IN_DIFFERENCES,
    EVENT_DIFFERENCES,
    BuiltIn,
    Declaration,
    Difference,
)
from app.domain.agent.capability import matrix as matrix_module
from app.domain.agent.capability.matrix import MatrixIncomplete, declarations, matrix
from app.domain.agent.harness import CLAUDE_CODE, CODEX, HARNESSES, Harness
from app.domain.agent.harness.claude_code.remote_execution import bootstrap
from app.domain.agent.harness.claude_code.remote_execution import client as execution

BACKEND = Path(__file__).resolve().parents[2]
APP = BACKEND / "app"
HARNESS_PACKAGE = APP / "domain/agent/harness"


# --- 1. 表上版本 -------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(HARNESSES))
def test_a_declaration_was_verified_against_the_version_that_is_pinned(
    name: str,
) -> None:
    """升级 pin 就要重新读一遍行为声明，不然红在这里。

    这不是「改个数字」的提醒：新 build 里那几格还成不成立，只有读过才知道，而
    ``verified_against`` 就是那次阅读留下的唯一痕迹。
    """
    declared = declarations()[name]
    assert declared.verified_against == declared.pinned_version, (
        f"{name} 的 pin 是 {declared.pinned_version}，"
        f"而行为声明上次是对着 {declared.verified_against} 读的。"
        "去重新读一遍它自带什么、平台还关不关得掉，再改 VERIFIED_AGAINST。"
    )


def test_every_harness_this_deployment_runs_has_a_declaration() -> None:
    """注册表里多一个骨架而没有行为声明，是一个没人写过行为说明就上线的骨架。"""
    assert set(declarations()) == set(HARNESSES)


def test_every_copy_of_a_pin_is_held_to_the_one_the_adapter_declares() -> None:
    """有三份复制品，每一份都有 import 不到适配层的理由，也都写在自己那一行上。

    复制品不是第二个答案——这条守卫才是把它们钉在同一个值上的东西。任意一处先
    动，这里红。
    """
    pins = {name: d.pinned_version for name, d in declarations().items()}
    # 机器上单独跑的两个脚本：一个由平台 exec 出一段字符串，一个作为松散文件送上
    # 机器，两个都不在包里，import 不到 device_launch。
    assert execution.PINNED_VERSION == pins[CLAUDE_CODE]
    assert bootstrap.VERSION == pins[CLAUDE_CODE]
    # 装机脚本：引用适配层的常量会把整个 codex 包连着 ORM 一起拖进来。
    installer = ast.parse(
        (BACKEND / "scripts/install_codex_session_host.py").read_text()
    )
    declared = next(
        ast.literal_eval(node.value)
        for node in installer.body
        if isinstance(node, ast.Assign)
        and any(getattr(t, "id", "") == "VERSION" for t in node.targets)
    )
    assert declared == pins[CODEX]


def test_no_second_literal_of_a_pin_hides_in_the_harness_packages() -> None:
    """版本号在哪几个文件里出现过，是一张白名单，而白名单是棘轮。

    多一个文件写下同一个字符串就红：pin 被抄第二遍的那一刻，「升级要改几处」
    这件事就已经没有人知道了。还清一处（改成引用常量）要回来删掉那一行。
    """
    pins = {d.pinned_version for d in declarations().values()}
    allowed = {
        HARNESS_PACKAGE / "claude_code/device_launch.py",
        HARNESS_PACKAGE / "claude_code/remote_execution/client.py",
        HARNESS_PACKAGE / "claude_code/remote_execution/bootstrap.py",
        HARNESS_PACKAGE / "codex/host.py",
        HARNESS_PACKAGE / "pi/device_launch.py",
        HARNESS_PACKAGE / "claude_code/behaviour.py",
        HARNESS_PACKAGE / "codex/behaviour.py",
        HARNESS_PACKAGE / "pi/behaviour.py",
    }
    stray = [
        f"{path.relative_to(APP)}: {pin}"
        for path in sorted(HARNESS_PACKAGE.rglob("*.py"))
        if path not in allowed
        for pin in pins
        if f'"{pin}"' in path.read_text()
    ]
    assert not stray, "版本号在适配层里被抄了第二遍：\n  " + "\n  ".join(stray)


# --- 2. 矩阵没有空格（I6） ---------------------------------------------------


def test_the_matrix_has_a_filled_cell_for_every_harness_and_concept() -> None:
    table = matrix()
    assert set(table) == set(HARNESSES)
    for name, row in table.items():
        assert set(row) == set(BuiltIn), name
        for concept, cell in row.items():
            assert isinstance(cell, Difference) or cell.strip(), f"{name}/{concept}"


def test_a_cell_left_empty_is_refused_rather_than_drawn(monkeypatch) -> None:
    """空格的危险不是画不出来，是画得出来——一张有空格的表读起来跟填满的一样。"""
    blank = Declaration(
        pinned_version="9.9.9",
        built_ins=frozenset({BuiltIn.ASK}),
        how_disabled={BuiltIn.ASK: "   "},
        verified_against="9.9.9",
    )
    monkeypatch.setitem(matrix_module._DECLARED, CLAUDE_CODE, lambda: blank)
    with pytest.raises(MatrixIncomplete):
        matrix()


def test_every_difference_code_belongs_to_exactly_one_axis() -> None:
    """一份枚举，两条轴，每条码正好归一条。

    没有这条守卫，往 ``Difference`` 里加一条码就会落在谁也没认领的地方：两侧的
    校验各自不认它，而它在表上跟一条真的码长得一模一样。
    """
    assert BUILT_IN_DIFFERENCES | EVENT_DIFFERENCES == set(Difference)
    assert not BUILT_IN_DIFFERENCES & EVENT_DIFFERENCES


def test_a_code_from_the_other_axis_is_not_an_answer(monkeypatch) -> None:
    """「待办这一格：报不出会话 id」——跨轴的胡话，也得红。

    合并成一份码之后这是唯一挡得住它的地方：它是一条真的 ``Difference``，所以
    「要么有、要么一条码」这句话本身已经拦不住它了。
    """
    nonsense = Declaration(
        pinned_version="9.9.9",
        built_ins=frozenset(),
        how_disabled=dict.fromkeys(BuiltIn, Difference.NO_SESSION_ID_OF_ITS_OWN),
        verified_against="9.9.9",
    )
    monkeypatch.setitem(matrix_module._DECLARED, CLAUDE_CODE, lambda: nonsense)
    with pytest.raises(MatrixIncomplete):
        matrix()


def test_a_cell_that_denies_what_the_declaration_claims_is_refused(monkeypatch) -> None:
    """自相矛盾的两个方向都查：写了关闭动作却说不自带，和自带却填「不自带」。"""
    contradictory = Declaration(
        pinned_version="9.9.9",
        built_ins=frozenset({BuiltIn.TODO}),
        how_disabled=dict.fromkeys(BuiltIn, Difference.NOT_BUILT_IN),
        verified_against="9.9.9",
    )
    monkeypatch.setitem(matrix_module._DECLARED, CLAUDE_CODE, lambda: contradictory)
    with pytest.raises(MatrixIncomplete):
        matrix()


def test_a_concept_left_unanswered_is_refused_too(monkeypatch) -> None:
    """漏掉一列和填一个空格是同一件事：这个骨架没有回答过这个概念。"""
    silent = Declaration(
        pinned_version="9.9.9",
        built_ins=frozenset(),
        how_disabled={BuiltIn.ASK: Difference.NOT_BUILT_IN},
        verified_against="9.9.9",
    )
    monkeypatch.setitem(matrix_module._DECLARED, CLAUDE_CODE, lambda: silent)
    with pytest.raises(MatrixIncomplete):
        matrix()


# --- 3. 能力位有真读者（I7） -------------------------------------------------


def _capability_bits() -> list[str]:
    """``Harness`` 上的能力位：布尔的那几个字段。名字和标签不是能力位。"""
    return [f.name for f in fields(Harness) if f.type in (bool, "bool")]


@functools.cache
def _readers(bit: str) -> dict[str, list[str]]:
    """实现目录之外，读这个能力位的地方：函数全名 → 出现处。"""
    found: dict[str, list[str]] = {}
    for path in sorted(APP.rglob("*.py")):
        if path.is_relative_to(HARNESS_PACKAGE):
            continue
        tree = ast.parse(path.read_text())
        scope: list[str] = []

        def walk(node: ast.AST, scope: list[str] = scope, path: Path = path) -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    scope.append(child.name)
                    walk(child)
                    scope.pop()
                    continue
                if isinstance(child, ast.Attribute) and child.attr == bit:
                    where = f"{path.relative_to(APP)}::{'.'.join(scope) or '<module>'}"
                    found.setdefault(where, []).append(f"line {child.lineno}")
                walk(child)

        walk(tree)
    return found


@pytest.mark.parametrize("bit", _capability_bits())
def test_a_capability_bit_has_at_least_one_reader_upstream(bit: str) -> None:
    """没人读的能力位是一个洞的反面：一个谁也不消费的声明。

    ``carries_subscription`` 曾经的唯一读者是一个下拉菜单。读者没了而位还在，
    就该进 ``Difference``，而不是留在结构体上装作还在用。
    """
    assert _readers(bit), (
        f"{bit} 在 harness 实现目录之外一个读者都没有。"
        "要么它有真的上游读者，要么它进 Difference。"
    )


@pytest.mark.parametrize("bit", _capability_bits())
def test_all_readers_of_a_capability_bit_read_the_same_function(bit: str) -> None:
    """两处各自解释同一个布尔，就是两处会各自漂。"""
    where = _readers(bit)
    assert len(where) == 1, (
        f"{bit} 被 {len(where)} 个函数各读了一次：{sorted(where)}。"
        "让它们读同一个判定函数。"
    )
