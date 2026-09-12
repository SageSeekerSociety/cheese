"""Build a device screen that runs an interactive `claude` with OUR hooks (P3).

A screen the platform opens on a device runs a self-contained launcher (no files
pre-baked on the device): the ``command`` is a ``bash -lc`` script that writes an
isolated ``~/.claude`` with (1) pre-accepted first-launch gates (so the first prompt
isn't eaten — spike 坑 #1), (2) a ``settings.json`` wiring Claude Code COMMAND hooks
to a ``cheese-hook`` forwarder, and (3) the forwarder itself (POSTs each hook's JSON
to the backend). Then it ``exec``s ``claude --dangerously-skip-permissions``.

Perception is entirely via those hooks (``hook_events.translate_hook``); prompts
arrive on the rendezvous socket the launcher arms. The raw screen bytes the host
relays are for the human viewer only — never parsed.

Everything here is pure (string/dict building) so it is unit-testable without a
device. ``build_screen_launch`` returns ``(command, env)`` for
``DeviceHub.open_screen``.
"""

import json
import shlex
from pathlib import Path

# Perception wiring (settings.json + the cheese-hook forwarder) is the SHARED
# substrate — identical for the local (tmux) and remote (device) backends so it
# can't drift (fusion-design §8.6). Re-exported here (`hooks_settings`) because
# this module's launcher and its callers build on it.
from app.domain.agent import environment_runner, machine_tunnel, preview_tunnel
from app.domain.agent.harness.claude_code import event_drain, event_spool, startup_cache
from app.domain.agent.harness.claude_code.cli import CLAUDE_BASE_CMD
from app.domain.agent.harness.claude_code.hooks_substrate import CHEESE_HOOK_SCRIPT
from app.domain.agent.harness.claude_code.remote_execution import (
    client as execution_client,
)
from app.domain.agent.harness.claude_code.session_launch import hooks_settings
from app.domain.agent.skills import native_skill_files

# First-launch gates (Claude Code 2.1.x) for $CLAUDE_CONFIG_DIR/.claude.json,
# kept here as the readable statement of what the launch script writes inline.
# Without the per-project trust gate a "do you trust this folder?" dialog appears
# and eats the first prompt (its menu even renders a `❯`, fooling readiness);
# without bypassPermissionsModeAccepted the permissions gate does the same.
_CLAUDE_JSON_GATES = {
    "hasCompletedOnboarding": True,
    "autoUpdates": False,
    "bypassPermissionsModeAccepted": True,
}

# Kept as a module-level alias so existing callers/tests referencing this name
# keep working; the source of truth is hooks_substrate.CHEESE_HOOK_SCRIPT.
_CHEESE_HOOK_SCRIPT = CHEESE_HOOK_SCRIPT

# --- the version this delivery path is pinned to -----------------------------
# A device screen no longer types prompts into a terminal: it writes them to the
# rendezvous socket Claude Code binds for itself, where they are enqueued as
# `origin: {kind:"human"}`. That socket is undocumented private surface, and its
# frames moved between 2.1.220 and 2.1.224 — so the launcher prefers ONE
# verified build and refuses anything under the floor rather than silently
# degrading to a paste-and-pray driver.
#
# Raising these is a deliberate act: re-run cli/e2e (CHEESE_RV=1) against the
# new build first, because "it launched" is not evidence the frames still work.
CLAUDE_PINNED_VERSION = "2.1.261"
CLAUDE_MIN_VERSION = "2.1.261"

# CLAUDE_BASE_CMD starts with the bare word `claude`; the launcher resolves a
# specific binary (pin, then ~/.local/bin, then PATH) and needs only the flags.
CLAUDE_BASE_ARGS = CLAUDE_BASE_CMD.removeprefix("claude")

# Screen-env keys the connector reads to find a screen's rendezvous socket. The
# token lives in a FILE, not the env: an adopted `claude` keeps the token it
# booted with, so a freshly minted env value would never match.
ENV_RV_SOCK = "CHEESE_RV_SOCK"
ENV_RV_TOKEN_FILE = "CHEESE_RV_TOKEN_FILE"


def rendezvous_paths(topic_id: str) -> tuple[str, str]:
    """``(socket, token_file)`` for a topic, short enough to be bindable.

    A unix socket path is capped near 104 bytes and an isolated home already
    spends ~105 (`~/.cheese/home/<project-uuid>/<topic-uuid>/.claude/`), so the
    socket cannot live beside the session it belongs to. `/tmp` plus 12 hex of
    the topic id keeps it at ~32 bytes and still unique per topic."""
    short = topic_id.replace("-", "")[:12]
    return f"/tmp/cheese-rv-{short}.sock", f"/tmp/cheese-rv-{short}.token"


# Reports what a turn cost. Claude Code writes a usage block per assistant
# message into its transcript; nothing else on the machine knows those numbers,
# and the transcript dies with the machine — which is why a week of spend could
# not be attributed to a project, a topic, or even a prompt.
#
# Kept OUT of the launcher f-string on purpose: it is dense with braces, and
# escaping them inside an f-string is a silent-corruption risk for no benefit.
#
# python3 rather than shell: the transcript is JSONL, there is no jq on a
# machine, and a grep/sed parser works right up until a field moves.
CHEESE_USAGE_READER = """import json, sys
try:
    hook = json.loads(sys.stdin.read())
except Exception:
    sys.exit(0)
path = hook.get("transcript_path")
if not path:
    sys.exit(0)
tot = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
model = ""
try:
    with open(path, errors="ignore") as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except Exception:
                continue
            m = d.get("message") or {}
            u = m.get("usage") or {}
            if not u:
                continue
            model = m.get("model") or model
            tot["input"] += u.get("input_tokens") or 0
            tot["output"] += u.get("output_tokens") or 0
            tot["cache_read"] += u.get("cache_read_input_tokens") or 0
            tot["cache_write"] += u.get("cache_creation_input_tokens") or 0
except OSError:
    sys.exit(0)
if any(tot.values()):
    print(json.dumps({"hook_event_name": "CheeseUsage", "model": model, **tot}))
"""

# The reader has to be a FILE, not a heredoc: `python3 - <<PY` hands python the
# heredoc as its stdin, so the hook payload we actually need to read would never
# arrive. Verified by running it both ways.
CHEESE_USAGE_SCRIPT = """#!/bin/sh
python3 "$HOME/.claude/cheese-usage.py" | cheese-hook >/dev/null 2>&1 || true
"""

