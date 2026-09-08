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
from app.domain.agent.harness.claude_code import event_drain, startup_cache
from app.domain.agent.harness.claude_code.cli import CLAUDE_BASE_CMD
from app.domain.agent.harness.claude_code.hooks_substrate import CHEESE_HOOK_SCRIPT
from app.domain.agent.harness.claude_code.session_launch import hooks_settings

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
# Linux: match the topic on each process's own environ → per-topic precise. The
# connector (same user as the screen it spawned) can read that same-uid /proc entry.
if [ -d /proc ] && [ -r /proc/self/environ ]; then
  for c in /proc/[0-9]*/cmdline; do
    [ -r "$c" ] || continue
    case "$(tr '\0' ' ' < "$c" 2>/dev/null)" in *claude*) ;; *) continue ;; esac
    d="${c%/cmdline}"
    if tr '\0' '\n' < "$d/environ" 2>/dev/null | grep -qx "CHEESE_TOPIC=$topic"; then
      echo alive; exit 0
    fi
  done
  # /proc was readable but no live `claude` carries this topic → its process is gone.
  echo dead; exit 0
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


# The spool drainer, shipped to "$HOME/.claude/cheese-drain" and started INSIDE
# claude's own tmux session (same life, same death). It used to be backgrounded
# in the OUTER launcher tree — the CONNECTOR's process tree — so a connector
# restart killed the drainer while claude survived inside tmux: claude kept
# working, every hook landed in the spool, and nothing ever sent them (84 events
# piled up on a dev box while the platform read the turn as unresponsive).
#
# Config (spool dir / hook URL / token) is sourced from "$0.env" on EVERY pass:
# the launcher rewrites that file (atomically) on each launch, so a drainer
# adopted from an earlier turn delivers with the CURRENT turn's token and URL,
# not the ones its session was born with. "$0.pid" is the idempotence handle —
# a relaunch checks it before starting a second drainer. (Both are per isolated
# HOME, matching the spool itself, which a project's topics share.)
#
# CHEESE_DRAIN_TETHER (set by the revival path only): the pid of the claude
# pane this drainer was revived NEXT TO. A revived drainer runs in its own tmux
# window, and a window with no exit condition would hold the session open after
# claude died — the next launch would then adopt a claude-less session and
# prompt into nothing. The tether makes it exit when claude goes, taking the
# window (and with it the otherwise-empty session) down.
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
        time.sleep(0.1)
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
    return "#!/bin/sh\nexec python3 - \"$0\" <<'PY'\n" + source + "\nPY\n"


