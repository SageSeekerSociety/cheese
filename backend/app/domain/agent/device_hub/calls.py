"""发出一个请求，然后等设备回话：``exec`` 和 ``call_executor``。

两条路都是「登记一个 future、写一帧、按 id 等结果」，只是等的东西不同：``exec``
等命令的 stdout/stderr/exit，``call_executor`` 等屏幕里 runner 的一次调用，它的
正文分多帧回来（``execution.data`` 攒够再 ``execution.result``），等待也长得多。

失败分界只有一条，在发出之前：没有链路、或者在重连窗口之外，就直接
``DeviceOffline``/``DeviceUnreachable``；发出去之后的超时是「不知道结果」，
``device_contract`` 那边有说明。``logger`` 用门面那个名字，理由见下。
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from app.domain.agent import device_link
from app.domain.agent.device_contract import (
    EXEC_REPLY_SLACK_S,
    DeviceNotReady,
    DeviceOffline,
    DeviceUnreachable,
)
from app.domain.agent.device_hub.link import _read_a_failure_nobody_awaited

if TYPE_CHECKING:
    from app.domain.agent import device_hub as pkg

# 整条 hub 的日志都记在门面那个名字下，拆出来的几片也一样：按这个名字调级别、
# 过滤或抓取的人，看到的是完整的一条链路，而不是只在门面那一层留一句转发。同
# ``app/domain/agent/turn/store/events.py`` 记 ``app.domain.agent.room_events``。
logger = logging.getLogger("app.domain.agent.device_hub")


async def exec(
    hub: pkg.DeviceHub,
    device_id: str,
    argv: list[str],
    *,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    timeout: float = 60,
    stdin: str | None = None,
    on_stderr: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """One-shot command on the device → ``{stdout, stderr, exit, truncated}``.

    ``on_stderr`` hears each line the command writes to stderr while it runs.
    Only a connector that streams sends any; from one built before, it hears
    nothing and the result is the same. It is called on the link's read loop,
    which every other frame of the machine arrives on: it returns at once and
    never raises.

    Raises ``DeviceOffline`` when the device has no link and is not on its
    way back (``_linked``), rather than sending into the void and timing out
    ``timeout``+5s later."""
    device = hub._device(device_id)
    if not await hub._linked(device):
        raise DeviceOffline(device_id)
    device.exec_seq += 1
    eid = f"e{device.exec_seq}"
    fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
    device.exec_pending[eid] = fut
    if on_stderr is not None:
        device.exec_listeners[eid] = on_stderr
    try:
        await device.send(
            device_link.exec_cmd(
                exec_id=eid,
                command=argv,
                timeout=int(timeout),
                cwd=device.spelled(cwd) if cwd else cwd,
                env={k: device.spelled(v) for k, v in env.items()} if env else env,
                stdin=stdin,
                stream=on_stderr is not None,
            )
        )
        return await asyncio.wait_for(fut, timeout=timeout + EXEC_REPLY_SLACK_S)
    except TimeoutError:
        await device.send(device_link.exec_cancel(eid))
        raise
    finally:
        device.exec_pending.pop(eid, None)
        device.exec_listeners.pop(eid, None)
        _read_a_failure_nobody_awaited(fut)


async def call_executor(
    hub: pkg.DeviceHub,
    device_id: str,
    state: str,
    method: str,
    params: dict,
    *,
    timeout: float = 660,
    trace_id: str | None = None,
) -> dict:
    device = hub._device(device_id)
    if not await hub._linked(device):
        # 发出之前。往下每一步都可能是「已经出去了」，所以这条分界只在这里。
        raise DeviceUnreachable(device_id)
    identifier = trace_id or "execution-" + uuid.uuid4().hex
    if not device.executor:
        raise DeviceNotReady(
            f"device {device_id} is still updating its connector; "
            "execution is available once it reports ready"
        )
    future = asyncio.get_running_loop().create_future()
    device.executor_pending[identifier] = (future, bytearray())
    # The stage lines are DEBUG, and `device_error` below is not. They carry
    # raw `mono_ns` for someone to subtract while chasing latency (#951) —
    # a debugging artifact, not an event an operator acts on. At INFO they
    # made this process's log unreadable: measured 2026-09-15, four of them
    # per call meant a 600-line tail of the owner held TWELVE SECONDS, and
    # the refused-socket RuntimeError behind a room that would not answer
    # had already scrolled out of reach of the only probe that can read it.
    try:
        logger.debug(
            "execution_timing stage=device_send_start trace=%s mono_ns=%d",
            identifier,
            time.monotonic_ns(),
        )
        await device.send(
            device_link.execution_call(
                call_id=identifier,
                state=device.spelled(state),
                method=method,
                params=params,
                timeout=int(timeout),
            )
        )
        logger.debug(
            "execution_timing stage=device_sent trace=%s mono_ns=%d",
            identifier,
            time.monotonic_ns(),
        )
        return await asyncio.wait_for(future, timeout)
    except (TimeoutError, asyncio.CancelledError):
        await device.send(device_link.exec_cancel(identifier))
        raise
    finally:
        device.executor_pending.pop(identifier, None)
        _read_a_failure_nobody_awaited(future)
