"""pi 在会话机器上怎么起来：平台那五个洞，填的是 pi 的东西。

Same machine as the workspace, which is the whole reason this harness exists.
pi does not run on a subscription, so nothing about it needs a box we can put a
proxy in front of — it reaches the model through the platform's scoped token
like any other metered caller, and can therefore live where the files are.

What that buys, and why the runner is the screen's program rather than a daemon
started beside it: the session gets the project's prepared environment, the
event spool and its drainer, the preview tunnel, and a supervisor that reaps it
— the platform half of ``machine_launcher``, unchanged, because none of it was
ever about Claude Code.
"""

import base64
import json
import shlex
from dataclasses import dataclass

from app.domain.agent import machine_launcher
from app.domain.agent.harness.launch import MachineLaunch, MachinePlace
from app.domain.agent.harness.pi.bundle import build
from app.domain.agent.skills import native_skill_files

# The pinned agent, served by the platform the way the claude pin is: the
# machine fetches it from us, never from the vendor. `pi_dist` carries the
# argument; what matters here is that the version in this path is a fact about
# what we handed the machine rather than about what a registry resolved for it.
VERSION = "0.85.1"

# pi's config directory, which is to pi what CLAUDE_CONFIG_DIR is to Claude
# Code: set it and pi reads and writes nothing of the machine owner's.
CONFIG_DIR = "$HOME/.pi/agent"


def root(home: str) -> str:
    """Where the pin lives, under the machine OWNER's home.

    Version-named, so "is the pin installed" is a question about a path rather
    than about running a 100MB binary to ask it its name — and so a pin bump
    installs beside the old one instead of over a copy something may still be
    running. The owner's home and not the session's: a room is torn down and
    rebuilt for reasons that say nothing about which pi is installed.

    ``home`` is spelled differently by the two callers — the launcher has
    already resolved the owner's home into ``$REAL_HOME``, an enrollment script
    is simply running as that owner — and getting it wrong is not visible
    anywhere until a room fails to start, which is why neither writes the path
    out itself.
    """
    return f"{home}/.cheese/tools/pi/{VERSION}"


def install(*, home: str, base: str) -> str:
    """Shell that leaves the pinned pi installed and ``PI_BIN`` naming it.

    Run both by the launcher, so a pin bump reaches a machine enrolled under
    the previous one and a machine that never ran an enrollment script gets it
    at all, and by enrollment, so a cloud machine does not carry capacity it
    cannot deliver until the first room discovers it (#1034).

    ``base`` is the platform origin WITHOUT a trailing slash — ours, never the
    vendor's, for every reason ``pi_dist`` sets out.
    """
    target = root(home)
    return f"""\
PI_BIN="{target}/pi"
if [ ! -x "$PI_BIN" ]; then
  case "$(uname -m)" in
    x86_64|amd64) _piarch=x64 ;;
    aarch64|arm64) _piarch=arm64 ;;
    *) echo "pi has no build for $(uname -m)" >&2; exit 1 ;;
  esac
  if [ "$(uname -s)" = "Linux" ]; then
    # The vendor's Linux builds link glibc and there is no musl variant, so on
    # an Alpine-style machine the download would succeed and the loader would
    # then refuse the binary with a message about no such file. Say the real
    # reason instead of arranging for that one.
    if ldd /bin/ls 2>&1 | grep -q musl; then
      echo "pi has no musl build; this machine cannot run it." >&2
      exit 1
    fi
    _piplat="linux-$_piarch"
  else
    _piplat="darwin-$_piarch"
  fi
  _pitmp="{target}.incoming.$$"
  _pitgz="{target}.incoming.$$.tar.gz"
  mkdir -p "$(dirname "{target}")"
  rm -rf "$_pitmp"
  # Downloaded whole and then unpacked, not piped into tar: /bin/sh here is
  # dash, which has no pipefail, so a curl that died mid-transfer would leave
  # tar's success as the only status the script could see.
  if ! curl -fsSL --retry 3 --retry-delay 2 -m 600 \
      "{base}/connector/pi/{VERSION}/$_piplat/pi.tar.gz" -o "$_pitgz" \
      || [ ! -s "$_pitgz" ]; then
    rm -f "$_pitgz"
    echo "could not fetch pi {VERSION} for $_piplat from the platform" >&2
    exit 1
  fi
  mkdir -p "$_pitmp"
  if ! tar xzf "$_pitgz" --strip-components=1 -C "$_pitmp"; then
    rm -rf "$_pitmp" "$_pitgz"
    echo "pi {VERSION} for $_piplat did not unpack" >&2
    exit 1
  fi
  rm -f "$_pitgz"
  # Renamed last, so the version-named path is either absent or a complete
  # install — never a half-unpacked tree the next launch would accept as done.
  mv "$_pitmp" "{target}"
  # Only on the branch that just installed: what we placed has to run before
  # anything believes it is there, and asking an already-installed pin its
  # version on every launch buys nothing for a third of a second each time.
  if [ "$("$PI_BIN" --version 2>/dev/null)" != "{VERSION}" ]; then
    rm -rf "{target}"
    echo "pi at $PI_BIN is not {VERSION} and will not run here" >&2
    exit 1
  fi
fi
"""