# Bringing a device's workspace up, kept out of the launcher's f-string (and
# raw, so it reads as the shell it is) because a test runs this exact text
# against real repositories — a fix that never reached the launcher must not be
# able to pass.
#
# "There is a .git directory" is not the same question as "this workspace
# works", and three separate incidents came out of the gap. git points HEAD at
# refs/heads/.invalid for the WHOLE duration of a clone and only names the real
# branch as its last step (poll HEAD through a clone and that is exactly what
# you see). A clone killed in between — a container torn down, a machine
# rebooted — leaves a .git that satisfies [ -d ], answers `git log` with "fatal:
# your current branch appears to be broken", and still lets `git status` exit 0
# on a clean tree. The old guard was `[ ! -d .git ]`, so every later launch
# stepped over the wreck and left the agent to find it.
#
# So: ask git whether it can read HEAD, repair in place when it can be repaired,
# and say out loud when it cannot. Nothing here ever deletes a .git — a
# repository this platform cannot read may still be the only place the agent's
# unpushed commits exist.
CHEESE_WORKSPACE_BRINGUP = r"""# A device starts with an empty work dir.
if [ -n "${CHEESE_GIT_REMOTE:-}" ]; then
  CHEESE_WS_BRANCH="${CHEESE_GIT_BRANCH:-main}"
  CHEESE_WS_NOTE=""
  cheese_ws_say() {
    CHEESE_WS_NOTE="${CHEESE_WS_NOTE:+$CHEESE_WS_NOTE; }$1"
  }
  # Unreadable, not merely unusual. A detached HEAD (an agent bisecting) and an
  # unborn branch are both fine and both must be left alone; only a HEAD that
  # answers NEITHER question is the placeholder a killed clone leaves behind.
  cheese_ws_unreadable() {
    git -C "$CHEESE_WORK" rev-parse --verify -q HEAD >/dev/null 2>&1 && return 1
    git -C "$CHEESE_WORK" symbolic-ref -q HEAD >/dev/null 2>&1 && return 1
    return 0
  }
  # Adopt whatever .git is there: name the branch, put HEAD on it, fill in files
  # that are simply absent. Deliberately conservative — it never moves a branch
  # that already exists and never overwrites a file that is already there,
  # because either one can be the only copy of something. It reports success
  # only if git can read HEAD afterwards.
  cheese_ws_adopt() {
    git -C "$CHEESE_WORK" config user.name "${CHEESE_GIT_AUTHOR_NAME:-芝士}" 2>/dev/null
    git -C "$CHEESE_WORK" config user.email \
      "${CHEESE_GIT_AUTHOR_EMAIL:-cheese@zhishi.local}" 2>/dev/null
    git -C "$CHEESE_WORK" config http.extraHeader \
      "X-Cheese-Token: $CHEESE_TOKEN" 2>/dev/null
    git -C "$CHEESE_WORK" show-ref -q --verify \
      "refs/heads/$CHEESE_WS_BRANCH" 2>/dev/null \
      || git -C "$CHEESE_WORK" update-ref "refs/heads/$CHEESE_WS_BRANCH" \
           "refs/remotes/origin/$CHEESE_WS_BRANCH" 2>/dev/null \
      || git -C "$CHEESE_WORK" update-ref "refs/heads/$CHEESE_WS_BRANCH" \
           HEAD 2>/dev/null \
      || true
    git -C "$CHEESE_WORK" symbolic-ref HEAD \
      "refs/heads/$CHEESE_WS_BRANCH" 2>/dev/null || true
    git -C "$CHEESE_WORK" reset -q --mixed 2>/dev/null || true
    git -C "$CHEESE_WORK" checkout-index -a -q 2>/dev/null || true
    git -C "$CHEESE_WORK" symbolic-ref -q HEAD >/dev/null 2>&1
  }
  if [ -d "$CHEESE_WORK/.git" ] && cheese_ws_unreadable; then
    if cheese_ws_adopt; then
      cheese_ws_say "repaired a workspace an interrupted clone left behind"
    elif mv "$CHEESE_WORK/.git" "$CHEESE_WORK/.git.broken.$$" 2>/dev/null; then
      cheese_ws_say "set an unreadable .git aside as .git.broken.$$"
    else
      cheese_ws_say "found an unreadable .git and could not move it aside"
    fi
  fi
  if [ ! -d "$CHEESE_WORK/.git" ]; then
    # Clone next door and move the .git in, rather than cloning INTO the work
    # dir: git refuses to clone into a directory that is not empty, so a
    # workspace that had lost only its .git got no repository at all — and
    # `2>/dev/null || true` made that indistinguishable from success.
    CHEESE_WS_TMP="$CHEESE_WORK.clone.$$"
    rm -rf "$CHEESE_WS_TMP"
    # A shallow, single-branch clone: the tip of one branch, never the project's
    # whole history. A device only clones and pushes its own topic tree — every
    # history-dependent operation (diff, merge-base, 采纳's merge) runs on the
    # platform's full repo, not here — so the tip is all a workspace needs, and
    # fetching it is O(one commit) rather than O(every topic branch this project
    # has ever opened). A full clone of an active project's proxy is minutes and
    # hundreds of MB of history the agent never reads; this is seconds.
    cheese_ws_git() {
      git -c http.extraHeader="X-Cheese-Token: $CHEESE_TOKEN" "$@"
    }
    # WHICH branch to clone is decided by asking the server whether the topic
    # branch is there yet — not by cloning it and falling back on failure. The
    # difference matters: a resumed topic, or one a fileless device already
    # pushed to, carries commits on its branch that are the only checkout of that
    # work, and a transient clone failure must be REPORTED, never quietly
    # answered by checking out the base branch instead — that would hand the
    # agent a stale tree and let its next push clobber or diverge from the real
    # tip. So: clone the topic branch only when it provably exists; otherwise
    # (a brand-NEW topic, whose branch the device itself creates from the base
    # tip and pushes later) clone the default branch and let cheese_ws_adopt
    # synthesize the topic branch from its HEAD, exactly as the old full clone
    # did. Either way it is one branch, one commit deep.
    if cheese_ws_git ls-remote --exit-code --heads \
         "$CHEESE_GIT_REMOTE" "$CHEESE_WS_BRANCH" >/dev/null 2>&1; then
      CHEESE_WS_ERR="$(cheese_ws_git clone -q --no-checkout --depth 1 \
        --single-branch --branch "$CHEESE_WS_BRANCH" \
        "$CHEESE_GIT_REMOTE" "$CHEESE_WS_TMP" 2>&1)" || true
    else
      CHEESE_WS_ERR="$(cheese_ws_git clone -q --no-checkout --depth 1 \
        "$CHEESE_GIT_REMOTE" "$CHEESE_WS_TMP" 2>&1)" || true
    fi
    if [ -d "$CHEESE_WS_TMP/.git" ] \
       && mv "$CHEESE_WS_TMP/.git" "$CHEESE_WORK/.git" 2>/dev/null; then
      cheese_ws_adopt \
        || cheese_ws_say "cloned it but could not put HEAD on $CHEESE_WS_BRANCH"
    else
      cheese_ws_say "could not clone the repository: ${CHEESE_WS_ERR:-git said nothing}"
    fi
    rm -rf "$CHEESE_WS_TMP"
  fi
  # Quotes and newlines are dropped rather than escaped: a git error message is
  # worth carrying verbatim, its punctuation is not worth a quoting bug.
  if [ -n "$CHEESE_WS_NOTE" ]; then
    printf '{"hook_event_name":"CheeseWorkspace","detail":"%s"}' \
      "$(printf '%s' "$CHEESE_WS_NOTE" | tr -d '"\\' | tr '\n\r\t' '   ')" \
      | cheese-hook >/dev/null 2>&1 || true
  fi
fi
"""