# READ-ONLY against the machine owner's files, by contract (#5). History, so
# nobody reintroduces the write: the 2026-08-02 measurement showed the login
# user's ~/.claude/settings.json env block wins over the process environment,
# so earlier launches REWROTE that file to be routed at all — which hijacked
# every claude the machine's owner started by hand (their sessions suddenly
# went through our meter), and burned their credentials' refresh chain when we
# later touched .credentials.json too. CLAUDE_CONFIG_DIR removes the premise:
# claude no longer reads the owner's settings.json at all (verified 2026-08-15
# — with the config dir set, a bogus credential inside it fails 401 while a
# valid one sits in ~/.claude untouched), so the process environment and OUR
# config dir are the control point, and the owner's files need no agreeing
# with. What remains of the reconcile is extraction: on the machine-ticket
# path, the machine's own ccproxy ticket lives in the OWNER's files (MicroCloud
# seeds settings.json; a login-style box keeps .credentials.json), and this
# script READS it out and hands it to the launcher through argv[2]. It opens
# the owner's files only ever to read.
CHEESE_SETTINGS_RECONCILE = """import json, os, sys

path = sys.argv[1]

# Only the machine-ticket path has anything to extract; every other supply
# shape is fully described by the process environment and our own config dir.
if not (
    os.environ.get("CHEESE_TUNNEL_URL") or os.environ.get("CHEESE_MACHINE_TICKET")
):
    print("ok (env only)")
    raise SystemExit(0)

def read_json(p):
    try:
        with open(p) as fh:
            loaded = json.load(fh)
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        return {}

# The machine's own ccproxy ticket, wherever the machine keeps it. A ticket is
# distinguishable by shape: a scoped cheese token is `body.signature` (has a
# dot), a ccproxy ticket has no dot — a dotted value here is residue of an old
# launcher that WROTE into this file, never a ticket.
#
# Order matters and encodes freshness:
#   1. settings.json env — where MicroCloud seeds the ticket on its machines,
#      and where a refresh lands on machines that run with that file as the
#      credential source (measured 2026-08-14: the live field held the fresh
#      ticket while the backup's copy had already expired);
#   2. .credentials.json — the client's canonical store, the ONLY copy on a
#      login-style box that never had a ticket in settings.json;
#   3. the .cheese-orig backup — written by launchers that predate the
#      read-only reconcile; exists only on machines they touched.
machine_ticket = ""
live = (read_json(path).get("env") or {}).get("CLAUDE_CODE_OAUTH_TOKEN") or ""
if live and "." not in live:
    machine_ticket = live
if not machine_ticket:
    creds = os.path.join(os.path.dirname(path), ".credentials.json")
    machine_ticket = (read_json(creds).get("claudeAiOauth") or {}).get(
        "accessToken"
    ) or ""
if not machine_ticket:
    backup = (read_json(path + ".cheese-orig").get("env") or {}).get(
        "CLAUDE_CODE_OAUTH_TOKEN"
    ) or ""
    if backup and "." not in backup:
        machine_ticket = backup

if not machine_ticket:
    # Named loudly: without a ticket the launcher keeps the scoped token as the
    # bearer, and on a pass-through deployment that dies upstream as an opaque
    # `401 Invalid bearer token` — this line is what tells the two apart.
    print("no-machine-ticket")
    raise SystemExit(0)

# Hand the ticket to the launcher through a file, never stdout: stdout is
# reported into a hook payload on failure, and a credential must not travel
# that way.
if len(sys.argv) > 2:
    out = sys.argv[2]
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(machine_ticket)
print("ok (ticket extracted)")
"""


# Liveness probe for a device screen (turn 活跃度检测 — the device half of the
# two-layer idle-suspect / hard-ceiling check). Run over the hub's `exec` once a
# turn crosses the idle-suspect threshold, to tell a `claude` that is silently
# working — a long FOREGROUND command (pytest, a build) emits NO interim hook, yet
# its process is alive — from one whose process actually died.
#
# There is no cheap screen-byte signal on a HEADLESS device (the hub relays raw
# screen bytes only to a live browser viewer, and only while one is subscribed), so
# the probe asks the box directly: the process-tree signal, the one that stays
# valid through a hook-silent window where transcript mtime / statusline / OTel do
# not. It keys on the `claude` process's own CHEESE_TOPIC env, so it is per-topic
# precise even with several screens on one machine.
#
# Prints exactly one of `alive` / `dead` / `unknown`. The caller treats ONLY an
# explicit `dead` as fatal; `unknown` (unsupported process metadata) and any
# exec error are read conservatively as alive, so a probe hiccup never false-kills.
DEVICE_ALIVE_PROBE = r"""topic="${CHEESE_ALIVE_TOPIC:-}"
[ -n "$topic" ] || { echo unknown; exit 0; }
# A prepared process acquired its topic after exec; its original /proc environ
# cannot identify that assignment. Check the durable binding and native pane.
if [ -f "$HOME/.cheese/native-warm/state.json" ]; then
  warm_status="$(python3 "$HOME/.cheese/warm-native-runner.py" probe-topic \
    "$HOME/.cheese/native-warm" "$topic" 2>/dev/null)"
  case "$warm_status" in alive|dead) echo "$warm_status"; exit 0;; esac
fi
# Linux: match the topic on each process's own environ → per-topic precise. The
# connector (same user as the screen it spawned) can read that same-uid /proc entry.
if [ -d /proc ] && [ -r /proc/self/environ ]; then
  # One reader avoids spawning a tr process for every process on the host.
  python3 - "$topic" <<'PY'
import os
import sys

topic = b"CHEESE_TOPIC=" + os.fsencode(sys.argv[1])
with os.scandir("/proc") as entries:
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            with open(entry.path + "/cmdline", "rb") as source:
                if b"claude" not in source.read():
                    continue
            with open(entry.path + "/environ", "rb") as source:
                if topic in source.read().split(b"\0"):
                    print("alive")
                    sys.exit(0)
        except OSError:
            # Processes may exit or belong to another user during the scan.
            continue
print("dead")
PY
  exit $?
fi
# macOS has no /proc. Read the environment of candidate Claude processes via
# ps, without emitting it: a dead Mac session must not be adopted on retry.
if [ "$(uname -s)" = Darwin ]; then
  processes="$(ps -axo pid=,comm= 2>/dev/null)" || { echo unknown; exit 0; }
  pids="$(printf '%s\n' "$processes" | awk '
    $2 == "claude" || $0 ~ /\/claude\/versions\// || $0 ~ /\/claude$/ {print $1}')"
  for pid in $pids; do
    if ps eww -p "$pid" -o command= 2>/dev/null \
      | grep -Eq "(^| )CHEESE_TOPIC=$topic( |$)"; then
      echo alive; exit 0
    fi
  done
  echo dead; exit 0
fi
# No supported per-topic view: keep an uncertain session alive.
echo unknown
"""


# Is the machine-local tunnel helper this topic's `claude` was pointed at still
# listening? Companion to DEVICE_ALIVE_PROBE, for the OTHER half of "the process
# is alive but cannot reach the model".
#
# The helper is started ONLY by `cheese-tunnel-up`, which runs ONLY as the
# launcher's prefix — and a reused screen is reasserted (an adopt-create),
# never relaunched. So a helper that dies under a still-running `claude` never
# comes back on its own, and `claude` bakes its HTTPS_PROXY at startup and never
# re-reads it: every turn thereafter dies with `API Error: Unable to connect to
# API (ConnectionRefused)` while the process-tree probe above reports `alive`.
# Measured 2026-08-18 on the dev box: five screens in that state, one of them
# replaying the same 28-message batch for the 30th time, ~3 minutes burnt per
# attempt, with no path in the system able to restore them.
#
# LISTEN on the port is the right question, not "is the helper's pid alive": the
# port is exactly what `claude` connects to, and ConnectionRefused is exactly
# "nothing is listening there". A lingering helper process that has lost its
# upstream still holds the port and is NOT this failure.
#
# Prints exactly one of `up` / `down` / `unknown`, same tri-state discipline as
# the probe above: only an explicit `down` is actionable, so a missing /proc, an
# absent awk, or any hiccup leaves a working screen alone.
DEVICE_TUNNEL_PROBE = r"""port="${CHEESE_TUNNEL_PROBE_PORT:-}"
case "$port" in ''|*[!0-9]*) echo unknown; exit 0 ;; esac
[ -r /proc/net/tcp ] || { echo unknown; exit 0; }
command -v awk >/dev/null 2>&1 || { echo unknown; exit 0; }
hex=$(printf '%04X' "$port" 2>/dev/null) || { echo unknown; exit 0; }
# /proc/net/tcp columns: $2 is local_address as HEXIP:HEXPORT, $4 is the state
# (0A = LISTEN). The header row's $4 is the literal "st", so it never matches.
# tcp6 has a 32-char hex address but the same `:PORT` suffix, hence split on ":"
# and compare the LAST field rather than matching the whole column.
for f in /proc/net/tcp /proc/net/tcp6; do
  [ -r "$f" ] || continue
  if awk -v p="$hex" '$4=="0A" { n=split($2,a,":"); if (a[n]==p) f=1 }
                      END { exit f?0:1 }' "$f"; then
    echo up; exit 0
  fi
done
echo down
"""


