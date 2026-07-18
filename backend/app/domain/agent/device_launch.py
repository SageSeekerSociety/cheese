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

# Top-level first-launch gates (Claude Code 2.1.x) for $HOME/.claude.json. The
# PER-PROJECT trust gate (projects[workdir].hasTrustDialogAccepted) is added by the
# launcher for the resolved work dir — without it a "do you trust this folder?" dialog
# appears and eats the first prompt (its menu even renders a `❯`, fooling readiness).
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


def build_launch_script() -> str:
    """The ``bash -lc`` body run as the screen's program. It reads a few env vars the
    screen is created with: ``CHEESE_HOME`` (isolated config/home dir),
    ``CHEESE_WORK`` (cwd), plus the hook wiring (``CHEESE_HOOK_URL``/``CHEESE_TOKEN``)
    and ``CLAUDE_MODEL`` (optional)."""
    settings_json = json.dumps(hooks_settings(), ensure_ascii=False)
    # The settings.json / cheese-hook heredocs are quoted ('JSON'/'SH') so the shell
    # never expands them. ~/.claude.json is built by node (from the base gates in the
    # $CHEESE_CLAUDE_GATES env var) because it needs the RESOLVED work dir as a dynamic
    # key (per-project trust) — a quoted heredoc can't do that. Reading the base from an
    # env var avoids any nested-quoting between the shell, node, and the JSON.
    return f"""set -e
export HOME="${{CHEESE_HOME:-$HOME}}"
export CHEESE_WORK="${{CHEESE_WORK:-$HOME}}"
mkdir -p "$HOME/.claude" "$CHEESE_WORK"
# Canonicalize the work dir (resolve symlinks, e.g. macOS /tmp → /private/tmp) so the
# per-project trust key matches the path Claude Code actually canonicalizes cwd to.
export CHEESE_WORK="$(cd "$CHEESE_WORK" && pwd -P)"
cat > "$HOME/.claude/.mktrust.js" <<'JS'
const fs = require("fs");
const base = JSON.parse(process.env.CHEESE_CLAUDE_GATES);
const w = process.env.CHEESE_WORK;
base.projects = {{
  [w]: {{ hasTrustDialogAccepted: true, hasCompletedProjectOnboarding: true }},
}};
fs.writeFileSync(process.env.HOME + "/.claude.json", JSON.stringify(base));
JS
node "$HOME/.claude/.mktrust.js"
cat > "$HOME/.claude/settings.json" <<'JSON'
{settings_json}
JSON
cat > "$HOME/.claude/cheese-hook" <<'SH'
{_CHEESE_HOOK_SCRIPT}SH
chmod +x "$HOME/.claude/cheese-hook"
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
  tmux has-session -t cheese 2>/dev/null || tmux new-session -d -s cheese "$CLAUDE"
  exec tmux attach -t cheese
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
) -> tuple[list[str], dict[str, str], str]:
    """Assemble ``(command, env, cheeselet_source)`` for ``DeviceHub.open_screen``.

    ``command`` is a self-contained ``bash -lc`` launcher; ``env`` carries the hook
    wiring + home/work dirs + model + any provider (gateway) vars; ``cheeselet_source``
    is the minimal prompt-typing driver."""
    script = build_launch_script()
    command = ["bash", "-lc", script]
    env: dict[str, str] = {
        "CHEESE_HOOK_URL": hook_url,
        "CHEESE_TOKEN": hook_token,
        "CHEESE_HOME": home_dir,
        "CHEESE_WORK": work_dir,
        # Base first-launch gates the launcher's node reads to build ~/.claude.json
        # (it adds the per-project trust entry for the resolved work dir).
        "CHEESE_CLAUDE_GATES": json.dumps(_CLAUDE_JSON_GATES, ensure_ascii=False),
    }
    if model:
        env["CLAUDE_MODEL"] = model
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
