"""Claude Code 自带哪些产品概念，平台各自怎么关掉，对着哪个 build 读出来的。

结论 48 的声明侧。每一格写的是平台**今天真的在做的那个动作**，附它在代码里的位
置——一句「已关闭」而指不出是哪一行关的，下一个人没有办法核，也没有办法在它失
效的时候发现。
"""

from app.domain.agent.capability import BuiltIn, Declaration, Difference
from app.domain.agent.harness.claude_code.cli import DISALLOWED_TOOLS
from app.domain.agent.harness.claude_code.device_launch import CLAUDE_PINNED_VERSION

#: 这份声明是对着哪个 build 读出来的。**升级 pin 就要重新读一遍再改这里**——
#: 它跟 ``CLAUDE_PINNED_VERSION`` 不等的时候 CI 就红，红的意思不是「改个数字」，
#: 是「新 build 里这几格还成立吗」。
VERIFIED_AGAINST = "2.1.277"


def declaration() -> Declaration:
    return Declaration(
        pinned_version=CLAUDE_PINNED_VERSION,
        built_ins=frozenset({BuiltIn.ASK, BuiltIn.TODO}),
        how_disabled={
            # `cli.DISALLOWED_TOOLS` 同时进 argv 的 --disallowedTools 和
            # settings.json 的 deny 两侧，所以感知侧和启动侧说的是同一句拒绝。
            # 关掉它是必须的而不是偏好：交互式 claude 把选项器画在 screen 里，
            # 没有人够得着，于是一轮就挂在那里等一个永远不会来的按键。
            BuiltIn.ASK: (
                f"harness/claude_code/cli.py 的 DISALLOWED_TOOLS={DISALLOWED_TOOLS}，"
                "启动参数与 settings.json 两侧同时拒绝；平台的等价物是 `cheese ask`"
            ),
            # TodoWrite 自带一份任务清单，平台的活（split / ready / close-task）
            # 是另一份。今天平台没有关掉它：`agent/chat.py` 的现场动词表里它是
            # 「更新任务清单」，也就是照常显示。
            BuiltIn.TODO: Difference.NO_OFF_SWITCH,
            BuiltIn.REMINDER: Difference.NOT_BUILT_IN,
            # 平台自己装了一个 Stop hook（device_launch.py 的 cheese-sync）来做
            # 同步，这就是这个骨架没有自动同步的证据。
            BuiltIn.AUTO_SYNC: Difference.NOT_BUILT_IN,
        },
        verified_against=VERIFIED_AGAINST,
    )