# Starts the tunnel helper and does NOT return until its port answers.
#
# The wait is the point. `claude` reads HTTPS_PROXY once at startup and makes its
# first request (the login/profile check) immediately, so a helper that is merely
# "starting" loses that race and the screen boots looking unauthenticated. Bounded
# rather than unbounded: if it cannot bind in five seconds it is not going to, and
# hanging the launch would be a worse failure than a loud one.
#
# Adopt-if-alive for the same reason the drainer does: a screen is reused across
# turns, and a second helper on the same port would exit immediately, leaving
# whichever one won holding a token file the other launch had already replaced.
#
# `nohup`, and the LISTEN check below, are what make the reuse path actually
# heal. Measured 2026-08-30 on the dev box: fifteen topics whose helper was gone
# and whose `claude` had been dialling a dead port for days — one of them re-@'d
# four times in three hours with not one reply. Their `cheese-tunnel.log` said
# `tunnel listening` at the timestamp of the last launch, so the launcher HAD run
# and this script HAD started a helper; the helper simply did not outlive the
# `tmux new-window` the reuse branch starts it from. That window's command is
# this script, this script backgrounds the helper and returns, and the window's
# process group is torn down the moment it does — SIGHUP, and the port is dead
# again before the turn it was started for reaches the model. `nohup` is what
# makes the helper outlive the window that bore it; the direct call in the CREATE
# branch never noticed, because there the process that returns is the one that
# goes on to be `claude`.
#
# The adopt test is the port, not the pid, for the reason DEVICE_TUNNEL_PROBE
# gives: `claude` connects to a port, and ConnectionRefused is exactly "nothing
# is listening there". A recorded pid that is alive proves only that SOME process
# holds that number — after a reboot, or on a box that has burnt through the pid
# space, that is a coincidence, and adopting on it leaves the port dead for the
# life of the screen with nothing anywhere reporting a fault.
CHEESE_TUNNEL_UP = """#!/bin/sh
PIDF="$HOME/.claude/cheese-tunnel.pid"
STAMPF="$HOME/.claude/cheese-tunnel.stamp"
# Is anything answering on the port `claude` was pointed at? python3 rather than
# bash's /dev/tcp for the same reason the readiness wait below uses it: /bin/sh
# is dash on the machine images and dash has no /dev/tcp.
tunnel_listening() {
  python3 - "$CHEESE_TUNNEL_PORT" <<'PROBEPY'
import socket, sys

try:
    socket.create_connection(("127.0.0.1", int(sys.argv[1])), timeout=0.5).close()
except OSError:
    raise SystemExit(1)
raise SystemExit(0)
PROBEPY
}
# Adopt a live helper ONLY if it is running the helper we just wrote. The
# launcher rewrites cheese-tunnel.py on every launch, so a shipped fix would
# otherwise never reach a machine whose helper is still alive — it would keep
# serving the old code indefinitely, and nothing would look wrong.
WANT="$(cksum "$HOME/.claude/cheese-tunnel.py" 2>/dev/null | cut -d" " -f1)"
HAVE="$(cat "$STAMPF" 2>/dev/null || true)"
PID="$(cat "$PIDF" 2>/dev/null || true)"
if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
  if [ -n "$WANT" ] && [ "$WANT" = "$HAVE" ] && tunnel_listening; then
    exit 0
  fi
  # Different code, or a pid that is alive without the port being served:
  # retire it. In-flight turns see one connection reset, which claude retries;
  # a permanently stale helper does not heal at all.
  kill "$PID" 2>/dev/null || true
fi
nohup python3 "$HOME/.claude/cheese-tunnel.py" \\
  --port "$CHEESE_TUNNEL_PORT" --url "$CHEESE_TUNNEL_URL" \\
  --token-file "$HOME/.claude/cheese-tunnel.token" \\
  >"$HOME/.claude/cheese-tunnel.log" 2>&1 &
echo $! > "$PIDF"
printf '%s\n' "$WANT" > "$STAMPF"
# The readiness check runs in python3, NOT with bash's /dev/tcp: this script is
# invoked as `sh`, /bin/sh is dash on the machine images, and dash has no
# /dev/tcp — the redirect fails on EVERY iteration, so the loop would spend its
# whole budget and then report "not ready" for a helper that came up fine.
# python3 is not an extra dependency here; the helper itself is written in it.
python3 - "$CHEESE_TUNNEL_PORT" <<'WAITPY'
import socket, sys, time
port = int(sys.argv[1])
deadline = time.monotonic() + 5.0
while time.monotonic() < deadline:
    try:
        socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
        raise SystemExit(0)
    except OSError:
        # Native adoption waits here; avoid adding a full 100 ms after port bind.
        time.sleep(0.02)
raise SystemExit(1)
WAITPY
"""


# Brings 运行环境预览's helper up, and does nothing at all until an agent has asked
# for a preview. The port file is that ask: `cheese serve` writes it and then runs
# this script, and every later launch re-runs it so a preview that was declared
# once survives a helper's death (a machine reboot, a killed process) without the
# agent having to declare it again.
#
# Adopt-if-alive on the same cksum, for the same reason the tunnel helper does:
# the launcher rewrites cheese-preview.py on every launch, so a shipped fix would
# otherwise never reach a machine whose helper is still running — it would keep
# serving the old code indefinitely with nothing looking wrong.
#
# No readiness wait here (unlike the tunnel's): nothing on this machine is
# blocked on the tunnel being up. The one caller that needs it up — `cheese serve`
# declaring the preview — waits on the BACKEND side, where the helper's arrival is
# actually observable.
CHEESE_PREVIEW_UP = """#!/bin/sh
# $1, optional: the port.
# `cheese serve`
# passes it and nothing else does, which is what keeps the file layout of the
# preview helper entirely inside the launcher — the CLI knows only this script.
PORTF="$HOME/.claude/cheese-preview.port"
PIDF="$HOME/.claude/cheese-preview.pid"
STAMPF="$HOME/.claude/cheese-preview.stamp"
if [ -n "$1" ]; then
  printf '%s\\n' "$1" > "$PORTF.tmp" && mv "$PORTF.tmp" "$PORTF"
fi
[ -s "$PORTF" ] || exit 0
[ -n "${CHEESE_PREVIEW_URL:-}" ] || exit 0
WANT="$(cksum "$HOME/.claude/cheese-preview.py" 2>/dev/null | cut -d" " -f1)"
HAVE="$(cat "$STAMPF" 2>/dev/null || true)"
PID="$(cat "$PIDF" 2>/dev/null || true)"
if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
  if [ -n "$WANT" ] && [ "$WANT" = "$HAVE" ]; then
    exit 0
  fi
  kill "$PID" 2>/dev/null || true
fi
python3 "$HOME/.claude/cheese-preview.py" \\
  --url "$CHEESE_PREVIEW_URL" \\
  --token-file "$HOME/.claude/cheese-preview.token" \\
  --port-file "$PORTF" \\
  >"$HOME/.claude/cheese-preview.log" 2>&1 &
echo $! > "$PIDF"
printf '%s\\n' "$WANT" > "$STAMPF"
"""


