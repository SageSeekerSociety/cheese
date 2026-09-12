"""一次启动的两半：机器说「在哪」，harness 说「跑什么」。

A launch is the one moment the two axes have to meet. A channel knows where a
screen keeps its files and what the session's cwd is; it does not know what
belongs there — and a channel that decides can only ever host the one harness it
was written against. A harness knows the command, the environment and the files
that must be on disk before the process starts; it does not know where any
machine put them.

So neither side answers alone. The channel hands over a ``ScreenPlace``, the
harness hands back a ``LaunchSpec``, and ``LaunchPlan`` is the sentence between
them: *tell me what to run, now that I have said where*.

Nothing below mentions Claude Code, which is why it lives here and not inside
the adapter that happens to be the first thing to satisfy it. The whole point of
the seam is that a transport can hold a launch without being able to read it.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class ExecutorLaunch(Protocol):
    """Harness-owned installation and history transfer over a device transport."""

    def payload_for(self, project_id, resource_id, env: dict) -> dict: ...

    def script(self, project_id, resource_id, env: dict) -> str: ...

    async def transfer_history(
        self, hub, source, center, project, resource, resume: str
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class SessionFile:
    """One file the harness reads at launch, named relative to its state dir.

    ``mode`` is carried because the two sides of the mount are different users:
    the backend plants these and the sandbox's process rewrites some of them, so
    a file it must be able to replace has to be writable by both.
    """

    name: str
    content: str
    mode: int


@dataclass(frozen=True, slots=True)
class LaunchSpec:
    """Everything a channel needs to start a harness, and nothing else.

    A channel that can honour these three has that harness on it; what it does
    with them — a tmux session in a container, a launcher shipped to someone's
    machine — is its own business, and this type is where that stops being the
    harness's problem.

    ``env`` is only the part the harness itself reads. A channel adds its own
    wiring to it before starting the session, because the session takes ONE
    environment and both halves have to be in it.
    """

    command: str
    env: dict[str, str]
    files: tuple[SessionFile, ...]


@dataclass(frozen=True, slots=True)
class ScreenPlace:
    """Where a screen keeps a harness's state, as both sides of a mount see it.

    The machine's half of a launch, and the only thing a harness gets told about
    the machine. ``state_dir`` and ``workdir`` are paths AS THE SCREEN SEES
    THEM: they go into the command and the environment, so the session's own
    view is the only one that resolves.

    ``state_at`` is that same state directory as the BACKEND can read it, when
    it can at all. A harness that decides anything by looking at what a previous
    session left behind needs the path on this side of the mount — and a
    transport where the backend simply cannot reach it answers None, rather than
    a path that exists only somewhere else.
    """

    state_dir: str
    workdir: str
    state_at: Path | None = None


class LaunchPlan(Protocol):
    """跑什么 —— 在任何机器说「在哪」之前。

    Handed to a channel in place of the loose values it used to take and turn
    into a command itself. ``at`` is the whole of it: the channel calls it with
    the one thing it knows, and gets back a launch it never has to understand —
    which is what lets the same channel host whichever harness was asked for.

    The three below are READ-ONLY, and readable at all only because a transport
    whose launch is not a command — a remote machine built entirely out of a
    shell script it writes — still has to put the same three things into that
    script. Reading them is the part of this seam nobody has paid off yet, and a
    channel that reads them can host exactly one harness. Writing them was never
    on the table: a transport that could edit the prompt or swap the model would
    make what ran differ from what the turn asked for, with nothing left to say
    where the change came from.
    """

    @property
    def system_prompt(self) -> str: ...

    @property
    def model(self) -> str | None: ...

    @property
    def resume_session_id(self) -> str | None: ...

    @property
    def execution(self) -> ExecutorLaunch: ...

    def at(self, place: ScreenPlace) -> LaunchSpec:
        """The launch, now that the machine has said where."""
        ...
