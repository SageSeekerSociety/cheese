"""把一个替身骨架注册进这套部署，好让用例挑一个不是默认的来跑。

「部署设置指向的那个就是跑的那个」「跑着不说网关那套话的骨架时列出来的是哪一
批」这些问题，要一个第二名字才问得出——拿 Codex 或 pi 当那个名字，等于假定它们
在跑。
"""

from app.domain.agent import harness as harness_module
from app.domain.agent.harness import Harness


def registered(monkeypatch, name: str, **bits: bool) -> None:
    stand_in = Harness(name, name, **bits)
    monkeypatch.setitem(harness_module.HARNESSES, name, stand_in)
