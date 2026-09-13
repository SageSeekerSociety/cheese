"""Install and start a project-scoped runner on the central session machine."""

import base64
import fcntl
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from app.domain.agent.harness.codex.runner import socket_path

VERSION = "0.154.0"


def ping(state: Path) -> dict | None:
    connection = socket.socket(socket.AF_UNIX)
    connection.settimeout(2)
    try:
        connection.connect(socket_path(state))
        connection.sendall(b'{"method":"ping"}\n')
        with connection.makefile("rb") as stream:
            result = json.loads(stream.readline())["result"]
        return result if result["alive"] else None
    except (FileNotFoundError, ConnectionRefusedError):
        return None
    finally:
        connection.close()


def configure(payload: dict) -> dict:
    state = (
        Path(payload["state"].replace("$HOME", str(Path.home()))).expanduser().resolve()
    )
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (state / "bootstrap.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        running = ping(state)
        if running:
            previous = json.loads((state / "runner.json").read_text())
            requested = payload["config"]
            if any(
                previous[key] != requested[key]
                for key in ("execution_target", "mcp_servers")
            ) or any(
                previous["opening"].get(key) != requested["opening"].get(key)
                for key in ("model", "owner", "agent_handle")
            ):
                raise RuntimeError(
                    "The running session belongs to a different opening or executor"
                )
            resume = requested["opening"].get("resume_token")
            if resume and resume != running["thread_id"]:
                raise RuntimeError(
                    "The running session cannot resume a different thread"
                )
            return running
        config = dict(payload["config"])
        binary = Path(config["binary"]).expanduser().resolve()
        version = subprocess.run(
            [str(binary), "--version"],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        ).stdout.strip()
        if version != f"codex-cli {VERSION}":
            raise RuntimeError(f"Expected Codex {VERSION}, got {version}")
        config["binary"] = str(binary)
        workspace = state / "workspace"
        workspace.mkdir(exist_ok=True)
        config["cwd"] = str(workspace)
        home = state / "home"
        home.mkdir(exist_ok=True, mode=0o700)
        codex_home = state / "codex"
        codex_home.mkdir(exist_ok=True, mode=0o700)
        (codex_home / "config.toml").write_text(payload["codex_config"])
        config_path = state / "runner.json"
        config_path.write_text(json.dumps(config))
        config_path.chmod(0o600)
        archive = base64.b64decode(payload["archive"], validate=True)
        # A new deployment never overwrites modules used by an existing process.
        artifact = state / f"runner-{hashlib.sha256(archive).hexdigest()}.pyz"
        if not artifact.exists():
            artifact.write_bytes(archive)
        env = {
            "PATH": os.environ["PATH"],
            **payload["env"],
            "HOME": str(home),
            "CODEX_HOME": str(codex_home),
        }
        with (state / "runner.log").open("ab") as log:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-I",
                    "-S",
                    str(artifact),
                    "--state",
                    str(state),
                    "--config",
                    str(config_path),
                ],
                cwd=workspace,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
        deadline = time.monotonic() + 30
        while process.poll() is None:
            running = ping(state)
            if running:
                return running
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"Codex startup has not completed; see {state / 'runner.log'}"
                )
            time.sleep(0.05)
        raise RuntimeError(f"Codex startup failed; see {state / 'runner.log'}")


def main() -> None:
    print(json.dumps(configure(json.load(sys.stdin))))


if __name__ == "__main__":
    main()