def build_drain_script() -> str:
    source = Path(event_drain.__file__).read_text(encoding="utf-8")
    return '#!/bin/sh\nexec python3 - "$0" "$@" <<\'PY\'\n' + source + "\nPY\n"


# Rooms start in a plain coordination directory. Task checkouts are prepared
# by `cheese worktree`; stopping the session backs up every task independently.
CHEESE_SYNC_SCRIPT = """#!/bin/sh
exec cheese sync --all
"""


def build_launch_script(
    sync_on_stop: bool = False,
    system_prompt: str = "",
    ca_pem: str = "",
    remote_control: bool = False,
    remote_execution: bool = False,
) -> str:
    """The ``bash -lc`` body run as the screen's program. It reads a few env vars the
    screen is created with: ``CHEESE_HOME`` (isolated config/home dir),
    ``CHEESE_WORK`` (cwd), plus the hook wiring (``CHEESE_HOOK_URL``/``CHEESE_TOKEN``)
    and ``CLAUDE_MODEL`` (optional).

    ``system_prompt`` (the platform's assembled system prompt) is embedded in the
    script itself — written to ``$HOME/.claude/cheese-system-prompt.md`` on the
    device and handed to `claude` via ``--append-system-prompt-file``. Embedding
    beats an env var here: the connector's env transport is not guaranteed to
    survive multi-KB values with newlines, while a quoted heredoc is.

    ``ca_pem`` (the metering proxy's CA, subscription turns only) rides the same
    way for the same reason, written to ``$HOME/.claude/proxy-ca.pem`` with
    ``NODE_EXTRA_CA_CERTS`` exported over whatever placeholder the screen env
    carried — the server cannot know the device user's home, so only the script
    can name the real absolute path (an untrusted CA fails as an opaque TLS
    error far from its cause)."""
    # The heredoc delimiter must sit on its own line, so the content always ends
    # with exactly one newline (empty stays empty → the [ -s ] launch guard skips
    # the flag and claude runs with its stock prompt).
    system_prompt = system_prompt.rstrip("\n") + "\n" if system_prompt else ""
    ca_block = ""
    if ca_pem:
        ca_pem = ca_pem.rstrip("\n") + "\n"
        ca_block = f"""cat > "$HOME/.claude/proxy-ca.pem" <<'CHEESECA'
{ca_pem}CHEESECA
export NODE_EXTRA_CA_CERTS="$HOME/.claude/proxy-ca.pem"
"""
    usage_script = CHEESE_USAGE_SCRIPT
    usage_reader = CHEESE_USAGE_READER
    sync_script = CHEESE_SYNC_SCRIPT
    settings_reconcile = CHEESE_SETTINGS_RECONCILE
    startup_cache_source = Path(startup_cache.__file__).read_text()
    webfetch_transport = Path(__file__).with_name("webfetch_transport.cjs").read_text()
    skill_setup = "\n".join(
        f'mkdir -p "$CLAUDE_CONFIG_DIR/{Path(name).parent}"\n'
        f"cat > \"$CLAUDE_CONFIG_DIR/{name}\" <<'CHEESE_NATIVE_SKILL'\n"
        f"{content}\nCHEESE_NATIVE_SKILL"
        for name, content in native_skill_files().items()
    )
    drain_script = build_drain_script()
    cli_source = (Path(__file__).resolve().parents[5] / "sandbox" / "cheese").read_text(
        encoding="utf-8"
    )
    # Shipped by reading the module's own bytes rather than by keeping a second
    # copy here: it is a real, linted, unit-tested module precisely so there is
    # only one version of it to be wrong.
    tunnel_helper = Path(machine_tunnel.__file__).read_text().rstrip("\n") + "\n"
    environment_helper = (
        Path(environment_runner.__file__).read_text().rstrip("\n") + "\n"
    )
    tunnel_up = CHEESE_TUNNEL_UP
    preview_helper = Path(preview_tunnel.__file__).read_text().rstrip("\n") + "\n"
    preview_up = CHEESE_PREVIEW_UP
    settings_json = json.dumps(
        hooks_settings(
            ["cheese-sync", "cheese-usage"] if sync_on_stop else ["cheese-usage"],
            remote_control=remote_control,
        ),
        ensure_ascii=False,
    )
    claude_args = (
        " --dangerously-skip-permissions --remote-control Cheese"
        if remote_control
        else CLAUDE_BASE_ARGS
    )
    execution_setup = ""
    pinned_version = (
        execution_client.PINNED_VERSION if remote_execution else CLAUDE_PINNED_VERSION
    )
    minimum_version = pinned_version if remote_execution else CLAUDE_MIN_VERSION
    if remote_execution:
        source_dir = Path(execution_client.__file__).parent
        execution_setup = 'mkdir -p "$HOME/.claude/remote-execution"\n'
        for name in (
            "client.py",
            "proxy.js",
            "private.py",
            "runtime.py",
            "context_service.py",
        ):
            execution_setup += (
                f'cat > "$HOME/.claude/remote-execution/{name}" '
                "<<'CHEESE_EXECUTION_SOURCE'\n"
                + (source_dir / name).read_text()
                + "\nCHEESE_EXECUTION_SOURCE\n"
            )
        for name, source in {
            "event_spool.py": Path(event_spool.__file__).read_text(),
            "platform-hook-source": CHEESE_HOOK_SCRIPT,
        }.items():
            execution_setup += (
                f'cat > "$HOME/.claude/remote-execution/{name}" '
                "<<'CHEESE_EXECUTION_SOURCE'\n"
                + source.rstrip("\n")
                + "\nCHEESE_EXECUTION_SOURCE\n"
            )
        execution_setup += """printf '%s' "$CHEESE_EXECUTION_TARGET" \\
  > "$HOME/.claude/remote-target.json"
EXECUTOR_CLIENT="$HOME/.claude/remote-execution/client.py"
EXECUTOR_TARGET="$HOME/.claude/remote-target.json"
CLAUDE="python3 \\"$EXECUTOR_CLIENT\\" bootstrap \\"$EXECUTOR_TARGET\\" $CLAUDE"
"""
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
# Bash exposes this clock without spawning date. Other shells skip diagnostics.
# Append across relaunches; only phase names and timestamps enter this file.
cheese_launch_phase() {{
  [ -n "${{EPOCHREALTIME:-}}" ] || return 0
  [ -d "$REAL_HOME/.cheese/launch" ] || return 0
  printf '%s %s\\n' "$EPOCHREALTIME" "$1" \\
    >> "$REAL_HOME/.cheese/launch/$CHEESE_TOPIC.timing" 2>/dev/null || :
}}
cheese_launch_phase started
CH="${{CHEESE_HOME:?Cheese session home is required}}"
CW="${{CHEESE_WORK:?Cheese work directory is required}}"
case "$CH" in "\\$HOME"*) CH="$REAL_HOME${{CH#\\$HOME}}";; esac
case "$CW" in "\\$HOME"*) CW="$REAL_HOME${{CW#\\$HOME}}";; esac
WARM_ROOT=""
if [ -z "${{CHEESE_EXECUTION_TARGET:-}}" ] && \\
   [ -f "$REAL_HOME/.cheese/native-warm/binding.json" ]; then
  python3 "$REAL_HOME/.cheese/warm-native-runner.py" recover-room \\
    "$REAL_HOME/.cheese/native-warm"
