"""pi 在中心机上怎么起来：钉住的版本、它的配置、和一个守着它的 runner。

pi runs on the central session host, beside the room's other sessions, and its
hands are on the room's machine (`machine.py`, #1106): the session does not have
to wait for a machine to talk, and the machine is taken only when the work
needs it. What reaches the host is a Python script over the connector's stdin,
as Codex's does (`HostLaunch.program`): the runner archive when the host does
not hold it yet, its configuration, and the shell that leaves the pinned pi
installed; `host.configure` does the rest.
"""

import base64
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from app.domain.agent.harness.pi.bundle import build

# The pinned agent, served by the platform the way the claude pin is: the
# machine fetches it from us, never from the vendor. `pi_dist` carries the
# argument; what matters here is that the version in this path is a fact about
# what we handed the machine rather than about what a registry resolved for it.
VERSION = "1.0.0"


def extension() -> dict[str, str]:
    """What the extension is, as files. `index.ts` is what pi is pointed at —
    TypeScript uncompiled, because pi loads extensions through jiti, so the
    file written is the file that runs. It travels as content rather than as a
    path for the reason the skills and the system prompt do: the session host
    has no copy of it."""
    return {
        "index.ts": (Path(__file__).parent / "platform.ts").read_text(encoding="utf-8")
    }


def root(home: str) -> str:
    """Where the pin lives, under the session host's own home.

    Version-named, so "is the pin installed" is a question about a path rather
    than about running a 100MB binary to ask it its name — and so a pin bump
    installs beside the old one instead of over a copy a room may still be
    running. One copy for every room the host runs: which pi is installed says
    nothing about any one room.
    """
    return f"{home}/.cheese/tools/pi/{VERSION}"


def install(*, home: str, base: str) -> str:
    """Shell that leaves the pinned pi installed and ``PI_BIN`` naming it.

    Run by every launch (`host.configure`), so a pin bump reaches the session
    host with the first room that starts after it, and costs nothing once the
    pin is there.

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


def arguments(model: str) -> list[str]:
    """pi's own argv, minus what the runner adds (mode, session, prompt)."""
    return [
        "--provider",
        "cheese",
        "--model",
        f"cheese/{model}",
        # A room's context is the platform's to assemble; a file the session
        # host happens to hold is not part of it.
        "--no-context-files",
        # PI_CODING_AGENT_DIR moves pi's own config, and that is ALL it moves:
        # skills, extensions, prompt templates and themes are still discovered
        # from the host's ~/.agents and cwd. Verified 2026-09-14 — a plain run
        # carried the owner's SKILL.md files into the system prompt with the
        # config dir already pointed elsewhere. The project's own skills and the
        # platform's come back through `--skill`, which is additive even with
        # this; the runner names both.
        "--no-skills",
        "--no-extensions",
        "--no-prompt-templates",
        "--no-themes",
        # Nothing a room waits on may depend on reaching pi.dev.
        "--offline",
    ]


@dataclass(frozen=True)
class HostLaunch:
    """One launch of a room's pi on the session host, before it is sent.

    ``contract`` is everything about it that a running pi could not be made to
    adopt — the build, the argv, the execution target, the extension, the
    skills — and not the room's system prompt. A runner started with a
    different one is replaced once it is idle (`host.configure`).
    """

    contract: str
    payload: dict
    archive: bytes

    def program(self, *, ship: bool) -> str:
        """The Python the session host runs (`python3 -`) to start the room's pi.

        The host keeps the runner archive under its digest. Without ``ship``
        the program carries only that digest, and a host that does not hold
        the file answers ``{"runner": "missing"}`` and starts nothing, for the
        launch to be sent again with the archive.
        """
        payload = {**self.payload, "digest": hashlib.sha256(self.archive).hexdigest()}
        if ship:
            payload["archive"] = base64.b64encode(self.archive).decode()
        return f"""import base64,hashlib,json,os,sys,tempfile
from pathlib import Path
payload=json.loads({json.dumps(payload)!r})
state=Path(payload["state"].replace("$HOME",str(Path.home()))).expanduser().resolve()
state.mkdir(parents=True,exist_ok=True,mode=0o700)
digest=payload.pop("digest")
shipped=payload.pop("archive",None)
# A new deployment never overwrites the modules a live runner is already using.
artifact=state/f"runner-{{digest}}.pyz"
if not artifact.exists():
    if shipped is None:
        print(json.dumps({{"runner":"missing"}}))
        sys.exit(0)
    archive=base64.b64decode(shipped,validate=True)
    if hashlib.sha256(archive).hexdigest()!=digest:
        sys.exit("the runner archive does not match its digest")
    with tempfile.NamedTemporaryFile(dir=state,delete=False) as output:
        output.write(archive)
    os.replace(output.name,artifact)
payload["artifact"]=str(artifact)
sys.path.insert(0,str(artifact))
from app.domain.agent.harness.pi.host import configure
print(json.dumps(configure(payload)))
"""


def _uncredentialed(target: dict | None) -> dict | None:
    """The target as a launch contract compares it: a credential carried in it
    (a document's 芝士 reading the room's machine) is minted afresh for every
    question, and a new one alone is no reason to start pi again."""
    if target is None or "execution_token" not in target:
        return target
    return {k: v for k, v in target.items() if k != "execution_token"}


def on_host(
    *,
    state: str,
    config: dict,
    api_base: str,
    model: str,
    env: dict[str, str],
    models: str | None = None,
    host: dict | None = None,
) -> HostLaunch:
    """The room's pi as the session host is to start it. ``config`` is what
    the runner reads (`entry.py`) without its ``contract``.

    ``models`` is the provider file when this session's model needs one of its
    own (``provider``'s by default). ``host`` is what ``host.configure`` does
    beyond starting the runner, for a person's 芝士: how many of the person's
    sessions may run at once, the memory each may use, and pi's settings.
    """
    archive = build()
    models = models or provider(api_base, model)
    contract = hashlib.sha256(
        json.dumps(
            [
                hashlib.sha256(archive).hexdigest(),
                models,
                {
                    **config,
                    "opening": None,
                    "execution_target": _uncredentialed(config["execution_target"]),
                },
            ],
            sort_keys=True,
        ).encode()
    ).hexdigest()
    return HostLaunch(
        contract=contract,
        payload={
            "state": state,
            "config": {**config, "contract": contract},
            "models": models,
            "install": install(home="$HOME", base=api_base.rstrip("/")),
            "env": env,
            **(host or {}),
        },
        archive=archive,
    )
