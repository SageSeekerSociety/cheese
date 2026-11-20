"""屏幕（会话）：在设备上开一个屏幕，并记住它。

一次 ``session.create`` 之后，服务端就知道这台设备上跑着哪个 sid、它的屏幕令牌
是什么、它替谁（``agent_user_id``/``agent_handle``）跑。设备重启过、服务端没重启
要 ``adopt_screen``；两边都重启过要 ``reassert_screen``；设备确认停下之后才
``close_screen``。

登记表（``hub._screens``、``hub._by_screen_token``）和 ``hub._devices[sid]`` 上的
屏幕表是 ``DeviceHub`` 自己的状态，所以这里的函数都收 hub 作第一个参数，门面上的
同名方法只做一行转发。要等设备回话的 ``session_request`` 也在这里：``close_screen``
和 ``list_screens`` 都走它。
"""

from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING, Any

from app.domain.agent import device_link
from app.domain.agent.device_contract import DeviceOffline, HubScreen
from app.domain.agent.device_hub.link import _read_a_failure_nobody_awaited

if TYPE_CHECKING:
    from app.domain.agent import device_hub as pkg


async def open_screen(
    hub: pkg.DeviceHub,
    device_id: str,
    command: list[str],
    *,
    agent_user_id: int,
    agent_handle: str,
    project_id: uuid.UUID | None = None,
    topic_id: uuid.UUID | None = None,
    env: dict[str, str] | None = None,
    cols: int = 120,
    rows: int = 32,
) -> HubScreen:
    """Open a screen (an agent) on the device acting as ``agent_user_id``: mint
    its per-screen token, ship the command, and inject ``env`` into the screen
    process. The token becomes ``CHEESE_SCREEN`` inside the screen so a call from
    within it proves which screen it belongs to."""
    device = hub._device(device_id)
    sid = "s" + uuid.uuid4().hex[:8]
    screen = HubScreen(
        sid=sid,
        device_id=device_id,
        command=command,
        token=uuid.uuid4().hex,
        agent_user_id=agent_user_id,
        agent_handle=agent_handle,
        project_id=project_id,
        topic_id=topic_id,
    )
    device.screens[sid] = screen
    hub._screens[sid] = screen
    hub._by_screen_token[screen.token] = screen
    sent = await device.send(
        device_link.session_create(
            sid=sid,
            command=command,
            screen_token=screen.token,
            cols=cols,
            rows=rows,
            env={k: device.spelled(v) for k, v in env.items()} if env else env,
        )
    )
    if not sent:
        # No link: nothing reached the machine. Say so instead of returning a
        # success the room would trust — its turn waited on a runner that was
        # never started, until its own timeout. The screen stays on the books on
        # purpose: the cleanup path finds it by topic, and a reassert when the
        # machine comes back is what brings it up.
        raise DeviceOffline(device_id)
    return screen


async def reassert_screen(
    hub: pkg.DeviceHub,
    screen: HubScreen,
    *,
    command: list[str],
    env: dict[str, str] | None = None,
    cols: int = 120,
    rows: int = 32,
) -> None:
    """Re-send an existing screen's ``session.create`` with ``adopt`` set — the
    frozen cli's designed re-provision path. The registry alone never proves the
    device still runs a screen: the connector process may have restarted (its
    tmux sessions outlive it, but its in-memory session map does not, so it
    knows nothing about this sid until a create makes it re-adopt), or the
    original create was written into a link whose peer had already vanished and
    never arrived.
    An adopt-create is
    idempotent on the device: a live session keeps running untouched; a lost one
    is respawned under the SAME sid + screen token, so viewers and attribution
    stay intact."""
    screen.command = command
    device = hub._device(screen.device_id)
    sent = await device.send(
        device_link.session_create(
            sid=screen.sid,
            command=command,
            screen_token=screen.token,
            cols=cols,
            rows=rows,
            env={k: device.spelled(v) for k, v in env.items()} if env else env,
            adopt=True,
        )
    )
    if not sent:
        # Same as open_screen: no link, nothing adopted. The caller re-asserts
        # again after the machine comes back.
        raise DeviceOffline(screen.device_id)