# The end-of-turn snapshot. Two different jobs live here, and conflating them
# cost a day's work: PUBLISHING what the agent committed, so 采纳 and the PR see
# it, and BACKING UP what it has not committed, so a machine that disappears
# does not take the only copy.
#
# The old script did both with one `git add -A && git commit && git push`, which
# moved HEAD. That is what turned a backup into a hazard. An agent mid-debug had
# temporarily reverted a file to prove a test went red; the turn ended, the
# snapshot committed the revert and pushed it, and the agent's next
# `git checkout HEAD -- <file>` — the ordinary way back — restored the REVERT,
# because HEAD was no longer the commit it had read. Thirty-three lines of
# production code left the shared branch with every test still green and nothing
# in `git status` to see. Bisecting, A/B comparison, "prove it fails first", a
# temporary print: every one of them assumes HEAD stays put.
#
# So HEAD is never moved here, and neither is the branch — the branch only ever
# advances to commits the agent made itself. Uncommitted work is written as a
# commit OBJECT through a scratch index (so the real index is untouched too) and
# pushed to refs/cheese/snapshots/<branch>: off the machine and recoverable,
# where it cannot be mistaken for delivered work.
CHEESE_SYNC_SCRIPT = r"""#!/bin/sh
# Silent by design in the sense that matters — a Stop hook that fails must never
# take the turn down with it. It exits 0 either way; what it must not do is
# leave no trace, which is why the outcome goes out through cheese-hook (durable
# spool, retries until the backend acknowledges).
[ -n "${CHEESE_GIT_REMOTE:-}" ] || exit 0
[ -d "$CHEESE_WORK/.git" ] || exit 0
cd "$CHEESE_WORK" || exit 0
branch="${CHEESE_GIT_BRANCH:-main}"
head="$(git rev-parse --verify -q HEAD 2>/dev/null || true)"
failed=""
tried=""
if [ -n "$head" ]; then
  tried=1
  git push -q origin "$head:refs/heads/$branch" >/dev/null 2>&1 || failed=1
fi
# The scratch index lives inside .git so it is never something the agent can see
# and never a path git would try to add to itself.
snapshot=""
idx="$CHEESE_WORK/.git/cheese-snapshot-index"
rm -f "$idx"
if [ -n "$head" ]; then
  GIT_INDEX_FILE="$idx" git read-tree "$head" >/dev/null 2>&1 || rm -f "$idx"
fi
tree=""
if GIT_INDEX_FILE="$idx" git add -A >/dev/null 2>&1; then
  tree="$(GIT_INDEX_FILE="$idx" git write-tree 2>/dev/null || true)"
fi
rm -f "$idx"
headtree=""
if [ -n "$head" ]; then
  headtree="$(git rev-parse -q --verify "$head^{tree}" 2>/dev/null || true)"
fi
if [ -n "$tree" ] && [ "$tree" != "$headtree" ]; then
  if [ -n "$head" ]; then
    snapshot="$(git commit-tree "$tree" -p "$head" \
      -m "cheese: uncommitted workspace after an agent turn" 2>/dev/null || true)"
  else
    snapshot="$(git commit-tree "$tree" \
      -m "cheese: uncommitted workspace after an agent turn" 2>/dev/null || true)"
  fi
fi
if [ -n "$snapshot" ]; then
  tried=1
  git push -q -f origin "$snapshot:refs/cheese/snapshots/$branch" >/dev/null 2>&1 \
    || failed=1
fi
[ -n "$tried" ] || exit 0
if [ -n "$failed" ]; then status=failed; else status=ok; fi
{
  printf '{"hook_event_name":"CheeseSync","status":"%s",' "$status"
  printf '"commit":"%s","snapshot":"%s","branch":"%s"}' "$head" "$snapshot" "$branch"
} | cheese-hook >/dev/null 2>&1 || true
"""


