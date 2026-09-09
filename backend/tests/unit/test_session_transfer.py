"""Transfer complete multi-file history without modifying the source records."""

import asyncio
import json
import os
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from app.domain.agent.central_provider import CentralChannel
from app.domain.agent.device_provider import DeviceChannel
from app.domain.agent.harness.claude_code import ScreenSetupError


@pytest.mark.anyio
async def test_history_transfer_preserves_large_transcripts_and_subagents(tmp_path):
    project, resource, resume = uuid.uuid4(), uuid.uuid4(), str(uuid.uuid4())
    owners = {name: tmp_path / name for name in ("source", "center")}
    roots = {
        name: owner / ".cheese/home" / str(project) / str(resource) / ".claude/projects"
        for name, owner in owners.items()
    }
    source = roots["source"] / "-old-work"
    source.mkdir(parents=True)
    original = (
        json.dumps({"message": "完整内容", "sessionId": resume}) + "\n"
    ).encode() * 25000
    (source / f"{resume}.jsonl").write_bytes(original)
    child = source / resume / "subagents" / "agent-one.jsonl"
    child.parent.mkdir(parents=True)
    child.write_bytes(b'{"message":"child"}\n')

    async def execute(device, command, *, stdin, timeout):
        process = await asyncio.create_subprocess_exec(
            *command,
            env={**os.environ, "HOME": str(owners[device])},
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            process.communicate(stdin.encode()), timeout
        )
        assert len(stdout) < 1 << 20
        return {
            "exit": process.returncode,
            "stdout": stdout.decode(),
            "stderr": stderr.decode(),
        }

    hub: Any = SimpleNamespace(exec=execute)
    central = CentralChannel(DeviceChannel(hub=hub))
    await central._transfer_history("source", "center", project, resource, resume)
    for path in roots["source"].rglob("*.jsonl"):
        assert (
            roots["center"] / path.relative_to(roots["source"])
        ).read_bytes() == path.read_bytes()
    assert (source / f"{resume}.jsonl").read_bytes() == original
    # A repeated handoff is safe, but an existing divergent history is preserved.
    await central._transfer_history("source", "center", project, resource, resume)
    destination = roots["center"] / "-old-work" / f"{resume}.jsonl"
    destination.write_bytes(original + b"central continuation\n")
    with pytest.raises(ScreenSetupError, match="different bytes"):
        await central._transfer_history("source", "center", project, resource, resume)
    assert destination.read_bytes() == original + b"central continuation\n"
