"""Build the standalone executor installation sent through DeviceHub.exec."""

import asyncio
import base64
import hashlib
import json
import time
from pathlib import Path

from app.core.config import settings
from app.core.sentences import say
from app.domain.agent import (
    environment_runner,
    forge_cli,
    machine_tunnel,
    preview_tunnel,
    toolchain,
)
from app.domain.agent.harness.channel import ScreenSetupError
from app.domain.agent.harness.claude_code.remote_execution import (
    bootstrap,
    cli_client,
    confinement,
    private,
    runtime,
    sandbox_host,
    session_transfer,
)
from app.domain.agent.machine_launcher import CHEESE_PREVIEW_UP, toolchain_fetcher
from app.domain.project_skill.service import project_skill_names, session_skill_files

# What the session's Stop checkpoint runs on the executor (`runtime.control`):
# every task checkout with something unpushed backed up and pushed.
CHEESE_SYNC_SCRIPT = """#!/bin/sh
exec cheese sync --all
"""


class SandboxRefused(RuntimeError):
    """The machine refused to install a room's executor because it cannot
    give the room its isolated environment (`bootstrap.SandboxUnavailable`);
    the message is the machine's, saying what would let it."""


def refused(installed: dict) -> SandboxRefused | None:
    """The refusal an install's exec result reports, if it is one."""
    if installed.get("exit") != bootstrap.SANDBOX_UNAVAILABLE_EXIT:
        return None
    return SandboxRefused((installed.get("stderr") or "").strip())


def can_prepare(info, sandbox=None):
    """Whether the running executor that answered `info` can be prepared in
    place: it runs this release, and, when `sandbox` is given, runs in a
    sandbox exactly when asked to. One that does not is installed again, and
    the install replaces it once it is idle (`bootstrap.prepared`)."""
    return (
        (sandbox is None or bool(info.get("sandbox")) == bool(sandbox))
        and "prepare" in info.get("capabilities", [])
        and not info.get("upgrading")
        and info.get("protocol_version") == runtime.PROTOCOL_VERSION
        and all(
            info.get("files", {}).get(name)
            == hashlib.sha256(content.encode()).hexdigest()
            for name, content in file_sources().items()
        )
    )


BACKEND = Path(__file__).resolve().parents[6]


def file_sources():
    return {
        "remote-execution/bootstrap.py": Path(bootstrap.__file__).read_text(),
        "remote-execution/bin/cheese": Path(cli_client.__file__).read_text(),
        "remote-execution/bin/gh": Path(forge_cli.__file__).read_text(),
        "remote-execution/bin/fj": Path(forge_cli.__file__).read_text(),
        "cheese-environment.py": Path(environment_runner.__file__).read_text(),
        "cheese-toolchain": toolchain_fetcher(),
        "cheese-tunnel.py": Path(machine_tunnel.__file__).read_text(),
        "cheese-preview.py": Path(preview_tunnel.__file__).read_text(),
        "cheese-preview-up": CHEESE_PREVIEW_UP,
        "cheese-sync": CHEESE_SYNC_SCRIPT,
        "remote-execution/sandbox_host.py": Path(sandbox_host.__file__).read_text(),
        "remote-execution/confinement.py": Path(confinement.__file__).read_text(),
        **{
            name: (BACKEND / source).read_text()
            for name, source in runtime.RELEASE_FILES.items()
        },
    }


def payload_for(
    project_id, resource_id, env, known_files=None, *, sandbox, platform_machine
):
    """What the executor is installed or prepared from. ``sandbox`` says
    whether it runs in a sandbox of its own (`bootstrap.sandbox_argv`) or over
    the whole machine, as the room's access to that machine decides.
    ``platform_machine``: the platform provisioned the machine, so the install
    may add what a sandbox needs there and the sandbox gets a network and
    limits of its own, sent here. On a machine a person enrolled nothing is
    installed and there is no root to give either: its sandbox is sent
    without limits and shares the machine's network."""
    files = file_sources()
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
        "protocol_version": runtime.PROTOCOL_VERSION,
        "toolchain_fonts": toolchain.fonts_pin(),
        "sandbox": (
            {
                "memory_mb": settings.cloud_sandbox_memory_mb,
                "swap_mb": settings.cloud_sandbox_swap_mb,
                "cpus": settings.cloud_sandbox_cpus,
                "pids": settings.cloud_sandbox_pids,
            }
            if platform_machine
            else {"limits": None}
        )
        if sandbox
        else None,
        "platform_machine": platform_machine,
        "project": str(project_id),
        "resource": str(resource_id),
        "env": values,
        "environment": environment,
        # Carried as content, not as a release file: the agent's shell runs on
        # THIS machine, and the skill text it was handed names
        # `$CLAUDE_CONFIG_DIR/skills/...` — a path that only resolves here. The
        # executor has no claude of its own, so nothing else on this side
        # installs them (bootstrap.prepared writes them out). Kept out of
        # `file_sources()` on purpose: a skill edit is not a reason to
        # restart every room's executor, and the release digest would make it one.
        "skills": session_skill_files(project_id),
        # The project's own skills, by folder: one deleted since the last
        # prepare is removed from the machine instead of lingering there.
        "project_skills": project_skill_names(project_id),
        "file_names": list(files),
        "files": {
            name: base64.b64encode(content.encode()).decode()
            for name, content in files.items()
            if (known_files or {}).get(name)
            != hashlib.sha256(content.encode()).hexdigest()
        },
    }


def script(project_id, resource_id, env, *, sandbox, platform_machine):
    payload = payload_for(
        project_id,
        resource_id,
        env,
        sandbox=sandbox,
        platform_machine=platform_machine,
    )
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
            raise ScreenSetupError(
                result.get("stderr") or say("handoffHistoryMigrationFailed")
            )
        return json.loads(result["stdout"])

    deadline = time.monotonic() + 60
    stopped = await exchange(source, "stop", request_exit=True)
    while not stopped["stopped"]:
        if time.monotonic() >= deadline:
            raise ScreenSetupError(say("handoffSessionNotExited"))
        await asyncio.sleep(0.5)
        stopped = await exchange(source, "stop", request_exit=False)
    manifest = (await exchange(source, "list"))["files"]
    if not any(Path(item["path"]).name == resume + ".jsonl" for item in manifest):
        raise ScreenSetupError(say("handoffSessionFilesIncomplete"))
    for item in manifest:
        offset = 0
        while offset < item["size"] or item["size"] == 0 and offset == 0:
            chunk = await exchange(source, "read", path=item["path"], offset=offset)
            count = len(base64.b64decode(chunk["data"], validate=True))
            if not count and item["size"]:
                raise ScreenSetupError(say("handoffSessionFileEndedEarly"))
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
        raise ScreenSetupError(say("handoffSessionStillChanging"))
