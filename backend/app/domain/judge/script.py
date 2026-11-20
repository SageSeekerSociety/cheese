"""评测主机上跑的那一整段脚本：``compare``、``container``、``reports``、``runner`` 按这个
顺序拼起来。

``runner`` 在评测主机上用 ``python3 -I -c`` 跑，主机上没有这个仓库；它要的比对和容器
参数就随着它一起发过去 —— 先定义，再用。几份源码都只用标准库、都不写
``from __future__``（拼起来以后它就不在第一行了）。
"""

from pathlib import Path

from app.domain.judge import compare, container, reports, runner


def _source() -> str:
    return "\n\n".join(
        Path(module.__file__ or "").read_text()
        for module in (compare, container, reports, runner)
    )


#: What ``python3 -I -c`` runs on the judge host.
RUNNER_SRC = _source()
