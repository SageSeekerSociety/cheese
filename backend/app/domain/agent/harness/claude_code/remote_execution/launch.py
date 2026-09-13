"""Build the standalone executor installation sent through DeviceHub.exec."""

import asyncio
import base64
import hashlib
import json
import time
from pathlib import Path

from app.domain.agent import environment_runner, preview_tunnel
from app.domain.agent.harness.channel import ScreenSetupError
from app.domain.agent.harness.claude_code.device_launch import (
    CHEESE_PREVIEW_UP,
    CHEESE_SYNC_SCRIPT,
)
from app.domain.agent.harness.claude_code.hooks_substrate import CHEESE_HOOK_SCRIPT
from app.domain.agent.harness.claude_code.remote_execution import (
    bootstrap,
    cli_client,
    cli_worker,
    private,
    runtime,
    session_transfer,
)


def can_prepare(info):
    return (
        "prepare" in info.get("capabilities", [])
        and info.get("runtime_sha256") == runtime.SOURCE_SHA256
    )


def payload_for(project_id, resource_id, env, known_files=None):
    files = {
        "remote-execution/bootstrap.py": Path(bootstrap.__file__).read_text(),
        "remote-execution/runtime.py": Path(runtime.__file__).read_text(),
        "remote-execution/cli_worker.py": Path(cli_worker.__file__).read_text(),
        "remote-execution/bin/cheese": Path(cli_client.__file__).read_text(),
        "cheese-environment.py": Path(environment_runner.__file__).read_text(),
        "cheese-preview.py": Path(preview_tunnel.__file__).read_text(),
        "cheese-preview-up": CHEESE_PREVIEW_UP,
        "cheese-sync": CHEESE_SYNC_SCRIPT,
        "cheese-hook": CHEESE_HOOK_SCRIPT,
        "cheese": (Path(__file__).resolve().parents[6] / "sandbox/cheese").read_text(),
    }
    values = {
        name: value
        for name, value in env.items()
        if name.startswith(("CHEESE_", "GIT_"))
    }
    environment = (
        json.loads(values.pop("CHEESE_ENVIRONMENT"))
        if values.get("CHEESE_ENVIRONMENT")
        else None
    )
    return {
        "project": str(project_id),
        "resource": str(resource_id),
        "env": values,
        "environment": environment,
        "file_names": list(files),
        "files": {
            name: base64.b64encode(content.encode()).decode()
            for name, content in files.items()
            if (known_files or {}).get(name)
            != hashlib.sha256(content.encode()).hexdigest()
        },
    }


def script(project_id, resource_id, env):
    payload = payload_for(project_id, resource_id, env)
    return (
        Path(bootstrap.__file__).read_text()
        + "\nconfigure(json.loads("
        + repr(json.dumps(payload))
        + "))\n"
    )


def private_script(target: dict, env: dict) -> str:
    source = Path(private.__file__).read_text()
    return (
        "import json\n"
        f"scope = {{'__name__': 'cheese_private_executor'}}\nexec({source!r}, scope)\n"
        f"target = json.loads({json.dumps(target)!r})\n"
        f"env = json.loads({json.dumps(env)!r})\n"
        "from pathlib import Path\n"
        "directory = Path(target['home'].replace('$HOME', str(Path.home())))\n"
        "print(json.dumps(scope['ensure'](target, directory, env)))\n"
    )


async def transfer_history(hub, source, center, project, resource, resume):
    if source == center:
        return
    program = Path(session_transfer.__file__).read_text()

    async def exchange(device, action, **values):
        payload = {
            "project": str(project),
            "resource": str(resource),
            "action": action,
            **values,
        }
        result = await hub.exec(
            device,
            ["python3", "-"],
            timeout=60,
            stdin=program
            + "\ntransfer(json.loads("
            + repr(json.dumps(payload))
            + "))\n",
        )
        if result.get("exit") != 0 or result.get("truncated"):
            raise ScreenSetupError(result.get("stderr") or "完整会话历史迁移失败")
        return json.loads(result["stdout"])

    deadline = time.monotonic() + 60
    stopped = await exchange(source, "stop", request_exit=True)
    while not stopped["stopped"]:
        if time.monotonic() >= deadline:
            raise ScreenSetupError("原机器上的会话尚未退出，尚未切换到中心")
        await asyncio.sleep(0.5)
        stopped = await exchange(source, "stop", request_exit=False)
    manifest = (await exchange(source, "list"))["files"]
    if not any(Path(item["path"]).name == resume + ".jsonl" for item in manifest):
        raise ScreenSetupError("原机器缺少要继续的完整会话文件，尚未切换到中心")
    for item in manifest:
        offset = 0
        while offset < item["size"] or item["size"] == 0 and offset == 0:
            chunk = await exchange(source, "read", path=item["path"], offset=offset)
            count = len(base64.b64decode(chunk["data"], validate=True))
            if not count and item["size"]:
                raise ScreenSetupError("原会话文件在复制完成前结束")
            await exchange(
                center,
                "write",
                path=item["path"],
                offset=offset,
                data=chunk["data"],
                final=offset + count == item["size"],
                sha256=item["sha256"],
            )
            offset += count
            if item["size"] == 0:
                break
    if (await exchange(source, "list"))["files"] != manifest:
        raise ScreenSetupError("原会话记录仍在变化，尚未切换到中心")
