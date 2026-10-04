"""What a live screen is compared against, to decide whether it can be reused.

A screen's session reads what it was started with once and keeps it, so a turn
that finds one running asks whether that is still what the backend would start
today (``launch_identity``), and a screen a turn already brought up to date is
known by what it was brought up to date with (``settled_with``). What this
process knows of each, it keeps in a ``ScreenLedger``.
"""

import dataclasses
import hashlib
import json
import uuid

from app.core.sandbox_auth import scoped_token_claims
from app.domain.agent import machine_launcher
from app.domain.agent.harness.launch import MachinePlace
from app.domain.agent.place import footprint_root


def launch_identity(
    *,
    agent_configuration: str,
    harness_contract: str,
    execution_target: dict | None,
) -> str:
    """What a live session is compared against to decide it still matches what
    the backend would start today.

    Everything a running process cannot adopt without being restarted, in one
    value: the model and role it was born with, the harness's whole launch
    (``MachineLaunch.contract``), the platform's half of the launcher, the
    executor it was handed, and the directory the platform installed itself
    into. Anything left out is a change that lands in the code and never
    reaches the rooms already running — a pinned harness version once moved
    while every reused screen kept the one it started with, and later a new
    shell prefix reached no room that was already open.
    """
    return hashlib.sha256(
        json.dumps(
            {
                "agent": agent_configuration,
                "harness": harness_contract,
                # The platform half has no per-room content of its own: that
                # arrives as environment, so with empty holes it is the same
                # script for every room.
                "launcher": hashlib.sha256(
                    machine_launcher.launch_script(command="").encode()
                ).hexdigest(),
                "target": execution_target,
                "root": footprint_root(),
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()


def settled_with(*, agent_configuration: str, place: MachinePlace, token: str) -> str:
    """What a screen brought fully up to date was brought up to date with.

    Within one process the launch identity (``launch_identity``) is a function
    of the agent configuration and the place alone — the harness's launch, the
    launcher and the install root are this process's code — so the same value
    here means the same identity, without building the launch to find out.
    The credential's binding stands for the token file the full path rewrites:
    the same room generation, session and lease mean the file already holds a
    credential that is admitted.
    """
    claims = scoped_token_claims(token) or {}
    return hashlib.sha256(
        json.dumps(
            {
                "agent": agent_configuration,
                "place": dataclasses.asdict(place),
                "binding": {
                    key: value
                    for key, value in claims.items()
                    if key not in ("iat", "exp")
                },
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()


class ScreenLedger:
    """What this process brought each live screen up to date with, and which
    seats owe a relaunch it put off. Memory only: a backend that takes the
    rooms over compares every screen again (`RoomSessions.unchecked`)."""

    def __init__(self) -> None:
        # sid → what a turn here brought that screen up to date with
        # (``settled_with``), and when the credential it wrote expires.
        self._settled: dict[str, tuple[str, int]] = {}
        # (topic, acting agent) whose session is not what the backend would
        # start today, its relaunch put off because it was working: the room
        # brings it up to date once it goes quiet (`agent.prewarm`) instead of
        # on the next message.
        self._owed: set[tuple[uuid.UUID, str]] = set()

    def current(self, sid: str, settled_with: str, *, valid_after: int) -> bool:
        """Brought up to date with exactly this, on a credential still good
        past ``valid_after``."""
        settled = self._settled.get(sid)
        return (
            settled is not None
            and settled[0] == settled_with
            and settled[1] > valid_after
        )

    def record(
        self,
        sid: str,
        seat: tuple[uuid.UUID, str],
        settled_with: str,
        token: str,
        *,
        settled: bool = True,
    ) -> None:
        """A turn here brought the screen up to date, or put its relaunch off."""
        if not settled:
            self._owed.add(seat)
            return
        claims = scoped_token_claims(token) or {}
        self._settled[sid] = (settled_with, int(claims.get("exp") or 0))
        self._owed.discard(seat)

    def forget(self, sid: str) -> None:
        self._settled.pop(sid, None)

    def owes(self, seat: tuple[uuid.UUID, str]) -> bool:
        return seat in self._owed
