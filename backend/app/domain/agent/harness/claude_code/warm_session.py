"""Run an unassigned native session in its own home and tmux server.

This module is shipped to a cloud machine and uses only the standard library.
Room adoption is separate: preparation never submits a prompt or enables RC.
"""

import hashlib
import json
import os
import secrets
import shlex
import subprocess
import sys
import time
from pathlib import Path


def _tmux(state: dict, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["tmux", "-S", state["socket"], *args],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
    )


def prepare(directory: Path, binary: Path, environment: dict[str, str]) -> dict:
    """Start once; a repeated call resumes observation of the same process."""
    directory = directory.resolve()
    fingerprint = hashlib.sha256(
        json.dumps(environment, sort_keys=True).encode()
    ).hexdigest()
    state_path = directory / "state.json"
    if state_path.exists():
        state = json.loads(state_path.read_text())
        if state["fingerprint"] != fingerprint or state["binary"] != str(binary):
            raise ValueError("Warm session credentials or binary changed")
        if _tmux(state, "has-session", "-t", "native-warm").returncode:
            raise RuntimeError("Prepared native process is no longer running")
    else:
        directory.mkdir(parents=True, exist_ok=False)
        directory.chmod(0o700)
        home = directory / "home"
        config = home / ".claude"
        work = directory / "neutral"
        config.mkdir(parents=True)
        work.mkdir()
        # Keep unix socket paths short even when the isolated home is on macOS.
        socket_root = Path("/tmp") / ("cheese-warm-" + secrets.token_hex(8))
        socket_root.mkdir(mode=0o700)
        state = {
            "socket": str(socket_root / "tmux.sock"),
            "rendezvous": str(socket_root / "rv.sock"),
            "token_file": str(directory / "rv.token"),
            "home": str(home),
            "work": str(work),
            "binary": str(binary),
            "fingerprint": fingerprint,
            "created_at": time.time(),
        }
        (directory / "rv.token").write_text(secrets.token_urlsafe(32))
        (directory / "environment.json").write_text(json.dumps(environment))
        (directory / "runner.py").write_text(Path(__file__).read_text())
        (config / ".claude.json").write_text(
            json.dumps(
                {
                    "hasCompletedOnboarding": True,
                    "bypassPermissionsModeAccepted": True,
                    "projects": {str(work): {"hasTrustDialogAccepted": True}},
                }
            )
        )
        ready_command = shlex.join(["touch", str(directory / "ready")])
        (config / "settings.json").write_text(
            json.dumps(
                {
                    "hooks": {
                        "SessionStart": [
                            {"hooks": [{"type": "command", "command": ready_command}]}
                        ]
                    }
                }
            )
        )
        state_path.write_text(json.dumps(state))
        launched = _tmux(
            state,
            "-f",
            "/dev/null",
            "new-session",
            "-d",
            "-s",
            "native-warm",
            "-c",
            str(work),
            shlex.join(
                [sys.executable, str(directory / "runner.py"), "run", str(directory)]
            ),
        )
        if launched.returncode:
            raise RuntimeError("Could not start the isolated native tmux session")
    deadline = time.monotonic() + 45
    while not (directory / "ready").exists():
        if _tmux(state, "has-session", "-t", "native-warm").returncode:
            raise RuntimeError("Native process exited before SessionStart")
        if time.monotonic() >= deadline:
            raise TimeoutError("Native SessionStart is still pending")
        time.sleep(0.05)
    return state


def run(directory: Path) -> None:
    state = json.loads((directory / "state.json").read_text())
    environment = json.loads((directory / "environment.json").read_text())
    # Do not inherit the operator's model identity, RC switches or room scope.
    env = {
        "PATH": os.environ["PATH"],
        "TERM": "xterm-256color",
        **environment,
        "HOME": state["home"],
        "CLAUDE_CONFIG_DIR": str(Path(state["home"]) / ".claude"),
        "DISABLE_AUTOUPDATER": "1",
        "CLAUDE_BG_BACKEND": "daemon",
        "CLAUDE_BG_RENDEZVOUS_SOCK": state["rendezvous"],
        "CLAUDE_BG_RV_AUTH": (directory / "rv.token").read_text(),
    }
    os.chdir(state["work"])
    os.execve(
        state["binary"],
        [state["binary"], "--dangerously-skip-permissions"],
        env,
    )


if __name__ == "__main__":
    os.umask(0o077)
    action, root = sys.argv[1:3]
    if action == "prepare":
        print(json.dumps(prepare(Path(root), Path(sys.argv[3]), json.load(sys.stdin))))
    elif action == "run":
        run(Path(root))
    else:
        raise ValueError("Unknown warm session operation")
