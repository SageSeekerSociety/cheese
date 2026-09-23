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
        built_ins=frozenset({BuiltIn.ASK, BuiltIn.TODO, BuiltIn.REMINDER}),
        how_disabled={
            # 这一格是有条件的，两侧的条件是同一个。启动侧：
            # `--disallowedTools` 只长在 `CLAUDE_BASE_ARGS` 上
            # （cli.py 的 CLAUDE_BASE_CMD → device_launch.py 的
            # CLAUDE_BASE_ARGS），而 remote-control 那条启动串整个把它换掉了。
            # 感知侧：session_launch.py 的 hooks_settings 写 deny 时同样问一句
            # remote_control。
            # 非 RC 里关掉它是必须的而不是偏好：交互式 claude 把选项器画在
            # screen 里，没有人够得着，于是一轮就挂在那里等一个永远不会来的按
            # 键。RC 会话有回答通道，问题答得掉，所以平台故意留着它自带的这一
            # 份——这一格写成无条件的「已关闭」，下一个核表的人就核不出 RC 那一
            # 半。
            BuiltIn.ASK: (
                "非 remote-control 会话：harness/claude_code/cli.py 的 "
                f"DISALLOWED_TOOLS={DISALLOWED_TOOLS} 进启动参数的 "
                "--disallowedTools，harness/claude_code/session_launch.py 的 "
                "hooks_settings 同时把它写进 settings.json 的 deny；"
                "remote-control 会话两侧都不拒绝，答案走 RC 通道。"
                "平台的等价物是 `cheese ask`"
            ),
            BuiltIn.TODO: (
                "TodoWrite and TaskCreate/Update/List/Get are denied by CLI "
                "arguments and settings in every session. Tasks use Cheese cards; "
                "TaskStop and SendMessage remain available for native children."
            ),
            BuiltIn.REMINDER: (
                "CronCreate/Delete/List and ScheduleWakeup are denied by CLI "
                "arguments and settings in every session. Reminders use the "
                "platform's durable delivery records."
            ),
            # 平台自己装了一个 Stop hook（device_launch.py 的 cheese-sync）来做
            # 同步，这就是这个骨架没有自动同步的证据。
            BuiltIn.AUTO_SYNC: Difference.NOT_BUILT_IN,
        },
        verified_against=VERIFIED_AGAINST,
    )
