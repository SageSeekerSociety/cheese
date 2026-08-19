"""怎么在一块屏幕上把 claude 开起来，以及它开机前要在盘上看到什么。

Symmetric to ``device_launch.build_screen_launch``, for a screen the backend
shares a filesystem with: the argv, the env keys ``claude`` itself reads, and
the files it reads exactly once at launch. A channel takes the three and does
its own transport with them — write the files where its mount points, pass the
env the way its sessions take env, run the command.

Every line here is a fact about Claude Code and none is a fact about a
transport, which is the whole reason it moved. It used to live inside the tmux
backend, tangled with `docker exec tmux new-session -e`, so the second
transport that wanted a claude would have copied it and the second HARNESS
could not have replaced it without editing a docker call.

The three files are not belt-and-braces:

- ``settings.json`` wires the COMMAND hooks — this harness's only sense organ.
- ``.claude.json`` pre-accepts the first-launch dialogs. With
  ``CLAUDE_CONFIG_DIR`` set, claude reads AND writes its config under THAT
  directory and never falls back to ``$HOME`` (verified on the device path), so
  an image that bakes the gates into ``$HOME`` does not help: the onboarding
  dialog eats the first prompt, the pane never reaches ``❯``, and the turn dies
  at the ready handshake with nothing saying why. The trust entry names the
  session's OWN cwd for the same reason — a baked file cannot know it.
- the system prompt file, which ``--append-system-prompt-file`` points at.

All three are read ONCE, at launch: a session that is merely reused keeps what
it started with. They are (re)written every turn anyway, because the write is
for the NEXT fresh session — a container rebuild, a crash — and a stale prompt
served to that one is the failure this prevents.
"""

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.domain.agent import clone
from app.domain.agent.harness import CLAUDE_CODE
from app.domain.agent.harness.claude_code.cli import CLAUDE_BASE_CMD
from app.domain.agent.harness.claude_code.hooks_substrate import hooks_settings

# THE isolation boundary between two claudes on one machine: claude reads AND
# writes its config — settings.json, .claude.json, the transcripts --resume
# reads — under this directory, and never falls back to $HOME/.claude when it is
# set. HOME can stay shared; this cannot.
CONFIG_DIR_ENV = "CLAUDE_CONFIG_DIR"

# WHAT was started on this screen, stamped into its own environment. One machine
# can host sessions of more than one harness, and after a restart the only thing
# left to tell them apart is what each session carries: a runtime that claimed a
# screen it does not drive would translate another harness's output with its own
# assembler and report it as its own. Absent means the session predates tagging,
# and only one harness existed then.
HARNESS_ENV = "CHEESE_HARNESS"

SETTINGS_FILE = "settings.json"
GATES_FILE = ".claude.json"
SYSTEM_PROMPT_FILE = "cheese-system-prompt.md"


@dataclass(frozen=True, slots=True)
class SessionFile:
    """One file claude reads at launch, named relative to its config dir.

    ``mode`` is carried because the two sides of the mount are different users:
    the backend plants these and the sandbox's claude rewrites some of them, so
    a file it must be able to replace has to be writable by both.
    """

    name: str
    content: str
    mode: int


@dataclass(frozen=True, slots=True)
class LaunchSpec:
    """Everything a channel needs to start this harness, and nothing else.

    A channel that can honour these three has a claude on it; what it does with
    them — a tmux session in a container, a launcher shipped to someone's
    machine — is its own business, and this type is where that stops being the
    harness's problem.

    ``env`` is only the part claude itself reads. A channel adds its own wiring
    to it before starting the session, because the session takes ONE
    environment and both halves have to be in it.
    """

    command: str
    env: dict[str, str]
    files: tuple[SessionFile, ...]


def build_session_launch(
    *,
    config_dir: str,
    workdir: str,
    system_prompt: str,
    model: str | None = None,
    resume_session_id: str | None = None,
    transcripts_at: Path | None = None,
) -> LaunchSpec:
    """The launch for one screen's claude.

    ``config_dir`` is where claude's own state lives AS THE SCREEN SEES IT;
    ``transcripts_at`` is that same directory as the BACKEND can read it, when
    it can. They differ whenever a mount is involved, and the resume guard needs
    the second: it must look at the transcript before deciding to ask for it.

    ``resume_session_id`` is honoured only when its transcript is actually
    there. The tmux session IS the continuity for an ordinary topic, so
    ``--resume`` exists for exactly one case (enabling clone, fusion-design §6)
    — a forked conversation written into a fresh topic's config dir. Guarding on
    the transcript is what keeps every other topic on the unchanged path:
    without one, there is nothing to resume and asking would fail the launch.
    """
    command = CLAUDE_BASE_CMD
    if system_prompt:
        command += f" --append-system-prompt-file {config_dir}/{SYSTEM_PROMPT_FILE}"
    if (
        resume_session_id
        and transcripts_at is not None
        and clone.find_transcript(transcripts_at, resume_session_id) is not None
    ):
        command += f" --resume {resume_session_id}"
    # An empty model means "use whatever this provider defaults to" — on the
    # subscription that is its own Sonnet, on the gateway the gateway's pick.
    # Never pin a name the provider does not serve.
    if model:
        command += f" --model {model}"
    return LaunchSpec(
        command=command,
        env={CONFIG_DIR_ENV: config_dir, HARNESS_ENV: CLAUDE_CODE},
        files=(
            # 0o666: the sandbox's claude rewrites both of these itself, under a
            # different uid than the backend that plants them.
            SessionFile(
                SETTINGS_FILE,
                json.dumps(hooks_settings(), ensure_ascii=False),
                0o666,
            ),
            SessionFile(GATES_FILE, _gates(workdir), 0o666),
            # Ours alone; claude only reads it.
            SessionFile(SYSTEM_PROMPT_FILE, system_prompt, 0o644),
        ),
    )


