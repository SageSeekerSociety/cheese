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
from app.core.config import settings
from app.domain.agent import machine_launcher
from app.domain.agent.harness.claude_code.cli import CLAUDE_BASE_CMD, disallowed_tools
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
#
# 这是这个骨架**唯一**的 pin：行为声明（``behaviour.py``）引用它，
# ``scripts/test_harness_contracts.py`` 装二进制时问的也是它。另外这几处写着同一
# 个版本号，每一处都 import 不到这里，所以它们是复制品而不是第二个答案——升级要
# 改的就是这张单子，守卫在测试里，改漏一处就红：
#   * ``remote_execution/client.py`` 的 ``PINNED_VERSION``、
#     ``remote_execution/bootstrap.py`` 的 ``VERSION``（机器上单独跑的两个脚本）
#   * ``sandbox/Dockerfile.private`` 里装的那个 claude，以及镜像 tag 的三处写法：
#     ``remote_execution/private.py`` 的 ``IMAGE``、``core/config.py`` 的
#     ``private_chat_executor_image`` 默认值、
#     ``.github/workflows/remote-execution.yml`` build 时打的 tag
#     —— 以上都由 ``tests/unit/test_capability_matrix.py`` 钉住
#   * ``.github/workflows/cli.yml`` 装的那个 claude 与 ``sandbox/Dockerfile`` 的
#     ``ARG CLAUDE_CODE_VERSION``，由 ``tests/unit/test_device_rendezvous.py`` 钉住
# ``scripts/remote_execution/package.json`` 和它的 lock 也装一个固定版本，那份没
# 有守卫：对不上时 ``remote_execution/client.py`` 的版本闸门在 CI 里当场拒掉，
# 红得见。
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


# What a session carries when its host has no Claude login: enough for Claude
# Code to boot, so a project on the API-key pool still runs. The metering proxy
# refuses a subscription request that carries it
# (deploy/metering-proxy/cheese_billing_core.py, the same value).
NO_LOGIN_PLACEHOLDER = "sk-ant-oat01-cheese-no-claude-login-on-this-host"


def rendezvous_paths() -> tuple[str, str]:
    """Allocate a socket and token file for one model launch.

    A unix socket path is capped near 104 bytes and an isolated home already
    spends ~105 (`~/.cheese/home/<project-uuid>/<topic-uuid>/.claude/`), so the
    socket cannot live beside the session it belongs to. Separate launches of
    the same topic must not bind or authenticate through each other's files.
    """
    identity = uuid4().hex
    return f"/tmp/cheese-rv-{identity}.sock", f"/tmp/cheese-rv-{identity}.token"


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
      matched=0; input=0
      sub(/cmdline$/,"environ",path)
      while ((getline part < path)>0) {
        if (part=="CHEESE_TOPIC="topic) matched=1
        if (part ~ /^CLAUDE_BG_RENDEZVOUS_SOCK=.+/) input=1
      }
      close(path)
      # A headless child can inherit the topic without hosting its input.
      if (matched && input) { alive=1; print "alive"; exit }
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
    if ps eww -p "$pid" -o command= 2>/dev/null | awk -v topic="$topic" '
      { for (i=1; i<=NF; i++) {
          if ($i=="CHEESE_TOPIC="topic) matched=1
          if ($i ~ /^CLAUDE_BG_RENDEZVOUS_SOCK=.+/) input=1
      } }
      END { exit !(matched && input) }
    '; then
      echo alive; exit 0
    fi
  done
  echo dead; exit 0
