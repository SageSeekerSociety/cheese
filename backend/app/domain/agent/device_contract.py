"""设备调用的共同词汇：失败、屏幕的形状、调用能等多久。

``device_hub`` holds the connection and the screens; a rolling backend reaches
that owner over HTTP through ``device_hub_rpc``. Both sides have to mean the
same thing by a machine that cannot be reached (``DeviceOffline`` and its
subclasses), by a machine that answered with a failure of its own
(``DeviceCallError``), by the screen they hand each other (``HubScreen``), and
by how long a call may take before it is given up on
(``RECONNECT_GRACE_S``, ``EXEC_REPLY_SLACK_S``).

They live here because this is the only thing the two share: the facade hands
back screens it must not have to import the hub to name, and the hub must not
have to be able to open an HTTP call. A facade that imported the hub, while the
hub picks its own facade at import time, is a cycle (``.importlinter``, C3).

``device_hub`` re-exports everything here, so importing a failure from there —
where it was written down before — keeps working. The three failures clients
answer with (``DeviceOffline``, ``LinkInterrupted``, ``DeviceCallError``) and
``offline_headers`` are re-exported FROM ``app/core/device_failures.py``: what
answers with them is the error tree, and the error tree is core, so the word a
domain and core both need has to be owned below both (``.importlinter``, C1).
"""

import uuid
from dataclasses import dataclass, field
from typing import Protocol

from app.core.device_failures import (
    DeviceCallError,
    DeviceOffline,
    LinkInterrupted,
    offline_headers,
)

__all__ = [
    "DeviceCallError",
    "DeviceNotReady",
    "DeviceOffline",
    "DeviceUnreachable",
    "EXEC_REPLY_SLACK_S",
    "HubScreen",
    "LinkInterrupted",
    "RECONNECT_GRACE_S",
    "ViewerTransport",
    "offline_headers",
]


class DeviceUnreachable(DeviceOffline):
    """链路根本不在，所以这一帧一个字节都没有写出去。

    和 ``DeviceOffline`` 本身的差别只有一个，而那一个决定平台事后敢不敢重做这件事：
    这一档是**发出之前**就失败的，那台机器没见过这次调用。一次已经写进 socket 的调用
    在等结果的时候链路断了，也是 ``DeviceOffline``（``drop_transport`` 把在飞的
    future 全置成它）—— 那一档里那件事做没做过，这一侧不知道。

    所以只有这一档能被记成 ``failed``「确定没发生」（``agent/dispatch_log.py``）。
    一个子类，因为除此之外它和链路不在是同一件事：所有 ``except DeviceOffline`` 照旧。
    """


class DeviceNotReady(DeviceCallError):
    """The link is up, but this machine cannot serve calls yet.

    A connector that has just dialled in finishes updating itself before it can
    run anything, and a call that arrives in that window has nothing to reach.
    It is the same standing as the machine being away — the caller's next poll,
    a second or two later, finds it ready — but as a bare RuntimeError it was an
    unhandled 500 and one alert per release: 「Device connector must finish
    updating before execution」 arrived that way at 01:47 UTC on 2026-09-20,
    seconds after the owner was replaced and the fleet re-attached.
    """


# How much longer than a command's own deadline `exec` waits for the machine's
# reply before it gives up and raises `TimeoutError`.
EXEC_REPLY_SLACK_S = 5
# How long after its link drops a machine counts as reconnecting rather than
# gone. A connector redials at once and then after 1s, 2s, 4s, 5s
# (`cli/internal/link/link.go`), so a link that blinks is back within a few
# seconds; three of those attempts fit here. A call that finds no link inside
# this window waits for the machine to say hello again, instead of reporting a
# machine that is on its way back as offline. Past it, the machine is away.
RECONNECT_GRACE_S = 15.0


class ViewerTransport(Protocol):
    """A browser terminal viewer. It only ever *receives* raw screen bytes."""

    async def send_bytes(self, data: bytes) -> None: ...


@dataclass
class HubScreen:
    sid: str
    device_id: str
    command: list[str]
    token: str  # the CHEESE_SCREEN value injected into the screen
    # A screen *is* an agent (一个 agent 是一个屏幕): it acts as one agent-user in its
    # project/topic. Attribution of the screen's cheese-api calls keys on
    # agent_user_id.
    agent_user_id: int
    agent_handle: str
    project_id: uuid.UUID | None = None
    topic_id: uuid.UUID | None = None
    resource_id: uuid.UUID | None = None
    # UNIX expiry of the model credential the screen's `claude` was LAUNCHED with
    # (its `CHEESE_TOKEN_EXPIRES`). A bare `claude` reads that credential — the
    # HTTPS_PROXY CONNECT password / CLAUDE_CODE_OAUTH_TOKEN — ONCE at startup and
    # never re-reads it, and a reused screen is only reasserted (an adopt-create),
    # never relaunched, so once this passes the process is a corpse
    # that 407s/401s every turn while still alive. The DeviceChannel stamps it at
    # open time and folds it into the reuse decision (retire + reopen past it),
    # and the zero-output fuse reads it to fast-fail with the true reason (#388).
    # `None` = never recorded (a screen adopted after a server restart, or a dev
    # token with no decodable expiry) → treated as fresh, never retired on it.
    credential_expires: int | None = None
    agent_configuration: str = ""
    execution_target: dict | None = None
    closing: bool = False
    viewers: set[ViewerTransport] = field(default_factory=set)
