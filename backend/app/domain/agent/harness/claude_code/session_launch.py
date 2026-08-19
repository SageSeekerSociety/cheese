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

import json
from dataclasses import dataclass
from pathlib import Path

from app.domain.agent import clone
from app.domain.agent.harness.claude_code.cli import CLAUDE_BASE_CMD
from app.domain.agent.harness.claude_code.hooks_substrate import hooks_settings

# THE isolation boundary between two claudes on one machine: claude reads AND
# writes its config — settings.json, .claude.json, the transcripts --resume
# reads — under this directory, and never falls back to $HOME/.claude when it is
# set. HOME can stay shared; this cannot.
CONFIG_DIR_ENV = "CLAUDE_CONFIG_DIR"

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
        env={CONFIG_DIR_ENV: config_dir},
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
