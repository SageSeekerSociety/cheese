"""把一个替身骨架注册进这套部署，好让用例挑一个不是默认的来跑。

注册表今天只有 Claude Code（结论 43：答不出四条硬性要求的骨架留着代码、不注册），
而「部署设置指向的那个就是跑的那个」「跑着不说网关那套话的骨架时列出来的是哪一
批」这些问题，要一个第二名字才问得出——拿 Codex 或 pi 当那个名字，等于假定它们
在跑。替身答四条时指向的是 ``agent/harness/`` 这个目录：它不是真骨架，指不出更
具体的行。
"""

from app.domain.agent import harness as harness_module
from app.domain.agent.harness import Harness, SubagentRequirement


def registered(monkeypatch, name: str, **bits: bool) -> None:
    stand_in = Harness(
        name,
        name,
        subagents=dict.fromkeys(SubagentRequirement, "替身，见 `agent/harness/`"),
        **bits,
    )
    monkeypatch.setitem(harness_module.HARNESSES, name, stand_in)
