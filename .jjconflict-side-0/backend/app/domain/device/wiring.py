"""``device`` 领域的标准接线。

``DeviceService`` 故意只认 ``DeviceRepository`` 协议（见本包 ``__init__`` 的说明：
transport-free / storage-agnostic，内存实现和 SQL 实现都能喂给它）。代价是每个调用方
都得自己写 ``DeviceService(SqlDeviceRepository(session))`` —— 于是别的领域为了接线
去 import 本领域的 repository，把「repository 是领域内部的东西」这条捅穿了。

这个模块就是那个缺掉的接缝：**要 SQL 后端的设备能力，调这里，别自己碰
``device.sql_repository``**（守卫见 ``tests/unit/test_domain_import_guard.py``）。
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.device.service import DeviceService

__all__ = ["sql_device_service"]


def sql_device_service(session: AsyncSession) -> DeviceService:
    """接在这个 session 上的、SQL 后端的 ``DeviceService``。"""
    from app.domain.device.sql_repository import SqlDeviceRepository

    return DeviceService(SqlDeviceRepository(session))
