"""Build the standalone executor installation sent through DeviceHub.exec."""

import base64
import json
from pathlib import Path

from app.domain.agent import environment_runner, preview_tunnel
from app.domain.agent.harness.claude_code.device_launch import (
    CHEESE_PREVIEW_UP,
    CHEESE_SYNC_SCRIPT,
    CHEESE_WORKSPACE_BRINGUP,
)
from app.domain.agent.harness.claude_code.hooks_substrate import CHEESE_HOOK_SCRIPT
from app.domain.agent.harness.claude_code.remote_execution import bootstrap, runtime


def script(project_id, resource_id, env):
    files = {
        "remote-execution/bootstrap.py": Path(bootstrap.__file__).read_text(),
        "remote-execution/runtime.py": Path(runtime.__file__).read_text(),
        "cheese-environment.py": Path(environment_runner.__file__).read_text(),
        "cheese-preview.py": Path(preview_tunnel.__file__).read_text(),
        "cheese-preview-up": CHEESE_PREVIEW_UP,
        "cheese-workspace": CHEESE_WORKSPACE_BRINGUP,
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
    payload = {
        "project": str(project_id),
        "resource": str(resource_id),
        "env": values,
        "environment": environment,
        "files": {
            name: base64.b64encode(content.encode()).decode()
            for name, content in files.items()
        },
    }
    return (
        Path(bootstrap.__file__).read_text()
        + "\nconfigure(json.loads("
        + repr(json.dumps(payload))
        + "))\n"
    )
