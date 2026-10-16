"""观看端：浏览器连着屏幕看它、按键、改大小。

屏幕的原始字节是转发的，服务端不解析（见门面的模块说明）。这里的四个函数只做一件
事：把浏览器的动作翻成一条 ``screen.*`` 帧发给设备。第一个观看端进来才
``screen.subscribe``（设备那边才去挂一个 tmux 客户端），最后一个走了就
``screen.unsubscribe``。``detach_viewer`` 收的是 ``screen.device_id``：调用方只有
sid，屏幕在哪台机器上由登记表说。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.domain.agent import device_link
from app.domain.agent.device_contract import ViewerTransport
from app.domain.agent.device_hub.sessions import _require_screen

if TYPE_CHECKING:
    from app.domain.agent import device_hub as pkg


async def attach_viewer(
    hub: pkg.DeviceHub,
    device_id: str,
    sid: str,
    viewer: ViewerTransport,
    *,
    cols: int = 120,
    rows: int = 32,
) -> None:
    """Attach a browser viewer to a screen. The first viewer triggers a
    ``screen.subscribe`` carrying its size (the device attaches a tmux client)."""
    screen = _require_screen(hub, device_id, sid)
    first = not screen.viewers
    screen.viewers.add(viewer)
    if first:
        await hub._device(device_id).send(device_link.screen_subscribe(sid, cols, rows))


async def detach_viewer(
    hub: pkg.DeviceHub, device_id: str, sid: str, viewer: ViewerTransport
) -> None:
    screen = hub._screens.get(sid)
    if screen is None:
        return
    screen.viewers.discard(viewer)
    device = hub._device(screen.device_id)
    if not screen.viewers:
        await device.send(device_link.screen_unsubscribe(sid))


async def viewer_input(
    hub: pkg.DeviceHub, device_id: str, sid: str, data: bytes
) -> None:
    await hub._device(device_id).send(device_link.screen_input(sid, data))


async def viewer_resize(
    hub: pkg.DeviceHub, device_id: str, sid: str, cols: int, rows: int
) -> None:
    await hub._device(device_id).send(device_link.screen_resize(sid, cols, rows))