def _gates(workdir: str) -> str:
    """The first-launch dialogs, pre-accepted for this session's own cwd.

    Nothing credential-shaped is ever planted: login is via
    CLAUDE_CODE_OAUTH_TOKEN in the screen's env, NOT a `.credentials.json` —
    that file gets a local validation the env var skips, and rejected our
    placeholder as "Not logged in".
    """
    return json.dumps(
        {
            "hasCompletedOnboarding": True,
            "autoUpdates": False,
            # Legacy fallback, still honored; it migrates to
            # skipDangerousModePermissionPrompt on first run.
            "bypassPermissionsModeAccepted": True,
            "projects": {
                workdir: {
                    "hasTrustDialogAccepted": True,
                    "hasCompletedProjectOnboarding": True,
                }
            },
        },
        ensure_ascii=False,
    )


# How long a fresh `claude` gets to draw its input box, and how often to look.
# A cold start on a new container is the slow case (the launcher's first-run
# gates, then the TUI's own boot); past this the screen is not coming up and the
# turn ends with a clean error instead of hanging.
READY_TIMEOUT_S = 45.0
READY_POLL_S = 0.4


def input_box_ready(screen_text: str) -> bool:
    """True when a captured screen shows Claude Code's input box.

    The ``❯`` prompt is the ONLY signal that this TUI is ready to be typed at,
    and typing before it appears loses the prompt into a boot-time modal. Pure,
    so it costs no container to test.
    """
    return "❯" in screen_text


async def wait_for_input_box(capture: Callable[[], Awaitable[str | None]]) -> bool:
    """Poll a screen until claude's input box shows, or give up (就绪握手).

    The transport supplies the reading; how long a claude takes to come up, and
    what "up" looks like, are this side's.
    """
    deadline = asyncio.get_event_loop().time() + READY_TIMEOUT_S
    while asyncio.get_event_loop().time() < deadline:
        screen_text = await capture()
        if screen_text is not None and input_box_ready(screen_text):
            return True
        await asyncio.sleep(READY_POLL_S)
    return False


class ScreenHost(Protocol):
    """What a transport must be able to do to a screen for a claude to live on
    it. Six verbs, none of which mention Claude Code — the policy that sequences
    them (``ensure_claude``) is the part that does.
    """

    async def session_exists(self, screen: object) -> bool:
        """Is there still a session here at all?"""
        ...

    async def session_deaf(self, screen: object) -> bool:
        """Can this session still reach us? A live one that cannot is worse than
        none: it works perfectly and reports nothing."""
        ...

    async def retire_session(self, screen: object) -> None:
        """Take this session down, and say so — its conversation goes with it."""
        ...

    async def start_session(self, screen: object, launch: LaunchSpec) -> None:
        """Bring a fresh session up running ``launch``."""
        ...

    async def reclaim_session(self, screen: object) -> None:
        """Make a session that was left running usable again."""
        ...

    async def capture_session(self, screen: object) -> str | None:
        """What the screen currently shows, or None if it cannot be read."""
        ...


async def ensure_claude(host: ScreenHost, screen: object, launch: LaunchSpec) -> bool:
    """Have a claude on this screen, ready to be typed at. False = it never came
    up in time.

    A session that already exists is REUSED — it is the conversation's
    continuity, and restarting it throws that away — but only if it can still
    report. A claude reads its wiring once at exec and never again, so a session
    whose reporting path has since died is a process that works perfectly and
    tells nobody: cheaper to lose its memory than to run turns nobody can see.

    The input-box wait happens on both paths, not just the fresh one. A reused
    session can be mid-render (a previous turn's output still painting), and the
    first thing done to it either way is typing.
    """
    if await host.session_exists(screen):
        if await host.session_deaf(screen):
            await host.retire_session(screen)
            await host.start_session(screen, launch)
        else:
            await host.reclaim_session(screen)
    else:
        await host.start_session(screen, launch)
    return await wait_for_input_box(lambda: host.capture_session(screen))


def harness_of(env: dict[str, str]) -> str:
    """Which harness a session was started to run, from its own environment."""
    return env.get(HARNESS_ENV) or CLAUDE_CODE
