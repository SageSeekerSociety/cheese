"""怎么在一块屏幕上把 claude 开起来，以及它开机前要在盘上看到什么。

Symmetric to ``device_launch.build_screen_launch``, for a screen the backend
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
from dataclasses import dataclass
from pathlib import Path

from app.domain.agent import clone
from app.domain.agent.harness import CLAUDE_CODE
from app.domain.agent.harness.claude_code.cli import CLAUDE_BASE_CMD, DISALLOWED_TOOLS
from app.domain.agent.harness.launch import LaunchSpec, ScreenPlace, SessionFile

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


# The fetch layer, as an MCP server rather than the built-in WebFetch.
#
# WebFetch has no timeout: when its fetch/decode path fails to settle, the tool
# call never returns and never errors. Measured here on 2.1.224 against
# https://www.skills.sh/ — two of two attempts hung (1,028 s and 390 s), both
# ended by hand, while a LARGER page (996 KB Wikipedia) returned in 5 s, so size
# is not the trigger. Upstream anthropics/claude-code#86910 is open, and no
# CHANGELOG entry between 2.1.117 and 2.1.263 touches the hang.
#
# Nothing in Claude Code bounds that tool: the binary carries no WebFetch
# timeout knob (BASH_DEFAULT_TIMEOUT_MS is Bash's, API_TIMEOUT_MS is the API's).
# An MCP server is the only fetch path we can put a deadline on, which is the
# whole reason this exists — not better extraction.
#
# The official server, measured against the same pages: the page that hangs
# WebFetch forever returns in 6.8 s, a Chinese article in 2.9 s, a robots.txt
# refusal in 0.3 s, a dead host in 5.1 s. Nothing unbounded. It also truncates
# to max_length and pages via start_index, so one document cannot flood a turn
# the way a full page can (a 999 KB page extracts to 33k tokens whole).
#
# robots.txt stays ENFORCED. Measured across nine real URLs, --ignore-robots-txt
# changed the outcome for one of them, and that one returned article metadata
# rather than the article: the sites that refuse a crawler refuse it at the HTTP
# layer too (zhihu answers 403 either way). The flag is a norm to trade away for
# nothing, so it is not traded.
def mcp_servers() -> dict:
    """MCP servers every sandbox gets, whatever repo it is hosting.

    Planted by the platform, never by the repo: a hosted repository must not have
    to add a `.mcp.json` to be fetched from safely.
    """
    return {
        "fetch": {
            "command": "uvx",
            "args": ["mcp-server-fetch"],
        }
    }


def hooks_settings(extra_stop: list[str] | None = None) -> dict:
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
        # Deadlines for the two tool paths that can otherwise hang a turn open.
        # MCP_TOOL_TIMEOUT bounds every MCP tool call (the fetch server above);
        # the stream watchdog bounds a summarisation stream that stops
        # delivering. Neither reaches the built-in WebFetch, which is why the
        # fetch server exists — a reporter on #86910 measured the watchdog
        # making no difference when the stall is in WebFetch's own page fetch.
        "env": {
            "MCP_TOOL_TIMEOUT": "60000",
            "CLAUDE_ENABLE_STREAM_WATCHDOG": "1",
        },
        # Previews belong in Cheese, not on claude.ai via the Artifact tool.
        "enableArtifact": False,
        # Tools with no way out of this platform (AskUserQuestion — see
        # cli.DISALLOWED_TOOLS). Also passed as --disallowedTools on the
        # launch line; a deny rule that only lives in one of the two is a deny
        # rule that a future launcher tweak can silently drop.
        "permissions": {"deny": list(DISALLOWED_TOOLS)},
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
            "mcpServers": mcp_servers(),
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
