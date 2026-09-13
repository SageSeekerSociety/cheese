"""Ship the runner through the connector's existing Python stdin transport."""

import base64
import json

from app.domain.agent.harness.codex.bundle import build


def script(*, state: str, config: dict, codex_config: str, env: dict[str, str]) -> str:
    payload = {
        "state": state,
        "config": config,
        "codex_config": codex_config,
        "env": env,
        "archive": base64.b64encode(build()).decode(),
    }
    return f"""import base64,hashlib,json,os,sys,tempfile
from pathlib import Path
payload=json.loads({json.dumps(payload)!r})
state=Path(payload["state"].replace("$HOME",str(Path.home()))).expanduser().resolve()
state.mkdir(parents=True,exist_ok=True,mode=0o700)
archive=base64.b64decode(payload["archive"],validate=True)
artifact=state/f"runner-{{hashlib.sha256(archive).hexdigest()}}.pyz"
if not artifact.exists():
    with tempfile.NamedTemporaryFile(dir=state,delete=False) as output:
        output.write(archive)
    os.replace(output.name,artifact)
sys.path.insert(0,str(artifact))
from app.domain.agent.harness.codex.host import configure
print(json.dumps(configure(payload)))
"""
