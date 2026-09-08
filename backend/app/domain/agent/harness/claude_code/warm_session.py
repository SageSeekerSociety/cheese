"""Run an unassigned native session in its own home and tmux server.

This module is shipped to a cloud machine and uses only the standard library.
Room adoption is separate: preparation never submits a prompt or enables RC.
"""

import fcntl
import hashlib
import json
import os
import secrets
import shlex
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path


def _write(path: Path, value: str) -> None:
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as file:
        file.write(value)
        staged = Path(file.name)
    staged.replace(path)


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
        workspace = directory / "workspace"
        config.mkdir(parents=True)
        work.mkdir()
        workspace.mkdir()
        # Keep unix socket paths short even when the isolated home is on macOS.
        socket_root = Path("/tmp") / ("cheese-warm-" + secrets.token_hex(8))
        socket_root.mkdir(mode=0o700)
        state = {
            "socket": str(socket_root / "tmux.sock"),
            "rendezvous": str(socket_root / "rv.sock"),
            "token_file": str(directory / "rv.token"),
            "home": str(home),
            "work": str(work),
            "workspace": str(workspace),
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
                    "projects": {
                        str(path): {"hasTrustDialogAccepted": True}
                        for path in (work, workspace)
                    },
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


def bind(
    directory: Path,
    *,
    project_id: str,
    topic_id: str,
    work: Path,
    settings: dict,
    system_prompt: str,
) -> dict:
    """Assign a prepared process once and load the room before accepting input."""
    project_id, topic_id = str(uuid.UUID(project_id)), str(uuid.UUID(topic_id))
    work = work.resolve(strict=True)
    state = json.loads((directory / "state.json").read_text())
    if work != Path(state["workspace"]):
        raise ValueError("Room must use this process's prepared workspace")
    if not (directory / "ready").exists():
        raise RuntimeError("Native session has not reported SessionStart")
    if _tmux(state, "has-session", "-t", "native-warm").returncode:
        raise RuntimeError("Prepared native process is no longer running")
    # Refuse adoption when project settings would override platform instructions.
    for name in ("settings.json", "settings.local.json"):
        project_settings = work / ".claude" / name
        if project_settings.exists() and json.loads(project_settings.read_text()).get(
            "outputStyle"
        ):
            raise ValueError("Project has its own native output style")
    intent = {
        "project_id": project_id,
        "topic_id": topic_id,
        "work": str(work),
        "context": hashlib.sha256(
            json.dumps([settings, system_prompt], sort_keys=True).encode()
        ).hexdigest(),
    }
    binding_path = directory / "binding.json"
    # This runs unattended during a claim. Persist ownership before any changes
    # so a crash or a second claimant cannot redirect a partially bound process.
    with (directory / "binding.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if binding_path.exists():
            binding = json.loads(binding_path.read_text())
            if any(binding[key] != value for key, value in intent.items()):
                raise ValueError("Native session binding does not match this request")
            if binding["phase"] == "bound":
                return binding
        _write(binding_path, json.dumps({**intent, "phase": "binding"}))
        config = Path(state["home"]) / ".claude"
        styles = config / "output-styles"
        styles.mkdir(exist_ok=True)
        style = "CheeseRoom" + topic_id.replace("-", "")
        _write(
            styles / (style + ".md"),
            "---\nname: "
            + style
            + "\ndescription: Room instructions\nkeep-coding-instructions: true\n---\n"
            + system_prompt,
        )
        _write(
            config / "settings.json",
            json.dumps({**settings, "outputStyle": style}),
        )
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(10)
            client.connect(state["rendezvous"])
            client.sendall(
                (
                    json.dumps(
                        {
                            "role": "attacher",
                            "auth": Path(state["token_file"]).read_text(),
                        }
                    )
                    + "\n"
                ).encode()
            )
            client.sendall(
                (
                    json.dumps({"type": "reply", "text": "/cd " + str(work)}) + "\n"
                ).encode()
            )
            deadline = time.monotonic() + 10
            while True:
                current = _tmux(
                    state,
                    "display-message",
                    "-p",
                    "-t",
                    "native-warm",
                    "#{pane_current_path}",
                )
                if current.returncode:
                    raise RuntimeError("Native process exited during room binding")
                if current.stdout.decode().strip() == str(work):
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError("Native project directory is still pending")
                time.sleep(0.02)
        binding = {**intent, "phase": "bound"}
        _write(binding_path, json.dumps(binding))
        return binding


if __name__ == "__main__":
    os.umask(0o077)
    action, root = sys.argv[1:3]
    if action == "prepare":
        print(json.dumps(prepare(Path(root), Path(sys.argv[3]), json.load(sys.stdin))))
    elif action == "run":
        run(Path(root))
    elif action == "bind":
        body = json.load(sys.stdin)
        body["work"] = Path(body["work"])
        print(json.dumps(bind(Path(root), **body)))
    else:
        raise ValueError("Unknown warm session operation")
