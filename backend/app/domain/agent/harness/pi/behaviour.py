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
from app.domain.agent.harness.pi.launch import VERSION

#: 见 ``claude_code/behaviour.py`` 同名常量：升级 pin 就要重新读一遍再改这里。
VERIFIED_AGAINST = "1.0.0"

# 1.0.0 里「pi 能递给模型什么」是两处封闭名单加一处开关，四格都对着这三样读的：
# 工具是 packages/coding-agent/src/core/tools/index.ts 的 ToolName 联合
# （read、bash、powershell、edit、write、grep、find、ls，和 0.85.1 同一个）；
# 内建扩展是 builtin:mcp、builtin:llama.cpp、builtin:codemode、builtin:tool-search
# （docs/settings.md）；而 ``--no-extensions``（launch.py 的 arguments()）把发现
# 到的、配置的、内建的扩展一起关掉（docs/cli.md），examples/extensions/ 下那些
# 示例扩展我们一律不加载。0.85.1 时 ASK 与 AUTO_SYNC 填的是「未核」；这一次读了，
# 两格都读得出结论，所以下面写的是读到的。


def declaration() -> Declaration:
    return Declaration(
        pinned_version=VERSION,
        built_ins=frozenset(),
        how_disabled={
            # 1.0.0 的工具联合里没有提问工具，内建扩展里也没有；examples/ 下有一个
            # question.ts，那是给人抄的示例，我们不加载任何扩展。所以模型手里没
            # 有可以问人的工具，提问归平台的 `cheese_ask`（sandbox/cheese 的
            # PLATFORM_TOOLS，经 harness/pi/catalog.py 进 platform.ts 的
            # registerPlatformTools）。
            BuiltIn.ASK: Difference.NOT_BUILT_IN,
            # 1.0.0 的工具联合里没有清单工具（上面那八个），内建扩展四个里也没有，
            # 它的 README 也不再有内置待办的说法。所以没有东西要关，清单是平台建
            # 的 `todo_write`（sandbox/cheese 的 PLATFORM_TOOLS，同上进
            # registerPlatformTools）。
            BuiltIn.TODO: Difference.NOT_BUILT_IN,
            # 1.0.0 没有提醒：工具联合里没有，内建扩展四个里没有，docs/ 下也
            # 没有定时或唤醒的说法。房间太久没听到消息时的提醒是平台的
            # ChatService.remind_silent_turns，它不看骨架；平台给 pi 的
            # extension 里也没有第二份。
            BuiltIn.REMINDER: Difference.NOT_BUILT_IN,
            # 1.0.0 会自己做的是会话的持久化与压缩，不是把工作同步回仓库：工具联
            # 合里没有同步工具，内建扩展里也没有，examples/extensions/ 里的
            # auto-commit-on-exit.ts 是示例、不被加载。同步由平台自己的机制做
            # （每轮结束时 harness/driven/runner.py 的 turn_ended 让机器跑一次
            # checkpoint，即 `cheese sync --all`，和 Claude Code 的 Stop 钩子同一个
            # 动作），和骨架无关——也就是这个骨架没有自动同步的证据。
            BuiltIn.AUTO_SYNC: Difference.NOT_BUILT_IN,
        },
        verified_against=VERIFIED_AGAINST,
    )
