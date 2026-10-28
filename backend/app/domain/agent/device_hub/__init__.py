"""``DeviceHub`` — the server end of the frozen ``link.Msg`` protocol (P3).

One long-lived control channel per connected device, over which the server opens
*screens* (each running a harness's runner), relays a screen's raw terminal
bytes to browser viewers (relayed, never parsed), reaches a screen's runner
(``call_executor``), and runs one-shot ``exec`` on the device. The wire shape is
the ``cli/`` ``link.Msg`` contract (``device_link``).

Ported from the reference ``app/agent/hub.py`` and kept I/O-free: it depends only on
two tiny transport Protocols, so it is exercised with in-process fakes — no
WebSocket, no device, no DB. What the agent *does* is read from its runner's
journal, NOT from the screen, so the raw screen channel here serves only the
human viewer.

Our adaptations vs the reference:
  * a screen carries our identity shape — ``agent_user_id: uuid`` + ``agent_handle``
    (the authorship key) — plus its ``project_id``/``topic_id``.

**This package is the hub's facade, and the hub is split by responsibility.** One
file held all of it until 2026-10-16; the bodies moved into the sibling modules
of this package, and what stays in ``__init__.py`` is what the tree patches or
imports by *this* module:

  * the timing constants — ``RECONNECT_GRACE_S``/``LINK_SILENCE_S``/
    ``BUILD_CHECK_S`` are monkeypatched on this module object, so every decision
    that reads one (``_linked``, ``reconnecting``, ``silence_allowed``,
    ``_check_build``) is here, next to the constant it reads;
  * the public surface of ``DeviceHub`` — the remote hub must have every public
    method this class has (``test_remote_hub_answers_every_hub_call.py``), so
    each one stays in the class body as a one-line delegation;
  * the other half of the same rule — the names the tree reaches through an
    *object* rather than this module. The audit takes them off
    ``vars(DeviceHub)`` (``test_remote_hub_answers_every_hub_call.py``); the
    hub's own tests call ``_device`` and ``attach_device``/``detach_device`` on
    an instance (``test_device_hub.py``); a test asks ``screens_in_project``
    whether it goes through the ``is_online`` it just put on one
    (``test_a_replaced_hub_method_is_what_the_hub_uses.py``), and a stand-in hub
    answers ``all_online_screens`` on one (``test_session_records_its_machine.py``,
    ``test_own_agent_machine.py``);
  * the singleton at the bottom, and the failures/screen shape re-exported from
    ``device_contract``.

Where each moved piece went: ``link`` (one link's record — ``HubDevice``, its
write and its drop), ``sessions`` (screens), ``calls`` (``exec`` and
``call_executor``), ``local_fs`` (本机目录授权), ``viewers`` (browser viewers) and
``inbound`` (frames coming back). A forwarding method takes no decision — that is
what keeps the two copies from drifting apart, and it is why every body that does
decide stayed put. The forwarders carry no docstring of their own, for the same
reason: the prose lives on the module function they call (``feedback/services``
does it this way too).

**A package rather than six flat siblings.** C3 (``sibling domains under
app.domain are acyclic``) counts cycles among ``app.domain``'s children and among
theirs, and these bodies call into each other — ``viewers`` asks ``sessions`` for
a screen, ``inbound`` asks ``link`` to drop one — which as flat modules under
``app.domain.agent`` is five new pairs, counting the type-only import of this
module back into the modules it forwards to. ``check_boundaries.py`` says exactly
that about the flat shape. A child of this package importing its own package is
not one of them, so the internal cycle stays inside: it is the hub's own bodies
sharing the hub's state, not the cross-feature coupling C3 is there to catch.
``app/domain/feedback/services`` took the same shape for the same reason;
``device_hub_rpc.py`` stays the flat sibling it was.
"""

import asyncio
import logging
import uuid
from collections.abc import Callable
from typing import Any

from app.domain.agent import connector_build, device_link
from app.domain.agent.device_contract import (
    EXEC_REPLY_SLACK_S,  # noqa: F401
    RECONNECT_GRACE_S,
    DeviceCallError,  # noqa: F401
    DeviceNotReady,  # noqa: F401
    DeviceOffline,
    DeviceUnreachable,  # noqa: F401
    HubScreen,
    LinkInterrupted,  # noqa: F401
    ViewerTransport,
    offline_headers,  # noqa: F401
)
from app.domain.agent.device_hub import calls, inbound, local_fs, sessions, viewers
from app.domain.agent.device_hub.link import DeviceTransport, HubDevice

