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
        built_ins=frozenset({BuiltIn.ASK, BuiltIn.TODO}),
        how_disabled={
            # thread/start 的 config 里直接关掉，和 shell_tool / view_image 同一
            # 处：文件与命令归房间执行器，提问归 `cheese_ask`。
            BuiltIn.ASK: (
                "harness/codex/session.py 的 thread/start config 里 "
                "tools.experimental_request_user_input.enabled=False"
            ),
            # 这个 build 自带 `update_plan`（二进制里它自己的话：「update_plan
            # is a TODO/checklist tool」）。把 tools.update_plan.enabled 设成
            # True，它就出现在发给模型的工具里；平台显式设成 False，不靠这个
            # build 对某个模型的默认值。tests/unit/test_codex_provider_requests.py
            # 对着钉住的二进制断言发给模型的工具里没有它。
            BuiltIn.TODO: (
                "harness/codex/session.py 的 thread/start config 里 "
                "tools.update_plan.enabled=False。"
                "平台的等价物是 `todo_write`（sandbox/cheese 的 PLATFORM_TOOLS，"
                "经 harness/codex/tools.py 的 platform_tools() 进 dynamicTools）"
            ),
            # 这两格没有人对着钉住的 0.154.0 读过。填「未核」而不是「不自带」：
            # 适配器里没有痕迹，只说明平台没碰过它，不说明这个 build 里没有。
            BuiltIn.REMINDER: Difference.NOT_CHECKED_AGAINST_THE_PIN,
            BuiltIn.AUTO_SYNC: Difference.NOT_CHECKED_AGAINST_THE_PIN,
        },
        verified_against=VERIFIED_AGAINST,
    )
