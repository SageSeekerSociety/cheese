"""The broker an api pool process has: what it publishes goes to the realtime
process's broker, through Valkey.

The broker is one process's fan-out (`broker.InProcessBroker`): the room sockets
it feeds are all on the realtime process, and so is every turn it tracks. A
pool process (`core/process_role`) answers plain reads, but a read can still
write — a block that lands, a notice staged for after commit — and those
publish. Here they are sent on, in the order they were made, to
`RELAY_CHANNEL`; the realtime process takes each one in (`relay_into`) exactly
as if it had published it itself.

Delivery keeps the broker's own promise and no more: at most once. A frame
lost on the way is lost the way a frame to a socket that just dropped is lost;
the next frame or the stored block carries the whole state again.

Everything else the broker answers — who listens, which turns run, the replay
buffer, typing, a session's last answer — is the realtime process's own and
does not exist here. Asking for it raises `ProcessRoleError`: an answer made
up from an empty broker would be wrong without saying so.
"""

import asyncio
import contextlib
import json
import logging
from collections.abc import Callable
from typing import Any

from app.core.process_role import ProcessRoleError
from app.core.redis import get_redis_client
from app.domain.agent.realtime.subscriber_queue import Frame

logger = logging.getLogger("cheesex.runtime")

#: The Valkey pub/sub channel pool processes publish on and the realtime
#: process listens to.
RELAY_CHANNEL = "cheesex:broker-relay"


def encode(op: dict[str, Any]) -> str:
    # `default=str`, as a socket serialises a frame (`subscriber_queue`): a
    # value JSON cannot carry fails at the socket, not at its publisher.
    return json.dumps(op, ensure_ascii=False, separators=(",", ":"), default=str)


class RelayBroker:
    """The broker of a pool process: publishing, relayed; nothing to read."""

    def __init__(self) -> None:
        # Listeners register at import (`project_feed`); a pool process has no
        # turns starting or ending, so they are never called here.
        self.busy_changed: list[Callable[[str], None]] = []

    async def _send(self, op: dict[str, Any]) -> None:
        client = get_redis_client()
        if client is None:
            logger.warning("broker_relay_unavailable op=%s", op.get("op"))
            return
        try:
            await client.publish(RELAY_CHANNEL, encode(op))
        except Exception:
            # Publishing never fails its caller (the in-process broker cannot
            # either): the frame is lost, as one to a dropped socket is.
            logger.exception("broker_relay_publish_failed op=%s", op.get("op"))

    async def publish(self, channel: str, frame: Frame) -> None:
        await self._send({"op": "publish", "channel": channel, "frame": frame})

    async def typing(self, channel: str, member: str, *, active: bool) -> None:
        await self._send(
            {"op": "typing", "channel": channel, "member": member, "active": active}
        )

    def has_subscribers(self, channel: str) -> bool:
        # Whether anyone listens is the realtime process's to know. Answering
        # yes costs a relayed frame nobody reads; answering no would drop one
        # somebody does.
        return True

    def any_subscribed(self, prefix: str) -> bool:
        return True

    def __getattr__(self, name: str) -> Any:
        raise ProcessRoleError(
            f"an api pool process has no realtime broker state (asked for {name!r})"
        )


async def apply(broker: Any, raw: bytes | str) -> None:
    """Take one relayed operation into the realtime process's broker."""
    op = json.loads(raw)
    kind = op.get("op")
    if kind == "publish":
        await broker.publish(str(op["channel"]), op["frame"])
    elif kind == "typing":
        await broker.typing(
            str(op["channel"]), str(op["member"]), active=bool(op["active"])
        )
    else:
        logger.warning("broker_relay_unknown_op op=%s", kind)


async def relay_into(broker: Any, *, retry_s: float = 1.0) -> None:
    """Listen for what pool processes publish, for as long as this process
    runs, and take it into ``broker``. A lost connection is dialled again; what
    was published meanwhile is lost (at most once, as above)."""
    client = get_redis_client()
    if client is None:
        return
    while True:
        pubsub = client.pubsub(ignore_subscribe_messages=True)
        try:
            await pubsub.subscribe(RELAY_CHANNEL)
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                try:
                    await apply(broker, message["data"])
                except Exception:
                    logger.exception("broker_relay_apply_failed")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("broker_relay_listen_failed")
            await asyncio.sleep(retry_s)
        finally:
            with contextlib.suppress(Exception):
                await pubsub.aclose()
