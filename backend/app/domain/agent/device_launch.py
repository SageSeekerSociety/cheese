"""Build a device screen that runs an interactive `claude` with OUR hooks (P3).

A screen the platform opens on a device runs a self-contained launcher (no files
pre-baked on the device): the ``command`` is a ``bash -lc`` script that writes an
isolated ``~/.claude`` with (1) pre-accepted first-launch gates (so the first prompt
isn't eaten — spike 坑 #1), (2) a ``settings.json`` wiring Claude Code COMMAND hooks
to a ``cheese-hook`` forwarder, and (3) the forwarder itself (POSTs each hook's JSON
to the backend). Then it ``exec``s ``claude --dangerously-skip-permissions``.

Perception is entirely via those hooks (``hook_events.translate_hook``); the minimal
cheeselet (``cheeselets/claude_min.js``) only types the prompt. The raw screen bytes
the host relays are for the human viewer only — never parsed.

Everything here is pure (string/dict building) so it is unit-testable without a
device. ``build_screen_launch`` returns ``(command, env, cheeselet_source)`` for
``DeviceHub.open_screen``.
"""

import json
import shlex
from importlib import resources
from pathlib import Path

# Perception wiring (settings.json + the cheese-hook forwarder) is the SHARED
# substrate — identical for the local (tmux) and remote (device) backends so it
# can't drift (fusion-design §8.6). Re-exported here (`hooks_settings`) because
# this module's launcher and its callers build on it.
from app.domain.agent.hooks_substrate import CHEESE_HOOK_SCRIPT, hooks_settings

# First-launch gates (Claude Code 2.1.x) for $HOME/.claude.json, kept here as the
# readable statement of what the launch script writes inline. Without the
# per-project trust gate a "do you trust this folder?" dialog appears and eats the
# first prompt (its menu even renders a `❯`, fooling readiness); without
# bypassPermissionsModeAccepted the permissions gate does the same.
_CLAUDE_JSON_GATES = {
    "hasCompletedOnboarding": True,
    "autoUpdates": False,
    "bypassPermissionsModeAccepted": True,
}

# Kept as a module-level alias so existing callers/tests referencing this name
# keep working; the source of truth is hooks_substrate.CHEESE_HOOK_SCRIPT.
_CHEESE_HOOK_SCRIPT = CHEESE_HOOK_SCRIPT


def cheeselet_source() -> str:
    """The minimal cheeselet JS shipped into the screen (types the prompt only)."""
    return (
        resources.files("app.domain.agent.cheeselets")
        .joinpath("claude_min.js")
        .read_text(encoding="utf-8")
    )


