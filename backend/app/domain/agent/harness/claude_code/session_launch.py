"""怎么在一块屏幕上把 claude 开起来，以及它开机前要在盘上看到什么。

Symmetric to ``device_launch.on_machine``, for a screen the backend
shares a filesystem with: the argv, the env keys ``claude`` itself reads, and
the files it reads exactly once at launch. A channel takes the three and does
its own transport with them — write the files where its mount points, pass the
env the way its sessions take env, run the command.

It is never a channel that asks for them. ``ClaudeLaunch`` is what a channel is
handed, and all it can do with one is say where its screen keeps things and take
back a launch; the answer being Claude Code's is not something the transport
finds out.

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
  an image that bakes the gates into ``$HOME`` does not help: the fresh session
  comes up showing an onboarding dialog instead of taking input, and nothing
  says why. The trust entry names the session's OWN cwd for the same reason — a
  baked file cannot know it.
- the system prompt file, which ``--append-system-prompt-file`` points at.

All three are read ONCE, at launch: a session that is merely reused keeps what
it started with. They are (re)written every turn anyway, because the write is
for the NEXT fresh session — a container rebuild, a crash — and a stale prompt
served to that one is the failure this prevents.
"""

import json
import shlex
from dataclasses import dataclass
from pathlib import Path

from app.domain.agent import clone
from app.domain.agent.harness import CLAUDE_CODE
from app.domain.agent.harness.claude_code.cli import CLAUDE_BASE_CMD, DISALLOWED_TOOLS
from app.domain.agent.harness.launch import (
    ExecutorLaunch,
    LaunchSpec,
    MachineLaunch,
    MachinePlace,
    ScreenPlace,
    SessionFile,
)
from app.domain.agent.skills import native_skill_files

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


def hooks_settings(
    extra_stop: list[str] | None = None, *, remote_control: bool = False
) -> dict:
    """``~/.claude/settings.json`` for a hooks-driven session: pre-accept the
    bypass disclaimer AND forward every structured event to our hook endpoint via
    a COMMAND hook (``cheese-hook``).

    Command (not the built-in ``"type":"http"``) hooks: Claude Code 2.1.x BLOCKS
    HTTP hooks whose host resolves to a non-loopback / private IP, and only
    127.0.0.1/::1 are allowed — which a container / device can't use to reach the
    backend. The ``cheese-hook`` forwarder reads the hook JSON on stdin and POSTs
    it to ``CHEESE_HOOK_URL`` with the ``CHEESE_TOKEN`` header, sidestepping that.

    Shared by the local (tmux) and remote (device) backends so their perception
    wiring is one thing — change it here, both backends move together."""
    cmd = {"type": "command", "command": "cheese-hook"}
    tool_matched = [{"matcher": "*", "hooks": [cmd]}]
    plain = [{"hooks": [cmd]}]
    # A remote machine also has to hand its work back at turn end; the local
    # container edits the real worktree and has nothing to send.
    stop_hooks = [cmd] + [
        {"type": "command", "command": name} for name in (extra_stop or [])
    ]
    return {
        "skipDangerousModePermissionPrompt": True,
        # Stream liveness is separate from WebFetch's response-error handling.
        "env": {
            "CLAUDE_ENABLE_STREAM_WATCHDOG": "1",
        },
        # Previews belong in Cheese, not on claude.ai via the Artifact tool.
        "enableArtifact": False,
        # Native questions need the RC answer channel. Without it, keep the
        # matching CLI deny rule so a question cannot strand the turn.
        "permissions": {"deny": [] if remote_control else list(DISALLOWED_TOOLS)},
        # Set before the first turn: changing this later cannot remove a URL
        # already present in the conversation's model-visible history.
        **({"attribution": {"sessionUrl": False}} if remote_control else {}),
        "hooks": {
            "SessionStart": plain,
            # The consumption receipt. A prompt reaches the session over its
            # rendezvous socket, and that protocol has no positive ack: a frame
            # that was written and not refused has entered the queue, and nothing
            # on that channel says it was read. This hook fires when the session
            # takes a queued text as a user turn, so its arrival is the proof.
            # Not every build fires it for every consumption — see the note in
            # hooks_substrate's `send` about what 2.1.224 does with a text
            # delivered while a tool is running.
            "UserPromptSubmit": plain,
            "PreToolUse": tool_matched,
            "PostToolUse": tool_matched,
            # The tool call that ended in an error. Claude Code fires this
            # INSTEAD of `PostToolUse` (same shape as `StopFailure` below), so
            # without it a failed step is indistinguishable from one still
            # running: the platform sees the call start and nothing come back.
            "PostToolUseFailure": tool_matched,
            "MessageDisplay": plain,
            # A subagent's own boundaries. Its tool calls already arrive through
            # PreToolUse/PostToolUse above (those fire inside a subagent exactly
            # as on the main thread) — what they cannot say is that a worker
            # started, or which of several is speaking, because the room sees
            # one undifferentiated stream. These two carry that: the id to
            # attribute the rest by, and the closing message, which otherwise
            # reaches only the thread that spawned it.
            "SubagentStart": plain,
            # NOT part of `stop_hooks`: those hand a machine's work back at TURN
            # end, and a subagent finishing is not the turn finishing — the
            # session keeps working, and often spawns another.
            "SubagentStop": plain,
            "Stop": [{"hooks": stop_hooks}],
            # The turn that ends because the API refused it. Claude Code fires
            # this INSTEAD of `Stop` (measured on 2.1.224 and 2.1.260, in `-p`
            # and interactive, for 529/402/429/401), so without it such a turn
            # never ends from the platform's side: the process sits alive at its
            # prompt, the probe says alive, and nothing else is coming.
            "StopFailure": plain,
        },
    }


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
        env={
            CONFIG_DIR_ENV: config_dir,
            HARNESS_ENV: CLAUDE_CODE,
            "BUN_OPTIONS": shlex.quote(
                "--preload=" + config_dir + "/webfetch_transport.cjs"
            ),
        },
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
            SessionFile(
                "webfetch_transport.cjs",
                Path(__file__).with_name("webfetch_transport.cjs").read_text(),
                0o644,
            ),
            *(
                SessionFile(name, content, 0o644)
                for name, content in native_skill_files().items()
            ),
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


