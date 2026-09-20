"""Central bootstrap detaches once and does not inherit host credentials."""

import base64
import io
import json
import os
import signal
import sys
import zipfile

import pytest

from app.domain.agent.harness.codex.host import configure


def test_bootstrap_scopes_environment_and_reuses_only_matching_session(
    tmp_path, monkeypatch
):
    binary = tmp_path / "codex"
    binary.write_text("#!/bin/sh\nprintf 'codex-cli 0.154.0\\n'\n")
    binary.chmod(0o700)
    state = tmp_path / "session"
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(
            "__main__.py",
            """
import hashlib,json,os,socket,sys
from pathlib import Path
state=Path(sys.argv[sys.argv.index("--state")+1])
(state/"environment.json").write_text(json.dumps(dict(os.environ)))
digest=hashlib.sha256(str(state.resolve()).encode()).hexdigest()[:24]
listener=socket.socket(socket.AF_UNIX)
listener.bind(f"/tmp/cheese-execution-{os.getuid()}-{digest}.sock")
listener.listen()
while True:
    connection,_=listener.accept()
    connection.recv(4096)
    connection.sendall(json.dumps({"result":{"alive":True,"pid":os.getpid(),"thread_id":"thread"}}).encode()+b"\\n")
    connection.close()
""",
        )
    monkeypatch.setenv("OPENAI_API_KEY", "host-secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "host-claude-secret")
    payload = {
        "state": str(state),
        "archive": base64.b64encode(output.getvalue()).decode(),
        "config": {
            "binary": str(binary),
            "opening": {"model": "fixture"},
            "execution_target": {"url": "http://fixture"},
            "mcp_servers": [],
        },
        "codex_config": 'model_provider = "cheese"\n',
        "env": {
            "CHEESE_TOKEN": "project-token",
            "PATH": os.path.dirname(sys.executable),
        },
    }
    running = configure(payload)
    try:
        assert configure(payload)["pid"] == running["pid"]
        actual = json.loads((state / "environment.json").read_text())
        assert actual["HOME"] == str(state / "home")
        assert actual["CODEX_HOME"] == str(state / "codex")
        assert actual["CHEESE_TOKEN"] == "project-token"
        assert "OPENAI_API_KEY" not in actual
        assert "ANTHROPIC_API_KEY" not in actual
        assert (state / "runner.json").stat().st_mode & 0o777 == 0o600
        payload["config"]["opening"]["agent_handle"] = "different"
        with pytest.raises(RuntimeError, match="different opening"):
            configure(payload)
    finally:
        os.kill(running["pid"], signal.SIGTERM)
        os.waitpid(running["pid"], 0)
        from app.domain.agent.harness.codex.runner import socket_path

        os.unlink(socket_path(state))
