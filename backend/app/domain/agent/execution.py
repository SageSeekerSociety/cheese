"""Room execution over its recorded device connector."""

import base64
import json
import uuid

from app.domain.agent.device_hub import device_hub


async def call(target: dict, method: str, params: dict, *, hub=None) -> dict:
    hub = hub or device_hub
    identifier = str(uuid.uuid4())
    home = target["home"]
    command = [
        "sh",
        "-c",
        f'exec python3 "{home}/.claude/remote-execution/runtime.py" relay '
        f'--state "{home}/.claude/executor"',
    ]

    async def exchange(action, **values):
        result = await hub.exec(
            target["device_id"],
            command,
            stdin=json.dumps({"id": identifier, "action": action, **values}),
            timeout=660 if action == "call" else 30,
        )
        if result.get("exit") != 0 or result.get("truncated"):
            raise RuntimeError(result.get("stderr") or "Execution transport failed")
        return json.loads(result["stdout"])

    response = await exchange("call", method=method, params=params)
    if "result" in response:
        return response["result"]
    data = bytearray()
    try:
        while len(data) < response["size"]:
            part = await exchange("read", offset=len(data))
            chunk = base64.b64decode(part["data"], validate=True)
            if not chunk:
                raise RuntimeError("Executor response ended before its recorded size")
            data.extend(chunk)
        return json.loads(data)
    finally:
        await exchange("drop")
