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

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable


class ExecutorLaunch(Protocol):
    """Harness-owned installation and history transfer over a device transport."""

    def can_prepare(self, info: dict) -> bool: ...

    def payload_for(
        self, project_id, resource_id, env: dict, known_files: dict | None = None
    ) -> dict: ...

    def script(self, project_id, resource_id, env: dict) -> str: ...

    def private_script(self, target: dict, env: dict) -> str: ...

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
class MachinePlace:
    """机器说「在哪」—— 一台设备这一侧的那一半。

    Everything here is a fact about the room and the machine, never an
    instruction about what to do with it: this room has a git remote, an
    execution target, a CA to trust, an operator who may drive it directly. One
    harness turns those into a settings file and a version floor; the next one
    ignores most of them. A channel that decided which is which could only ever
    host the harness it was written against.

    ``home`` and ``workdir`` are as the SESSION sees them; ``state`` is as the
    CONNECTOR resolves it (a literal ``$HOME/...``), because that is the string
    the backend records and later derives a socket from.
    """

    home: str
    workdir: str
    state: str
    api_base: str
    project_id: str
    topic_id: str
    agent_handle: str
    git_remote: str | None = None
    execution_target: dict | None = None
    remote_control: bool = False
    ca_pem: str = ""


@dataclass(frozen=True, slots=True)
class MachineLaunch:
    """跑什么 —— 平台骨架上那几段，加上只有这个 harness 自己读的 env。

    The五段 are named for WHEN they run on the machine, which is the only thing
    the platform half knows about them; ``machine_launcher`` documents each.
    ``env`` is the harness's own: a channel merges it into the one environment
    the session is started with, without having to read a line of it.
    """

    command: str
    contract: str = ""
    staging: str = ""
    configure: str = ""
    credentials: str = ""
    prepare: str = ""
    env: dict[str, str] = field(default_factory=dict)


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


@runtime_checkable
class MachinePlan(Protocol):
    """跑什么 —— 在任何机器说「在哪」之前。

    Handed to a channel in place of the loose values it used to take and turn
    into a command itself. ``on`` is the whole of it: the channel calls it with
    the one thing it knows, and gets back a launch it never has to understand —
    which is what lets the same channel host whichever harness was asked for.

    The three read-only values below are readable at all only because a
    transport whose launch is not a command still has to put the same three
    things into the script it writes. Writing them was never on the table: a
    transport that could edit the prompt or swap the model would make what ran
    differ from what the turn asked for, with nothing left to say where the
    change came from.

    This is everything a MACHINE needs. What a room running on an assigned
    executor needs on top of it is ``LaunchPlan`` below — a harness that runs
    where the files are does not have to answer for an executor it never uses.
    """

    # WHICH harness this plan is for. A channel reads it to NAME things — the
    # directory a machine keeps this session's state in, the row that says what
    # is running there — never to decide what to do, which is the whole point of
    # the two methods below.
    @property
    def harness(self) -> str: ...

    @property
    def system_prompt(self) -> str: ...

    @property
    def model(self) -> str | None: ...

    @property
    def resume_session_id(self) -> str | None: ...

    def on(self, place: MachinePlace) -> MachineLaunch:
        """The launch, for a machine whose launch is a shell script.

        A harness that has to FIND its binary and decide what resuming means
        can only do that on the machine, so what a device gets is a script it
        runs rather than a command we assembled."""
        ...


@runtime_checkable
class ExecutorPlan(Protocol):
    """在一台单独的执行机上开工，需要的那一半。

    A room whose work happens on an assigned executor needs its harness to
    install one and to move a conversation onto it. A harness that runs where
    the files already are has neither to offer, and saying so by not satisfying
    this is more honest than a method that raises.

    Deliberately smaller than ``LaunchPlan``: Codex prepares an executor and
    then starts its own runner over ``hub.exec``, never opening a screen, so
    what it hands this route is these two values and nothing else.
    """

    @property
    def execution(self) -> ExecutorLaunch: ...

    @property
    def resume_session_id(self) -> str | None: ...


@runtime_checkable
class LaunchPlan(MachinePlan, ExecutorPlan, Protocol):
    """两边都答得上来的计划：一台机器要的，加上一台执行机要的。

    ``at`` belongs to the transport that hands a container files and a command
    rather than a script — the shape a harness answers when the backend shares
    a filesystem with the screen.
    """

    def at(self, place: ScreenPlace) -> LaunchSpec:
        """The launch, now that the machine has said where."""
        ...
