"""Claude Code's half of a device launch: its config, its binary, its runner.

A session the platform opens on the session host runs a self-contained launcher
(no files pre-baked on the machine): the platform half (``machine_launcher``)
prepares the home, the platform CLI and the supervisor, and this half writes an
isolated ``$CLAUDE_CONFIG_DIR`` (settings, first-launch gates, the platform's
skills and system prompt), places the pinned binary, and hands the command that
starts it to the runner (``runner.py``), which is what the screen runs. The
runner starts Claude Code headless and holds its stdin and stdout for the life of
the session.

Everything here is pure (string building) so it is unit-testable without a
device. ``on_machine`` answers a ``MachinePlace`` with this harness's half of a
launch; ``machine_launcher`` owns the other half and joins the two.
"""

import base64
import dataclasses
import gzip
import hashlib
import json
import shlex
from pathlib import Path

from app.core.config import settings
from app.domain.agent import machine_launcher
from app.domain.agent.harness.claude_code.bundle import build
from app.domain.agent.harness.claude_code.cli import LAUNCH_ARGS
from app.domain.agent.harness.claude_code.remote_execution import release
from app.domain.agent.harness.claude_code.session_launch import session_settings
from app.domain.agent.harness.launch import MachineLaunch, MachinePlace
from app.domain.agent.skills import native_skill_files

# --- the version this session is pinned to ----------------------------------
# The runner drives Claude Code over its stream-json pipes, a protocol no
# document pins: `scripts/remote_execution/headless_contract.py` is what says
# the build still speaks it, so the launcher starts ONE verified build and
# refuses anything under the floor rather than a build nobody checked.
#
# Raising these is a deliberate act: re-run the headless contract against the
# new build first, because "it launched" is not evidence the protocol still holds.
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
#   * ``sandbox/Dockerfile`` 的 ``ARG CLAUDE_CODE_VERSION``
#     —— 以上都由 ``tests/unit/test_capability_matrix.py`` 钉住
# ``scripts/remote_execution/package.json`` 和它的 lock 也装一个固定版本，那份没
# 有守卫：对不上时 ``remote_execution/client.py`` 的版本闸门在 CI 里当场拒掉，
# 红得见。
CLAUDE_PINNED_VERSION = "2.1.282"
CLAUDE_MIN_VERSION = "2.1.282"

# What every session carries as its Claude login. It authenticates nothing; the
# metering proxy replaces it with the platform's credential, or answers for it
# when there is none (deploy/metering-proxy/cheese_billing_core.py, the same
# value).
NO_LOGIN_PLACEHOLDER = "sk-ant-oat01-cheese-no-claude-login-on-this-host"


# Is the machine-local tunnel helper this room's `claude` was pointed at still
# listening? The session's runner answers whether the process is alive; this
# answers the other half of "the process is alive but cannot reach the model".
#
# The helper is started ONLY by `cheese-tunnel-up`, which runs ONLY before an
# agent is started or claimed — and a reused screen is reasserted (an
# adopt-create), never relaunched. So a helper that dies under a still-running
# `claude` never comes back on its own, and `claude` bakes its HTTPS_PROXY at
# startup and never re-reads it: every turn thereafter dies with `API Error:
# Unable to connect to API (ConnectionRefused)` while the runner reports the
# process alive.
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
# Prints exactly one of `up` / `down` / `unknown`: only an explicit `down` is
# actionable, so a missing /proc, an
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


