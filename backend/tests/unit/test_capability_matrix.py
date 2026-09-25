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

from app.core.config import Settings
from app.domain.agent.capability import BuiltIn, Declaration, Difference
from app.domain.agent.capability import matrix as matrix_module
from app.domain.agent.capability.matrix import (
    MatrixIncomplete,
    declarations,
    matrix,
    written,
)
from app.domain.agent.harness import CLAUDE_CODE, CODEX, HARNESSES, Harness
from app.domain.agent.harness.claude_code.remote_execution import bootstrap, private
from app.domain.agent.harness.claude_code.remote_execution import client as execution

BACKEND = Path(__file__).resolve().parents[2]
REPO = BACKEND.parent
APP = BACKEND / "app"
HARNESS_PACKAGE = APP / "domain/agent/harness"


# --- 1. 表上版本 -------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(written()))
def test_a_declaration_was_verified_against_the_version_that_is_pinned(
    name: str,
) -> None:
    """升级 pin 就要重新读一遍行为声明，不然红在这里。

    这不是「改个数字」的提醒：新 build 里那几格还成不成立，只有读过才知道，而
    ``verified_against`` 就是那次阅读留下的唯一痕迹。

    每一份写下来的声明，不只是在跑的那些：一个骨架可以留着适配层而不上注册表
    （结论 43），而它的 pin 一样会被人升级。
    """
    declared = written()[name]
    assert declared.verified_against == declared.pinned_version, (
        f"{name} 的 pin 是 {declared.pinned_version}，"
        f"而行为声明上次是对着 {declared.verified_against} 读的。"
        "去重新读一遍它自带什么、平台还关不关得掉，再改 VERIFIED_AGAINST。"
    )


def test_every_harness_this_deployment_runs_has_a_declaration() -> None:
    """注册表里多一个骨架而没有行为声明，是一个没人写过行为说明就上线的骨架。"""
    assert set(declarations()) == set(HARNESSES)


def test_every_copy_of_a_pin_is_held_to_the_one_the_adapter_declares() -> None:
    """每一份复制品，每一份都有 import 不到适配层的理由，也都写在自己那一行上。

    复制品不是第二个答案——这条守卫才是把它们钉在同一个值上的东西。任意一处先
    动，这里红。

    漏掉一份的代价不是一张表不好看：私聊执行器那几处对不上的时候，CI 全绿而机器
    上起不来——镜像里装的还是上一个 build，``remote_execution/client.py`` 的版本
    闸门当场拒掉那一轮；tag 那几处各自不同步，则是 ``docker run`` 找不到镜像。
    """
    pins = {name: d.pinned_version for name, d in written().items()}
    claude = pins[CLAUDE_CODE]
    # 机器上单独跑的两个脚本：一个由平台 exec 出一段字符串，一个作为松散文件送上
    # 机器，两个都不在包里，import 不到 device_launch。
    assert execution.PINNED_VERSION == claude
    assert bootstrap.VERSION == claude
    # 私聊执行器那一串：镜像里装的 claude 要过上面那道版本闸门，镜像的 tag 就是
    # 那个版本，而打 tag 的 CI、选镜像的配置默认值各写了一遍那个 tag。
    dockerfile = (BACKEND / "sandbox/Dockerfile.private").read_text()
    assert f"ARG CLAUDE_CODE_VERSION={claude}\n" in dockerfile
    assert private.IMAGE == f"cheese-private-executor:{claude}"
    assert Settings.model_fields["private_chat_executor_image"].default == private.IMAGE
    workflow = (REPO / ".github/workflows/remote-execution.yml").read_text()
    assert f"-t {private.IMAGE} " in workflow
    # The agent image bakes the same build a device launches, so "the same turn"
    # means the same runtime on either side; and the connector's delivery e2e in
    # CI boots the build production launches.
    sandbox = (BACKEND / "sandbox/Dockerfile").read_text()
    assert f"ARG CLAUDE_CODE_VERSION={claude}" in sandbox
    connector = (REPO / ".github/workflows/cli.yml").read_text()
    assert f"@anthropic-ai/claude-code@{claude}" in connector
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


def _string_literals(path: Path) -> list[str]:
    """这个文件里的字符串字面量，不含文档字符串与注释。

    散文里的一个版本号讲的是一件史实（「2.1.277 起 TaskOutput 不再服务」），
    升级 pin 也不该跟着改它；复制品则总是一个字面量——哪怕嵌在一句话中间，像
    ``"cheese-private-executor:2.1.277"`` 那样。
    """
    tree = ast.parse(path.read_text())
    prose = {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
    }
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in prose
    ]


