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
VERIFIED_AGAINST = "2.1.282"


def declaration() -> Declaration:
    return Declaration(
        pinned_version=CLAUDE_PINNED_VERSION,
        built_ins=frozenset({BuiltIn.ASK, BuiltIn.TODO, BuiltIn.REMINDER}),
        how_disabled={
            # 两侧拒的是同一张单子：启动参数的 --disallowedTools 和 settings.json
            # 的 deny。房间里没有人答得了这个工具的提问——它会作为一次请求停在
            # runner 的 stdin 上——所以它一律不提供，提问走平台的 `cheese_ask`。
            BuiltIn.ASK: (
                "harness/claude_code/cli.py 的 "
                f"DISALLOWED_TOOLS={DISALLOWED_TOOLS} 进 LAUNCH_ARGS 的 "
                "--disallowedTools，harness/claude_code/session_launch.py 的 "
                "session_settings 同时把它写进 settings.json 的 deny。"
                "平台的等价物是 `cheese_ask`"
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
            # 平台自己在会话的 Stop 上装了执行机的 checkpoint（remote_execution/
            # client.py 的 checkpoint）来做同步，这就是这个骨架没有自动同步的证据。
            BuiltIn.AUTO_SYNC: Difference.NOT_BUILT_IN,
        },
        verified_against=VERIFIED_AGAINST,
    )
