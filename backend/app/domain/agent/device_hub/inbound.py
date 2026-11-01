"""设备发回来的一帧：认它是哪个调用的答案，然后交给等它的那个 future。

``on_device_message`` 是这条链路唯一的读入口。它先记下「这台机器还活着」（任何一
帧都算），再按 ``t`` 认：``hello`` 是这一条链路重新立起来的时刻（认识它自报的
build/target/executor，唤醒在等重连的调用，顺手看看它的 build 是不是我们发布的
那个）；``exec.result``/``execution.*``/``session.result``/``localfs.*`` 各自去
``HubDevice`` 上那张对应的 future 表里找 ``id``，找到就设结果或异常；``machine.*`` 是
沙箱从平台 socket 发起的流，交给 ``machine``；``screen.data``
是屏幕的原始字节，转发给观看端。认不出来的帧就这样丢掉——连接器也是这么对待它不认
识的帧的（见 ``_update_if_stale`` 的说明）。

``_fan_out_screen_data`` 不收 hub：它只碰一个屏幕和它上面的观看端。
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from typing import TYPE_CHECKING, Any

from app.domain.agent import device_link
from app.domain.agent.device_contract import DeviceCallError, HubScreen, ViewerTransport
from app.domain.agent.device_hub import machine

if TYPE_CHECKING:
    from app.domain.agent import device_hub as pkg

# 整条 hub 的日志都记在门面那个名字下，拆出来的几片也一样：按这个名字调级别、
# 过滤或抓取的人，看到的是完整的一条链路（``execution_timing`` 的四个阶段有
# 两个在本模块、两个在 ``calls``）。同
# ``app/domain/agent/turn/store/events.py`` 记 ``app.domain.agent.room_events``。
logger = logging.getLogger("app.domain.agent.device_hub")


async def on_device_message(
    hub: pkg.DeviceHub, device_id: str, m: dict[str, Any]
) -> None:
    """Dispatch one inbound ``link.Msg`` from the device."""
    device = hub._device(device_id)
    # Any inbound frame is a liveness signal (heartbeats included) — monotonic.
    device.last_seen = asyncio.get_event_loop().time()
    msg = device_link.LinkMsg.parse(m)
    screen = device.screens.get(msg.sid)

    if msg.t == "hello":
        device.proto = msg.v
        device.build = msg.build
        device.target = msg.target
        device.executor = msg.executor
        device.profiles = msg.profiles
        device.dropped_at = None
        device.announced.set()
        if device.proto not in (None, device_link.PROTOCOL_VERSION):
            hub._on_version_skew(device_id, device.proto)
        device.build_checked_at = device.last_seen
        await hub._update_if_stale(device)
        return
    if msg.t == "session.error":
        # The device could not start (or attach) this screen. There is no
        # pending future to fail — the turn discovers the loss when its runner
        # never answers — but dropping the one frame that says WHY turns that
        # into a blank error, so at least keep the reason in the log.
        logger.warning(
            "device %s screen %s reported session.error: %s",
            device_id,
            msg.sid,
            msg.error,
        )
        return
    if msg.t == "heartbeat":
        device.heartbeats = True
        hub._check_build(device)
        return
    if msg.t == "session.ready":
        return
    if msg.t == "exec.result":
        fut = device.exec_pending.get(msg.id)
        if fut is not None and not fut.done():
            fut.set_result(
                {
                    "stdout": msg.stdout,
                    "stderr": msg.stderr,
                    "exit": msg.exit,
                    "truncated": msg.truncated,
                }
            )
        return
    if msg.t == "exec.output":
        listener = device.exec_listeners.get(msg.id)
        if listener is not None:
            listener(msg.stderr)
        return
    if msg.t in {"execution.data", "execution.result"}:
        pending = device.executor_pending.get(msg.id)
        if pending is None or pending[0].done():
            return
        future, data = pending
        try:
            if msg.t == "execution.data":
                if not data:
                    logger.debug(
                        "execution_timing stage=device_first_data trace=%s mono_ns=%d",
                        msg.id,
                        time.monotonic_ns(),
                    )
                data.extend(base64.b64decode(msg.data, validate=True))
            elif msg.error:
                logger.info(
                    "execution_timing stage=device_error trace=%s mono_ns=%d",
                    msg.id,
                    time.monotonic_ns(),
                )
                raise DeviceCallError(msg.error)
            else:
                logger.debug(
                    "execution_timing stage=device_complete trace=%s mono_ns=%d",
                    msg.id,
                    time.monotonic_ns(),
                )
                response = json.loads(data)
                if "error" in response:
                    raise DeviceCallError(response["error"])
                future.set_result(response["result"])
        except (ValueError, KeyError, RuntimeError) as exc:
            future.set_exception(exc)
        return
    if msg.t.startswith("machine."):
        await machine.on_frame(hub, device_id, msg)
        return
    if msg.t == "screen.data" and screen is not None:
        await _fan_out_screen_data(screen, msg.decoded_data())
        return
    if msg.t == "session.result":
        fut = device.session_pending.get(msg.id)
        if fut is not None and not fut.done():
            if msg.error:
                fut.set_exception(DeviceCallError(msg.error))
            else:
                fut.set_result(msg.value)
        return
    if msg.t in ("localfs.grants.result", "localfs.op.result"):
        # One map for both: the id is unique per device across the channel, and
        # an o/g prefix already says which kind answered.
        fut = device.local_fs_pending.get(msg.id)
        if fut is not None and not fut.done():
            if msg.error:
                fut.set_exception(DeviceCallError(msg.error))
            else:
                fut.set_result(msg.value)
        return


async def _fan_out_screen_data(screen: HubScreen, raw: bytes) -> None:
    dead: list[ViewerTransport] = []
    for viewer in list(screen.viewers):
        try:
            await viewer.send_bytes(raw)
        except Exception:  # a dead viewer is dropped, not fatal
            dead.append(viewer)
    for viewer in dead:
        screen.viewers.discard(viewer)
