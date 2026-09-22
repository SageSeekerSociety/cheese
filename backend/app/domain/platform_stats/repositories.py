"""设备/机器的**存量** —— 看板「平台」那一块的第二半。

**这里只能报存量，报不了在线数**，这一条写在最前面是因为它是个容易假设错了没人会
发现的缺口：`device` 表既没有 `online` 也没有 `last_seen` 列，在线状态住在进程内存里
（`domain/agent/device_hub.py` 的 `DeviceHub._devices`，它自己的 docstring 写着
「kept I/O-free … no WebSocket, no device, no DB」），而且设备连接地址设了的时候还会
被路由到另一个进程。所以「现在有几台在线」这个数字在这一层**没有**答案 —— 要报它得
先有一个写得进库的最近可见时间，那是另一件事。看板上写「设备」而不是「在线设备」，
就是这个原因。

四张表都是台账，行数是「有多少台」这种量级（不是每调一次接口长一行），所以四个计数
都是诚实的全表聚合。
"""

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.device.models import DeviceRow, HostedDeviceRow
from app.domain.machine.models import ProjectMachine, WarmMachine


class MachineInventoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def counts(self) -> dict[str, int]:
        """四类存量各一个数，四张表各一次 `count(*)`。

        分四次查而不是拼成一条 `UNION ALL`：四张表的行数都在几位数到几千这个量级，
        合并之后省下的那点时间回答不了它带来的问题（一条语句里四个分支，读的人得
        自己把它们拆开看）；多几个来回在这个规模上量不出来。哪天真到了需要合并的
        规模，那时这张表也早就不该用 `count(*)` 了。
        """

        async def _count(model: type[Any]) -> int:
            return int(
                (
                    await self._session.execute(select(func.count()).select_from(model))
                ).scalar_one()
                or 0
            )

        return {
            "devices": await _count(DeviceRow),
            "hosted_devices": await _count(HostedDeviceRow),
            "warm_machines": await _count(WarmMachine),
            "project_machines": await _count(ProjectMachine),
        }