def provider(api_base: str, model: str) -> str:
    """``models.json``: one provider, pointing at this platform's gateway.

    The token is named, not written: ``$CHEESE_TOKEN`` is resolved by pi at
    request time, so the room's scoped credential never lands in a file on
    someone else's machine, nor in the argv of a process anybody can list.
    """
    return json.dumps(
        {
            "providers": {
                "cheese": {
                    "baseUrl": f"{api_base}/llm/v1",
                    "api": "openai-completions",
                    "apiKey": "$CHEESE_TOKEN",
                    "authHeader": True,
                    # Measured 2026-09-15 against the GLM the gateway serves:
                    # without these pi sends `store`, `max_completion_tokens`
                    # and (on a reasoning model) a `developer` role — none of
                    # which that endpoint declares support for. It tolerated
                    # them for one turn; tolerated is not supported.
                    #
                    # These are the upstream MODEL's properties, and a gateway
                    # is exactly what hides which model that is. Serving a
                    # family with a different profile means this block has to
                    # come from wherever the model is configured, not from here.
                    "compat": {
                        "supportsStore": False,
                        "supportsDeveloperRole": False,
                        "maxTokensField": "max_tokens",
                    },
                    "models": [{"id": model}],
                }
            }
        },
        ensure_ascii=False,
        indent=2,
    )


@dataclass(frozen=True)
class PiLaunch:
    """跑什么 —— pi 的那一半，等机器说完「在哪」。

    Where the runner keeps its journal and which gateway it reaches are the
    MACHINE's half of the answer, so they arrive in ``place`` — held here they
    would be the same two facts written down twice.
    """

    system_prompt: str
    model: str
    resume_session_id: str | None = None
    agent_handle: str | None = None
    harness: str = "pi"

    def arguments(self) -> list[str]:
        """pi's own argv, minus what the runner adds (mode, session, prompt)."""
        return [
            "--provider",
            "cheese",
            "--model",
            f"cheese/{self.model}",
            # A room's context is the platform's to assemble; a file the machine
            # happens to be holding is not part of it.
            "--no-context-files",
            # PI_CODING_AGENT_DIR moves pi's own config, and that is ALL it
            # moves: skills, extensions, prompt templates and themes are still
            # discovered from the machine owner's ~/.agents and cwd. Verified
            # 2026-09-14 — a plain run carried the owner's SKILL.md files into
            # the system prompt with the config dir already pointed elsewhere.
            # A room's agent must not read what the person who lent us the
            # machine happens to keep in their home. The platform's own skills
            # come back through `--skill`, which is additive even with this —
            # the runner adds those, being the only side that knows where it
            # wrote them.
            "--no-skills",
            "--no-extensions",
            "--no-prompt-templates",
            "--no-themes",
            # Nothing a room waits on may depend on reaching pi.dev.
            "--offline",
        ]

    def configuration(self) -> dict:
        """What the runner reads. Paths are NOT in here on purpose — the shell
        knows them and JSON written by a shell cannot carry a system prompt
        safely, so the two travel separately and meet in the runner's argv."""
        return {
            "opening": {
                "system_prompt": self.system_prompt,
                "resume_token": self.resume_session_id,
                "model": self.model,
                "agent_handle": self.agent_handle,
            },
            "args": self.arguments(),
            # Carried as content, not as paths: these are the platform's files,
            # and the machine has no copy of them. Same reason the system prompt
            # travels this way — the runner writes both and points pi at them.
            "skills": native_skill_files(),
        }

    def contract(self) -> str:
        """What the connector compares to decide a live session still matches."""
        return json.dumps(
            {"harness": "pi", "version": VERSION, "args": self.arguments()},
            sort_keys=True,
        )

    def on(self, place: MachinePlace) -> MachineLaunch:
        """pi, now that a machine has said where.

        Most of what ``place`` states is not pi's business: it has no warm pool
        to stage, no subscription CA to trust, and no executor to hand its tools
        to — on this machine the tools ARE local. What it takes is where the
        session lives and how to reach the model.
        """
        return MachineLaunch(
            configure=_configure(provider(place.api_base, self.model)),
            prepare=_prepare(place.state, self.configuration()),
            contract=self.contract(),
            command='"$PI_RUNNER"',
        )