fi
# No supported per-topic view: keep an uncertain session alive.
echo unknown
"""


# Is the machine-local tunnel helper this room's `claude` was pointed at still
# listening? Companion to DEVICE_ALIVE_PROBE, for the OTHER half of "the process
# is alive but cannot reach the model".
#
# The helper is started ONLY by `cheese-tunnel-up`, which runs ONLY before an
# agent is started or claimed — and a reused screen is reasserted (an
# adopt-create), never relaunched. So a helper that dies under a still-running
# `claude` never comes back on its own, and `claude` bakes its HTTPS_PROXY at
# startup and never re-reads it: every turn thereafter dies with `API Error:
# Unable to connect to API (ConnectionRefused)` while the process-tree probe
# above reports `alive`.
# Measured 2026-08-18 on the dev box: five screens in that state, one of them
# replaying the same 28-message batch for the 30th time, ~3 minutes burnt per
# attempt, with no path in the system able to restore them.
#
# The question is whether THIS room's helper holds the port: LISTEN by the pid
# the room recorded. LISTEN alone is not enough. Once a helper dies, the kernel
# may hand its free port to another room's helper, which then answers every
# connection this room's `claude` makes, on that room's credential — the same
# silent share a derived port used to cause. A lingering helper that has lost its
# upstream still holds its own port and is NOT this failure.
#
# The port is read from the room's own `cheese-tunnel.port`, where the helper
# recorded what the kernel gave it; nothing else knows it. CHEESE_TUNNEL_PROBE_HOME
# is the room's home as the backend names it, with the literal `$HOME` placeholder
# only this machine can resolve.
#
# Prints exactly one of `up` / `down` / `unknown`, same tri-state discipline as
# the probe above: only an explicit `down` is actionable, so a missing /proc, an
# absent awk, a port file that is missing or unreadable, or any hiccup leaves a
# working screen alone.
DEVICE_TUNNEL_PROBE = r"""home="${CHEESE_TUNNEL_PROBE_HOME:-}"
case "$home" in
  '') echo unknown; exit 0 ;;
  '$HOME'*) home="$HOME${home#'$HOME'}" ;;
esac
port=$(cat "$home/.cheese/cheese-tunnel.port" 2>/dev/null) || { echo unknown; exit 0; }
case "$port" in ''|*[!0-9]*) echo unknown; exit 0 ;; esac
pid=$(cat "$home/.cheese/cheese-tunnel.pid" 2>/dev/null) || { echo unknown; exit 0; }
case "$pid" in ''|*[!0-9]*) echo unknown; exit 0 ;; esac
[ -r /proc/net/tcp ] || { echo unknown; exit 0; }
command -v awk >/dev/null 2>&1 || { echo unknown; exit 0; }
hex=$(printf '%04X' "$port" 2>/dev/null) || { echo unknown; exit 0; }
# The recorded helper is gone, so whatever holds its port now is not ours.
[ -d "/proc/$pid" ] || { echo down; exit 0; }
[ -r "/proc/$pid/fd" ] || { echo unknown; exit 0; }
# The helper's sockets, as the inodes its fds point at (`socket:[INODE]`).
inodes=$(ls -l "/proc/$pid/fd" 2>/dev/null \
  | sed -n 's/.*socket:\[\([0-9]*\)\].*/\1/p' | tr '\n' ' ')