def test_no_second_literal_of_a_pin_hides_in_the_harness_packages() -> None:
    """版本号在哪几个文件里出现过，是一张白名单，而白名单是棘轮。

    多一个文件写下同一个字符串就红：pin 被抄第二遍的那一刻，「升级要改几处」
    这件事就已经没有人知道了。还清一处（改成引用常量）而忘了回来删掉那一行，也
    红：一条陈旧的豁免会一声不响地把那个文件重新对复制品开放，而白名单读起来跟
    没还过一样。两个方向都断言，名单才是一张清单而不是一道单向棘轮。

    找的是裸版本号，不是 ``"2.1.277"`` 这样带引号的一整个字面量：嵌在字符串中间
    的那一份（``private.py`` 的 ``IMAGE``）在带引号的匹配下天然隐身，而它恰恰是
    一份升级时要跟着改的复制品。
    """
    pins = {d.pinned_version for d in written().values()}
    allowed = {
        HARNESS_PACKAGE / "claude_code/device_launch.py",
        HARNESS_PACKAGE / "claude_code/remote_execution/client.py",
        HARNESS_PACKAGE / "claude_code/remote_execution/bootstrap.py",
        HARNESS_PACKAGE / "claude_code/remote_execution/private.py",
        HARNESS_PACKAGE / "codex/host.py",
        HARNESS_PACKAGE / "pi/device_launch.py",
        HARNESS_PACKAGE / "claude_code/behaviour.py",
        HARNESS_PACKAGE / "codex/behaviour.py",
        HARNESS_PACKAGE / "pi/behaviour.py",
    }

    def writes_a_pin(path: Path) -> bool:
        return any(pin in literal for literal in _string_literals(path) for pin in pins)

    stray = [
        f"{path.relative_to(APP)}: {pin}"
        for path in sorted(HARNESS_PACKAGE.rglob("*.py"))
        if path not in allowed
        for pin in pins
        if any(pin in literal for literal in _string_literals(path))
    ]
    assert not stray, "版本号在适配层里被抄了第二遍：\n  " + "\n  ".join(stray)
    settled = sorted(
        str(path.relative_to(APP)) for path in allowed if not writes_a_pin(path)
    )
    assert not settled, (
        "这几处已经不写版本号字面量了，豁免却还留着：\n  "
        + "\n  ".join(settled)
        + "\n把它们从 allowed 里删掉。"
    )


# --- 2. 矩阵没有空格（I6） ---------------------------------------------------


def test_the_matrix_has_a_filled_cell_for_every_harness_and_concept() -> None:
    table = matrix()
    assert set(table) == set(HARNESSES)
    for name, row in table.items():
        assert set(row) == set(BuiltIn), name
        for concept, cell in row.items():
            assert isinstance(cell, Difference) or cell.strip(), f"{name}/{concept}"


def test_every_difference_code_is_one_some_declaration_fills_in() -> None:
    """没有人填的差异码，是一句替谁也没读过的一格印好的答案。

    这条守卫同时是结论 43 的那一条：四条硬性要求
    （``harness.SubagentRequirement``）在这张表上没有格子——答不出的骨架不在注册
    表里——所以一条「本骨架不支持子 agent」的码进来之后，没有任何一份声明用得上
    它，红在这里。名单封闭的意义就在这儿：一条填不进任何一格的码，是给一件本来
    不该发生的事先备好的说法。
    """
    used = {
        cell
        for declared in written().values()
        for cell in declared.how_disabled.values()
        if isinstance(cell, Difference)
    }
    orphans = sorted(set(Difference) - used)
    assert not orphans, f"这几条差异码没有任何一份声明在用：{orphans}"


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


def test_a_cell_that_claims_more_than_the_declaration_does_is_refused(
    monkeypatch,
) -> None:
    """A disable action requires the concept to be declared as built in."""
    overclaiming = Declaration(
        pinned_version="9.9.9",
        built_ins=frozenset(),
        how_disabled=dict.fromkeys(BuiltIn, "disabled through permissions"),
        verified_against="9.9.9",
    )
    monkeypatch.setitem(matrix_module._DECLARED, CLAUDE_CODE, lambda: overclaiming)
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
    """实现目录之外，读这个能力位的地方：读点的全名 → 出现处。

    类也压进这个全名，所以 key 是「文件::类.方法」。只压函数的话，同一个文件里
    两个类各有一个同名方法、各自解释同一个布尔，就折叠成一个 key——而这正是下面
    那条守卫要抓的东西。
    """
    found: dict[str, list[str]] = {}
    for path in sorted(APP.rglob("*.py")):
        if path.is_relative_to(HARNESS_PACKAGE):
            continue
        tree = ast.parse(path.read_text())
        scope: list[str] = []

        def walk(node: ast.AST, scope: list[str] = scope, path: Path = path) -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(
                    child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
                ):
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