fi
# Reuse one interpreter for the check and staging, including on existing spares.
if [ -z "${{CHEESE_EXECUTION_TARGET:-}}" ] && \\
   [ -f "$REAL_HOME/.cheese/native-warm/state.json" ]; then
  WARM_ROOT="$(python3 - "$REAL_HOME/.cheese/native-warm" "$CH" "$CW" \\
    <<'CHEESE_WARM_STAGE'
import json, os, runpy, sys
from pathlib import Path
directory = Path(sys.argv[1])
runner = runpy.run_path(str(directory.parent / "warm-native-runner.py"))
state = json.loads((directory / "state.json").read_text())
if (directory / "ready").exists() and runner["_native_alive"](state):
    runner["stage"](
        directory,
        project_id=os.environ["CHEESE_PROJECT"],
        topic_id=os.environ["CHEESE_TOPIC"],
        home=Path(sys.argv[2]),
        work=Path(sys.argv[3]),
        rendezvous=Path(os.environ["CHEESE_RV_SOCK"]),
        token_file=Path(os.environ["CHEESE_RV_TOKEN_FILE"]),
    )
    print(directory)
CHEESE_WARM_STAGE
)"
fi
export HOME="$CH" CHEESE_WORK="$CW"
cheese_launch_phase warm_staged
mkdir -p "$HOME" "$CHEESE_WORK"
# Canonicalize to absolutes (resolve symlinks) so nothing depends on cwd —
# the tmux-hosted claude below runs from a fresh server with its own cwd.
export HOME="$(cd "$HOME" && pwd -P)"
export CHEESE_WORK="$(cd "$CHEESE_WORK" && pwd -P)"
mkdir -p "$HOME/.claude"
cat > "$HOME/.claude/cheese-environment.py" <<'CHEESE_ENV_PY'
{environment_helper}CHEESE_ENV_PY
# THE isolation boundary on a machine we do not own (#5): claude reads AND
# writes its config — settings.json, .claude.json, .credentials.json — under
# CLAUDE_CONFIG_DIR when it is set, and never falls back to the login user's
# ~/.claude for any of them (verified 2026-08-15: a bogus credential in the
# config dir fails 401 with a valid one sitting in ~/.claude, untouched; hooks
# and the onboarding gate inside the dir both take effect). Without this,
# os.homedir() ignores our exported $HOME and claude lands in the machine
# owner's real ~/.claude — which is why earlier launches had to REWRITE the
# owner's settings.json to be routed at all, hijacking every claude the owner
# starts by hand. With it, the owner's files are never read and never written.
export CLAUDE_CONFIG_DIR="$HOME/.claude"
export DISABLE_AUTOUPDATER=1
cat > "$CLAUDE_CONFIG_DIR/webfetch_transport.cjs" <<'CHEESE_WEBFETCH'
{webfetch_transport}CHEESE_WEBFETCH
WEBFETCH_PRELOAD="$CLAUDE_CONFIG_DIR/webfetch_transport.cjs"
# Quote the whole first option: Bun skips quoted paths after another option
# and rejects quotes after the equals sign in --preload="path".
export BUN_OPTIONS="\\"--preload=$WEBFETCH_PRELOAD\\"${{BUN_OPTIONS:+ $BUN_OPTIONS}}"
# Chat guidance is now in the system prompt. Retire the generated skill on reuse.
rm -f "$CLAUDE_CONFIG_DIR/skills/cheese-chat/SKILL.md"
{skill_setup}
{ca_block}
# Written by the shell, not node: a machine whose `claude` is the native binary
# has no node at all (MicroCloud's Debian image is exactly that), and under
# `set -e` a missing node aborted the whole launch — the screen opened, claude
# never started, and the turn hung with nothing anywhere saying why. The only
# dynamic value here is a work dir this platform generates, so an unquoted
# heredoc is enough and depends on nothing.
#
# Inside CLAUDE_CONFIG_DIR, not at $HOME/.claude.json: with the config dir set,
# claude reads the onboarding/trust gates from THERE (verified — the gate in the
# dir let a non-interactive run proceed), and a file at $HOME/.claude.json would
# just be dead weight in the isolated home.
if [ -z "$WARM_ROOT" ]; then
cat > "$CLAUDE_CONFIG_DIR/.claude.json" <<JSON
{{"hasCompletedOnboarding":true,"autoUpdates":false,"bypassPermissionsModeAccepted":true,"projects":{{"$CHEESE_WORK":{{"hasTrustDialogAccepted":true,"hasCompletedProjectOnboarding":true}}}}}}
JSON
fi
cat > "$HOME/.claude/settings.json" <<'JSON'
{settings_json}
JSON
cat > "$HOME/.claude/cheese-system-prompt.md" <<'SYSPROMPT'
{system_prompt}SYSPROMPT
cat > "$HOME/.claude/cheese-sync" <<'SYNC'
{sync_script}SYNC
chmod +x "$HOME/.claude/cheese-sync"
cat > "$HOME/.claude/cheese-usage.py" <<'USAGEPY'
{usage_reader}USAGEPY
cat > "$HOME/.claude/cheese-usage" <<'USAGE'
{usage_script}USAGE
chmod +x "$HOME/.claude/cheese-usage"
cat > "$HOME/.claude/cheese-hook" <<'SH'
{_CHEESE_HOOK_SCRIPT}SH
chmod +x "$HOME/.claude/cheese-hook"
# Ship the platform CLI with the launcher over the existing device connection.
# A separate public HTTP download added 0.39-1.24s to measured launches and
# could block each launch for its 10s timeout.
cat > "$HOME/.claude/cheese" <<'CHEESE_PLATFORM_CLI'
{cli_source}CHEESE_PLATFORM_CLI
chmod +x "$HOME/.claude/cheese"
export PATH="$HOME/.claude:$PATH"
cheese_launch_phase files_written
# Extract the machine's own ccproxy ticket, READING the owner's files only —
# see CHEESE_SETTINGS_RECONCILE for why nothing is written there any more
# (CLAUDE_CONFIG_DIR made the owner's settings.json irrelevant to routing).
# Runs unconditionally: the script itself is a no-op off the machine-ticket
# path, and the owner's settings.json may be absent on a box whose ticket
# lives only in .credentials.json. Non-fatal, never silent: anything but "ok"
# is reported, and "no-machine-ticket" is the interesting case — without a
# ticket a pass-through turn dies upstream as an opaque 401.
cat > "$HOME/.claude/cheese-settings-reconcile.py" <<'RECONCILE'
{settings_reconcile}RECONCILE
rm -f "$HOME/.claude/cheese-machine.token"
CHEESE_ROUTE="$(python3 "$HOME/.claude/cheese-settings-reconcile.py" \\
  "$REAL_HOME/.claude/settings.json" \\
  "$HOME/.claude/cheese-machine.token" 2>&1 || echo "reconcile-crashed")"
