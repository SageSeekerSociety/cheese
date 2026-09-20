"""三份行为声明，汇总成一张没有空格的功能矩阵。

结论 48：每个骨架的适配层带一份行为声明——它自带哪些产品概念、每一项平台怎么
关掉、这份声明对着哪个钉住的版本验证过。本模块是那件事的汇总侧：把三份声明并
成一张表，并且**在生成的时候就不让任何一格是空的**（不变量 I6）。

一格只有三种可能：一句「怎么关的」（那就是「有」），或者 ``BUILT_IN_DIFFERENCES``
里的一条码；那份名单是封闭的，所以「这一格我说不清」这句话本身也得选一个已经存
在的说法，而不是随手写一行散文，也不能借用另一条轴上的码来冒充一个回答。这是结论 48 的第三条判据，也是这张表跟一张 README
里的表格唯一的区别。

**钉住的版本号只写一处。** 每个骨架的适配层各一个常量，声明引用它；
``backend/scripts/test_harness_contracts.py`` 装二进制的时候问的也是这里，而不
是自己去 ``ast`` 解一个文件或者把版本号再抄一遍。

**这张表只覆盖骨架。** 地点与托管方的两张随 P22、P30 各自交付，形状照抄这里。
"""

from app.domain.agent.capability import (
    BUILT_IN_DIFFERENCES,
    BuiltIn,
    Declaration,
    Difference,
)
from app.domain.agent.harness import CLAUDE_CODE, CODEX, HARNESSES, PI
from app.domain.agent.harness.claude_code import declaration as claude_code_declaration
from app.domain.agent.harness.codex import declaration as codex_declaration
from app.domain.agent.harness.pi import declaration as pi_declaration

#: 每个骨架的声明函数。三个骨架三个调用者——注册表里多一个骨架而这里没有它，
#: ``declarations()`` 就红，所以这份名单不会悄悄落后于 ``HARNESSES``。
_DECLARED = {
    CLAUDE_CODE: claude_code_declaration,
    CODEX: codex_declaration,
    PI: pi_declaration,
}


class MatrixIncomplete(RuntimeError):
    """矩阵里有一格没人填。不是「渲染不出来」，是「这件事没有人回答过」。"""


def declarations() -> dict[str, Declaration]:
    """每个这套部署真的会跑的骨架，和它的行为声明。

    注册表是权威：``HARNESSES`` 里有而这里没有声明的骨架，是一个没人写过行为说
    明就上了线的骨架，所以它红在这里而不是红在某一格上。
    """
    missing = sorted(set(HARNESSES) - set(_DECLARED))
    if missing:
        raise MatrixIncomplete(f"这些骨架没有行为声明：{missing}")
    return {name: _DECLARED[name]() for name in HARNESSES}


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
                # 码是全平台一份，轴不是：事件契约那侧的码填进这张表是一句跨轴
                # 的胡话，而胡话在表上跟一条真的差异码长得一模一样。
                if cell not in BUILT_IN_DIFFERENCES:
                    raise MatrixIncomplete(
                        f"{name} 的「{concept}」填了 {cell.value}，那是事件契约"
                        "那条轴上的码。这张表只认 "
                        f"{sorted(code.value for code in BUILT_IN_DIFFERENCES)}。"
                    )
                if cell is Difference.NOT_BUILT_IN and concept in declared.built_ins:
                    raise MatrixIncomplete(
                        f"{name} 说它自带「{concept}」，这一格却填了「不自带」。"
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
