"""Transfer complete multi-file history without modifying the source records."""

import asyncio
import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.domain.agent.central_provider import CentralChannel
from app.domain.agent.device_provider import DeviceChannel
from app.domain.agent.harness.claude_code import ScreenSetupError


@pytest.mark.skipif(not shutil.which("tmux"), reason="requires a real tmux terminal")
@pytest.mark.parametrize("legacy_workspace", [True, False])
def test_exited_session_with_retained_terminal_allows_transfer(
    tmp_path, monkeypatch, capsys, legacy_workspace
):
    from app.domain.agent.session_transfer import transfer

    monkeypatch.setenv("HOME", str(tmp_path))
    project, resource = str(uuid.uuid4()), str(uuid.uuid4())
    home = tmp_path / ".cheese/home" / project / resource / ".claude"
    home.mkdir(parents=True)
    work = tmp_path / ".cheese/work" / project / resource
    if not legacy_workspace:
        work = home.parent / "room"
    checksum = subprocess.check_output(["cksum"], input=str(work).encode())
    session = "cheese_" + checksum.decode().split()[0]
    # macOS pytest paths can exceed the Unix socket path-length limit.
    socket = Path("/tmp") / ("cheese-transfer-" + uuid.uuid4().hex[:12] + ".sock")
    config = tmp_path / "tmux.conf"
    config.write_text("set -g remain-on-exit on\n")
    command = ["tmux", "-S", str(socket)]
    subprocess.run(
        [
            *command,
            "-f",
            str(config),
            "new-session",
            "-d",
            "-s",
            session,
            "read answer",
        ],
        check=True,
    )
    (home / "environment-session.json").write_text(json.dumps([str(socket), session]))
    payload = {
        "project": project,
        "resource": resource,
        "action": "stop",
        "request_exit": False,
    }
    try:
        transfer(payload)
        assert json.loads(capsys.readouterr().out) == {"stopped": False}
        subprocess.run([*command, "send-keys", "-t", session, "Enter"], check=True)
        deadline = time.monotonic() + 5
        while (
            subprocess.check_output(
                [*command, "list-panes", "-t", session, "-F", "#{pane_dead}"]
            ).strip()
            != b"1"
        ):
            assert time.monotonic() < deadline
            time.sleep(0.01)
        transfer(payload)
        assert json.loads(capsys.readouterr().out) == {"stopped": True}
    finally:
        subprocess.run([*command, "kill-server"], check=False, capture_output=True)
        socket.unlink(missing_ok=True)


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