def build_launch_script(launch: PiLaunch, place: MachinePlace) -> str:
    """The device launcher for a pi session: the platform's, filled with pi's."""
    holes = launch.on(place)
    return machine_launcher.launch_script(
        configure=holes.configure,
        prepare=holes.prepare,
        contract=holes.contract,
        command=holes.command,
    )


def _configure(models_json: str) -> str:
    return f"""\
# pi reads and writes its config under PI_CODING_AGENT_DIR when it is set, and
# never falls back to the machine owner's ~/.pi — the same isolation boundary
# CLAUDE_CONFIG_DIR draws for the other harness, drawn before pi ever starts.
export PI_CODING_AGENT_DIR="{CONFIG_DIR}"
# Startup network calls are not free on somebody else's machine, and a version
# check that fails must never be the reason a room cannot answer.
export PI_OFFLINE=1
export PI_TELEMETRY=0
mkdir -p "$PI_CODING_AGENT_DIR"
cat > "$PI_CODING_AGENT_DIR/models.json" <<'PIMODELS'
{models_json}
PIMODELS
"""


def _prepare(state: str, configuration: dict) -> str:
    # base64 through a quoted heredoc: the archive is bytes, and the config is
    # the room's system prompt, which may hold any `$` or backtick a person
    # typed. Neither may be re-read by the shell on its way to disk.
    # Single-quoted into the script: the literal `$HOME` in it is the
    # CONNECTOR's placeholder, and a double-quoted assignment would have the
    # session's own shell expand it to the ISOLATED home instead.
    quoted_state = shlex.quote(state)
    archive = base64.b64encode(build()).decode()
    config = base64.b64encode(
        json.dumps(configuration, ensure_ascii=False).encode()
    ).decode()
    return (
        "# --- pi, pinned, and the runner that owns it ---------------------------\n"
        + install(home="$REAL_HOME", base="${CHEESE_API%/}")
        + f"""\
# The runner outlives this launch and every backend that talks to it, so its
# state goes where the CONNECTOR resolves the socket from (runner.socket_path),
# which is the owner's home — the session home is rebuilt, this is not.
PI_STATE={quoted_state}
case "$PI_STATE" in "\\$HOME"*) PI_STATE="$REAL_HOME${{PI_STATE#\\$HOME}}";; esac
mkdir -p "$PI_STATE"
chmod 700 "$PI_STATE"
PI_ARTIFACT="$(python3 - "$PI_STATE" <<'PIRUNNER'
import base64, hashlib, os, sys, tempfile
from pathlib import Path
state = Path(sys.argv[1])
(state / "runner.json").write_bytes(base64.b64decode("{config}", validate=True))
(state / "runner.json").chmod(0o600)
archive = base64.b64decode("{archive}", validate=True)
# A new deployment never overwrites the modules a live runner is already using.
artifact = state / f"runner-{{hashlib.sha256(archive).hexdigest()}}.pyz"
if not artifact.exists():
    with tempfile.NamedTemporaryFile(dir=state, delete=False) as output:
        output.write(archive)
    os.replace(output.name, artifact)
print(artifact, end="")
PIRUNNER
)"
PI_RUNNER="python3 -I -S \\"$PI_ARTIFACT\\" --state \\"$PI_STATE\\" \\
  --config \\"$PI_STATE/runner.json\\" --binary \\"$PI_BIN\\" --cwd \\"$CHEESE_WORK\\""
"""
    )