# The failures, the screen shape, the call timings and ``offline_headers`` are
# re-exported from ``device_contract`` — they were written down here, so
# importing them from here keeps working, but the module that hands the hub's
# HTTP facade its screens cannot import the hub itself (C3). The one rule for
# how a ``DeviceOffline`` crosses HTTP lives in ``core/device_failures``, where
# the error tree that answers with it can use it too.
#
# The `noqa: F401` ones are no longer read here, and stay so this module's name
# set does not shrink: drop one and ``from app.domain.agent.device_hub import
# <它>`` starts failing at import time. Same rule as ``gateway_admin``. What
# does not come back is the private helper a moved function carried off
# (``_read_a_failure_nobody_awaited``, now in ``link`` next to
# ``drop_transport``, which is what fails those futures): it is not this
# module's vocabulary, and leaving a copy here would be a patch point nothing
# reads.

logger = logging.getLogger(__name__)


# The hub's own clock, and the version it speaks. Kept here rather than moving
# with the code that reads them: a test shortens a wait by patching this
# module's attribute (`tests/support/fast_timings.py` names the module for
# ``LINK_SILENCE_S``), so the read has to happen here too — see the class
# docstring. ``PROTOCOL_VERSION`` reads the same value the inbound dispatcher
# does (`device_link.PROTOCOL_VERSION`); it stays as the module's own name.
PROTOCOL_VERSION = device_link.PROTOCOL_VERSION

# How often a connector says it is there (`pingPeriod` in
# cli/internal/link/link.go), and how long its link may go unheard before it is
# taken as lost: three missed beats (`DeviceHub.silence_allowed`).
HEARTBEAT_S = 15.0
LINK_SILENCE_S = 3 * HEARTBEAT_S

# How often a machine that stays connected is asked again whether its connector
# is the published one (`DeviceHub._check_build`): as often as the published
# answer can change (`connector_build.PUBLISHED_TTL_S`).
BUILD_CHECK_S = connector_build.PUBLISHED_TTL_S