# /proc/net/tcp columns: $2 is local_address as HEXIP:HEXPORT, $4 is the state
# (0A = LISTEN), $10 the socket inode. The header row's $4 is the literal "st",
# so it never matches. tcp6 has a 32-char hex address but the same `:PORT`
# suffix, hence split on ":" and compare the LAST field.
for f in /proc/net/tcp /proc/net/tcp6; do
  [ -r "$f" ] || continue
  if awk -v p="$hex" -v mine=" $inodes" '$4=="0A" {
         n=split($2,a,":"); if (a[n]==p && index(mine, " " $10 " ")) { f=1; exit } }
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
    resume_session_id: str | None = None,
    topic_id: str | None = None,
) -> MachineLaunch:
    """Claude Code's half of a device launch: the four holes, and its own env.

    The platform half is ``machine_launcher``; nothing below belongs to it. It
    reads a few env vars the screen is created with: ``CHEESE_HOME`` (isolated
    config/home dir), ``CHEESE_WORK`` (cwd), plus the hook wiring
    (``CHEESE_HOOK_URL``/``CHEESE_TOKEN``).

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
        " --dangerously-skip-permissions --remote-control Cheese --disallowedTools "
        + " ".join(disallowed_tools(remote_control=True))
        if remote_control
        else CLAUDE_BASE_ARGS
    )
    pinned_version = CLAUDE_PINNED_VERSION
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
        # of the room itself. Depth is a harness setting and not a design
        # constraint (结论 33): work is flat in the room, there are no child
        # cards, and nothing on the platform branches on this number — a
        # deployment that wants a piece of work to spawn work of its own raises
        # it and no code here changes. Concurrency 4: how many pieces of work a
        # room runs at once; they share one tree, so the ceiling is about how
        # much simultaneous editing of one tree stays comprehensible, not about
        # machine capacity.
        "CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH": str(
            settings.claude_code_max_subagent_spawn_depth
        ),
        "CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS": "4",
        # 关掉 Claude Code **自带**的 `/feedback` 与 `SendFeedback` 工具。
        #
        # 两个意图相同的工具并排放在同一个清单里，模型会选错那一个：它撞到的毛病是
        # **这个平台**的，而官方那个入口把草稿写进本机队列、由人自己找地方发出去，
        # 结果就是「提了、但没到平台的反馈里」——一份谁都看不见的证据。
        #
        # 这一条必须是**环境变量**：那个开关按设计只在进程启动时读一次，改
        # settings.json 不管用（官方的开关就是给部署方这么用的）。
        # （旧名 `DISABLE_BUG_COMMAND` 官方也还认，用新名。）
        "DISABLE_FEEDBACK_COMMAND": "1",
    }
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
# starts by hand. With it, claude keeps its config out of the owner's files; the
# one thing it shares with the host user is the Claude login, which the
# credentials hole below points it at on purpose.
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
""",
        credentials=f"""\
# The session's Claude login is its host's own. A host with a one-year
# `claude setup-token` at ~/.cheese/claude-setup-token uses that; it never
# refreshes. Otherwise it is the OS user's `~/.claude` login, and every session
# points its credential store at it with CLAUDE_SECURESTORAGE_CONFIG_DIR while
# keeping its own config dir: one shared store is what lets Claude Code's own
# refresh, under its own lock, renew the login for all of them — a private copy
# per session would be stranded by the first sibling's refresh, since a refresh
# rotates the pair. A host with neither still boots Claude Code on a
# placeholder that authenticates nothing, so projects on the API-key pool run;
# the metering proxy refuses a subscription turn that carries it. An inherited
# env token would win over the store, so none is let through.
unset CLAUDE_CODE_OAUTH_TOKEN
if [ -s "$REAL_HOME/.cheese/claude-setup-token" ]; then
  CLAUDE_CODE_OAUTH_TOKEN="$(cat "$REAL_HOME/.cheese/claude-setup-token")"
  export CLAUDE_CODE_OAUTH_TOKEN
elif [ -s "$REAL_HOME/.claude/.credentials.json" ]; then
  export CLAUDE_SECURESTORAGE_CONFIG_DIR="$REAL_HOME/.claude"
else
  export CLAUDE_CODE_OAUTH_TOKEN="{NO_LOGIN_PLACEHOLDER}"
fi
cheese_launch_phase credentials_selected
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
if [ -z "$CLAUDE_V" ] || [ "$(printf '%s\\n%s\\n' "{pinned_version}" "$CLAUDE_V" \\
    | sort -V | head -n 1)" != "{pinned_version}" ]; then
  echo "cheese-launch: claude ${{CLAUDE_V:-unknown}} at $CLAUDE_BIN is older than \\
{pinned_version}, and the platform's pinned build is not at $_pin; prompt \\
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
CLAUDE="\\"$CLAUDE_BIN\\"{claude_args}"
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
""",
        contract=claude_args,
        command="$CLAUDE",
        env=env,
    )


def build_launch_script(**named) -> str:
    """The launcher a device runs for a Claude Code session."""
    holes = launch_holes(**named)
    return machine_launcher.launch_script(
        configure=holes.configure,
        credentials=holes.credentials,
        prepare=holes.prepare,
        command=holes.command,
    )


def on_machine(
    place: MachinePlace,
    *,
    system_prompt: str,
    resume_session_id: str | None,
) -> MachineLaunch:
    """Claude Code, now that a machine has said where and what this room is.

    ``place`` states facts; what they mean is decided here. Every room ships
    the executor client and hands ``claude`` to it, one whose operator may drive
    it directly runs without the permission prompt, and a CA to trust is a file
    only the script can name an absolute path for.
    """
    return launch_holes(
        # A room whose work is done on an executor has nothing of its own to
        # hand back; the executor owns the checkout.
        sync_on_stop=bool(place.project_id) and place.execution_target is None,
        system_prompt=system_prompt,
        ca_pem=place.ca_pem,
        remote_control=place.remote_control,
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