def build_launch_script(
    sync_on_stop: bool = False, system_prompt: str = "", ca_pem: str = ""
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
    workspace_bringup = CHEESE_WORKSPACE_BRINGUP
    settings_reconcile = CHEESE_SETTINGS_RECONCILE
    startup_cache_source = Path(startup_cache.__file__).read_text()
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
            ["cheese-sync", "cheese-usage"] if sync_on_stop else ["cheese-usage"]
        ),
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
cat > "$CLAUDE_CONFIG_DIR/.claude.json" <<JSON
{{"hasCompletedOnboarding":true,"autoUpdates":false,"bypassPermissionsModeAccepted":true,"projects":{{"$CHEESE_WORK":{{"hasTrustDialogAccepted":true,"hasCompletedProjectOnboarding":true}}}}}}
JSON
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
# Give every device the topic branch: a real checkout it can push back from so
# 采纳 sees what the agent wrote. It runs here, after the forwarder is on PATH,
# because what this step has to say when it goes wrong is the whole point of it.
{workspace_bringup}
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
# The drainer is NOT started here: this launcher runs in the CONNECTOR's
# process tree, which dies with the connector while claude survives in its own
# tmux — a drainer backgrounded here died exactly then, and every later hook
# spooled with no sender. It starts inside claude's tmux session below (same
# life, same death). Only its config is written here, atomically (tmp + mv, so
# a running drainer never sources a half-written file) and on EVERY launch, so
# an adopted drainer always delivers with the current turn's token/URL.
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
  # CHEESE_TOKEN, deliberately — not CLAUDE_CODE_OAUTH_TOKEN. The helper's token
  # answers "which project is opening this tunnel, and may it spend", which is
  # the scoped cheese token's job and nothing else's. They used to be the same
  # string, so reading either worked by accident; on an enrolled machine
  # CLAUDE_CODE_OAUTH_TOKEN is now the machine's ccproxy ticket, and stamping
  # THAT as the CONNECT password would get every tunnel refused with 407.
  cat > "$HOME/.claude/cheese-tunnel.token.tmp" <<TUNNELTOK
$CHEESE_TOKEN
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
# Host claude in a PERSISTENT tmux session so it survives a link/screen drop: the
# session keeps running on the device and re-opening the screen re-attaches to it
# (the PTY mirrors the pane for the human viewer). Direct exec if tmux isn't
# installed.
# Prefix that must complete BEFORE claude: it brings the tunnel helper up and
# waits for its port, because claude reads HTTPS_PROXY once and calls out
# immediately. Empty when this deployment has no tunnel, so the direct path
# does not pay for a script it does not use.
TUP=""
if [ -n "${{CHEESE_TUNNEL_URL:-}}" ]; then
  TUP="sh \\"$HOME/.claude/cheese-tunnel-up\\" >/dev/null 2>&1;"
fi
# Same shape for the preview helper, and it is genuinely a prefix rather than a
# background job of its own: the script backgrounds the helper and returns at
# once, and it exits without doing anything at all when no preview was ever
# declared here.
PUP=""
if [ -n "${{CHEESE_PREVIEW_URL:-}}" ]; then
  PUP="sh \\"$HOME/.claude/cheese-preview-up\\" >/dev/null 2>&1;"