cheese_launch_phase credentials_reconciled
# The model credential claude will actually use, asserted into the process
# environment — which is authoritative now that CLAUDE_CONFIG_DIR keeps claude
# out of the owner's settings.json (whose env block used to override us).
#
# Only the model call changes hands here. The scoped cheese token keeps doing
# its own job (proving which project to bill) as CHEESE_TOKEN and as the
# CONNECT password the tunnel helper stamps; the two are different credentials
# answering different questions, and conflating them is what sent a cheese
# token to Anthropic.
if [ -s "$HOME/.claude/cheese-machine.token" ]; then
  CLAUDE_CODE_OAUTH_TOKEN="$(cat "$HOME/.claude/cheese-machine.token")"
  export CLAUDE_CODE_OAUTH_TOKEN
fi
case "$CHEESE_ROUTE" in
  ok\\ *) ;;
  *) printf '{{"hook_event_name":"CheeseRoute","status":"failed","detail":"%s"}}' \\
       "$CHEESE_ROUTE" | cheese-hook >/dev/null 2>&1 || true ;;
esac
# Durable event delivery on the device: cheese-hook spools every hook and (via
# CHEESE_HOOK_SPOOL_ONLY) skips its own inline curl, so the cheese-drain script
# is the sole sender — it retries each spooled event until the backend DURABLY
# accepts it (code:200 = the backend wrote the event to the topic's server-side
# spool; anything else leaves this machine's copy in place, which is the only
# one there is), so a link/backend outage never drops an event. A 24h age cap
# stops an unreachable backend from accumulating retries forever. The backend
# dedups re-deliveries by event-id.
#
# The supervisor below owns the drainer for a newly started session.
export CHEESE_HOOK_SPOOL="$HOME/.claude/cheese-spool"
export CHEESE_HOOK_SPOOL_ONLY=1
mkdir -p "$CHEESE_HOOK_SPOOL"
cat > "$HOME/.claude/cheese-drain" <<'DRAIN'
{drain_script}DRAIN
chmod +x "$HOME/.claude/cheese-drain"
cat > "$HOME/.claude/cheese-drain.env.tmp" <<DRAINENV
CHEESE_HOOK_SPOOL="$CHEESE_HOOK_SPOOL"
CHEESE_HOOK_URL="$CHEESE_HOOK_URL"
CHEESE_TOKEN="$CHEESE_TOKEN"
DRAINENV
mv "$HOME/.claude/cheese-drain.env.tmp" "$HOME/.claude/cheese-drain.env"
# The tunnel helper, for a machine that cannot reach the meter's listener
# directly. Written on EVERY launch, token included: the helper re-reads the
# token per connection, so replacing this file is how a refreshed credential
# reaches a still-running helper (#385's shape, one layer down).
if [ -n "${{CHEESE_TUNNEL_URL:-}}" ]; then
  cat > "$HOME/.claude/cheese-tunnel.py" <<'TUNNELPY'
{tunnel_helper}TUNNELPY
  # Use the place-scoped CONNECT credential, including its RC claim. The hook
  # token can have project scope; the machine OAuth ticket is never a tunnel
  # credential. A missing CONNECT token must not fall back to either one.
  cat > "$HOME/.claude/cheese-tunnel.token.tmp" <<TUNNELTOK
$CHEESE_CONNECT_TOKEN
TUNNELTOK
  chmod 600 "$HOME/.claude/cheese-tunnel.token.tmp"
  mv "$HOME/.claude/cheese-tunnel.token.tmp" "$HOME/.claude/cheese-tunnel.token"
  cat > "$HOME/.claude/cheese-tunnel-up" <<'TUNNELUP'
{tunnel_up}TUNNELUP
  chmod +x "$HOME/.claude/cheese-tunnel-up"
fi
# 运行环境预览's helper. Written on EVERY launch, token included and for the same
# reason as the tunnel's: the helper re-reads the token per connection, so
# replacing this file is how a refreshed credential reaches a still-running one.
# Nothing is STARTED here — the up script is a no-op until an agent has declared
# a port with `cheese serve`, so a machine that never previews anything pays for
# no process.
if [ -n "${{CHEESE_PREVIEW_URL:-}}" ]; then
  cat > "$HOME/.claude/cheese-preview.py" <<'PREVIEWPY'
{preview_helper}PREVIEWPY
  cat > "$HOME/.claude/cheese-preview.token.tmp" <<PREVIEWTOK
$CHEESE_TOKEN
PREVIEWTOK
  chmod 600 "$HOME/.claude/cheese-preview.token.tmp"
  mv "$HOME/.claude/cheese-preview.token.tmp" "$HOME/.claude/cheese-preview.token"
  cat > "$HOME/.claude/cheese-preview-up" <<'PREVIEWUP'
{preview_up}PREVIEWUP
  chmod +x "$HOME/.claude/cheese-preview-up"
  # The ONE thing `cheese serve` needs to know about the preview helper. Exported
  # rather than reconstructed on the CLI's side: the server cannot know the
  # device user's home, and a path written down twice is a path that drifts.
  export CHEESE_PREVIEW_UP="$HOME/.claude/cheese-preview-up"
fi
cd "$CHEESE_WORK"
# --- prompt delivery: the rendezvous socket, and the version floor under it ---
# Prompts reach this claude over a unix socket it binds ITSELF (three env vars
# below), where the runtime enqueues them as `origin: {{kind:"human"}}` — the
# same place a keystroke lands. Nothing types into the terminal any more, so
# delivery no longer depends on pane width, TUI state, or reading the screen.
#
# The socket exists only from 2.1.224 on, and this launcher REFUSES to start an
# older claude rather than fall back to pasting: a silent downgrade to send-keys
# is exactly the failure mode this replaced (a prompt re-pasted 40 times with
# nobody the wiser). Exiting non-zero surfaces as a screen setup error on the
# turn, which is the honest outcome.
#
# The binary is pinned to a verified build: this is undocumented private
# surface and the frames DID move between 2.1.220 and 2.1.224, so "whatever
# `claude` resolves to today" is not a basis for a delivery path. PATH is the
# fallback, still gated by the floor.
#
# The pin is PLACED here when it is missing, from the platform — the same
# unauthenticated route enrollment downloads from — so a pin bump reaches a
# machine enrolled under the previous pin at its next launch, with no
# re-provisioning and no owner action. Before this, bumping the pin left every
# already-enrolled cloud machine with no binary at all: enrollment installs only
# versions/<pin> (deliberately no symlink), and nothing else on that machine
# has a claude. Non-fatal: the chain below still runs, and the floor check
# still refuses a build that is too old. A screen created without CHEESE_API
# skips this and behaves as before.
_pin="$REAL_HOME/.cheese/claude/versions/{pinned_version}"
if [ ! -x "$_pin" ] && [ -n "${{CHEESE_API:-}}" ]; then
  case "$(uname -m)" in
    x86_64|amd64) _carch=x64 ;;
    aarch64|arm64) _carch=arm64 ;;
    *) _carch="" ;;
  esac
  if [ -n "$_carch" ]; then
    if [ "$(uname -s)" = "Linux" ]; then
      if ldd /bin/ls 2>&1 | grep -q musl; then
        _cplat="linux-$_carch-musl"
      else
        _cplat="linux-$_carch"
      fi
    else
      _cplat="darwin-$_carch"
    fi
    mkdir -p "$(dirname "$_pin")"
    if curl -fsSL --retry 3 --retry-delay 2 -m 300 \\
        "${{CHEESE_API%/}}/connector/claude/{pinned_version}/$_cplat/claude" \\
        -o "$_pin.new" && [ -s "$_pin.new" ]; then
      chmod +x "$_pin.new" && mv "$_pin.new" "$_pin"
    else
      rm -f "$_pin.new"
      echo "cheese-launch: could not fetch claude {pinned_version} for \\
