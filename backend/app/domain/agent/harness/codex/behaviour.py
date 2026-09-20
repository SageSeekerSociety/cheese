"""Codex 自带哪些产品概念，平台各自怎么关掉，对着哪个 build 读出来的。

结论 48 的声明侧，写法见 ``claude_code/behaviour.py``。
"""

from app.domain.agent.capability import BuiltIn, Declaration, Difference
from app.domain.agent.harness.codex.host import VERSION

#: 见 ``claude_code/behaviour.py`` 同名常量：升级 pin 就要重新读一遍再改这里。
VERIFIED_AGAINST = "0.154.0"


def declaration() -> Declaration:
    return Declaration(
        pinned_version=VERSION,
        built_ins=frozenset({BuiltIn.ASK}),
        how_disabled={
            # thread/start 的 config 里直接关掉，和 shell_tool / view_image 同一
            # 处：文件与命令归房间执行器，提问归 `cheese ask`。
            BuiltIn.ASK: (
                "harness/codex/session.py 的 thread/start config 里 "
                "tools.experimental_request_user_input.enabled=False"
            ),
            # 这三格没有人对着钉住的 0.154.0 读过。填「未核」而不是「不自带」：
            # 适配器里没有痕迹，只说明平台没碰过它，不说明这个 build 里没有。
            BuiltIn.TODO: Difference.NOT_CHECKED_AGAINST_THE_PIN,
            BuiltIn.REMINDER: Difference.NOT_CHECKED_AGAINST_THE_PIN,
            BuiltIn.AUTO_SYNC: Difference.NOT_CHECKED_AGAINST_THE_PIN,
        },
        verified_against=VERIFIED_AGAINST,
    )