fi
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
_pin="$REAL_HOME/.local/share/claude/versions/{CLAUDE_PINNED_VERSION}"
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
        "${{CHEESE_API%/}}/connector/claude/{CLAUDE_PINNED_VERSION}/$_cplat/claude" \\
        -o "$_pin.new" && [ -s "$_pin.new" ]; then
      chmod +x "$_pin.new" && mv "$_pin.new" "$_pin"
    else
      rm -f "$_pin.new"
      echo "cheese-launch: could not fetch claude {CLAUDE_PINNED_VERSION} for \\
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
if [ -z "$CLAUDE_V" ] || [ "$(printf '%s\\n%s\\n' "{CLAUDE_MIN_VERSION}" "$CLAUDE_V" \\
    | sort -V | head -n 1)" != "{CLAUDE_MIN_VERSION}" ]; then
  echo "cheese-launch: claude ${{CLAUDE_V:-unknown}} at $CLAUDE_BIN is older than \\
{CLAUDE_MIN_VERSION}, and the platform's pinned build is not at $_pin; prompt \\
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
CLAUDE="\\"$CLAUDE_BIN\\"{CLAUDE_BASE_ARGS}"
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
# Only the CREATE branch below can use it: an adopted session already carries a
# running conversation, and $CLAUDE goes unread there.
RESUMEF="$HOME/.claude/cheese-resume.attempt"
RESUME_TRIED="$(cat "$RESUMEF" 2>/dev/null || true)"
# Consumed on read, always. The stamp says "the last launch asked to resume THIS
# id and we never saw that session live again" — a transcript claude cannot read
# would otherwise kill the pane, get the session retired for a dead pane, and be
# resumed again on the relaunch, forever. Consuming it costs at most one lost
# continuation and cannot become a wedge: the very next launch starts clean.
# A resume that WORKED clears it just as well, because the run that adopts that
# live session reads the stamp and writes nothing back.
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
if command -v tmux >/dev/null 2>&1; then
  # WHICH tmux server hosts the inner session decides who is able to wipe it.
  # The machine's DEFAULT server belongs to the person whose machine this is:
  # their `tmux kill-server` would take every agent on the box with it, our
  # teardown would take their sessions, and their `tmux ls` would list our
  # internals — the constraint device-self-hosting §0 exists to hold. So the
  # session lives in the CONNECTOR's private server: the very one this launcher
  # is already running inside.
  #
  # That socket needs no new contract between connector and launcher, because
  # tmux hands it to every pane as the first field of $TMUX
  # ("<socket>,<pid>,<session>"). Read it before unsetting. With no connector
  # around it, a per-user socket of our own still keeps us off the owner's
  # server — the invariant holds either way, which is the point.
  CHEESE_TMUX_SOCK="${{TMUX%%,*}}"
  if [ -z "$CHEESE_TMUX_SOCK" ]; then
    CHEESE_TMUX_DIR="/tmp/cheese-$(id -u)"
    (umask 077 && mkdir -p "$CHEESE_TMUX_DIR") || true
    CHEESE_TMUX_SOCK="$CHEESE_TMUX_DIR/agent.sock"
  fi
  # tmux refuses to attach from inside a pane while $TMUX is set, so it has to
  # go. Every command below names the socket explicitly instead of inheriting
  # one, so nothing in this block can silently land on the default server.
  unset TMUX
  atmux() {{ tmux -S "$CHEESE_TMUX_SOCK" "$@"; }}
  # The session name is derived from the WORK DIR, never a fixed "cheese": one
  # shared session made every topic on a device attach to whatever cwd the FIRST
  # topic had, so later topics edited the wrong tree and never saw new launcher
  # env (observed live: a 7-day-old session still serving new topics). Keying on
  # the work dir gives per-topic isolation AND retires a stale session whenever
  # the resolved work dir changes.
  SESSION="cheese_$(printf '%s' "$CHEESE_WORK" | cksum | cut -d' ' -f1)"
  python3 -c 'import json,sys; json.dump(sys.argv[1:], open(sys.argv[3], "w"))' \\
    "$CHEESE_TMUX_SOCK" "$SESSION" "$HOME/.claude/environment-session.json"
  # An agent session is never the owner's to carry. One sitting on the default
  # server is ours all the same, and it is not harmless: it holds this topic's
  # rendezvous socket, spool and work tree, so leaving it running means a second
  # claude answering for this topic forever. Retire it. `has-session` never
  # starts a server, so a machine with no default server keeps not having one.
  if tmux has-session -t "$SESSION" 2>/dev/null; then
    tmux kill-session -t "$SESSION" 2>/dev/null || true
  fi
  # A surviving inner session runs the `claude` it was BORN with, and claude
  # reads its model credential (CLAUDE_CODE_OAUTH_TOKEN / the HTTPS_PROXY
  # password) ONCE at startup — it never re-reads it. So the fresh scoped token
  # THIS launch just minted never reaches an adopted process: once the baked
  # token expires the metering proxy answers 407 on every turn, and no relaunch,
  # backend redeploy, or re-mint fixes it because the long-lived process keeps
  # the dead credential. That reuse is the second layer under #385 — extending
  # the TTL from 1h to a session only delays the day the baked token dies under a
  # still-running claude. So before adopting, retire a session whose recorded
  # token expiry (written in the create branch below) is past, seconds from
  # expiring, or missing; the create branch then replaces it with a claude
  # carrying THIS launch's live token. A session whose token is still good is
  # adopted unchanged — no churn, and an in-flight turn is never interrupted. The
  # margin is deliberately small: it only rejects an already-dead-or-dying token,
  # never a healthy one, so a short-lived credential (the gateway path's hour) is
  # re-minted at most once an hour rather than on every turn.
  # Configuration is checked at the turn boundary, including after backend restart.
  printf '%s' "${{CHEESE_AGENT_CONFIG:-}}" > "$HOME/.claude/agent-configuration"
  EXPFILE="$HOME/.claude/$SESSION.tokexp"
  if atmux has-session -t "$SESSION" 2>/dev/null; then
    TOKEXP="$(cat "$EXPFILE" 2>/dev/null || true)"
    case "$TOKEXP" in ''|*[!0-9]*) TOKEXP=0 ;; esac
    # Retire on a dead credential OR on a changed launch contract. claude reads
    # settings.json ONCE at startup, so a screen reused across turns keeps
    # whatever contract it was born with — a shipped change to WHICH ticket it
    # carries reaches the file and never reaches the process. Measured
    # 2026-08-14: the file said one thing and the running claude was still
    # failing on the other. Same reasoning as the tunnel helper's stamp.
    CFGF="$HOME/.claude/$SESSION.cfg"
    # The machine's ticket is part of the contract, and it does NOT live in that
    # settings.json as far as this process is concerned — the launcher exports
    # it, so a ticket that changed (or appeared for the first time, the moment
    # this path shipped) leaves the file byte-identical and the running claude
    # holding the old credential forever. Folding the ticket into the checksum
    # is what makes a rotation reach the process. On a device with no ticket the
    # file is absent and this is the old checksum unchanged, so nothing churns.
    CFGNOW="$(cat "$REAL_HOME/.claude/settings.json" \\
      "$HOME/.claude/cheese-machine.token" \\
      "$HOME/.claude/agent-configuration" 2>/dev/null | cksum | cut -d" " -f1)"
    CFGWAS="$(cat "$CFGF" 2>/dev/null || true)"
    RETIRE=0
    [ -f "$HOME/.claude/environment-restart" ] && RETIRE=1
    [ "$TOKEXP" -le "$(( $(date +%s) + 300 ))" ] && RETIRE=1
    [ -n "$CFGNOW" ] && [ "$CFGNOW" != "$CFGWAS" ] && RETIRE=1
    # The connector's server keeps a pane after its program exits, so a claude
    # that died leaves the session standing with a DEAD pane. Adopting that
    # hosts nothing: every later turn attaches to a corpse and the topic never
    # gets a claude again. A dead first pane is as good a reason to retire as a
    # dead credential.
    case "$(atmux list-panes -s -t "$SESSION" -F '#{{pane_dead}}' 2>/dev/null \\
      | head -n 1)" in 1) RETIRE=1 ;; esac
    if [ "$RETIRE" = 1 ]; then
      atmux kill-session -t "$SESSION" 2>/dev/null || true
    fi
  fi
  if atmux has-session -t "$SESSION" 2>/dev/null; then
    # Adopt: claude (and normally the drainer sharing its pane, started below)
    # is already running — never start a second drainer. But a session CAN
    # outlive its drainer (one created before the drainer moved in-session; a
    # crashed loop), so when the recorded pid is gone, revive one in a window
    # of THIS session — tethered to the claude pane so it can never outlive
    # claude and pin the session open.
    # Same reasoning as the drainer below: a session can outlive the helper
    # (connector restart, crashed loop), and claude keeps pointing at that dead
    # loopback port — every turn then fails looking exactly like a stalled model.
    # `cheese-tunnel-up` adopts a live one and starts a new one otherwise.
    #
    # No tether here, unlike the drainer's window. The drainer needs one because
    # it RUNS in that window and would hold the session open after claude died;
    # this window only starts a helper that now (`nohup`) outlives it and exits
    # straight away, so there is nothing left to pin the session down.
    if [ -n "${{CHEESE_TUNNEL_URL:-}}" ]; then
      atmux new-window -d -t "$SESSION" -n cheese-tunnel \\
        "CHEESE_TUNNEL_URL=$CHEESE_TUNNEL_URL CHEESE_TUNNEL_PORT=$CHEESE_TUNNEL_PORT \\
         exec sh \\"$HOME/.claude/cheese-tunnel-up\\"" \\
        || true
    fi
    # A preview declared on an earlier turn outlives the helper that carried it
    # (a killed process, a machine reboot), and the room would then show a dead
    # panel for an app that is still running. The up script adopts a live helper
    # and is a no-op when nobody ever declared a port, so running it on every
    # adopt costs a `cksum` and restores a preview that would otherwise need the
    # agent to declare it again.
    if [ -n "${{CHEESE_PREVIEW_URL:-}}" ]; then
      atmux new-window -d -t "$SESSION" -n cheese-preview \\
        "CHEESE_PREVIEW_URL=$CHEESE_PREVIEW_URL \\
         exec sh \\"$HOME/.claude/cheese-preview-up\\"" \\
        || true
    fi
    DRAIN_PID="$(cat "$HOME/.claude/cheese-drain.pid" 2>/dev/null || true)"
    if [ -z "$DRAIN_PID" ] || ! kill -0 "$DRAIN_PID" 2>/dev/null; then
      TETHER="$(atmux list-panes -s -t "$SESSION" -F '#{{pane_pid}}' \\
        2>/dev/null | head -n 1)"
      atmux new-window -d -t "$SESSION" -n cheese-drain \\
        "CHEESE_DRAIN_TETHER=$TETHER exec sh \\"$HOME/.claude/cheese-drain\\"" \\
        || true
    fi
  else
    # The drainer is backgrounded INSIDE the session command, then the shell
    # execs claude in the same pane: the whole delivery chain lives and dies
    # with the tmux session, not with the connector that spawned this launcher.
    # Stamp the token expiry this claude is BORN with so the gate above can later
    # tell a stale-credential session from a good one and retire only the stale.
    printf '%s\\n' "${{CHEESE_TOKEN_EXPIRES:-0}}" > "$EXPFILE" 2>/dev/null || true
    cat "$REAL_HOME/.claude/settings.json" \\
      "$HOME/.claude/cheese-machine.token" \\
      "$HOME/.claude/agent-configuration" 2>/dev/null | cksum | cut -d" " -f1 \\
      > "$HOME/.claude/$SESSION.cfg" 2>/dev/null || true
    # Hand THIS launch's credential / routing / attribution env to the new session
    # EXPLICITLY with -e, never by inheritance. tmux seeds a new session's env from
    # the tmux SERVER's GLOBAL env — frozen when that server first started — for
    # every var outside `update-environment` (which lists only DISPLAY / SSH_*).
    # CLAUDE_CODE_OAUTH_TOKEN, the HTTPS_PROXY password and the CHEESE_* wiring are
    # none of them, so on a server that has been up since another topic's launch
    # (which, now the connector's own server hosts these sessions, is the normal
    # case) a brand-new claude would silently boot with the token frozen into
    # that server when the connector started — a stale, wrong-topic credential
    # — instead of the one this turn minted. That is the 407 that outlives a
    # re-mint, a backend redeploy AND killing the old session: the dead token lives
    # in the server's global env, not the process, so recreating the session alone
    # inherits it again. -e writes the session env before claude execs, per key, so
    # each topic's claude runs on its OWN live credential.
    set -- new-session -d -s "$SESSION" -c "$CHEESE_WORK"
    # HOME and PATH are load-bearing for isolation and MUST travel per session:
    # both point into this topic's isolated home (PATH leads with its .claude,
    # where cheese-hook/cheese live), both are always non-empty, and neither is
    # in tmux's update-environment set — so without -e every claude after the
    # server's first would inherit the FIRST topic's HOME/PATH from the frozen
    # server global, resolve the first topic's hook forwarder, and report every
    # event as that topic (measured live 2026-08-15: /proc of topic B's claude
    # showed topic A's HOME and PATH).
    for _kv in \\
      "HOME=$HOME" "PATH=$PATH" \\
      "CLAUDE_CONFIG_DIR=$CLAUDE_CONFIG_DIR" \\
      "CLAUDE_CODE_OAUTH_TOKEN=$CLAUDE_CODE_OAUTH_TOKEN" \\
      "ANTHROPIC_AUTH_TOKEN=$ANTHROPIC_AUTH_TOKEN" \\
      "ANTHROPIC_BASE_URL=$ANTHROPIC_BASE_URL" \\
      "HTTPS_PROXY=$HTTPS_PROXY" "HTTP_PROXY=$HTTP_PROXY" \\
      "NO_PROXY=$NO_PROXY" "no_proxy=$no_proxy" \\
      "NODE_EXTRA_CA_CERTS=$NODE_EXTRA_CA_CERTS" \\
      "ANTHROPIC_CUSTOM_HEADERS=$ANTHROPIC_CUSTOM_HEADERS" \\
      "CLAUDE_MODEL=$CLAUDE_MODEL" \\
      "CLAUDE_BG_BACKEND=$CLAUDE_BG_BACKEND" \\
      "CLAUDE_BG_RENDEZVOUS_SOCK=$CLAUDE_BG_RENDEZVOUS_SOCK" \\
      "CLAUDE_BG_RV_AUTH=$CLAUDE_BG_RV_AUTH" \\
      "CHEESE_TOKEN=$CHEESE_TOKEN" "CHEESE_HOOK_URL=$CHEESE_HOOK_URL" \\
      "CHEESE_API=$CHEESE_API" "CHEESE_PROJECT=$CHEESE_PROJECT" \\
      "CHEESE_TOPIC=$CHEESE_TOPIC" "CHEESE_AUTHOR=$CHEESE_AUTHOR" \\
      "CHEESE_TUNNEL_URL=$CHEESE_TUNNEL_URL" \\
      "CHEESE_TUNNEL_PORT=$CHEESE_TUNNEL_PORT" \\
      "CHEESE_PREVIEW_URL=$CHEESE_PREVIEW_URL" \\
      "CHEESE_PREVIEW_UP=$CHEESE_PREVIEW_UP"; do
      # An empty value = a var this launch didn't set; skip it (a same-mode box's
      # frozen-global copy already matches, and forcing empty could flip modes).
      case "$_kv" in *=) ;; *) set -- "$@" -e "$_kv" ;; esac
    done
    # The -e list above is a curated view of THIS launcher's environment, and
    # every var it misses is inherited from the server's frozen global env —
    # i.e. from a DIFFERENT topic's launcher. That class of bug has now struck
    # three times (the token / #409, HOME+PATH / #433, CHEESE_HOOK_SPOOL —
    # measured 2026-08-16: topic E's claude spooled every hook into topic F's
    # dir, so F's drainer shipped E's events under F's identity). So the
    # session no longer TRUSTS inheritance at all: the launcher dumps its
    # complete environment (shell-quoted by python, atomically renamed) and
    # the session command sources it before exec'ing claude. The -e list stays
    # as a safety net for the window where the dump could not be written.
    # TMUX/TMUX_PANE are tmux's own (and deliberately unset here), PWD/OLDPWD
    # would lie about the session's real cwd, SHLVL/_ are shell bookkeeping.
    ENVF="$HOME/.claude/cheese-session-env"
    python3 -c 'import os, shlex
