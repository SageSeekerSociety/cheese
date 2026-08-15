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
from app.domain.agent import machine_tunnel
from app.domain.agent.hooks_substrate import CHEESE_HOOK_SCRIPT, hooks_settings
from app.domain.agent.service import CLAUDE_BASE_CMD

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

# Claude Code applies the `env` block of the machine user's own
# ~/.claude/settings.json OVER the process environment, key by key — and it
# reads that file from the LOGIN user's home, not from the isolated $HOME this
# launcher exports. A provisioned machine ships one, so every routing variable
# the platform injects is discarded and the turn goes wherever the image says.
#
# Measured on a real MicroCloud machine (2026-08-02), sink on loopback:
#   file present, base URL injected as env  -> 0 requests to us, answered by the
#                                              image's own endpoint
#   file's env block pointing at the sink   -> 7 requests to us
#   file's env block emptied, env injected  -> 7 requests to us
# so the file is the control point, and agreeing with it is the only way an
# injected route takes effect. (The first two rounds of that experiment were
# wrong because the image also sets HTTP(S)_PROXY there: a loopback sink is
# unreachable *through a proxy*, which looks exactly like "the route was
# ignored". Controlling for it is what produced the numbers above.)
#
# Merge rather than overwrite: the proxy and CA entries are the image's own
# supply route and destroying them would take the subscription path down with
# it. The original `env` is kept beside the file, once.
CHEESE_SETTINGS_RECONCILE = """import json, os, sys

path = sys.argv[1]
want_base = os.environ.get("ANTHROPIC_BASE_URL") or ""
want_token = os.environ.get("ANTHROPIC_AUTH_TOKEN") or ""

try:
    with open(path) as fh:
        data = json.load(fh)
except FileNotFoundError:
    # No image-supplied settings means nothing overrides us; the injected
    # environment already decides, and writing a file here would only invent a
    # new thing to keep in sync.
    print("absent")
    raise SystemExit(0)
except Exception as exc:
    print("unreadable %s" % exc)
    raise SystemExit(0)

if not isinstance(data, dict):
    print("unreadable not-an-object")
    raise SystemExit(0)

env = data.get("env")
if not isinstance(env, dict):
    env = {}
    data["env"] = env

backup = path + ".cheese-orig"
if not os.path.exists(backup):
    with open(backup, "w") as fh:
        json.dump({"env": dict(env)}, fh)

# Resolved below on the tunnel path, and handed to the caller through argv[2]
# because THIS file is not the one Claude Code reads. The launcher exports an
# isolated $HOME, so claude opens `$HOME/.claude/settings.json` — not the login
# user's. Writing the machine's ticket here and stopping was the whole bug:
# reconcile faithfully kept it, claude never saw it, and the scoped cheese token
# in the process environment stayed the model credential. Measured on machine
# 474 (2026-08-15): every turn came back `401 Invalid bearer token` from
# Anthropic, because that is what a cheese token looks like once ccproxy has
# declined to recognise it as one of its own tickets.
machine_ticket = ""

if want_base:
    env["ANTHROPIC_BASE_URL"] = want_base
    env["ANTHROPIC_AUTH_TOKEN"] = want_token
    # Reach our own gateway directly. Leaving the image's forward proxy in
    # charge of it would route platform traffic through a third party for no
    # reason, and a proxy that cannot resolve our host fails the whole turn.
    host = want_base.split("//", 1)[-1].split("/")[0].split(":")[0]
    if host:
        env["NO_PROXY"] = host
        env["no_proxy"] = host
else:
    # Subscription mode: we inject no base URL, so the image's has to go too —
    # left in place it would send the session to the image's endpoint instead
    # of the official one through the proxy.
    for key in ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "NO_PROXY", "no_proxy"):
        env.pop(key, None)
    # OUR subscription (the platform's metering proxy, marked by the injected
    # CLAUDE_CODE_OAUTH_TOKEN): the image's own proxy/CA entries would win over
    # the process environment key by key and send the session through the
    # image's supply route instead of the meter — so ours are asserted INTO the
    # file, not merely exported. Absent that marker the image's entries are its
    # own supply route and stay untouched, as before.
    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        keys = ["HTTPS_PROXY", "NO_PROXY", "no_proxy", "NODE_EXTRA_CA_CERTS"]
        # WHOSE ticket claude carries depends on what the meter will do with it.
        #
        # Swap path: the meter replaces the bearer with the credential the host
        # holds, so ours goes out and the image's is irrelevant — assert ours.
        #
        # Pass-through path (a remote machine on the tunnel, marked by
        # CHEESE_TUNNEL_URL; or a co-located device that brings its own ccproxy
        # identity, marked by CHEESE_MACHINE_TICKET — the dev box): the meter
        # forwards the bearer UNTOUCHED, because ccproxy only honours a
        # machine's ticket over that machine's own identity. So the ticket has
        # to be the machine's own — written here by MicroCloud on its machines,
        # or by the device's administrator on a self-hosted box. Overwriting it
        # sends OUR scoped token to Anthropic, which answers
        # `401 Invalid bearer token` (measured on both paths, 2026-08-14/15:
        # the whole chain up, refused at the far end). The scoped token still
        # travels, as the proxy password on the CONNECT — it authenticates the
        # project, not the model call.
        # Read from the BACKUP, not from the live file. The live file's token is
        # whatever the last launch left there — and every launch before this one
        # overwrote it with ours, so "keep what is there" would faithfully
        # preserve our own stale scoped token and change nothing. The backup is
        # written once, on the first reconcile, before anything was overwritten:
        # it is the only place the machine's original ticket still exists.
        if os.environ.get("CHEESE_TUNNEL_URL") or os.environ.get(
            "CHEESE_MACHINE_TICKET"
        ):
            # A ccproxy ticket EXPIRES, and ccproxy refreshes it the way its
            # clients do: Claude Code writes the new one back into this file. So
            # the live file is the freshest copy there is, and overwriting it —
            # even with the machine's original — hands ccproxy a ticket that
            # stopped being valid hours ago (measured 2026-08-14: a two-hour-old
            # one came back `401 OAuth access token has been revoked`).
            #
            # Ours is distinguishable by shape: a scoped cheese token is
            # `body.signature`, a ccproxy ticket has no dot. So keep a ticket,
            # and heal the field from the backup only when a previous launch
            # (before this path existed) left OUR token sitting in it.
            live = env.get("CLAUDE_CODE_OAUTH_TOKEN") or ""
            if live and "." not in live:
                machine_ticket = live
            else:
                # The live field holds OUR scoped token (has a dot): a previous
                # launch on the swap path wrote it, or a co-located device never
                # carried a ticket in settings.json at all. Recover the machine's
                # own ticket, preferring the client's canonical credential store
                # over the settings backup.
                #
                # `.credentials.json` is where Claude Code writes the ccproxy
                # ticket AND every refresh of it, so it is both the freshest copy
                # and — for a co-located device whose ticket was never in
                # settings.json — the ONLY copy. The settings backup only ever
                # held a ticket on a MicroCloud machine (MicroCloud seeds it into
                # settings.json's env), so it stays as the fallback for that
                # shape. Without the credentials source a co-located device loops
                # forever: live has a dot, the backup has no token, so the ticket
                # resolves empty and the field is rewritten with our scoped token
                # every launch (measured on the dev box, 2026-08-15).
                machine_ticket = ""
                creds = os.path.join(os.path.dirname(path), ".credentials.json")
                try:
                    with open(creds) as fh:
                        machine_ticket = (
                            ((json.load(fh) or {}).get("claudeAiOauth") or {}).get(
                                "accessToken"
                            )
                            or ""
                        )
                except Exception:
                    machine_ticket = ""
                if not machine_ticket:
                    try:
                        with open(backup) as fh:
                            machine_ticket = (
                                ((json.load(fh) or {}).get("env") or {}).get(
                                    "CLAUDE_CODE_OAUTH_TOKEN"
                                )
                                or ""
                            )
                    except Exception:
                        machine_ticket = ""
        if machine_ticket:
            env["CLAUDE_CODE_OAUTH_TOKEN"] = machine_ticket
        else:
            keys.insert(0, "CLAUDE_CODE_OAUTH_TOKEN")
        for key in keys:
            val = os.environ.get(key)
            if val is not None:
                env[key] = val
        env.pop("HTTP_PROXY", None)

with open(path, "w") as fh:
    json.dump(data, fh)

# Hand the ticket to the launcher, which is the only place that can put it where
# claude will read it. A FILE, not stdout: this script's stdout is reported into
# a hook payload on mismatch, and a credential must never travel that way.
if len(sys.argv) > 2 and machine_ticket:
    out = sys.argv[2]
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(machine_ticket)

# Report what the file SAYS, re-read from disk — not what we meant to write.
# A write that silently did not take is the failure this whole block exists to
# stop, so it must not be the one thing taken on trust.
try:
    with open(path) as fh:
        effective = ((json.load(fh) or {}).get("env") or {}).get(
            "ANTHROPIC_BASE_URL", ""
        )
except Exception as exc:
    print("verify-failed %s" % exc)
    raise SystemExit(0)

if effective == want_base:
    print("ok %s" % (effective or "(image default)"))
else:
    print("mismatch wanted=%s effective=%s" % (want_base or "(none)", effective))
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
# precise even with several screens on one machine, and is identical for a
# co-located device (the backend's own host) and a remote one (both reached over
# the same link `exec`).
#
# Prints exactly one of `alive` / `dead` / `unknown`. The caller treats ONLY an
# explicit `dead` as fatal; `unknown` (no /proc, an unreadable environ) and any
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
# No /proc (non-Linux) or an unreadable environ: no per-topic view, so never assert
# death — report unknown and let the caller keep the turn alive to the hard ceiling.
echo unknown
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
CHEESE_TUNNEL_UP = """#!/bin/sh
PIDF="$HOME/.claude/cheese-tunnel.pid"
STAMPF="$HOME/.claude/cheese-tunnel.stamp"
# Adopt a live helper ONLY if it is running the helper we just wrote. The
# launcher rewrites cheese-tunnel.py on every launch, so a shipped fix would
# otherwise never reach a machine whose helper is still alive — it would keep
# serving the old code indefinitely, and nothing would look wrong.
WANT="$(cksum "$HOME/.claude/cheese-tunnel.py" 2>/dev/null | cut -d" " -f1)"
HAVE="$(cat "$STAMPF" 2>/dev/null || true)"
PID="$(cat "$PIDF" 2>/dev/null || true)"
if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
  if [ -n "$WANT" ] && [ "$WANT" = "$HAVE" ]; then
    exit 0
  fi
  # Different code: retire it. In-flight turns see one connection reset, which
  # claude retries; a permanently stale helper does not heal at all.
  kill "$PID" 2>/dev/null || true
