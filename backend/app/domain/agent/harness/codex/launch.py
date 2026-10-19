"""Ship the runner through the connector's existing Python stdin transport."""

import base64
import hashlib
import json

from app.domain.agent.harness.codex.bundle import build


def launch_identity(config: dict) -> str:
    """What of a launch the host holds a running runner to (``host.configure``):
    the execution target and the opening's teammate and model. The host refuses
    a running runner a different target or teammate, and moves it to a
    different model; everything else a launch carries reaches only a runner it
    starts."""
    opening = config["opening"]
    return json.dumps(
        [
            config["execution_target"],
            *(opening.get(key) for key in ("agent_handle", "model")),
        ],
        sort_keys=True,
        default=str,
    )


def script(
    *, state: str, config: dict, codex_config: str, env: dict[str, str], ship: bool
) -> str:
    """The Python the session host runs (`python3 -`) to start the room's Codex.

    The host keeps the runner archive under its digest. Without ``ship`` the
    program carries only that digest, and a host that does not hold the file
    answers ``{"runner": "missing"}`` and starts nothing, for the launch to be
    sent again with the archive.
    """
    archive = build()
    payload = {
        "state": state,
        "config": config,
        "codex_config": codex_config,
        "env": env,
        "digest": hashlib.sha256(archive).hexdigest(),
    }
    if ship:
        payload["archive"] = base64.b64encode(archive).decode()
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
from app.domain.agent.harness.codex.host import configure
print(json.dumps(configure(payload)))
"""
