"""Exercise connector output limits and executor mutation receipts together."""

import asyncio
import json
import shutil
import subprocess
import sys
import uuid

import pytest

from app.domain.agent import execution
from app.domain.agent.harness.claude_code.remote_execution import runtime


@pytest.fixture
def executor(tmp_path):
    home = tmp_path / "session home"
    helper = home / ".claude/remote-execution/runtime.py"
    helper.parent.mkdir(parents=True)
    shutil.copyfile(runtime.__file__, helper)
    state = home / ".claude/executor"
    work = tmp_path / "project"
    work.mkdir()
    subprocess.run(
        [sys.executable, str(helper), "start", "--state", str(state)],
        input=json.dumps({"workspace": str(work), "env": {}}),
        text=True,
        capture_output=True,
        check=True,
        timeout=10,
    )
    yield {"device_id": "project-device", "home": str(home)}, work, state
    subprocess.run(
        [sys.executable, str(helper), "stop", "--state", str(state)],
        capture_output=True,
        check=True,
        timeout=15,
    )


class ProcessDevice:
    def __init__(self):
        self.sizes = []
        self.devices = []

    async def exec(self, device_id, command, *, stdin, timeout):
        self.devices.append(device_id)
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            process.communicate(stdin.encode()), timeout
        )
        self.sizes.append(len(stdout))
        return {
            "exit": process.returncode,
            "stdout": stdout[: 1 << 20].decode(),
            "stderr": stderr.decode(),
            "truncated": len(stdout) > 1 << 20,
        }


@pytest.mark.anyio
async def test_large_file_control_crosses_connector_without_truncation(executor):
    target, work, state = executor
    content = "中文 text\n" * 200_000
    (work / "large.txt").write_text(content)
    device = ProcessDevice()
    result = await execution.call(
        target, "control", {"subtype": "read_file", "path": "large.txt"}, hub=device
    )
    assert result["contents"] == content
    assert max(device.sizes) < 1 << 20
    assert set(device.devices) == {"project-device"}
    assert not list((state / "relay").iterdir())


@pytest.mark.anyio
async def test_repeated_mutation_id_does_not_repeat_shell_write(executor):
    target, work, _ = executor
    device = ProcessDevice()
    request = {
        "id": str(uuid.uuid4()),
        "tool": "Bash",
        "args": {"command": "printf once >> output.txt"},
    }
    first = await execution.call(target, "invoke", request, hub=device)
    second = await execution.call(target, "invoke", request, hub=device)
    assert "error" not in first
    assert first == second
    assert (work / "output.txt").read_text() == "once"
