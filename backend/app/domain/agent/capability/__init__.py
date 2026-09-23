"""声明用的词汇表：哪些产品概念、一条声明长什么样、不是「有」的格子能填什么。

住在包门口而不是 ``matrix.py`` 里，是为了让依赖只有一个方向。每个骨架的
``behaviour.py`` 要用这几个类型写自己的声明，而 ``matrix.py`` 要 import 那三份
声明才能汇总——两边都放进 ``matrix.py`` 就是一个环。声明侧依赖词汇表，汇总侧依
赖词汇表和声明侧，没有回边。

``Difference`` 只管这张表：填不出「怎么关的」那一格能说什么。事件契约那份
「这个骨架报不出某个事件」的码是另一件事，住在
``backend/tests/fixtures/harness-contract/vocabulary.json``，两份永远不会共用一
条码——合成一份再按轴切回去，换来的只是合成之前本来就有的性质。
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

    名单是封闭的，所以「这一格我说不清」这句话本身也得选一个已经存在的说法。

    名单也只管这张表里的格子。骨架契约的四条硬性要求
    （``agent.harness.SubagentRequirement``，结论 43）不在这里有码，也不许有：
    一条能填进来的差异码就是一个「暂缺」，而这四条答不出的骨架本来就不在注册表
    里，没有格子要填。加一条就会有一个没有任何声明用得上它的码——守卫红在那里。
    """

    #: 骨架根本不自带这个概念，所以没有东西要关。只在「平台自己把这件事建起来
    #: 了」能作证的时候填它——平台替它装了一个 Stop hook 去做同步，就是这个骨架
    #: 没有同步的证据。
    NOT_BUILT_IN = "not-built-in"
    #: 还没有人对着钉住的那个 build 读过这一格。也是「暂缺」的一种，但它是一次
    #: 核查就能消掉的那一种。
    NOT_CHECKED_AGAINST_THE_PIN = "not-checked-against-the-pin"


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
