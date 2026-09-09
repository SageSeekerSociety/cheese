"""Exercise HTTP error propagation and the preload used by warm native sessions."""

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from app.domain.agent.harness.claude_code import warm_session


@pytest.mark.skipif(shutil.which("node") is None, reason="Node runs the HTTP fixtures")
def test_webfetch_transport_http_behaviour():
    result = subprocess.run(
        [
            "node",
            "--test",
            str(Path(__file__).with_name("webfetch_transport.test.cjs")),
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_warm_native_exec_loads_the_transport_and_preserves_existing_options(tmp_path):
    runner = tmp_path / "runner.py"
    runner.write_text(Path(warm_session.__file__).read_text())
    binary = tmp_path / "capture"
    binary.write_text(
        f"#!{sys.executable}\nimport json, os\n"
        "print(json.dumps({'options': os.environ['BUN_OPTIONS'], "
        "'home': os.environ['HOME']}))\n"
    )
    binary.chmod(0o700)
    (tmp_path / "state.json").write_text(
        json.dumps(
            {
                "binary": str(binary),
                "work": str(tmp_path),
                "home": str(tmp_path / "home"),
                "claim_auth": "fixture",
                "claim_socket": str(tmp_path / "claim.sock"),
            }
        )
    )
    (tmp_path / "environment.json").write_text(json.dumps({"BUN_OPTIONS": "--smol"}))
    result = subprocess.run(
        [sys.executable, str(runner), "run", str(tmp_path)],
        env={"PATH": os.environ["PATH"]},
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert shlex.split(observed["options"]) == [
        "--smol",
        "--preload=" + str(tmp_path / "webfetch_transport.cjs"),
    ]
    assert observed["home"] == str(tmp_path / "home")


def test_prepare_refreshes_recovery_assets_without_restarting_spare(
    tmp_path, monkeypatch
):
    binary = tmp_path / "claude"
    claim = tmp_path / "claim.sock"
    claim.touch()
    state = {
        "binary": str(binary),
        "fingerprint": hashlib.sha256(b"{}").hexdigest(),
        "claim_socket": str(claim),
    }
    (tmp_path / "state.json").write_text(json.dumps(state))
    (tmp_path / "runner.py").write_text("old runner")
    monkeypatch.setattr(warm_session, "_native_alive", lambda state: True)
    monkeypatch.setattr(
        warm_session,
        "_tmux",
        lambda *args: pytest.fail("must not restart a live spare"),
    )
    assert warm_session.prepare(tmp_path, binary, {}) == state
    assert (tmp_path / "runner.py").read_text() == Path(
        warm_session.__file__
    ).read_text()
    assert (tmp_path / "webfetch_transport.cjs").read_bytes() == (
        Path(warm_session.__file__).with_name("webfetch_transport.cjs").read_bytes()
    )