class DeviceHub:
    def __init__(self) -> None:
        self._devices: dict[str, HubDevice] = {}
        self._screens: dict[str, HubScreen] = {}  # sid -> screen (across devices)
        self._by_screen_token: dict[str, HubScreen] = {}

    def _device(self, device_id: str) -> HubDevice:
        return self._devices.setdefault(device_id, HubDevice(device_id=device_id))

    async def _linked(self, device: HubDevice) -> bool:
        """Whether ``device`` has a link to send on, waiting out a reconnect.

        A machine whose link dropped less than ``RECONNECT_GRACE_S`` ago is
        waited for until it says hello again; one that has been away longer, or
        that this process has never seen linked (it started after the machine
        went, or the machine never came), is answered at once. Each caller then
        raises exactly what it raised before for a machine with no link.

        A machine that has dialled back in but not yet said hello is still on
        its way: what it can run is only known from that hello."""
        if device.dropped_at is None:
            return device.transport is not None
        if device.transport is not None and device.announced.is_set():
            return True
        loop = asyncio.get_running_loop()
        remaining = RECONNECT_GRACE_S - (loop.time() - device.dropped_at)
        if remaining > 0:
            try:
                async with asyncio.timeout(remaining):
                    await device.announced.wait()
            except TimeoutError:
                pass
        return device.transport is not None

    # -- device connection -------------------------------------------------

    async def attach_device(
        self, device_id: str, transport: DeviceTransport, *, name: str = ""
    ) -> None:
        device = self._device(device_id)
        if device.transport is not None and device.transport is not transport:
            device.drop_transport(device.transport)
        device.transport = transport
        device.heartbeats = False
        device.connection_generation += 1
        device.executor = False
        device.profiles = False
        if name:
            device.name = name
        device.update_pushed = ""
        await device.send(device_link.welcome())

    async def detach_device(self, device_id: str, transport: DeviceTransport) -> None:
        device = self._devices.get(device_id)
        if device is not None:
            device.drop_transport(transport)

    def is_online(self, device_id: str) -> bool:
        device = self._devices.get(device_id)
        return device is not None and device.transport is not None

    def reconnecting(self, device_id: str) -> bool:
        """Whether the machine has no link but lost it less than
        ``RECONNECT_GRACE_S`` ago: a call to it now waits for it to come back
        (``_linked``) rather than failing, so whoever decides whether to make
        that call should not read it as away."""
        device = self._devices.get(device_id)
        if device is None or device.transport is not None:
            return False
        if device.dropped_at is None:
            return False
        elapsed = asyncio.get_event_loop().time() - device.dropped_at
        return elapsed < RECONNECT_GRACE_S

    def silence_allowed(self, device_id: str) -> float | None:
        """How long the link may go without a frame from the machine before it
        is taken as lost; None when silence says nothing about it.

        The socket cannot answer this. A machine that sleeps, or loses its
        network, behind the proxy leaves the proxy holding the connection open;
        the server's keepalive notices, but closing a socket whose buffer will
        not drain waits until the proxy gives up, which is when the machine
        wakes. Until then the machine read as online, every call to it waited
        for an answer that could not come, and the room's tools hung for as
        long as the machine slept. A connector that heartbeats is heard from
        every ``HEARTBEAT_S``; one that is not heard from for three beats is
        gone. A connector that has sent none on this connection is one built
        before heartbeats, and it is told to update when it says hello.
        """
        device = self._devices.get(device_id)
        if device is None or not device.heartbeats:
            return None
        return LINK_SILENCE_S

    def online_device_ids(self) -> list[str]:
        return [d.device_id for d in self._devices.values() if d.transport is not None]

    def device_name(self, device_id: str) -> str:
        """The machine's name as announced at attach, or its id when unknown."""
        device = self._devices.get(device_id)
        return device.name if device is not None and device.name else device_id

    def target(self, device_id: str) -> str:
        """The `<os>-<arch>` the machine's connector said it was built for, or
        "" before this process has heard its `hello`."""
        device = self._devices.get(device_id)
        return device.target if device is not None else ""

    def last_seen_age(self, device_id: str) -> float | None:
        """Seconds since the device last sent any frame; None when it never has."""
        device = self._devices.get(device_id)
        if device is None or not device.last_seen:
            return None
        return asyncio.get_event_loop().time() - device.last_seen

    # -- screens (server -> device) ----------------------------------------

    async def open_screen(
        self,
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
        return await sessions.open_screen(
            self,
            device_id,
            command,
            agent_user_id=agent_user_id,
            agent_handle=agent_handle,
            project_id=project_id,
            topic_id=topic_id,
            env=env,
            cols=cols,
            rows=rows,
        )

    async def reassert_screen(
        self,
        screen: HubScreen,
        *,
        command: list[str],
        env: dict[str, str] | None = None,
        cols: int = 120,
        rows: int = 32,
    ) -> None:
        await sessions.reassert_screen(
            self, screen, command=command, env=env, cols=cols, rows=rows
        )

    def adopt_screen(
        self,
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
        return sessions.adopt_screen(
            self,
            device_id,
            sid,
            token=token,
            agent_user_id=agent_user_id,
            agent_handle=agent_handle,
            command=command,
            project_id=project_id,
            topic_id=topic_id,
            resource_id=resource_id,
            credential_expires=credential_expires,
            agent_configuration=agent_configuration,
            execution_target=execution_target,
        )

    def update_screen(
        self,
        sid: str,
        *,
        resource_id: uuid.UUID | None,
        execution_target: dict | None,
        credential_expires: int | None = None,
        agent_configuration: str | None = None,
    ) -> HubScreen:
        return sessions.update_screen(
            self,
            sid,
            resource_id=resource_id,
            execution_target=execution_target,
            credential_expires=credential_expires,
            agent_configuration=agent_configuration,
        )

    async def close_screen(self, device_id: str, sid: str) -> bool:
        return await sessions.close_screen(self, device_id, sid)

    async def session_request(
        self, device_id: str, message: dict[str, Any], *, timeout: float = 30
    ) -> Any:
        return await sessions.session_request(self, device_id, message, timeout=timeout)

    async def list_screens(self, device_id: str) -> list[dict[str, Any]]:
        return await sessions.list_screens(self, device_id)

    def screen(self, sid: str) -> HubScreen | None:
        return sessions.screen(self, sid)

    def all_online_screens(self) -> list[HubScreen]:
        return sessions.all_online_screens(self)

    def screens_in_project(self, project_id: uuid.UUID) -> list[HubScreen]:
        return sessions.screens_in_project(self, project_id)

    def screens_for_topic(self, topic_id: uuid.UUID) -> list[HubScreen]:
        return sessions.screens_for_topic(self, topic_id)

    # -- 本机目录授权 (server -> device, awaited) ---------------------------

    async def push_local_fs_grants(
        self,
        device_id: str,
        grants: list[dict[str, Any]],
        *,
        timeout: float = 20,
    ) -> dict[str, Any]:
        return await local_fs.push_local_fs_grants(
            self, device_id, grants, timeout=timeout
        )

    async def local_fs_op(
        self,
        device_id: str,
        op: dict[str, Any],
        *,
        timeout: float = 60,
    ) -> dict[str, Any]:
        return await local_fs.local_fs_op(self, device_id, op, timeout=timeout)

    # -- exec (server -> device, awaited) ----------------------------------

    async def exec(
        self,
        device_id: str,
        argv: list[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout: float = 60,
        stdin: str | None = None,
        on_stderr: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        return await calls.exec(
            self,
            device_id,
            argv,
            cwd=cwd,
            env=env,
            timeout=timeout,
            stdin=stdin,
            on_stderr=on_stderr,
        )

    async def call_executor(
        self,
        device_id: str,
        state: str,
        method: str,
        params: dict,
        *,
        timeout: float = 660,
        trace_id: str | None = None,
    ) -> dict:
        return await calls.call_executor(
            self,
            device_id,
            state,
            method,
            params,
            timeout=timeout,
            trace_id=trace_id,
        )

    # -- viewers (browser <-> device screen) -------------------------------

    async def attach_viewer(
        self,
        device_id: str,
        sid: str,
        viewer: ViewerTransport,
        *,
        cols: int = 120,
        rows: int = 32,
    ) -> None:
        await viewers.attach_viewer(self, device_id, sid, viewer, cols=cols, rows=rows)

    async def detach_viewer(
        self, device_id: str, sid: str, viewer: ViewerTransport
    ) -> None:
        await viewers.detach_viewer(self, device_id, sid, viewer)

    async def viewer_input(self, device_id: str, sid: str, data: bytes) -> None:
        await viewers.viewer_input(self, device_id, sid, data)

    async def viewer_resize(
        self, device_id: str, sid: str, cols: int, rows: int
    ) -> None:
        await viewers.viewer_resize(self, device_id, sid, cols, rows)

    # -- device -> server (inbound) ----------------------------------------

    async def on_device_message(self, device_id: str, m: dict[str, Any]) -> None:
        await inbound.on_device_message(self, device_id, m)

    def _check_build(self, device: HubDevice) -> None:
        """Ask again, on a connection that stays up, whether the machine's build
        is still the published one: a release publishes a new connector while
        machines stay connected, and `hello` alone told only the ones that
        happened to dial in afterwards. A Cloud machine stayed on the build it
        connected with for a day, and every room on it was refused what the
        newer build would have given it.

        At most once per ``BUILD_CHECK_S``, and off the link's read loop: the
        origin can take seconds to answer, and the frames behind this heartbeat
        are the machine's terminals and calls."""
        if device.build_check is not None and not device.build_check.done():
            return
        if device.last_seen - device.build_checked_at < BUILD_CHECK_S:
            return
        device.build_checked_at = device.last_seen
        device.build_check = asyncio.create_task(self._update_if_stale(device))

    async def _update_if_stale(self, device: HubDevice) -> None:
        """Tell a machine whose connector is not the one we serve to replace it.

        Nothing else closes the gap between what the server sends and what the
        far end can receive. A connector drops a frame it does not recognise
        without answering it, so drift surfaces as a timeout somewhere
        unrelated — a call that never returned — and no error anywhere names a
        version. Left to a person to notice, a machine stays behind for as long
        as nobody looks: one ran a build two weeks older than a frame it was
        being sent, and every one of those frames went into a silence.

        Only ever on a definite answer. A connector that identifies itself but
        whose bytes we cannot compare is left alone — telling it to update on a
        half-answer would re-exec the machine on every reconnect and never
        converge, which is worse than the drift.

        The build to be on is the one the origin publishes, the file the
        machine's self-update downloads (`connector_build.published_digest`),
        not this process's own copy, which lags it between owner releases.
        """
        generation = device.connection_generation
        build, target = device.build, device.target
        if build or target:
            if not (build and target):
                return
            published = await connector_build.published_digest(target)
            if published is None or published == build:
                return
        elif await asyncio.to_thread(connector_build.has_any_build):
            # It says nothing about itself, so it predates saying anything and
            # is old by construction — but only tell it to fetch a build if we
            # have one to give it.
            published = "<any>"
        else:
            return
        # The answer is about the connection that asked; one that replaced it
        # meanwhile says hello and is asked for itself.
        if device.update_pushed == published or (
            device.connection_generation != generation
        ):
            return
        device.update_pushed = published
        logger.warning(
            "device %s runs connector build %s for %s, not the published one — "
            "pushing self-update",
            device.device_id,
            build or "<unreported>",
            target or "<unreported>",
        )
        try:
            await device.send(device_link.update())
        except DeviceOffline:
            # The link went while we asked; the machine is told when it is back.
            pass

    # Overridable seam for logging/metrics; a no-op by default.
    def _on_version_skew(self, device_id: str, proto: int | None) -> None:
        pass


# Shared singleton: the connection-owner process keeps the local implementation;
# rolling business backends use its RPC facade.
from app.core.config import settings  # noqa: E402

if settings.device_connection_url:
    from app.domain.agent.device_hub_rpc import RemoteDeviceHub

    device_hub = RemoteDeviceHub(
        settings.device_connection_url, settings.device_connection_auth_secret
    )
else:
    device_hub = DeviceHub()
