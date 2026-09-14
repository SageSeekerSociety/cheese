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
from app.domain.agent.harness.pi.bundle import build

# The pinned CLI. pi ships as an npm package rather than a single binary, so
# unlike the claude pin there is nothing for the platform to serve — the machine
# installs it from the registry into a versioned prefix of its own.
PACKAGE = "@earendil-works/pi-coding-agent"
VERSION = "0.85.1"

# pi's config directory, which is to pi what CLAUDE_CONFIG_DIR is to Claude
# Code: set it and pi reads and writes nothing of the machine owner's.
CONFIG_DIR = "$HOME/.pi/agent"

BINARY = "$REAL_HOME/.cheese/tools/pi/node_modules/.bin/pi"


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

    ``state`` is where the runner keeps its journal and binds its socket, AS THE
    CONNECTOR WILL RESOLVE IT (a literal ``$HOME/...``): it is the backend's only
    address for this session, and it has to outlive the session home, so it sits
    under the machine owner's home rather than inside the isolated one.
    """

    system_prompt: str
    state: str
    api_base: str
    model: str
    resume_session_id: str | None = None
    agent_handle: str | None = None

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
            # machine happens to keep in their home.
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
        }

    def contract(self) -> str:
        """What the connector compares to decide a live session still matches."""
        return json.dumps(
            {"harness": "pi", "version": VERSION, "args": self.arguments()},
            sort_keys=True,
        )


def build_launch_script(launch: PiLaunch) -> str:
    """The device launcher for a pi session: the platform's, filled with pi's."""
    return machine_launcher.launch_script(
        configure=_configure(provider(launch.api_base, launch.model)),
        prepare=_prepare(launch.state, launch.configuration()),
        contract=launch.contract(),
        command='"$PI_RUNNER"',
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
    return f"""\
# --- pi, pinned, and the runner that owns it ------------------------------
# The pin lives under the machine owner's home, not the session's: a room is
# torn down and rebuilt for reasons that say nothing about which pi is
# installed, and re-installing the package on every one of those is a minute of
# someone's machine spent proving what the last launch already knew.
PI_BIN="{BINARY}"
if [ "$("$PI_BIN" --version 2>/dev/null)" != "{VERSION}" ]; then
  # One guard, not two: a machine with no npm and a machine whose npm cannot
  # reach the registry are the same answer to the room, and the shell's own
  # "npm: not found" lands on stderr right above this line either way.
  if ! npm install --prefix "$REAL_HOME/.cheese/tools/pi" \\
      --no-audit --no-fund --loglevel error "{PACKAGE}@{VERSION}" >&2; then
    echo "cheese-launch: could not install {PACKAGE}@{VERSION} on this machine; \\
it needs node and npm on PATH and access to the npm registry." >&2
    exit 1
  fi
fi
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