$_cplat from the platform; trying what the machine has" >&2
    fi
  fi
fi
CLAUDE_BIN=""
for _c in "$_pin" \\
          "$REAL_HOME/.local/bin/claude"; do
  if [ -x "$_c" ]; then CLAUDE_BIN="$_c"; break; fi
done
[ -z "$CLAUDE_BIN" ] && CLAUDE_BIN="$(command -v claude 2>/dev/null || true)"
if [ -z "$CLAUDE_BIN" ]; then
  echo "cheese-launch: no claude binary found" >&2
  exit 1
fi
CLAUDE_V="$("$CLAUDE_BIN" --version 2>/dev/null | head -n 1 | awk '{{print $1}}')"
cheese_launch_phase version_checked
if [ -z "$CLAUDE_V" ] || [ "$(printf '%s\\n%s\\n' "{minimum_version}" "$CLAUDE_V" \\
    | sort -V | head -n 1)" != "{minimum_version}" ]; then
  echo "cheese-launch: claude ${{CLAUDE_V:-unknown}} at $CLAUDE_BIN is older than \\
{minimum_version}, and the platform's pinned build is not at $_pin; prompt \\
delivery needs the rendezvous socket of a newer claude." >&2
  exit 1
fi
# One token per topic, on disk rather than in the env: an ADOPTED claude keeps
# the token it booted with, so a freshly generated value would never match. The
# file is the single copy the connector and this launcher both read.
if [ -n "${{CHEESE_RV_TOKEN_FILE:-}}" ]; then
  if [ ! -s "$CHEESE_RV_TOKEN_FILE" ] || [ ! -O "$CHEESE_RV_TOKEN_FILE" ]; then
    ( umask 077
      head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \\n' \\
        > "$CHEESE_RV_TOKEN_FILE.tmp" ) \\
      && mv "$CHEESE_RV_TOKEN_FILE.tmp" "$CHEESE_RV_TOKEN_FILE" \\
      || rm -f "$CHEESE_RV_TOKEN_FILE.tmp"
  fi
  CLAUDE_BG_BACKEND=daemon
  CLAUDE_BG_RENDEZVOUS_SOCK="$CHEESE_RV_SOCK"
  CLAUDE_BG_RV_AUTH="$(cat "$CHEESE_RV_TOKEN_FILE" 2>/dev/null || true)"
  export CLAUDE_BG_BACKEND CLAUDE_BG_RENDEZVOUS_SOCK CLAUDE_BG_RV_AUTH
