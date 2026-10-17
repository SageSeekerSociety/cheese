"""本机目录授权：把授权集推给机器，以及在授权的目录里读写列。

两个方向共用 ``device.local_fs_pending`` 那一张 future 表和一个序号（``g`` 推授权、
``o`` 一次操作），回话的 id 已经分得清是哪种，所以入站那边一条分支处理两者。

推授权和读写的失败规矩不一样，写在各自的 docstring 里：推授权时机器离线是**可以
照常继续**的（平台才是权威记录，机器重连时会再推一遍），读写时没有链路就直接
``DeviceOffline``——那是一次人等着结果的调用。
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from app.domain.agent import device_link
from app.domain.agent.device_contract import DeviceOffline
from app.domain.agent.device_hub.link import _read_a_failure_nobody_awaited

if TYPE_CHECKING:
    from app.domain.agent import device_hub as pkg


async def push_local_fs_grants(
    hub: pkg.DeviceHub,
    device_id: str,
    grants: list[dict[str, Any]],
    *,
    timeout: float = 20,
) -> dict[str, Any]:
    """Hand this machine its grant set and await the fingerprint it now holds.

    Raises ``DeviceOffline`` at once when the machine has no link — rather
    than sending into the void and timing out. That is not a failure of the
    grant: the platform is the authoritative record either way, and the set is
    pushed again when the device reattaches. A caller that has just recorded a
    grant must therefore catch this and carry on, which is exactly the
    degradation 「本机离线时项目照常可用」 describes.
    """
    device = hub._device(device_id)
    if device.transport is None:
        raise DeviceOffline(device_id)
    device.local_fs_seq += 1
    grants_id = f"g{device.local_fs_seq}"
    future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
    device.local_fs_pending[grants_id] = future
    try:
        await device.send(
            device_link.local_fs_grants(
                grants_id=grants_id, device_id=device_id, grants=grants
            )
        )
        return await asyncio.wait_for(future, timeout=timeout)
    finally:
        device.local_fs_pending.pop(grants_id, None)
        _read_a_failure_nobody_awaited(future)


async def local_fs_op(
    hub: pkg.DeviceHub,
    device_id: str,
    op: dict[str, Any],
    *,
    timeout: float = 60,
) -> dict[str, Any]:
    """Read, write or list inside a granted directory on this machine.

    Returns the device's ``localfs.Reply``. A refusal comes back as a normal
    result with ``decision == "denied"`` — it is an answer, not a transport
    failure, and the reason in it is what the person is shown.
    """
    device = hub._device(device_id)
    if not await hub._linked(device):
        raise DeviceOffline(device_id)
    device.local_fs_seq += 1
    op_id = f"o{device.local_fs_seq}"
    future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
    device.local_fs_pending[op_id] = future
    try:
        await device.send(device_link.local_fs_op(op_id=op_id, op=op))
        return await asyncio.wait_for(future, timeout=timeout)
    finally:
        device.local_fs_pending.pop(op_id, None)
        _read_a_failure_nobody_awaited(future)
