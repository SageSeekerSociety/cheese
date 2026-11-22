"""学情分析变了：告诉看着这块板的两页，该重新读一遍了。

「我的学习洞察」和「数据 › 学情分析」不会自己变：它们原来只在打开页面时读一次，于是
一两分钟前写下的判断要等人手动刷新才出现。这里给每一块板开一个房间级的推送频道：

- 写结果的几处（每分钟的读取写完一个人的一批、每小时的汇总跑完、巩固题交了卷改档位、
  标记不准）``publish(space_id)`` 一条帧（``{"type": "space_learning", "spaceId": n}``）；
- 页面订阅 ``WS /spaces/{spaceId}/learning-analysis/live``，收到一帧就把自己那一份
  重读一遍 —— 读的仍是原来那两条 GET，不另开一份数据。

**帧里不装分析结果**，只装「这块板变了」。谁该看到哪一条判断仍由那两条 GET 判（「只有
本人」「不足 3 人，不分析」都在那里），推送只是一声「重新读」。于是推送走漏一帧最多让
页面晚一轮，不会让谁看到不该看的。

**这一层不能 import 兄弟包**（``.importlinter`` C3/C5：``app.domain.space`` 不 import
``app.domain.agent``），而 broker 在 agent 那一包里。所以这里只留一个注入点：
``install(publisher)`` 由 ``main.lifespan`` 把本进程的 ``broker.publish`` 装上来，
domain 只按这个函数发帧。没装（脚本、单元测试）就静默不发。

推送是尽力而为：发不出去不报错；连接断了页面自己重连，重连时重读一次，漏掉的帧由那一次
补上。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

#: 帧的 ``type``：这块板的分析结果变了。
FRAME_TYPE = "space_learning"

#: ``(channel, frame) -> None``：本进程的 broker.publish，由 ``main.lifespan`` 装上。
_PUBLISH: Callable[[str, dict], Awaitable[Any]] | None = None


def install(publish: Callable[[str, dict], Awaitable[Any]] | None) -> None:
    """Called once at startup with the process's ``broker.publish``."""
    global _PUBLISH
    _PUBLISH = publish


def channel(space_id: int) -> str:
    """The broker channel this board's watchers subscribe to."""
    return f"space-learning:{space_id}"


def frame(space_id: int) -> dict:
    return {"type": FRAME_TYPE, "spaceId": space_id}


async def publish(space_id: int) -> None:
    """Tell this board's open pages that its analysis has new numbers."""
    publish_ = _PUBLISH
    if publish_ is None:
        return
    try:
        await publish_(channel(space_id), frame(space_id))
    except Exception:  # a page missing one refresh is not worth a failed write
        logger.warning("learning live publish failed space=%s", space_id, exc_info=True)
