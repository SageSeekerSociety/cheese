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
device. ``on_machine`` answers a ``MachinePlace`` with this harness's half of a
launch; ``machine_launcher`` owns the other half and joins the two.
"""

import json
import shlex
from pathlib import Path
from uuid import uuid4

# Perception wiring (settings.json + the cheese-hook forwarder) is the SHARED
# substrate — identical for the local (tmux) and remote (device) backends so it
# can't drift (fusion-design §8.6). Re-exported here (`hooks_settings`) because
# this module's launcher and its callers build on it.
from app.domain.agent import machine_launcher
from app.domain.agent.harness.claude_code import startup_cache
from app.domain.agent.harness.claude_code.cli import CLAUDE_BASE_CMD
from app.domain.agent.harness.claude_code.remote_execution import (
    client as execution_client,
)
from app.domain.agent.harness.claude_code.remote_execution import release
from app.domain.agent.harness.claude_code.session_launch import hooks_settings
from app.domain.agent.harness.launch import MachineLaunch, MachinePlace
from app.domain.agent.hook_forwarder import CHEESE_HOOK_SCRIPT
from app.domain.agent.skills import SKILL_HEREDOC_MARKER, native_skill_files

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
# keep working; the source of truth is hook_forwarder.CHEESE_HOOK_SCRIPT.
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
CLAUDE_PINNED_VERSION = "2.1.277"
CLAUDE_MIN_VERSION = "2.1.277"

# CLAUDE_BASE_CMD starts with the bare word `claude`; the launcher resolves a
# specific binary (pin, then ~/.local/bin, then PATH) and needs only the flags.
CLAUDE_BASE_ARGS = CLAUDE_BASE_CMD.removeprefix("claude")

# Screen-env keys the connector reads to find a screen's rendezvous socket. The
# token lives in a FILE, not the env: an adopted `claude` keeps the token it
# booted with, so a freshly minted env value would never match.
ENV_RV_SOCK = "CHEESE_RV_SOCK"
ENV_RV_TOKEN_FILE = "CHEESE_RV_TOKEN_FILE"


def rendezvous_paths() -> tuple[str, str]:
    """Allocate a socket and token file for one model launch.

    A unix socket path is capped near 104 bytes and an isolated home already
    spends ~105 (`~/.cheese/home/<project-uuid>/<topic-uuid>/.claude/`), so the
    socket cannot live beside the session it belongs to. Separate launches of
    the same topic must not bind or authenticate through each other's files.
    """
    identity = uuid4().hex
    return f"/tmp/cheese-rv-{identity}.sock", f"/tmp/cheese-rv-{identity}.token"


# Reports what a turn cost. Claude Code writes a usage block per assistant
# message into its transcript; nothing else on the machine knows those numbers,
# and the transcript dies with the machine — which is why a week of spend could
# not be attributed to a project, a topic, or even a prompt.
#
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
  command -v awk >/dev/null 2>&1 || { echo unknown; exit 0; }
  # Read NUL-delimited fields in one process, without Python startup per turn.
  #
  # Only argv[0] decides whether a process is a session — the same shape the
  # Darwin branch below asks through `comm`. Scanning the REST of the arguments
  # for the `claude` substring is what this must not do: the remote-execution
  # helpers (`.../.claude/remote-execution/forwarded_fs.py`) carry a `.claude`
  # path in their args, and one of them holding a stale CHEESE_TOPIC made the
  # platform adopt a session that no longer existed — every message for that
  # room then timed out instead of reopening a screen. `/.claude/` is not a
  # match for `/claude/`, so the config directory is excluded by construction:
  # a real executable is `<...>/claude`, `<...>/claude/versions/<v>` (the pin)
  # or `<...>/.local/bin/claude`.
  printf '%s\n' /proc/[0-9]*/cmdline | awk -v topic="$topic" '
  {
    path=$0; RS="\0"; candidate=0
    if ((getline part < path)>0)
      candidate = (part=="claude" || index(part,"/claude/") || part ~ /\/claude$/)
    close(path)
    if (candidate) {
      sub(/cmdline$/,"environ",path)
      while ((getline part < path)>0) {
        if (part=="CHEESE_TOPIC="topic) { alive=1; print "alive"; exit }
      }
      close(path)
    }
    RS="\n"
  }
  END { if (!alive) print "dead" }
  '
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
  if awk -v p="$hex" '$4=="0A" { n=split($2,a,":"); if (a[n]==p) { f=1; exit } }
                      END { exit f?0:1 }' "$f"; then
    echo up; exit 0
  fi
done
echo down
"""


# Rooms start in a plain coordination directory. Task checkouts are prepared
# by `cheese worktree`; stopping the session backs up every task independently.
CHEESE_SYNC_SCRIPT = """#!/bin/sh
exec cheese sync --all
"""