def build_launch_script(sync_on_stop: bool = False) -> str:
    """The ``bash -lc`` body run as the screen's program. It reads a few env vars the
    screen is created with: ``CHEESE_HOME`` (isolated config/home dir),
    ``CHEESE_WORK`` (cwd), plus the hook wiring (``CHEESE_HOOK_URL``/``CHEESE_TOKEN``)
    and ``CLAUDE_MODEL`` (optional)."""
    settings_json = json.dumps(
        hooks_settings(["cheese-sync"] if sync_on_stop else None),
        ensure_ascii=False,
    )
    # The settings.json / cheese-hook heredocs are quoted ('JSON'/'SH') so the shell
    # never expands them. ~/.claude.json is written by the shell (see below) so a
    # machine without node can still launch.
    return f"""set -e
# CHEESE_HOME/CHEESE_WORK arrive with a LITERAL "$HOME/..." placeholder (the
# server cannot know the device user's home). Substitute the REAL home first —
# treating it as a relative path only worked by accident from a writable cwd
# (a fresh service cwd of / made mkdir die with "cannot create '$HOME'").
# POSIX-only from here: the connector's tmux joins argv with spaces and
# re-parses through /bin/sh (dash on Debian/Ubuntu) — bashisms die silently.
REAL_HOME="$HOME"
CH="${{CHEESE_HOME:-$REAL_HOME}}"; CW="${{CHEESE_WORK:-$REAL_HOME}}"
case "$CH" in "\\$HOME"*) CH="$REAL_HOME${{CH#\\$HOME}}";; esac
case "$CW" in "\\$HOME"*) CW="$REAL_HOME${{CW#\\$HOME}}";; esac
export HOME="$CH" CHEESE_WORK="$CW"
mkdir -p "$HOME" "$CHEESE_WORK"
# Canonicalize to absolutes (resolve symlinks) so nothing depends on cwd —
# the tmux-hosted claude below runs from a fresh server with its own cwd.
export HOME="$(cd "$HOME" && pwd -P)"
export CHEESE_WORK="$(cd "$CHEESE_WORK" && pwd -P)"
# A machine on its own host starts with an EMPTY work dir, so whatever the agent
# writes there is unreachable — the topic branch never moves and 采纳 has nothing
# to take. Give it the branch itself: a real checkout it can push back from.
# (Co-located devices get the real worktree instead and skip this entirely.)
if [ -n "${{CHEESE_GIT_REMOTE:-}}" ] && [ ! -d "$CHEESE_WORK/.git" ]; then
  git -c http.extraHeader="X-Cheese-Token: $CHEESE_TOKEN" \
      clone -q "$CHEESE_GIT_REMOTE" "$CHEESE_WORK" 2>/dev/null || true
  if [ -d "$CHEESE_WORK/.git" ]; then
    git -C "$CHEESE_WORK" config user.name "芝士"
    git -C "$CHEESE_WORK" config user.email "cheese@zhishi.local"
    git -C "$CHEESE_WORK" config http.extraHeader "X-Cheese-Token: $CHEESE_TOKEN"
    git -C "$CHEESE_WORK" checkout -q -B "${{CHEESE_GIT_BRANCH:-main}}" \
      "origin/${{CHEESE_GIT_BRANCH:-main}}" 2>/dev/null \
      || git -C "$CHEESE_WORK" checkout -q -B "${{CHEESE_GIT_BRANCH:-main}}"
  fi
fi
mkdir -p "$HOME/.claude"
# Written by the shell, not node: a machine whose `claude` is the native binary
# has no node at all (MicroCloud's Debian image is exactly that), and under
# `set -e` a missing node aborted the whole launch — the screen opened, claude
# never started, and the turn hung with nothing anywhere saying why. The only
# dynamic value here is a work dir this platform generates, so an unquoted
# heredoc is enough and depends on nothing.
cat > "$HOME/.claude.json" <<JSON
{{"hasCompletedOnboarding":true,"autoUpdates":false,"bypassPermissionsModeAccepted":true,"projects":{{"$CHEESE_WORK":{{"hasTrustDialogAccepted":true,"hasCompletedProjectOnboarding":true}}}}}}
JSON
cat > "$HOME/.claude/settings.json" <<'JSON'
{settings_json}
JSON
cat > "$HOME/.claude/cheese-sync" <<'SYNC'
#!/bin/sh
# Runs at the end of every turn on a machine that owns its own tree: commit what
# the agent wrote and push the topic branch back, so 采纳 can see it. Silent by
# design — a hook that fails must never take the turn down with it.
[ -n "${{CHEESE_GIT_REMOTE:-}}" ] || exit 0
[ -d "$CHEESE_WORK/.git" ] || exit 0
cd "$CHEESE_WORK" || exit 0
git add -A >/dev/null 2>&1
git diff --cached --quiet && exit 0
git commit -q -m "芝士 edits" >/dev/null 2>&1 || exit 0
# Report the outcome through cheese-hook, which already has a durable spool and
# retries until the backend acknowledges. Staying non-fatal was right — a Stop
# hook that dies takes the turn with it — but silence was not: a rejected push
# left the turn reporting done while the only copy of the agent's work sat on a
# machine nobody would think to look at. Exit 0 either way; the difference is
# that failure now leaves a trace instead of nothing.
sha="$(git rev-parse HEAD 2>/dev/null)"
if git push -q origin "HEAD:${{CHEESE_GIT_BRANCH:-main}}" >/dev/null 2>&1; then
  status=ok
else
  status=failed
fi
printf '{{"hook_event_name":"CheeseSync","status":"%s","commit":"%s","branch":"%s"}}' \
  "$status" "$sha" "${{CHEESE_GIT_BRANCH:-main}}" | cheese-hook >/dev/null 2>&1 || true
SYNC
chmod +x "$HOME/.claude/cheese-sync"
cat > "$HOME/.claude/cheese-hook" <<'SH'
{_CHEESE_HOOK_SCRIPT}SH
chmod +x "$HOME/.claude/cheese-hook"
# The `cheese` platform-action CLI (accept cards / docs / decisions / memory): the
# local sandbox bakes it into the image; a device fetches it from the backend, gated
# by the same scoped token. Best-effort — a device without it (or without python3)
# can still do code work, just not platform actions. On PATH via $HOME/.claude below.
if [ -n "$CHEESE_CLI_URL" ]; then
  curl -s -m 10 -H "X-Cheese-Token: $CHEESE_TOKEN" "$CHEESE_CLI_URL" \\
    > "$HOME/.claude/cheese" 2>/dev/null && [ -s "$HOME/.claude/cheese" ] \\
    && chmod +x "$HOME/.claude/cheese" || rm -f "$HOME/.claude/cheese"
fi
export PATH="$HOME/.claude:$PATH"
# Durable event delivery on the device: cheese-hook spools every hook and (via
# CHEESE_HOOK_SPOOL_ONLY) skips its own inline curl, so this ONE background drainer is
# the sole sender — it retries each spooled event until the backend DURABLY accepts
# it (code:200 = pushed to the live turn OR parked in the topic's server-side spool
# for the next reconcile), so a link/backend outage never drops an event. A 24h age
# cap stops an unreachable backend from accumulating retries forever. The backend
# dedups re-deliveries by event-id.
export CHEESE_HOOK_SPOOL="$HOME/.claude/cheese-spool"
export CHEESE_HOOK_SPOOL_ONLY=1
mkdir -p "$CHEESE_HOOK_SPOOL"
( while true; do
    for f in "$CHEESE_HOOK_SPOOL"/[0-9]*; do
      [ -e "$f" ] || continue
      resp="$(curl -s -m 10 -X POST -H 'Content-Type: application/json' \\
        -H "X-Cheese-Token: $CHEESE_TOKEN" -H "X-Cheese-Event-Id: ${{f##*.}}" \\
        --data-binary @"$f" "$CHEESE_HOOK_URL" 2>/dev/null)"
      case "$resp" in *'"code":200'*) rm -f "$f";; esac
    done
    find "$CHEESE_HOOK_SPOOL" -type f -mmin +1440 -delete 2>/dev/null
    sleep 1
  done ) &
cd "$CHEESE_WORK"
# Host claude in a PERSISTENT tmux session so it survives a link/screen drop: the
# session keeps running on the device and re-opening the screen re-attaches to it
# (same hosting as the local tmux backend; the PTY mirrors the pane for the human
# viewer and the cheeselet types into it). Direct exec if tmux isn't installed.
CLAUDE="claude --dangerously-skip-permissions"
[ -n "$CLAUDE_MODEL" ] && CLAUDE="$CLAUDE --model $CLAUDE_MODEL"
if command -v tmux >/dev/null 2>&1; then
  # The screen runs inside the connector's own tmux, so $TMUX points at ITS
  # socket — inherited, new-session would land the claude session there (dying
  # with the connector) while attach looks at the default socket ("no
  # sessions", dead pane). unset TMUX for the WHOLE block: every command
  # targets the user's default server, decoupled from the connector.
  unset TMUX
  # The session name is derived from the WORK DIR, never a fixed "cheese": one
  # shared session made every topic on a device attach to whatever cwd the FIRST
  # topic had, so later topics edited the wrong tree and never saw new launcher
  # env (observed live: a 7-day-old session still serving new topics). Keying on
  # the work dir gives per-topic isolation AND retires a stale session whenever
  # the resolved work dir changes.
  SESSION="cheese_$(printf '%s' "$CHEESE_WORK" | cksum | cut -d' ' -f1)"
  tmux has-session -t "$SESSION" 2>/dev/null || \\
    tmux new-session -d -s "$SESSION" -c "$CHEESE_WORK" "$CLAUDE"
  exec tmux attach -t "$SESSION"
else
  exec $CLAUDE
fi
"""