fi
python3 - restore "$REAL_HOME" "$CLAUDE_V" \\
  "$CLAUDE_CONFIG_DIR" <<'CHEESE_NATIVE_CACHE'
{startup_cache_source}
CHEESE_NATIVE_CACHE
cheese_launch_phase cache_restored
CLAUDE="\\"$CLAUDE_BIN\\"{claude_args}"
[ -n "$CLAUDE_MODEL" ] && CLAUDE="$CLAUDE --model $CLAUDE_MODEL"
# 上一段对话接在哪儿。A screen is retired and reopened for reasons that have
# nothing to do with the conversation — an expired credential, a `claude` that
# died, a tunnel helper that went away — and the transcript of what was said
# outlives every one of them, sitting right here on this machine's disk. Without
# this the fresh `claude` starts from nothing and the topic loses its memory of
# its own turns each time.
#
# THE DECISION IS MADE HERE, on the machine, because this is where the file is.
# `--resume` pointed at a transcript that is not there does not degrade — claude
# exits and the pane never draws an input box — so it has to be guarded, and the
# backend cannot do the guarding: the device is behind NAT and its
# $CLAUDE_CONFIG_DIR is not a path the backend can stat. (That is also why the
# in-process `build_session_launch` guard, which reads the transcript through a
# shared mount, was never reachable from this transport.)
#
# The connector adopts live sessions without running this launcher again.
RESUMEF="$HOME/.claude/cheese-resume.attempt"
RESUME_TRIED="$(cat "$RESUMEF" 2>/dev/null || true)"
# Consumed on read, always. The stamp says "the last launch asked to resume THIS
# id and we never saw that session live again" — a transcript claude cannot read
# would otherwise kill the pane, get the session retired for a dead pane, and be
# resumed again on the relaunch, forever. Consuming it costs at most one lost
# continuation and cannot become a wedge: the very next launch starts clean.
# A prompt or completed turn clears the stamp through the hook forwarder.
rm -f "$RESUMEF"
if [ -n "${{CHEESE_RESUME_SESSION:-}}" ] \\
  && [ "$RESUME_TRIED" != "$CHEESE_RESUME_SESSION" ]; then
  # Any project slug: the filename is a uuid, so it identifies the session on its
  # own, and a transcript written under an older cwd is still this conversation.
  for _t in "$CLAUDE_CONFIG_DIR"/projects/*/"$CHEESE_RESUME_SESSION.jsonl"; do
    [ -s "$_t" ] || continue
    CLAUDE="$CLAUDE --resume $CHEESE_RESUME_SESSION"
    printf '%s\\n' "$CHEESE_RESUME_SESSION" > "$RESUMEF" 2>/dev/null || true
    break
  done
fi
# The platform system prompt (written next to settings.json above). The path is
# embedded QUOTED so both consumers survive a home dir with spaces: the tmux
# branch re-parses $CLAUDE through sh -c, the exec branch through eval.
CHEESE_SP="$HOME/.claude/cheese-system-prompt.md"
ENVIRONMENT_CMD=""
if [ -n "${{CHEESE_ENVIRONMENT:-}}" ]; then
  ENVIRONMENT_CMD="python3 \\"$HOME/.claude/cheese-environment.py\\" "
fi
[ -s "$CHEESE_SP" ] && CLAUDE="$CLAUDE --append-system-prompt-file \\"$CHEESE_SP\\""
{execution_setup}
if [ -n "$WARM_ROOT" ]; then
  cheese_launch_phase workspace_ready
  # Reuse the loaded helper for adoption and terminal attachment. The shipped
  # helper already exposes both operations, including on existing warm machines.
  exec python3 - "$REAL_HOME/.cheese/warm-native-runner.py" \\
    "$WARM_ROOT" 3<&0 <<'WARM_ATTACH'
import os
import runpy
import sys
import json
import subprocess
from pathlib import Path

# The heredoc carries code; tmux and environment scripts still need the PTY.
os.dup2(3, 0)
os.close(3)
runner = runpy.run_path(sys.argv[1])
directory = Path(sys.argv[2])
code = runner["adopt_room"](directory)
if code:
    raise SystemExit(code)
terminal = runner["connection"](
    directory, os.environ["CHEESE_PROJECT"], os.environ["CHEESE_TOPIC"]
)
outer_socket = os.environ["TMUX"].split(",", 1)[0]
outer_session = subprocess.check_output(
    ["tmux", "-S", outer_socket, "display-message", "-p", "-t",
     os.environ["TMUX_PANE"], "#S"],
    text=True,
).strip()
owned_terminal = {{
    "socket": terminal["command"][2],
    "session": terminal["command"][-1],
}}
subprocess.run(
    ["tmux", "-S", outer_socket, "set-option", "-t", outer_session,
     "@cheese-terminal", json.dumps(owned_terminal)],
    check=True,
)
os.environ.pop("TMUX", None)
os.execvp("tmux", terminal["command"])
WARM_ATTACH
fi
# The connector owns the terminal session. This shell owns its children.
if [ -n "${{TMUX:-}}" ]; then
  CHEESE_TMUX_SOCK="${{TMUX%%,*}}"
  SESSION="$(tmux -S "$CHEESE_TMUX_SOCK" display-message -p -t "$TMUX_PANE" '#S')"
  python3 -c 'import json,sys; json.dump(sys.argv[1:], open(sys.argv[3], "w"))' \\
    "$CHEESE_TMUX_SOCK" "$SESSION" "$HOME/.claude/environment-session.json"
fi
printf '%s' "${{CHEESE_AGENT_CONFIG:-}}" > "$HOME/.claude/agent-configuration"
printf '%s' {shlex.quote(claude_args)} > "$HOME/.claude/launch-contract"
CLAUDE_PID=""
DRAIN_PID=""
cleanup() {{
  trap '' HUP INT TERM
  [ -z "$CLAUDE_PID" ] || kill "$CLAUDE_PID" 2>/dev/null || true
  [ -z "$DRAIN_PID" ] || kill "$DRAIN_PID" 2>/dev/null || true
  wait 2>/dev/null || true
}}
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
trap cleanup EXIT
if [ -n "${{CHEESE_TUNNEL_URL:-}}" ]; then
  sh "$HOME/.claude/cheese-tunnel-up" || exit 1
fi
if [ -n "${{CHEESE_PREVIEW_URL:-}}" ]; then
  sh "$HOME/.claude/cheese-preview-up" || exit 1
fi
CHEESE_DRAIN_TETHER=$$ sh "$HOME/.claude/cheese-drain" >/dev/null 2>&1 &
DRAIN_PID=$!
# Preserve input before POSIX sh redirects an asynchronous command's fd 0.
exec 3<&0
eval "exec $ENVIRONMENT_CMD$CLAUDE" <&3 3<&- &
CLAUDE_PID=$!
RESULT=0
wait "$CLAUDE_PID" || RESULT=$?
CLAUDE_PID=""
exit "$RESULT"

"""


def build_screen_launch(
    *,
    hook_url: str,
    hook_token: str,
    home_dir: str,
    work_dir: str,
    model: str | None = None,
    resume_session_id: str | None = None,
    extra_env: dict[str, str] | None = None,
    api_base: str | None = None,
    project_id: str | None = None,
    topic_id: str | None = None,
    author: str | None = None,
    git_author: tuple[str, str] | None = None,
    git_remote: str | None = None,
    system_prompt: str = "",
    ca_pem: str = "",
    execution_target: dict | None = None,
) -> tuple[list[str], dict[str, str]]:
    """Assemble ``(command, env)`` for ``DeviceHub.open_screen``.

    ``command`` is a self-contained ``bash -lc`` launcher; ``env`` carries the hook
    wiring + home/work dirs + model + any provider (gateway) vars, and — for a
    screen with a topic — where its rendezvous socket lives. When
    ``api_base`` and the ``project_id``/``topic_id`` context are given, the
    bundled ``cheese`` CLI (accept cards / docs / decisions / memory) uses
    its ``CHEESE_*`` env — the same actions the in-container agent has locally."""
    script = build_launch_script(
        sync_on_stop=bool(git_remote) and execution_target is None,
        system_prompt=system_prompt,
        ca_pem=ca_pem,
        remote_control=(extra_env or {}).get("CHEESE_REMOTE_CONTROL") == "1",
        remote_execution=execution_target is not None,
    )
    command = ["bash", "-lc", script]
    env: dict[str, str] = {
        "CHEESE_HOOK_URL": hook_url,
        "CHEESE_TOKEN": hook_token,
        "CHEESE_HOME": home_dir,
        "CHEESE_WORK": work_dir,
        # Work is a subagent of the room's session, so these two are the shape
        # of the room itself. Depth 1: a piece of work does not split further —
        # its own children would be invisible to the platform (nothing binds
        # them to a card) and unaddressable by a person. Concurrency 4: how
        # many pieces of work a room runs at once; they share one worktree, so
        # the ceiling is about how much simultaneous editing of one tree stays
        # comprehensible, not about machine capacity.
        "CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH": "1",
        "CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS": "4",
    }
    if model:
        env["CLAUDE_MODEL"] = model
    if resume_session_id:
        # An OFFER, not an instruction: the launcher takes it only if the
        # transcript is on that machine's disk (see the script). Carried on the
        # env for the same reason the tunnel vars are — a remote launch is built
        # entirely out of `extra_env`, and there is no other channel into it.
        env["CHEESE_RESUME_SESSION"] = resume_session_id
    # Platform-action CLI wiring: the `cheese` script reads these (X-Cheese-Token =
    # CHEESE_TOKEN, the SAME scoped token the hook forwarder uses).
    if api_base:
        env["CHEESE_API"] = api_base
    if project_id:
        env["CHEESE_PROJECT"] = project_id
    if topic_id:
        env["CHEESE_TOPIC"] = topic_id
        # Where this screen's prompts arrive. The launcher turns these two into
        # Claude Code's own CLAUDE_BG_* trio and mints the token; the connector
        # reads the same two to dial. Keyed on the topic so an adopted screen
        # and a fresh one agree on the path.
        sock, token_file = rendezvous_paths(topic_id)
        env[ENV_RV_SOCK] = sock
        env[ENV_RV_TOKEN_FILE] = token_file
    if author:
        env["CHEESE_AUTHOR"] = author
    if git_remote:
        env["CHEESE_GIT_REMOTE"] = git_remote
    if git_author:
        # Who the turn's commits belong to (workspace/identity.py). Absent, the
        # launcher falls back to 芝士 — the same default the in-repo snapshot
        # path uses, so both surfaces agree.
        env["CHEESE_GIT_AUTHOR_NAME"], env["CHEESE_GIT_AUTHOR_EMAIL"] = git_author
        env["GIT_AUTHOR_NAME"], env["GIT_AUTHOR_EMAIL"] = git_author
        env["GIT_COMMITTER_NAME"] = "芝士"
        env["GIT_COMMITTER_EMAIL"] = "cheese@zhishi.local"
    if extra_env:
        env.update(extra_env)
    if execution_target is not None:
        env["CHEESE_EXECUTION_TARGET"] = json.dumps(execution_target)
        # The assigned executor already owns the checkout and its environment.
        for name in (
            "CHEESE_GIT_REMOTE",
            "CHEESE_GIT_BRANCH",
            "CHEESE_BRANCH_URL",
            "CHEESE_ENVIRONMENT",
            "CHEESE_PREVIEW_URL",
            "CHEESE_PREVIEW_UP",
        ):
            env.pop(name, None)
    return command, env


def ensure_dir(path: str) -> str:
    """Create ``path`` (the isolated home/work dir) if missing; return it. Used by the
    spike / local-device provider where the device is this same machine."""
    Path(path).mkdir(parents=True, exist_ok=True)
    return path


# shlex is imported for callers that need to render the command for logging/debug.
def command_for_log(command: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in command)