def adopt_screen(
    hub: pkg.DeviceHub,
    device_id: str,
    sid: str,
    *,
    token: str,
    agent_user_id: int,
    agent_handle: str,
    command: list[str] | None = None,
    project_id: uuid.UUID | None = None,
    topic_id: uuid.UUID | None = None,
    resource_id: uuid.UUID | None = None,
    credential_expires: int | None = None,
    agent_configuration: str = "",
    execution_target: dict | None = None,
) -> HubScreen:
    """Re-register a screen the *device* is still running after the server lost its
    in-memory state (a restart). The frozen cli auto-reconnects its control channel
    and re-announces the screens it kept alive; adopting rebinds the sid + its
    screen token to the agent identity so viewers/attribution work again without
    restarting the screen. Idempotent per (device, sid).

    The channel checks room ownership against the DB before adopting the
    identity retained by the device's tmux session.
    """
    device = hub._device(device_id)
    existing = device.screens.get(sid)
    if existing is not None:
        if (existing.project_id, existing.topic_id, existing.token) != (
            project_id,
            topic_id,
            token,
        ):
            raise ValueError("screen identity changed during recovery")
        return existing
    if sid in hub._screens or token in hub._by_screen_token:
        raise ValueError("screen identity belongs to another device")
    screen = HubScreen(
        sid=sid,
        device_id=device_id,
        command=command or [],
        token=token,
        agent_user_id=agent_user_id,
        agent_handle=agent_handle,
        project_id=project_id,
        topic_id=topic_id,
        resource_id=resource_id,
        credential_expires=credential_expires,
        agent_configuration=agent_configuration,
        execution_target=execution_target,
    )
    device.screens[sid] = screen
    hub._screens[sid] = screen
    hub._by_screen_token[token] = screen
    return screen


def update_screen(
    hub: pkg.DeviceHub,
    sid: str,
    *,
    resource_id: uuid.UUID | None,
    execution_target: dict | None,
    credential_expires: int | None = None,
    agent_configuration: str | None = None,
) -> HubScreen:
    screen = hub._screens.get(sid)
    if screen is None:
        raise KeyError("screen not found")
    screen.resource_id = resource_id
    screen.execution_target = execution_target
    if credential_expires is not None:
        screen.credential_expires = credential_expires
    if agent_configuration is not None:
        screen.agent_configuration = agent_configuration
    return screen


async def close_screen(hub: pkg.DeviceHub, device_id: str, sid: str) -> bool:
    """Forget a screen only after its owner confirms that it has stopped."""
    device = hub._device(device_id)
    screen = device.screens.get(sid)
    if screen is not None:
        screen.closing = True
    # Through the hub rather than the module function: this line was
    # ``self.session_request`` before the split, and a call through the object
    # is the one a replaced method (a subclass, a test) still intercepts.
    await hub.session_request(device_id, device_link.session_close(sid))
    if screen is None:
        return False
    device.screens.pop(sid, None)
    hub._screens.pop(sid, None)
    hub._by_screen_token.pop(screen.token, None)
    return True


async def session_request(
    hub: pkg.DeviceHub, device_id: str, message: dict[str, Any], *, timeout: float = 30
) -> Any:
    device = hub._device(device_id)
    if not await hub._linked(device):
        raise DeviceOffline(device_id)
    request_id = uuid.uuid4().hex
    future = asyncio.get_running_loop().create_future()
    device.session_pending[request_id] = future
    try:
        await device.send({**message, "id": request_id})
        return await asyncio.wait_for(future, timeout)
    finally:
        device.session_pending.pop(request_id, None)
        _read_a_failure_nobody_awaited(future)


async def list_screens(hub: pkg.DeviceHub, device_id: str) -> list[dict[str, Any]]:
    # Through the hub: see ``close_screen``.
    return await hub.session_request(device_id, {"t": "session.list"})


def screen(hub: pkg.DeviceHub, sid: str) -> HubScreen | None:
    return hub._screens.get(sid)


def all_online_screens(hub: pkg.DeviceHub) -> list[HubScreen]:
    return [s for s in hub._screens.values() if hub.is_online(s.device_id)]


def screens_in_project(hub: pkg.DeviceHub, project_id: uuid.UUID) -> list[HubScreen]:
    return [
        s
        for s in hub._screens.values()
        if s.project_id == project_id and hub.is_online(s.device_id)
    ]


def screens_for_topic(hub: pkg.DeviceHub, topic_id: uuid.UUID) -> list[HubScreen]:
    """Include offline screens so a close can remain pending until reconnect."""
    return [s for s in hub._screens.values() if s.topic_id == topic_id]


def _require_screen(hub: pkg.DeviceHub, device_id: str, sid: str) -> HubScreen:
    screen = hub._device(device_id).screens.get(sid)
    if screen is None:
        raise KeyError(f"no screen {sid!r} on device {device_id!r}")
    return screen