def launch_holes(
    sync_on_stop: bool = False,
    system_prompt: str = "",
    ca_pem: str = "",
    remote_control: bool = False,
    remote_execution: bool = False,
    model: str | None = None,
    resume_session_id: str | None = None,
    topic_id: str | None = None,
) -> MachineLaunch:
    """Claude Code's half of a device launch: the five holes, and its own env.

    The platform half is ``machine_launcher``; nothing below belongs to it. It
    reads a few env vars the screen is created with: ``CHEESE_HOME`` (isolated
    config/home dir), ``CHEESE_WORK`` (cwd), plus the hook wiring
    (``CHEESE_HOOK_URL``/``CHEESE_TOKEN``) and ``CLAUDE_MODEL`` (optional).

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
    sync_script = CHEESE_SYNC_SCRIPT
    settings_reconcile = CHEESE_SETTINGS_RECONCILE
    startup_cache_source = Path(startup_cache.__file__).read_text()
    webfetch_transport = Path(__file__).with_name("webfetch_transport.cjs").read_text()
    skill_setup = "\n".join(
        f'mkdir -p "$CLAUDE_CONFIG_DIR/{Path(name).parent}"\n'
        f"cat > \"$CLAUDE_CONFIG_DIR/{name}\" <<'{SKILL_HEREDOC_MARKER}'\n"
        f"{content}\n{SKILL_HEREDOC_MARKER}"
        for name, content in native_skill_files().items()
    )
    settings_json = json.dumps(
        hooks_settings(
            ["cheese-sync"] if sync_on_stop else [],
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
        helper_sources = release.sources()
        execution_setup = 'mkdir -p "$HOME/.cheese/remote-execution"\n'
        for name, source in helper_sources.items():
            execution_setup += (
                f'cat > "$HOME/.cheese/remote-execution/{name}" '
                "<<'CHEESE_EXECUTION_SOURCE'\n"
                + source
                + ("" if source.endswith("\n") else "\n")
                + "CHEESE_EXECUTION_SOURCE\n"
            )
        execution_setup += (
            f"printf %s {release.digest(helper_sources)} "
            '> "$HOME/.cheese/remote-execution/release-ready"\n'
        )
        execution_setup += """printf '%s' "$CHEESE_EXECUTION_TARGET" \\
  > "$HOME/.cheese/remote-target.json"
EXECUTOR_CLIENT="$HOME/.cheese/remote-execution/client.py"
EXECUTOR_TARGET="$HOME/.cheese/remote-target.json"
CLAUDE="python3 \\"$EXECUTOR_CLIENT\\" bootstrap \\"$EXECUTOR_TARGET\\" $CLAUDE"
"""
    env: dict[str, str] = {
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
        # entirely out of its environment, and there is no other channel into it.
        env["CHEESE_RESUME_SESSION"] = resume_session_id
    if topic_id:
        # Where this screen's prompts arrive. The launcher turns these two into
        # Claude Code's own CLAUDE_BG_* trio and mints the token; the connector
        # reads the same two to dial. An adopted screen retains its saved launch
        # environment, including the paths its running model already uses.
        sock, token_file = rendezvous_paths()
        env[ENV_RV_SOCK] = sock
        env[ENV_RV_TOKEN_FILE] = token_file
    # The settings.json / cheese-hook heredocs are quoted ('JSON'/'SH') so the shell
    # never expands them. ~/.claude.json is written by the shell (see below) so a
    # machine without node can still launch.
    return MachineLaunch(
        staging="""WARM_ROOT=""
if [ -z "${CHEESE_EXECUTION_TARGET:-}" ] && \\
   [ -f "$REAL_HOME/.cheese/native-warm/binding.json" ]; then
  python3 "$REAL_HOME/.cheese/warm-native-runner.py" recover-room \\
    "$REAL_HOME/.cheese/native-warm"
fi
# Reuse one interpreter for the check and staging, including on existing spares.
if [ -z "${CHEESE_EXECUTION_TARGET:-}" ] && \\
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
""",
        configure=f"""\
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
# Ours to create now that the platform keeps its own files in $HOME/.cheese:
# this directory is this harness's, and everything below writes into it.
mkdir -p "$CLAUDE_CONFIG_DIR"
# cheese-sync is a Stop hook, and settings.json names it by NAME — so this
# directory has to be on PATH too. The platform puts its own there later; the
# two never hold the same name.
export PATH="$CLAUDE_CONFIG_DIR:$PATH"
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
""",
        credentials=f"""\
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
""",
        prepare=f"""\
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
# One token per launch, on disk rather than in the env: an ADOPTED claude keeps
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
""",
        contract=claude_args,
        command="$CLAUDE",
        env=env,
    )


def build_launch_script(**named) -> str:
    """The launcher a device runs for a Claude Code session."""
    holes = launch_holes(**named)
    return machine_launcher.launch_script(
        staging=holes.staging,
        configure=holes.configure,
        credentials=holes.credentials,
        prepare=holes.prepare,
        command=holes.command,
    )


def on_machine(
    place: MachinePlace,
    *,
    system_prompt: str,
    model: str | None,
    resume_session_id: str | None,
) -> MachineLaunch:
    """Claude Code, now that a machine has said where and what this room is.

    ``place`` states facts; what they mean is decided here. A room with a git
    remote syncs at turn end (a Stop hook), one with an execution target ships
    the executor client and hands ``claude`` to it, one whose operator may drive
    it directly runs without the permission prompt, and a CA to trust is a file
    only the script can name an absolute path for.
    """
    return launch_holes(
        # A room whose work is done on an executor has nothing of its own to
        # hand back; the executor owns the checkout.
        sync_on_stop=bool(place.git_remote) and place.execution_target is None,
        system_prompt=system_prompt,
        ca_pem=place.ca_pem,
        remote_control=place.remote_control,
        remote_execution=place.execution_target is not None,
        model=model,
        resume_session_id=resume_session_id,
        topic_id=place.topic_id,
    )


def ensure_dir(path: str) -> str:
    """Create ``path`` (the isolated home/work dir) if missing; return it. Used by the
    spike / local-device provider where the device is this same machine."""
    Path(path).mkdir(parents=True, exist_ok=True)
    return path


# shlex is imported for callers that need to render the command for logging/debug.
def command_for_log(command: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in command)