def launch_holes(
    *,
    state: str,
    system_prompt: str = "",
    ca_pem: str = "",
    resume_session_id: str | None = None,
) -> MachineLaunch:
    """Claude Code's half of a device launch: the four holes, and its own env.

    The platform half is ``machine_launcher``; nothing below belongs to it. It
    reads a few env vars the screen is created with: ``CHEESE_HOME`` (isolated
    config/home dir), ``CHEESE_WORK`` (cwd) and ``CHEESE_EXECUTION_TARGET``.

    ``state`` is where the runner keeps its journal and socket, as the
    CONNECTOR resolves it (a literal ``$HOME/...``): the backend derives the
    socket it calls from that same string.

    ``system_prompt`` (the platform's assembled system prompt) is embedded in the
    script itself — written to ``$HOME/.claude/cheese-system-prompt.md`` on the
    device and handed to `claude` via ``--append-system-prompt-file``. Embedding
    beats an env var here: the connector's env transport is not guaranteed to
    survive multi-KB values with newlines, while a quoted heredoc is.

    ``ca_pem`` (the metering proxy's CA) rides the same way for the same reason,
    written to ``$HOME/.claude/proxy-ca.pem`` with ``NODE_EXTRA_CA_CERTS``
    exported over whatever placeholder the screen env carried — the server
    cannot know the device user's home, so only the script can name the real
    absolute path (an untrusted CA fails as an opaque TLS error far from its
    cause)."""
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
    webfetch_transport = Path(__file__).with_name("webfetch_transport.cjs").read_text()
    # Compressed: written out as heredocs the skills alone outgrew what one
    # shell argument may hold, and they only grow. Base64 has no quote in it.
    skills = base64.b64encode(
        gzip.compress(json.dumps(native_skill_files()).encode(), mtime=0)
    ).decode()
    skill_setup = f"""python3 - "$CLAUDE_CONFIG_DIR" <<'CHEESE_SKILLS'
import base64, gzip, json, os, sys
files = json.loads(gzip.decompress(base64.b64decode("{skills}")))
for name, content in files.items():
    path = os.path.join(sys.argv[1], name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as out:
        out.write(content)
CHEESE_SKILLS"""
    settings_json = json.dumps(session_settings(), ensure_ascii=False)
    claude_args = " " + shlex.join(LAUNCH_ARGS)
    pinned_version = CLAUDE_PINNED_VERSION
    helper_sources = release.sources()
    # base64 through the script: the archive is bytes. Single-quoted into the
    # script: the literal `$HOME` in the state is the CONNECTOR's placeholder,
    # and a double-quoted assignment would have the session's own shell expand
    # it to the ISOLATED home instead.
    quoted_state = shlex.quote(state)
    archive = base64.b64encode(build()).decode()

    def holes(system_prompt: str, helper_sources: dict[str, str]) -> tuple[str, str]:
        """The configure and prepare holes, written for this prompt and these
        helpers: once for the launch, and once for what the launch is compared
        by (below)."""
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
        configure = f"""\
# THE isolation boundary on a machine we do not own (#5): claude reads AND
# writes its config — settings.json, .claude.json, .credentials.json — under
# CLAUDE_CONFIG_DIR when it is set, and never falls back to the login user's
# ~/.claude for any of them (verified 2026-08-15: a bogus credential in the
# config dir fails 401 with a valid one sitting in ~/.claude, untouched).
# Without this, os.homedir() ignores our exported $HOME and claude lands in the
# machine owner's real ~/.claude — which is why earlier launches had to REWRITE
# the owner's settings.json to be routed at all, hijacking every claude the
# owner starts by hand. With it, claude never reads or writes the owner's
# files.
export CLAUDE_CONFIG_DIR="$HOME/.claude"
mkdir -p "$CLAUDE_CONFIG_DIR"
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
cat > "$CLAUDE_CONFIG_DIR/settings.json" <<'JSON'
{settings_json}
JSON
cat > "$CLAUDE_CONFIG_DIR/cheese-system-prompt.md" <<'SYSPROMPT'
{system_prompt}SYSPROMPT
"""
        prepare = f"""\
# --- the pinned build, and the floor under it ---------------------------------
# The binary is pinned to a verified build: the runner speaks its stream-json
# protocol, which no document pins, so "whatever `claude` resolves to today" is
# not a basis for a session. PATH is the fallback, still gated by the floor.
#
# The pin is PLACED here when it is missing, from the platform — the same
# unauthenticated route enrollment downloads from — so a pin bump reaches a
# machine enrolled under the previous pin at its next launch, with no
# re-provisioning and no owner action. Non-fatal: the chain below still runs,
# and the floor check still refuses a build that is too old. A screen created
# without CHEESE_API skips this.
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
{pinned_version}, and the platform's pinned build is not at $_pin." >&2
  exit 1
fi
CLAUDE="\\"$CLAUDE_BIN\\"{claude_args}"
# The platform system prompt (written next to settings.json above). The path is
# embedded QUOTED so a home dir with spaces survives the runner's `sh -c`.
CHEESE_SP="$HOME/.claude/cheese-system-prompt.md"
[ -s "$CHEESE_SP" ] && CLAUDE="$CLAUDE --append-system-prompt-file \\"$CHEESE_SP\\""
{execution_setup}
# --- the runner that owns the session -----------------------------------------
# It outlives this launch and every backend that talks to it, so its state goes
# where the CONNECTOR resolves the socket from (runner.socket_path), which is the
# owner's home — the session home is rebuilt, this is not.
CLAUDE_STATE={quoted_state}
case "$CLAUDE_STATE" in
  "\\$HOME"*) CLAUDE_STATE="$REAL_HOME${{CLAUDE_STATE#\\$HOME}}";;
esac
mkdir -p "$CLAUDE_STATE"
chmod 700 "$CLAUDE_STATE"
CLAUDE_ARTIFACT="$(python3 - "$CLAUDE_STATE" <<'CLAUDERUNNER'
import base64, hashlib, os, sys, tempfile
from pathlib import Path
state = Path(sys.argv[1])
archive = base64.b64decode("{archive}", validate=True)
# A new deployment never overwrites the modules a live runner is already using.
artifact = state / f"runner-{{hashlib.sha256(archive).hexdigest()}}.pyz"
if not artifact.exists():
    with tempfile.NamedTemporaryFile(dir=state, delete=False) as output:
        output.write(archive)
    os.replace(output.name, artifact)
print(artifact, end="")
CLAUDERUNNER
)"
# The command reaches the runner in its environment: it is a shell string, and
# the runner starts it with `sh -c` so the quoting above means what it says.
export CHEESE_CLAUDE_COMMAND="$CLAUDE"
# Its own stderr is kept: a runner that fails on the way up takes the pane with
# it, and this file is then the only record of why.
CLAUDE_RUNNER="python3 -I -S \\"$CLAUDE_ARTIFACT\\" --state \\"$CLAUDE_STATE\\" \\
  2>>\\"$CLAUDE_STATE/runner.log\\""
"""
        return configure, prepare

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
        # An OFFER, not an instruction: the runner takes it only if the
        # transcript is on that machine's disk (``runner.transcript``). Carried
        # on the env for the same reason the tunnel vars are — a remote launch
        # is built entirely out of its environment.
        env["CHEESE_RESUME_SESSION"] = resume_session_id
    configure, prepare = holes(system_prompt, helper_sources)
    launch = MachineLaunch(
        configure=configure,
        credentials=f"""\
# A session never holds a Claude credential. It boots on a placeholder that
# authenticates nothing, and the metering proxy puts the real credential on
# each request on its way to Anthropic when the platform has one, so logging
# in or out takes effect for running sessions without a restart. An env token
# is also one Claude Code never refreshes, so no session ever rotates a pair
# the proxy holds. An inherited token would win over this one: none is let
# through.
unset CLAUDE_CODE_OAUTH_TOKEN
export CLAUDE_CODE_OAUTH_TOKEN="{NO_LOGIN_PLACEHOLDER}"
cheese_launch_phase credentials_selected
""",
        prepare=prepare,
        command='"$CLAUDE_RUNNER"',
        env=env,
    )
    # What a running session is compared against: the whole launch, written
    # without the two things that are not the platform's. The system prompt is
    # the room's and is rebuilt every turn; a live session keeps the one it
    # started with. The resume offer names the conversation, which a relaunch
    # continues. The helpers a release puts into a running session are left
    # out too, since changing one is a release and not a relaunch. Everything
    # else here is read once by a process that keeps it for its life — the
    # binary, the argv, the settings, the skills, the runner, the environment,
    # and everything `client.prepare` writes — so any change to it has to
    # reach a live session as a new one.
    configured, prepared = holes("", release.launch_only(helper_sources))
    fixed_env = {k: v for k, v in env.items() if k != "CHEESE_RESUME_SESSION"}
    contract = hashlib.sha256(
        json.dumps(
            [configured, launch.credentials, prepared, launch.command, fixed_env],
            sort_keys=True,
        ).encode()
    ).hexdigest()
    return dataclasses.replace(launch, contract=contract)


def on_machine(
    place: MachinePlace,
    *,
    system_prompt: str,
    resume_session_id: str | None,
) -> MachineLaunch:
    """Claude Code, now that a machine has said where and what this room is.

    ``place`` states facts; what they mean is decided here. Every room ships
    the executor client and hands ``claude`` to it, and a CA to trust is a file
    only the script can name an absolute path for.
    """
    return launch_holes(
        state=place.state,
        system_prompt=system_prompt,
        ca_pem=place.ca_pem,
        resume_session_id=resume_session_id,
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
