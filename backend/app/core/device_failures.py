"""A machine that could not be reached: the failures and the headers they send.

``DeviceOffline`` and its subclasses are raised by the device link and by every
call that goes through it, and the two processes that answer with one — the
business backend (``main.py``) and the connection owner
(``device_connection_app.py``) — both answer 409 with ``X-Device-Id``, so a
client can tell 「the machine is not there」 from 「the call went wrong」.

They live in ``app.core`` because the thing that registers those handlers is the
error tree, and the error tree is core: this is the vocabulary the layer below
the domains owns, and the domains import it rather than the other way round.
``app/domain/agent/device_contract.py`` is the device side of the same words —
it adds what only the link needs (``DeviceUnreachable``, ``DeviceNotReady``,
``HubScreen``, ``ViewerTransport``, the timeouts) and re-exports these, so
``from app.domain.agent.device_hub import DeviceOffline`` keeps working.
"""


class DeviceOffline(RuntimeError):
    """The device has no live link, so a frame to it would go nowhere.

    Raised by the awaited calls (``exec``) instead of letting them wait out
    their timeout: ``HubDevice.send`` drops a frame to a device with no
    transport, and a caller that then waits 35s and reports "the connector did
    not answer" has described the opposite of what happened."""

    def __init__(self, device_id: str) -> None:
        super().__init__(f"device {device_id} is offline")
        self.device_id = device_id


class LinkInterrupted(DeviceOffline):
    """链路断在一次调用的半路：那一帧已经出去了，答复回不来了。

    那台机器可能已经把这件事做完了，也可能没有，这一侧不知道。所以它不能像
    ``DeviceUnreachable`` 那样被当成「确定没发生」，也不该说成「机器不在」：
    链路断了的机器多半几秒后就回来（``RECONNECT_GRACE_S``），下一次调用照样
    能用。告诉调用方的只有一件事 —— 这一次的结果不知道。
    """


class DeviceCallError(RuntimeError):
    """The machine answered a call with a failure of its own.

    「dial unix …sock: no such file」 is the connector saying the runner's
    socket is not there yet; 「lstat …/.cheese/executor: no such file」 that the
    home it was asked about is gone. Those are answers, and the message is the
    whole of what the person in the room can act on. Raised as a bare
    RuntimeError they were the owner's unhandled 500, the backend's unhandled
    500 on top of it, and two alerts describing this server for every one the
    machine sent — 17 alerts folding 29 more repeats on 2026-09-18 alone, with
    the machine's words cut out of the ones that reached the room.

    A subclass, so every ``except RuntimeError`` that already waits one of
    these out keeps doing so.
    """

    def __init__(self, message: str, *, failure_code: str | None = None) -> None:
        super().__init__(message)
        self.failure_code = failure_code


def offline_headers(exc: DeviceOffline) -> dict[str, str]:
    """The headers a 409 for ``exc`` carries, so the client can name the machine.

    ``X-Device-Link`` is added only when the link broke mid-call, because that
    is the case waiting does not fix."""
    headers = {"X-Device-Id": exc.device_id}
    if isinstance(exc, LinkInterrupted):
        headers["X-Device-Link"] = "interrupted"
    return headers
