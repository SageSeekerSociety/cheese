"""一条设备链路的记录：``HubDevice`` 是服务端关于这条链路知道的一切。

承载它的传输、它自报的 build/target、它上一次说话的时间、还没回话的那几个
future 和它们的序号。``send`` 是这条链路唯一的写入口（一把锁，一帧接一帧），
``drop_transport`` 是链路断掉那一下：等在这条链路上的东西一起失败，因为答案只
可能从这条链路回来。

从门面里分出来，是因为要等回话的那三片（屏幕/会话、exec、本机目录授权）都要用
``_read_a_failure_nobody_awaited``。门面要导入它们，它们再回头导入门面就成环；
放在这里，它们和门面都只往下导入这一个模块。读那几个定时常量的判定仍留在门面
（``RECONNECT_GRACE_S``、``LINK_SILENCE_S``、``BUILD_CHECK_S`` 是按门面模块的
属性打补丁的），判定读写的就是这里这个记录。
"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.domain.agent.device_contract import HubScreen, LinkInterrupted
from app.domain.agent.place import ROOT_PLACEHOLDER, footprint_root


class DeviceTransport(Protocol):
    """A live device control channel. Satisfied by a ``fastapi.WebSocket`` adapter
    and trivially fakeable.

    ``send_json`` raises ``ConnectionError`` when the channel can no longer carry
    a frame; the hub then treats the device as gone."""

    async def send_json(self, msg: dict[str, Any]) -> None: ...


@dataclass
class HubDevice:
    device_id: str
    transport: DeviceTransport | None = None
    # What a person calls this machine, as the connector route knows it at attach
    # time. Kept here so a failure on the link can name the machine without a
    # database read on a path that is already failing.
    name: str = ""
    proto: int | None = None
    # What the connector said about itself in `hello`: the sha256 of its own
    # executable and the `<os>-<arch>` it was built for. Both empty from a
    # connector built before it announced either.
    build: str = ""
    target: str = ""
    executor: bool = False
    # Whether the connector said in `hello` that it expands the enrollment's
    # root in the paths it resolves itself (`spelled`).
    profiles: bool = False
    # The published build this CONNECTION has been told to update to ("" when
    # it has not been told). Reset on every attach, so a machine whose
    # self-update failed is told again the next time it dials in rather than
    # once and never again; and a build published later is a new one to tell
    # it about, so a machine that stays connected is not left on the build it
    # dialled in with. Never twice for the same build on one connection.
    update_pushed: str = ""
    # When this connection last asked whether its build is still the published
    # one (loop time), and the check under way, if any (`_check_build`).
    build_checked_at: float = 0.0
    build_check: asyncio.Task[None] | None = None
    # Last time the device sent any frame (hello/heartbeat/…).
    last_seen: float = 0.0
    # When this machine's last link dropped (loop time); None while it is linked
    # and for a machine this process has never seen linked. See
    # `RECONNECT_GRACE_S`.
    dropped_at: float | None = None
    # Set by `hello` on the current link, cleared when that link drops: what a
    # call waiting out a reconnect waits on. Hello rather than attach, because
    # what a call needs (`executor` above) is only known from hello.
    announced: asyncio.Event = field(default_factory=asyncio.Event)
    # Whether the connector on THIS connection sends heartbeats. One that does
    # and then falls silent for ``LINK_SILENCE_S`` has lost its link, whatever
    # the socket says (`silence_allowed`). Reset on every attach.
    heartbeats: bool = False
    # The frame being written to the link right now, if any (`send`): dropping
    # the link ends its wait.
    writing: asyncio.Timeout | None = None
    screens: dict[str, HubScreen] = field(default_factory=dict)
    exec_seq: int = 0

    def spelled(self, value: str) -> str:
        """``value``, a path the connector resolves itself (a screen's or a
        command's environment, a working directory, an executor's state), as
        this connector reads it. One built before enrollments had roots of their
        own expands only ``$HOME``, and is a default-profile machine, whose root
        is the footprint root: it is told that root until its update lands.
        Before ``hello`` nothing is known, and the path is left as it is: told
        `.cheese`, a named profile would work in the default profile's files."""
        if self.profiles or not self.announced.is_set():
            return value
        return value.replace(ROOT_PLACEHOLDER, footprint_root())

    # 本机目录授权: one counter for both directions of the localfs channel (a grant
    # push and a read/write/list op), since they share one result future map.
    local_fs_seq: int = 0
    exec_pending: dict[str, asyncio.Future[dict[str, Any]]] = field(
        default_factory=dict
    )
    # The exec ids whose stderr lines someone hears while they run (``exec``'s
    # ``on_stderr``), fed by ``exec.output`` frames.
    exec_listeners: dict[str, Callable[[str], None]] = field(default_factory=dict)
    executor_pending: dict[str, tuple[asyncio.Future[Any], bytearray]] = field(
        default_factory=dict
    )
    session_pending: dict[str, asyncio.Future[Any]] = field(default_factory=dict)
    local_fs_pending: dict[str, asyncio.Future[Any]] = field(default_factory=dict)
    # Streams the machine's sandboxes opened on their platform sockets, by
    # stream id (``machine``): they live on this link and end with it.
    # (``machine.MachineStream``; not imported here, which would make the two
    # modules a cycle.)
    machine_streams: dict[str, Any] = field(default_factory=dict)
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    connection_generation: int = 0

    async def send(self, msg: dict[str, Any]) -> bool:
        """Write one frame to the machine; ``False`` when there was no link.

        A caller that tells anyone what it did must not report a frame that had
        nowhere to go: ``open_screen`` used to register a screen and return it
        after a send that quietly did nothing, so the room believed the screen
        was up and its turn waited on a runner that was never started.

        A frame that raises did not go out either — the link is gone."""
        # Serialize sends to one device: a WebSocket is not safe for concurrent
        # writes, and the orchestrator, exec, and every viewer's fan-out all send
        # here. Without this, interleaved frames corrupt the channel.
        async with self.send_lock:
            transport = self.transport
            if transport is None:
                return False
            # A write to a peer that stopped reading does not fail: the server
            # waits for its buffer to drain, which on a machine that went to
            # sleep behind the proxy is until the socket finally closes —
            # minutes. It holds this lock all the while, so every later frame to
            # the machine, a reconnect's welcome included, queues behind it. The
            # link being dropped is what ends that wait.
            stall = asyncio.timeout(None)
            try:
                async with stall:
                    self.writing = stall
                    try:
                        await transport.send_json(msg)
                    finally:
                        self.writing = None
            except TimeoutError:
                if stall.expired():
                    raise LinkInterrupted(self.device_id) from None
                raise
            except ConnectionError as exc:
                # The receive loop only learns of a dead link when the peer says
                # so; a peer that vanished never does, and then the socket stays
                # attached for good — every caller that trusts ``is_online`` sends
                # into it and fails, once a minute for a storage sweep. A failed
                # send is the proof the receive loop never gets.
                self.drop_transport(transport)
                raise LinkInterrupted(self.device_id) from exc
            return True

    def drop_transport(self, transport: DeviceTransport) -> bool:
        """Forget ``transport`` if it is still the live one; fail what waited on it.

        Everything that waits on an answer from the machine fails here, at once:
        an answer can only come back over this link, so a call left waiting
        would wait out its own timeout — eleven minutes for an executor install
        — for nothing."""
        if self.transport is not transport:
            return False
        self.transport = None
        self.dropped_at = asyncio.get_running_loop().time()
        self.announced.clear()
        if self.writing is not None:
            # Expire it now: the write is abandoned and ``send`` says offline.
            self.writing.reschedule(0)
        waiting = [
            *(future for future, _ in self.executor_pending.values()),
            *self.session_pending.values(),
            *self.exec_pending.values(),
            *self.local_fs_pending.values(),
        ]
        for future in waiting:
            if not future.done():
                future.set_exception(LinkInterrupted(self.device_id))
        # The connector closed their sockets when the link went: nobody is
        # left to read an answer.
        for stream in self.machine_streams.values():
            if stream.task is not None:
                stream.task.cancel()
        self.machine_streams.clear()
        return True


def _read_a_failure_nobody_awaited(future: asyncio.Future[Any]) -> None:
    """A call registers its future and then writes to the link; when the write
    itself finds the link dead, ``drop_transport`` fails that very future and
    the write raises ``DeviceOffline`` — so the caller leaves through the send
    and never awaits the future it registered. asyncio reports that at garbage
    collection as 「Future exception was never retrieved」, an ERROR with no
    route and a traceback into the collector, for a device the caller already
    reported offline. Reading the exception here is what makes that untrue;
    a future the caller did await answers the same thing twice for free."""
    if future.done() and not future.cancelled():
        future.exception()
