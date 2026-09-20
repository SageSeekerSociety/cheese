"""声明用的词汇表：哪些产品概念、一条声明长什么样、不是「有」的格子能填什么。

住在包门口而不是 ``matrix.py`` 里，是为了让依赖只有一个方向。每个骨架的
``behaviour.py`` 要用这几个类型写自己的声明，而 ``matrix.py`` 要 import 那三份
声明才能汇总——两边都放进 ``matrix.py`` 就是一个环。声明侧依赖词汇表，汇总侧依
赖词汇表和声明侧，没有回边。

``Difference`` 是**全平台唯一**的一份差异码，不是这张表自己的。
``backend/tests/fixtures/harness-contract/vocabulary.json`` 的 ``differences``
是同一份的散文版（那边还要给 TypeScript 那个 reader 读），两份由
``tests/contract/test_harness_contract.py`` 的守卫钉死在一起：往任何一边加一条
码，另一边不加就红。每条码属于哪条轴（下面那两份名单）也记在那份夹具里，同一条
守卫比对——轴只写在 Python 里的那一天，读同一份夹具的 TypeScript 那侧就会放跨轴
的格子过去。
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum


class BuiltIn(StrEnum):
    """平台自己有一套、而骨架也可能自带一套的产品概念。

    列是封闭的：新增一个骨架要把这几格都填掉（``matrix.matrix()`` 会红），
    新增一列要每个骨架都回答一次。
    """

    ASK = "提问"
    TODO = "待办"
    REMINDER = "提醒"
    AUTO_SYNC = "自动同步"


class Difference(StrEnum):
    """矩阵里凡不是「有」的那一格，只能填这里的一条码（不变量 I6）。

    前五条是事件契约那张表在用的（一个骨架报不出某个事件），后三条是本表在用的
    （平台关不掉某个自带实现）。同一个枚举，因为「说不清的那一格填什么」在几张
    矩阵上是同一个问题——分成两份的那一天，两份就会开始漂。一份枚举不等于一格可
    以随便填哪条：哪条码属于哪条轴，见下面的 ``BUILT_IN_DIFFERENCES`` 与
    ``EVENT_DIFFERENCES``。
    """

    NO_TOOL_FAILURE_SIGNAL = "no-tool-failure-signal"
    NO_CALL_ID_ON_TOOL_USE = "no-call-id-on-tool-use"
    NO_SUBAGENT_THREADS = "no-subagent-threads"
    NO_SUBAGENT_TOOL_RETURN = "no-subagent-tool-return"
    NO_SESSION_ID_OF_ITS_OWN = "no-session-id-of-its-own"

    #: 骨架根本不自带这个概念，所以没有东西要关。只在「平台自己把这件事建起来
    #: 了」能作证的时候填它——平台替它装了一个 Stop hook 去做同步，就是这个骨架
    #: 没有同步的证据。
    NOT_BUILT_IN = "not-built-in"
    #: 自带，而平台今天给不出关闭动作。这就是结论 48 说的「暂缺」。
    NO_OFF_SWITCH = "no-off-switch"
    #: 还没有人对着钉住的那个 build 读过这一格。也是「暂缺」的一种，但它是一次
    #: 核查就能消掉的那一种，所以跟上一条分开记。
    NOT_CHECKED_AGAINST_THE_PIN = "not-checked-against-the-pin"


#: 「平台关不掉某个自带实现」那条轴上能填的码，也就是功能矩阵认的那三条。
BUILT_IN_DIFFERENCES = frozenset(
    {
        Difference.NOT_BUILT_IN,
        Difference.NO_OFF_SWITCH,
        Difference.NOT_CHECKED_AGAINST_THE_PIN,
    }
)

#: 「一个骨架报不出某个事件」那条轴上能填的码，也就是事件契约的场景认的那五条。
EVENT_DIFFERENCES = frozenset(
    {
        Difference.NO_TOOL_FAILURE_SIGNAL,
        Difference.NO_CALL_ID_ON_TOOL_USE,
        Difference.NO_SUBAGENT_THREADS,
        Difference.NO_SUBAGENT_TOOL_RETURN,
        Difference.NO_SESSION_ID_OF_ITS_OWN,
    }
)
# 两份都是逐条写出来的，不是一份减另一份：写成补集，往 ``Difference`` 里加一条
# 码就会悄悄落到另一条轴上，而那条轴的校验从此放它过去。两份必须正好切开整个
# 枚举（守卫在 ``tests/unit/test_capability_matrix.py``），并且跟夹具里记的轴
# 一致（守卫在 ``tests/contract/test_harness_contract.py``）。
#
# 一条码只属于一条轴：跨轴填的那一格（「待办」这一格填「报不出会话 id」）是一句
# 胡话，而胡话跟一条真的差异码在表上长得一模一样，所以两侧各自只认自己那一份。


@dataclass(frozen=True, slots=True)
class Declaration:
    """一个骨架的行为声明，表上版本（结论 48）。

    ``pinned_version`` 引用适配层那一个常量，**不写第二遍字面量**；
    ``verified_against`` 才是人手写的那个版本号——它记的是「这份声明是对着哪个
    build 读出来的」。两者不等就是 CI 红：升级 pin 而没有重新读过一遍声明，红
    的地方正好在这里。
    """

    pinned_version: str
    #: 这个骨架已经查实自带的那些产品概念。
    built_ins: frozenset[BuiltIn]
    #: 每个概念一格：填一句「怎么关的」（那就是「有」），或者一条差异码。
    #: 一格都不能漏，漏了 ``matrix.matrix()`` 会红。
    how_disabled: Mapping[BuiltIn, str | Difference]
    verified_against: str
