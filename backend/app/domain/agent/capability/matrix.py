"""行为声明，汇总成一张没有空格的功能矩阵。

结论 48：每个骨架的适配层带一份行为声明——它自带哪些产品概念、每一项平台怎么
关掉、这份声明对着哪个钉住的版本验证过。本模块是那件事的汇总侧：把注册表里那些
骨架的声明并成一张表，并且**在生成的时候就不让任何一格是空的**（不变量 I6）。
一个留着代码而不注册的骨架（结论 43）在这张表上不占一列，它的声明仍然写着，见
``written()``。

一格只有两种可能：一句「怎么关的」（那就是「有」），或者 ``Difference`` 里的一
条码；那份名单是封闭的，所以「这一格我说不清」这句话本身也得选一个已经存在的说
法，而不是随手写一行散文。这是结论 48 的第三条判据，也是这张表跟一张 README 里
的表格唯一的区别。

**钉住的版本号只写一处。** 每个骨架的适配层各一个常量，声明引用它；
``backend/scripts/test_harness_contracts.py`` 装二进制的时候问的也是这里，而不
是自己去 ``ast`` 解一个文件或者把版本号再抄一遍。

**这张表只覆盖骨架。** 地点与托管方的两张随 P22、P30 各自交付，形状照抄这里。
"""

from app.domain.agent.capability import BuiltIn, Declaration, Difference
from app.domain.agent.harness import CLAUDE_CODE, CODEX, HARNESSES, PI
from app.domain.agent.harness.claude_code import declaration as claude_code_declaration
from app.domain.agent.harness.codex import declaration as codex_declaration
from app.domain.agent.harness.pi import declaration as pi_declaration

#: 每个有适配层的骨架的声明函数——包括今天不在注册表里的那些（结论 43：答不出四
#: 条硬性要求的骨架留着代码不注册）。注册表里多一个骨架而这里没有它，
#: ``declarations()`` 就红，所以这份名单不会悄悄落后于 ``HARNESSES``。
_DECLARED = {
    CLAUDE_CODE: claude_code_declaration,
    CODEX: codex_declaration,
    PI: pi_declaration,
}


class MatrixIncomplete(RuntimeError):
    """矩阵里有一格没人填。不是「渲染不出来」，是「这件事没有人回答过」。"""


def written() -> dict[str, Declaration]:
    """每一份写下来的行为声明，包括不在注册表里的那个骨架的。

    跟 ``declarations()`` 的分别就是「有代码」和「在跑」的分别：一个骨架可以留着
    适配层而不上注册表，而它的 pin 仍然归那条「版本号只写一处」的守卫管——那条守
    卫要是只看在跑的那些，摘掉一个骨架的同一天，它那几份复制品就没人管了。
    """
    return {name: declare() for name, declare in _DECLARED.items()}


def declarations() -> dict[str, Declaration]:
    """每个这套部署真的会跑的骨架，和它的行为声明。

    注册表是权威：``HARNESSES`` 里有而这里没有声明的骨架，是一个没人写过行为说
    明就上了线的骨架，所以它红在这里而不是红在某一格上。
    """
    missing = sorted(set(HARNESSES) - set(_DECLARED))
    if missing:
        raise MatrixIncomplete(f"这些骨架没有行为声明：{missing}")
    every = written()
    return {name: every[name] for name in HARNESSES}


def matrix() -> dict[str, dict[BuiltIn, str | Difference]]:
    """功能矩阵：骨架 × 产品概念，每一格是「怎么关的」或者一条差异码。

    校验就在生成里，不在生成之后：一张能画出来、只是有几格是空的表，会被当成一
    张已经填过的表读。
    """
    table: dict[str, dict[BuiltIn, str | Difference]] = {}
    for name, declared in declarations().items():
        row: dict[BuiltIn, str | Difference] = {}
        for concept in BuiltIn:
            cell = declared.how_disabled.get(concept)
            if isinstance(cell, Difference):
                if cell is Difference.NOT_BUILT_IN and concept in declared.built_ins:
                    raise MatrixIncomplete(
                        f"{name} 说它自带「{concept}」，这一格却填了「不自带」。"
                    )
                # 「自带、而且关不掉」的那一格，说的正是「自带」：``built_ins``
                # 里没有它，表上就同时立着两个都为真的答案——这一格说自带，那个
                # 字段说不自带。
                if (
                    cell is Difference.NO_OFF_SWITCH
                    and concept not in declared.built_ins
                ):
                    raise MatrixIncomplete(
                        f"{name} 的「{concept}」填了「关不掉」，"
                        "那就是自带，却没有写进 built_ins。"
                    )
                row[concept] = cell
                continue
            if not isinstance(cell, str) or not cell.strip():
                raise MatrixIncomplete(
                    f"{name} 的「{concept}」这一格是空的。"
                    "要么写清平台怎么关掉它，要么填一条 Difference 里的码。"
                )
            if concept not in declared.built_ins:
                raise MatrixIncomplete(
                    f"{name} 说它不自带「{concept}」，却又写了一个关闭动作。"
                )
            row[concept] = cell
        table[name] = row
    return table