@dataclass(frozen=True, slots=True)
class ClaudeLaunch:
    """Claude Code as a ``LaunchPlan``: 跑什么，交给机器去说在哪。

    The three values a turn actually chooses, held until a channel says where
    its screen keeps things — at which point ``at`` turns them into the argv and
    the files above. Nothing else about this harness reaches a transport: a
    channel holding one of these knows it has *a* launch to perform and not
    which one, which is exactly what lets the next harness reuse the channel
    without editing it.
    """

    system_prompt: str
    model: str | None = None
    resume_session_id: str | None = None
    harness: str = CLAUDE_CODE

    @property
    def execution(self) -> ExecutorLaunch:
        from app.domain.agent.harness.claude_code.remote_execution import launch

        return launch

    def on(self, place: MachinePlace) -> MachineLaunch:
        from app.domain.agent.harness.claude_code.device_launch import on_machine

        return on_machine(
            place,
            system_prompt=self.system_prompt,
            model=self.model,
            # The third thing a plan carries, and the one the device channel
            # used to drop on the floor. A screen is retired and reopened for
            # reasons that say nothing about the conversation, and until this
            # was passed on, every one of them started the topic's agent from a
            # blank slate — the room's memory of its own turns ending at
            # whichever gate last fired.
            resume_session_id=self.resume_session_id,
        )

    def at(self, place: ScreenPlace) -> LaunchSpec:
        return build_session_launch(
            config_dir=place.state_dir,
            workdir=place.workdir,
            system_prompt=self.system_prompt,
            model=self.model,
            resume_session_id=self.resume_session_id,
            # The same directory as the backend can read it: the resume guard
            # has to look at the transcript through the mount.
            transcripts_at=place.state_at,
        )


def harness_of(env: dict[str, str]) -> str:
    """Which harness a session was started to run, from its own environment."""
    return env.get(HARNESS_ENV) or CLAUDE_CODE