skip = ("TMUX", "TMUX_PANE", "PWD", "OLDPWD", "SHLVL", "_")
for k, v in os.environ.items():
    if k not in skip:
        print("export %s=%s" % (k, shlex.quote(v)))' > "$ENVF.tmp" \\
      && mv "$ENVF.tmp" "$ENVF" || rm -f "$ENVF.tmp"
    SRCENV=""
    [ -s "$ENVF" ] && SRCENV=". \\"$ENVF\\"; "
    DRAINCMD="sh \\"$HOME/.claude/cheese-drain\\" >/dev/null 2>&1"
    set -- "$@" "$SRCENV$TUP$PUP $DRAINCMD & exec $ENVIRONMENT_CMD$CLAUDE"
    # Fall back to a plain create ONLY when this tmux predates -e (< 3.0 says
    # "unknown flag" / prints usage). Any OTHER create failure fails LOUDLY:
    # the old catch-everything fallback turned a transient server error into a
    # silent degradation (#427) — and even though the sourced env file now
    # carries the full environment either way (#434), a masked failure still
    # costs its diagnosis. The launcher exiting non-zero surfaces as a screen
    # setup error on the turn, which is the honest outcome.
    if ! _ERR=$(atmux "$@" 2>&1); then
      case "$_ERR" in
        *"unknown flag"*|*"usage:"*|*"invalid option"*)
          atmux new-session -d -s "$SESSION" -c "$CHEESE_WORK" \\
            "$SRCENV$TUP$PUP $DRAINCMD & exec $ENVIRONMENT_CMD$CLAUDE"
          ;;
        *)
          echo "cheese-launch: tmux new-session failed: $_ERR" >&2
          exit 1 ;;
      esac
    fi
  fi
  rm -f "$HOME/.claude/environment-restart"
  exec tmux -S "$CHEESE_TMUX_SOCK" attach -t "$SESSION"