def build_screen_launch(
    *,
    hook_url: str,
    hook_token: str,
    home_dir: str,
    work_dir: str,
    model: str | None = None,
    extra_env: dict[str, str] | None = None,
    api_base: str | None = None,
    cli_url: str | None = None,
    project_id: str | None = None,
    topic_id: str | None = None,
    author: str | None = None,
    git_remote: str | None = None,
    git_branch: str | None = None,
) -> tuple[list[str], dict[str, str], str]:
    """Assemble ``(command, env, cheeselet_source)`` for ``DeviceHub.open_screen``.

    ``command`` is a self-contained ``bash -lc`` launcher; ``env`` carries the hook
    wiring + home/work dirs + model + any provider (gateway) vars; ``cheeselet_source``
    is the minimal prompt-typing driver. When ``cli_url``/``api_base`` and the
    ``project_id``/``topic_id`` context are given, the launcher also fetches the
    ``cheese`` platform-action CLI (accept cards / docs / decisions / memory) and wires
    its ``CHEESE_*`` env — the same actions the in-container agent has locally."""
    script = build_launch_script(sync_on_stop=bool(git_remote))
    command = ["bash", "-lc", script]
    env: dict[str, str] = {
        "CHEESE_HOOK_URL": hook_url,
        "CHEESE_TOKEN": hook_token,
        "CHEESE_HOME": home_dir,
        "CHEESE_WORK": work_dir,
        # Base first-launch gates the launcher's node reads to build ~/.claude.json
        # (it adds the per-project trust entry for the resolved work dir).
    }
    if model:
        env["CLAUDE_MODEL"] = model
    # Platform-action CLI wiring: the `cheese` script reads these (X-Cheese-Token =
    # CHEESE_TOKEN, the SAME scoped token the hook forwarder uses).
    if cli_url:
        env["CHEESE_CLI_URL"] = cli_url
    if api_base:
        env["CHEESE_API"] = api_base
    if project_id:
        env["CHEESE_PROJECT"] = project_id
    if topic_id:
        env["CHEESE_TOPIC"] = topic_id
    if author:
        env["CHEESE_AUTHOR"] = author
    if git_remote:
        # Only a machine on its own host gets these; a co-located device edits
        # the real worktree and must not clone over it.
        env["CHEESE_GIT_REMOTE"] = git_remote
        env["CHEESE_GIT_BRANCH"] = git_branch or "main"
    if extra_env:
        env.update(extra_env)
    return command, env, cheeselet_source()


def ensure_dir(path: str) -> str:
    """Create ``path`` (the isolated home/work dir) if missing; return it. Used by the
    spike / local-device provider where the device is this same machine."""
    Path(path).mkdir(parents=True, exist_ok=True)
    return path


# shlex is imported for callers that need to render the command for logging/debug.
def command_for_log(command: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in command)
