"""The released owner and Go connector used by the terminal acceptance cases."""

import os
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend/scripts"))

from device_connection_lifecycle_acceptance import (  # noqa: E402
    SECRET,
    image_owner,
    record,
)
from app.domain.agent.device_hub_rpc import RemoteDeviceHub  # noqa: E402

OWNER_ENV_DEFAULTS = {
    "ACCEPTANCE_OWNER_IMAGE": (
        "ghcr.io/sageseekersociety/cheese/backend@sha256:"
        "b5fc0a2172b3eb0a398af31321526a7f3a0ba654a3d448b4c860b8ac59207732"
    ),
    "ACCEPTANCE_OWNER_REVISION": "fbb08b0f874ba3bdb5b567efffcbcb83156ff144",
    "ACCEPTANCE_OWNER_PORT": "18783",
}


class WireOwner:
    def __init__(self, claude, *, exec_env=None):
        options = SimpleNamespace(
            owner_image=os.environ.get(
                "ACCEPTANCE_OWNER_IMAGE", OWNER_ENV_DEFAULTS["ACCEPTANCE_OWNER_IMAGE"]
            ),
            owner_revision=os.environ.get(
                "ACCEPTANCE_OWNER_REVISION",
                OWNER_ENV_DEFAULTS["ACCEPTANCE_OWNER_REVISION"],
            ),
            port=int(
                os.environ.get(
                    "ACCEPTANCE_OWNER_PORT", OWNER_ENV_DEFAULTS["ACCEPTANCE_OWNER_PORT"]
                )
            ),
            claude=Path(claude),
        )
        self.context = image_owner(options, ROOT, start_runtime=False)
        self.owner = self.context.__enter__()
        self.exec_env = exec_env or {}

    async def exec(self, device_id, command, **kwargs):
        kwargs["env"] = {**self.exec_env, **(kwargs.get("env") or {})}
        hub = RemoteDeviceHub(self.owner.url, SECRET)
        try:
            result = await hub.exec(device_id, command, **kwargs)
            record(
                self.owner.log, "wire_exec", device_id=device_id, exit=result["exit"]
            )
            return result
        finally:
            await hub.close()

    async def call_executor(self, device_id, state, method, params, **kwargs):
        hub = RemoteDeviceHub(self.owner.url, SECRET)
        try:
            result = await hub.call_executor(device_id, state, method, params, **kwargs)
            record(
                self.owner.log,
                "wire_executor_call",
                device_id=device_id,
                state=str(state),
                method=method,
            )
            return result
        finally:
            await hub.close()

    def close(self):
        self.context.__exit__(None, None, None)
