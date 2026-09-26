"""pi 自带哪些产品概念，平台各自怎么关掉，对着哪个 build 读出来的。

结论 48 的声明侧，写法见 ``claude_code/behaviour.py``。

这一行今天整行都是差异码，而那正是它值得存在的理由：平台给 pi 的那份 extension
在自己的抬头里写着「pi 的循环、它的工具、它的上下文处理都是它自己的」——平台往
里加工具，不关掉任何东西。所以四格一个关闭动作都没有，这不是漏填，是现状。

四格逐条写出来，和另外两份声明一样：一句 ``dict.fromkeys(BuiltIn, ...)`` 会让这
一行跟着 ``BuiltIn`` 自动长——往表上加一个概念，另外两个骨架红在矩阵上等人回答，
这里却已经替谁也没读过的一格印好了答案。
"""

from app.domain.agent.capability import BuiltIn, Declaration, Difference
from app.domain.agent.harness.pi.device_launch import VERSION

#: 见 ``claude_code/behaviour.py`` 同名常量：升级 pin 就要重新读一遍再改这里。
VERIFIED_AGAINST = "0.85.1"


def declaration() -> Declaration:
    return Declaration(
        pinned_version=VERSION,
        built_ins=frozenset(),
        how_disabled={
            BuiltIn.ASK: Difference.NOT_CHECKED_AGAINST_THE_PIN,
            # 0.85.1 的内置工具是 packages/coding-agent/src/core/tools/index.ts
            # 的 ToolName：read、bash、powershell、edit、write、grep、find、ls，
            # 没有清单工具；它的 README 写明「No built-in to-dos」。所以没有东西
            # 要关，清单是平台建的 `todo_write`（sandbox/cheese 的
            # PLATFORM_TOOLS，经 harness/pi/catalog.py 进 platform.ts 的
            # registerPlatformTools）。
            BuiltIn.TODO: Difference.NOT_BUILT_IN,
            BuiltIn.REMINDER: Difference.NOT_CHECKED_AGAINST_THE_PIN,
            BuiltIn.AUTO_SYNC: Difference.NOT_CHECKED_AGAINST_THE_PIN,
        },
        verified_against=VERIFIED_AGAINST,
    )