else
  # eval, not bare exec: $CLAUDE now carries a QUOTED file path, and plain
  # word-splitting would hand claude the quote characters themselves.
  # No tmux → claude stays in THIS process tree, so a drainer backgrounded
  # right here genuinely shares its fate; same-life-same-death holds as is.
  if [ -n "${{CHEESE_TUNNEL_URL:-}}" ]; then
    sh "$HOME/.claude/cheese-tunnel-up" >/dev/null 2>&1 || true
  fi
  if [ -n "${{CHEESE_PREVIEW_URL:-}}" ]; then
    sh "$HOME/.claude/cheese-preview-up" >/dev/null 2>&1 || true
  fi
  sh "$HOME/.claude/cheese-drain" >/dev/null 2>&1 &
  eval "exec $ENVIRONMENT_CMD$CLAUDE"
fi
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
    git_branch: str | None = None,
    system_prompt: str = "",
    ca_pem: str = "",
) -> tuple[list[str], dict[str, str]]:
    """Assemble ``(command, env)`` for ``DeviceHub.open_screen``.

    ``command`` is a self-contained ``bash -lc`` launcher; ``env`` carries the hook
    wiring + home/work dirs + model + any provider (gateway) vars, and — for a
    screen with a topic — where its rendezvous socket lives. When
    ``api_base`` and the ``project_id``/``topic_id`` context are given, the
    bundled ``cheese`` CLI (accept cards / docs / decisions / memory) uses
    its ``CHEESE_*`` env — the same actions the in-container agent has locally."""
    script = build_launch_script(
        sync_on_stop=bool(git_remote), system_prompt=system_prompt, ca_pem=ca_pem
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
        # Every device clones and pushes its own independent topic tree.
        env["CHEESE_GIT_REMOTE"] = git_remote
        env["CHEESE_GIT_BRANCH"] = git_branch or "main"
    if git_author:
        # Who the turn's commits belong to (workspace/identity.py). Absent, the
        # launcher falls back to 芝士 — the same default the in-repo snapshot
        # path uses, so both surfaces agree.
        env["CHEESE_GIT_AUTHOR_NAME"], env["CHEESE_GIT_AUTHOR_EMAIL"] = git_author
    if extra_env:
        env.update(extra_env)
    return command, env


def ensure_dir(path: str) -> str:
    """Create ``path`` (the isolated home/work dir) if missing; return it. Used by the
    spike / local-device provider where the device is this same machine."""
    Path(path).mkdir(parents=True, exist_ok=True)
    return path


# shlex is imported for callers that need to render the command for logging/debug.
def command_for_log(command: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in command)