fi
python3 "$HOME/.claude/cheese-tunnel.py" \\
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


CHEESE_DRAIN_SCRIPT = """#!/bin/sh
echo $$ > "$0.pid" 2>/dev/null || true
while true; do
  if [ -n "$CHEESE_DRAIN_TETHER" ] && ! kill -0 "$CHEESE_DRAIN_TETHER" 2>/dev/null
  then
    exit 0
  fi
  [ -r "$0.env" ] || { sleep 5; continue; }
  . "$0.env"
  [ -n "$CHEESE_HOOK_SPOOL" ] || { sleep 5; continue; }
  for f in "$CHEESE_HOOK_SPOOL"/[0-9]*; do
    [ -e "$f" ] || continue
    resp="$(curl -s -m 10 -X POST -H 'Content-Type: application/json' \\
      -H "X-Cheese-Token: $CHEESE_TOKEN" -H "X-Cheese-Event-Id: ${f##*.}" \\
      --data-binary @"$f" "$CHEESE_HOOK_URL" 2>/dev/null)"
    case "$resp" in *'"code":200'*) rm -f "$f";; esac
  done
  find "$CHEESE_HOOK_SPOOL" -type f -mmin +1440 -delete 2>/dev/null
  sleep 1
done
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
    settings_reconcile = CHEESE_SETTINGS_RECONCILE
    drain_script = CHEESE_DRAIN_SCRIPT
    # Shipped by reading the module's own bytes rather than by keeping a second
    # copy here: it is a real, linted, unit-tested module precisely so there is
    # only one version of it to be wrong.
    tunnel_helper = Path(machine_tunnel.__file__).read_text().rstrip("\n") + "\n"
    tunnel_up = CHEESE_TUNNEL_UP
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
{ca_block}
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
cat > "$HOME/.claude/cheese-system-prompt.md" <<'SYSPROMPT'
{system_prompt}SYSPROMPT
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
cat > "$HOME/.claude/cheese-usage.py" <<'USAGEPY'
{usage_reader}USAGEPY
cat > "$HOME/.claude/cheese-usage" <<'USAGE'
{usage_script}USAGE
chmod +x "$HOME/.claude/cheese-usage"
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
# Make the injected route actually take effect. See CHEESE_SETTINGS_RECONCILE:
# the machine user's own settings.json outranks the process environment, so
# without this the turn silently bills whatever endpoint the image was built
# with. Non-fatal — a machine we cannot reconcile still runs — but never
# silent: the outcome is reported, and a mismatch is the interesting case.
if [ -f "$REAL_HOME/.claude/settings.json" ]; then
  cat > "$HOME/.claude/cheese-settings-reconcile.py" <<'RECONCILE'
{settings_reconcile}RECONCILE
  CHEESE_ROUTE="$(python3 "$HOME/.claude/cheese-settings-reconcile.py" \\
    "$REAL_HOME/.claude/settings.json" \\
    "$HOME/.claude/cheese-machine.token" 2>&1 || echo "reconcile-crashed")"
  # The model credential claude will actually use. It has to be asserted into
  # the process environment because the settings.json holding the machine's
  # ticket lives in the LOGIN user's home, and claude reads the isolated $HOME
  # this launcher exports — so that file never reaches it.
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
    ok\\ *|absent) ;;
    *) printf '{{"hook_event_name":"CheeseRoute","status":"failed","detail":"%s"}}' \\
         "$CHEESE_ROUTE" | cheese-hook >/dev/null 2>&1 || true ;;
  esac
fi
# Durable event delivery on the device: cheese-hook spools every hook and (via
# CHEESE_HOOK_SPOOL_ONLY) skips its own inline curl, so the cheese-drain script
# is the sole sender — it retries each spooled event until the backend DURABLY
# accepts it (code:200 = pushed to the live turn OR parked in the topic's
# server-side spool for the next reconcile), so a link/backend outage never
# drops an event. A 24h age cap stops an unreachable backend from accumulating
# retries forever. The backend dedups re-deliveries by event-id.
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
cd "$CHEESE_WORK"
# Host claude in a PERSISTENT tmux session so it survives a link/screen drop: the
# session keeps running on the device and re-opening the screen re-attaches to it
# (same hosting as the local tmux backend; the PTY mirrors the pane for the human
# viewer and the cheeselet types into it). Direct exec if tmux isn't installed.
# Prefix that must complete BEFORE claude: it brings the tunnel helper up and
# waits for its port, because claude reads HTTPS_PROXY once and calls out
# immediately. Empty when this deployment has no tunnel, so the direct path
# does not pay for a script it does not use.
TUP=""
if [ -n "${{CHEESE_TUNNEL_URL:-}}" ]; then
  TUP="sh \\"$HOME/.claude/cheese-tunnel-up\\" >/dev/null 2>&1;"
fi
CLAUDE="{CLAUDE_BASE_CMD}"
[ -n "$CLAUDE_MODEL" ] && CLAUDE="$CLAUDE --model $CLAUDE_MODEL"
# The platform system prompt (written next to settings.json above). The path is
# embedded QUOTED so both consumers survive a home dir with spaces: the tmux
# branch re-parses $CLAUDE through sh -c, the exec branch through eval.
CHEESE_SP="$HOME/.claude/cheese-system-prompt.md"
[ -s "$CHEESE_SP" ] && CLAUDE="$CLAUDE --append-system-prompt-file \\"$CHEESE_SP\\""
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
  EXPFILE="$HOME/.claude/$SESSION.tokexp"
  if tmux has-session -t "$SESSION" 2>/dev/null; then
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
      "$HOME/.claude/cheese-machine.token" 2>/dev/null | cksum | cut -d" " -f1)"
    CFGWAS="$(cat "$CFGF" 2>/dev/null || true)"
    RETIRE=0
    [ "$TOKEXP" -le "$(( $(date +%s) + 300 ))" ] && RETIRE=1
    [ -n "$CFGNOW" ] && [ "$CFGNOW" != "$CFGWAS" ] && RETIRE=1
    if [ "$RETIRE" = 1 ]; then
      tmux kill-session -t "$SESSION" 2>/dev/null || true
    fi
  fi
  if tmux has-session -t "$SESSION" 2>/dev/null; then
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
    if [ -n "${{CHEESE_TUNNEL_URL:-}}" ]; then
      TETHER="$(tmux list-panes -s -t "$SESSION" -F '#{{pane_pid}}' \\
        2>/dev/null | head -n 1)"
      tmux new-window -d -t "$SESSION" -n cheese-tunnel \\
        "CHEESE_TUNNEL_URL=$CHEESE_TUNNEL_URL CHEESE_TUNNEL_PORT=$CHEESE_TUNNEL_PORT \\
         CHEESE_TUNNEL_TETHER=$TETHER exec sh \\"$HOME/.claude/cheese-tunnel-up\\"" \\
        || true
    fi
    DRAIN_PID="$(cat "$HOME/.claude/cheese-drain.pid" 2>/dev/null || true)"
    if [ -z "$DRAIN_PID" ] || ! kill -0 "$DRAIN_PID" 2>/dev/null; then
      TETHER="$(tmux list-panes -s -t "$SESSION" -F '#{{pane_pid}}' \\
        2>/dev/null | head -n 1)"
      tmux new-window -d -t "$SESSION" -n cheese-drain \\
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
      "$HOME/.claude/cheese-machine.token" 2>/dev/null | cksum | cut -d" " -f1 \\
      > "$HOME/.claude/$SESSION.cfg" 2>/dev/null || true
    # Hand THIS launch's credential / routing / attribution env to the new session
    # EXPLICITLY with -e, never by inheritance. tmux seeds a new session's env from
    # the tmux SERVER's GLOBAL env — frozen when that server first started — for
    # every var outside `update-environment` (which lists only DISPLAY / SSH_*).
    # CLAUDE_CODE_OAUTH_TOKEN, the HTTPS_PROXY password and the CHEESE_* wiring are
    # none of them, so on a box whose default tmux server is already up (it hosts
    # another topic, or a login shell) a brand-new claude would silently boot with
    # the token frozen into that server weeks ago — a stale, wrong-topic credential
    # — instead of the one this turn minted. That is the 407 that outlives a
    # re-mint, a backend redeploy AND killing the old session: the dead token lives
    # in the server's global env, not the process, so recreating the session alone
    # inherits it again. -e writes the session env before claude execs, per key, so
    # each topic's claude runs on its OWN live credential.
    set -- new-session -d -s "$SESSION" -c "$CHEESE_WORK"
    for _kv in \\
      "CLAUDE_CODE_OAUTH_TOKEN=$CLAUDE_CODE_OAUTH_TOKEN" \\
      "ANTHROPIC_AUTH_TOKEN=$ANTHROPIC_AUTH_TOKEN" \\
      "ANTHROPIC_BASE_URL=$ANTHROPIC_BASE_URL" \\
      "HTTPS_PROXY=$HTTPS_PROXY" "HTTP_PROXY=$HTTP_PROXY" \\
      "NO_PROXY=$NO_PROXY" "no_proxy=$no_proxy" \\
      "NODE_EXTRA_CA_CERTS=$NODE_EXTRA_CA_CERTS" \\
      "ANTHROPIC_CUSTOM_HEADERS=$ANTHROPIC_CUSTOM_HEADERS" \\
      "CLAUDE_MODEL=$CLAUDE_MODEL" \\
      "CHEESE_TOKEN=$CHEESE_TOKEN" "CHEESE_HOOK_URL=$CHEESE_HOOK_URL" \\
      "CHEESE_API=$CHEESE_API" "CHEESE_PROJECT=$CHEESE_PROJECT" \\
      "CHEESE_TOPIC=$CHEESE_TOPIC" "CHEESE_AUTHOR=$CHEESE_AUTHOR" \\
      "CHEESE_CLI_URL=$CHEESE_CLI_URL" \\
      "CHEESE_TUNNEL_URL=$CHEESE_TUNNEL_URL" \\
      "CHEESE_TUNNEL_PORT=$CHEESE_TUNNEL_PORT"; do
      # An empty value = a var this launch didn't set; skip it (a same-mode box's
      # frozen-global copy already matches, and forcing empty could flip modes).
      case "$_kv" in *=) ;; *) set -- "$@" -e "$_kv" ;; esac
    done
    DRAINCMD="sh \\"$HOME/.claude/cheese-drain\\" >/dev/null 2>&1"
    set -- "$@" "$TUP $DRAINCMD & exec $CLAUDE"
    # Fall back to a plain create if this tmux predates -e (< 3.0): the screen
    # still launches (with the old inheritance behaviour) rather than not at all.
    tmux "$@" || tmux new-session -d -s "$SESSION" -c "$CHEESE_WORK" \\
      "$TUP sh \\"$HOME/.claude/cheese-drain\\" >/dev/null 2>&1 & exec $CLAUDE"
  fi
  exec tmux attach -t "$SESSION"
else
  # eval, not bare exec: $CLAUDE now carries a QUOTED file path, and plain
  # word-splitting would hand claude the quote characters themselves.
  # No tmux → claude stays in THIS process tree, so a drainer backgrounded
  # right here genuinely shares its fate; same-life-same-death holds as is.
  if [ -n "${{CHEESE_TUNNEL_URL:-}}" ]; then
    sh "$HOME/.claude/cheese-tunnel-up" >/dev/null 2>&1 || true
  fi
  sh "$HOME/.claude/cheese-drain" >/dev/null 2>&1 &
  eval "exec $CLAUDE"
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
    system_prompt: str = "",
    ca_pem: str = "",
) -> tuple[list[str], dict[str, str], str]:
    """Assemble ``(command, env, cheeselet_source)`` for ``DeviceHub.open_screen``.

    ``command`` is a self-contained ``bash -lc`` launcher; ``env`` carries the hook
    wiring + home/work dirs + model + any provider (gateway) vars; ``cheeselet_source``
    is the minimal prompt-typing driver. When ``cli_url``/``api_base`` and the
    ``project_id``/``topic_id`` context are given, the launcher also fetches the
    ``cheese`` platform-action CLI (accept cards / docs / decisions / memory) and wires
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
