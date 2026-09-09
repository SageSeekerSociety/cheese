"""Run an unassigned native session in its own home and tmux server.

This module is shipped to a cloud machine and uses only the standard library.
Room adoption is separate: preparation never submits a prompt or enables RC.
Native OAuth can still perform its own quota probe during startup.
"""

import fcntl
import hashlib
import importlib.util
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


def build_warm_session_prepare(version: str) -> str:
    source = Path(__file__).read_text()
    return (
        'mkdir -p "$HOME/.cheese"\n'
        "cat > \"$HOME/.cheese/warm-native-runner.py\" <<'CHEESE_WARM_RUNNER'\n"
        f"{source}\nCHEESE_WARM_RUNNER\n"
        'python3 "$HOME/.cheese/warm-native-runner.py" prepare-machine "$HOME" '
        + shlex.quote(version)
        + "\n"
    )


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


def _native_alive(state: dict) -> bool:
    panes = _tmux(state, "list-panes", "-a", "-F", "#{pane_id} #{pane_dead}")
    return panes.returncode == 0 and (state["pane"] + " 0") in (
        panes.stdout.decode().splitlines()
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
        if not _native_alive(state):
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
            "-P",
            "-F",
            "#{pane_id}",
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
        # Window indexes can be reassigned after the native window exits.
        state["pane"] = launched.stdout.decode().strip()
        _write(state_path, json.dumps(state))
    deadline = time.monotonic() + 45
    while not (directory / "ready").exists():
        if not _native_alive(state):
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
    if not _native_alive(state):
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
    config = Path(state["home"]) / ".claude"
    style = "CheeseRoom" + topic_id.replace("-", "")
    bound_settings = {**settings, "outputStyle": style}
    # This runs unattended during a claim. Persist ownership before any changes
    # so a crash or a second claimant cannot redirect a partially bound process.
    with (directory / "binding.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if binding_path.exists():
            binding = json.loads(binding_path.read_text())
            if any(
                binding.get(key) != value
                for key, value in intent.items()
                if key != "context"
            ):
                raise ValueError("Native session binding does not match this request")
            if (
                binding["phase"] == "bound"
                and binding.get("context") == intent["context"]
                and json.loads((config / "settings.json").read_text()) == bound_settings
            ):
                return binding
        _write(binding_path, json.dumps({**intent, "phase": "binding"}))
        styles = config / "output-styles"
        styles.mkdir(exist_ok=True)
        _write(
            styles / (style + ".md"),
            "---\nname: "
            + style
            + "\ndescription: Room instructions\nkeep-coding-instructions: true\n---\n"
            + system_prompt,
        )
        _write(
            config / "settings.json",
            json.dumps(bound_settings),
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
            current = _tmux(
                state,
                "display-message",
                "-p",
                "-t",
                state["pane"],
                "#{pane_current_path}",
            )
            # Native settings reload on a directory change. Repeating /cd to
            # the current directory would retain the previous system instructions.
            destinations = (
                [Path(state["work"]), work]
                if current.stdout.decode().strip() == str(work)
                else [work]
            )
            for destination in destinations:
                client.sendall(
                    (
                        json.dumps({"type": "reply", "text": "/cd " + str(destination)})
                        + "\n"
                    ).encode()
                )
                deadline = time.monotonic() + 10
                while True:
                    current = _tmux(
                        state,
                        "display-message",
                        "-p",
                        "-t",
                        state["pane"],
                        "#{pane_current_path}",
                    )
                    if current.returncode:
                        raise RuntimeError("Native process exited during room binding")
                    if current.stdout.decode().strip() == str(destination):
                        break
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Native project directory is still pending")
                    time.sleep(0.02)
        binding = {**intent, "phase": "bound"}
        _write(binding_path, json.dumps(binding))
        return binding


def stage(
    directory: Path,
    *,
    project_id: str,
    topic_id: str,
    home: Path,
    work: Path,
    rendezvous: Path,
    token_file: Path,
) -> dict:
    """Connect ordinary room paths to an exclusively reserved prepared session."""
    project_id, topic_id = str(uuid.UUID(project_id)), str(uuid.UUID(topic_id))
    state = json.loads((directory / "state.json").read_text())
    if not (directory / "ready").exists() or not _native_alive(state):
        raise RuntimeError("Native session is not ready for assignment")
    intent = {
        "project_id": project_id,
        "topic_id": topic_id,
        "work": state["workspace"],
    }
    binding_path = directory / "binding.json"
    with (directory / "binding.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if binding_path.exists():
            binding = json.loads(binding_path.read_text())
            if any(binding[key] != value for key, value in intent.items()):
                raise ValueError("Prepared workspace belongs to another room")
        else:
            _write(binding_path, json.dumps({**intent, "phase": "staging"}))
        links = {
            home: Path(state["home"]),
            work: Path(state["workspace"]),
            rendezvous: Path(state["rendezvous"]),
            token_file: Path(state["token_file"]),
        }
        # Never replace existing room data or an unrelated socket/token.
        for alias, target in links.items():
            if alias.is_symlink() and alias.resolve() == target.resolve():
                continue
            if alias.exists() or alias.is_symlink():
                raise ValueError(
                    "Room path already exists outside the prepared session"
                )
        for alias, target in links.items():
            if not alias.is_symlink():
                alias.parent.mkdir(parents=True, exist_ok=True)
                alias.symlink_to(target)
    return {"home": state["home"], "work": state["workspace"]}


def connection(directory: Path, project_id: str, topic_id: str) -> dict:
    """Expose only the terminal and input socket assigned to this room."""
    binding = json.loads((directory / "binding.json").read_text())
    if (
        binding["phase"] != "bound"
        or binding["project_id"] != str(uuid.UUID(project_id))
        or binding["topic_id"] != str(uuid.UUID(topic_id))
    ):
        raise ValueError("Native terminal is not assigned to this room")
    state = json.loads((directory / "state.json").read_text())
    if not _native_alive(state):
        raise RuntimeError("Bound native process is no longer running")
    session_file = Path(state["home"]) / ".claude/environment-session.json"
    _write(
        session_file,
        json.dumps([state["socket"], "native-warm", str(session_file)]),
    )
    return {
        "command": [
            "tmux",
            "-S",
            state["socket"],
            "attach-session",
            "-t",
            "native-warm",
        ],
        "home": state["home"],
        "work": binding["work"],
        "env": {
            "CHEESE_RV_SOCK": state["rendezvous"],
            "CHEESE_RV_TOKEN_FILE": state["token_file"],
        },
    }


def adopt_room(directory: Path) -> int:
    """Bind launcher files and project setup to the existing native process."""
    state = json.loads((directory / "state.json").read_text())
    config = Path(state["home"]) / ".claude"

    def adopt(environment, work):
        for variable, helper in (
            ("CHEESE_TUNNEL_URL", "cheese-tunnel-up"),
            ("CHEESE_PREVIEW_URL", "cheese-preview-up"),
        ):
            if environment.get(variable):
                subprocess.run(
                    ["sh", str(config / helper)], env=environment, check=True
                )
        settings = json.loads((config / "settings.json").read_text())
        settings["env"] = {**settings.get("env", {}), **environment}
        bind(
            directory,
            project_id=environment["CHEESE_PROJECT"],
            topic_id=environment["CHEESE_TOPIC"],
            work=work,
            settings=settings,
            system_prompt=(config / "cheese-system-prompt.md").read_text(),
        )
        pane = _tmux(state, "display-message", "-p", "-t", state["pane"], "#{pane_pid}")
        pane.check_returncode()
        pid = int(pane.stdout.strip())
        _write(directory / "room-environment.json", json.dumps(environment))
        drain_pid = config / "cheese-drain.pid"
        running = False
        if drain_pid.exists():
            try:
                os.kill(int(drain_pid.read_text().strip()), 0)
                running = True
            except (ValueError, ProcessLookupError):
                pass
        if not running:
            _tmux(
                state,
                "new-window",
                "-d",
                "-t",
                "native-warm",
                "-n",
                "cheese-drain",
                shlex.join(
                    [
                        sys.executable,
                        str(directory / "runner.py"),
                        "run-drainer",
                        str(directory),
                        str(pid),
                    ]
                ),
            ).check_returncode()
        connection(
            directory, environment["CHEESE_PROJECT"], environment["CHEESE_TOPIC"]
        )
        commands = []
        if environment.get("CLAUDE_MODEL"):
            commands.append("/model " + environment["CLAUDE_MODEL"])
        rc_stamp = directory / "remote-control-requested"
        enable_rc = (
            environment.get("CHEESE_REMOTE_CONTROL") == "1" and not rc_stamp.exists()
        )
        if enable_rc:
            commands.append("/remote-control Cheese")
        if commands:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(10)
                client.connect(state["rendezvous"])
                frames = [
                    {"role": "attacher", "auth": Path(state["token_file"]).read_text()}
                ]
                frames.extend(
                    {"type": "reply", "text": command} for command in commands
                )
                client.sendall(
                    "".join(json.dumps(frame) + "\n" for frame in frames).encode()
                )
                if enable_rc:
                    preferences = json.loads((config / ".claude.json").read_text())
                    if preferences.get("oauthAccount", {}).get(
                        "organizationUuid"
                    ) and not preferences.get("hasUsedRemoteControl"):
                        deadline = time.monotonic() + 2
                        while time.monotonic() < deadline:
                            pane = _tmux(
                                state, "capture-pane", "-p", "-t", state["pane"]
                            )
                            if b"1. Enable Remote Control" in pane.stdout:
                                # Rendering precedes the native input handler.
                                time.sleep(0.3)
                                _tmux(
                                    state, "send-keys", "-t", state["pane"], "Enter"
                                ).check_returncode()
                                break
                            time.sleep(0.02)
                    _write(rc_stamp, str(pid))
        return pid

    if os.environ.get("CHEESE_ENVIRONMENT"):
        spec = importlib.util.spec_from_file_location(
            "cheese_environment", config / "cheese-environment.py"
        )
        assert spec is not None and spec.loader is not None
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
        return runner.run(
            json.loads(os.environ["CHEESE_ENVIRONMENT"]),
            Path(state["home"]) / ".cheese-environment",
            [],
            adopt=adopt,
        )
    adopt(dict(os.environ), Path(os.environ["CHEESE_WORK"]).resolve())
    return 0


if __name__ == "__main__":
    os.umask(0o077)
    action, root = sys.argv[1:3]
    if action == "prepare":
        print(json.dumps(prepare(Path(root), Path(sys.argv[3]), json.load(sys.stdin))))
    elif action == "prepare-machine":
        owner = Path(root)
        environment = json.loads((owner / ".claude/settings.json").read_text())["env"]
        if not environment.get("CLAUDE_CODE_OAUTH_TOKEN"):
            raise ValueError("Warm machine OAuth credential is missing")
        binary = owner / ".local/share/claude/versions" / sys.argv[3]
        print(json.dumps(prepare(owner / ".cheese/native-warm", binary, environment)))
    elif action == "run":
        run(Path(root))
    elif action == "bind":
        body = json.load(sys.stdin)
        body["work"] = Path(body["work"])
        print(json.dumps(bind(Path(root), **body)))
    elif action == "stage":
        body = json.load(sys.stdin)
        for field in ("home", "work", "rendezvous", "token_file"):
            body[field] = Path(body[field])
        print(json.dumps(stage(Path(root), **body)))
    elif action == "stage-environment":
        stage(
            Path(root),
            project_id=os.environ["CHEESE_PROJECT"],
            topic_id=os.environ["CHEESE_TOPIC"],
            home=Path(sys.argv[3]),
            work=Path(sys.argv[4]),
            rendezvous=Path(os.environ["CHEESE_RV_SOCK"]),
            token_file=Path(os.environ["CHEESE_RV_TOKEN_FILE"]),
        )
    elif action == "available":
        directory = Path(root)
        state = json.loads((directory / "state.json").read_text())
        raise SystemExit(
            0 if (directory / "ready").exists() and _native_alive(state) else 1
        )
    elif action == "probe-topic":
        directory = Path(root)
        binding_file = directory / "binding.json"
        binding = json.loads(binding_file.read_text()) if binding_file.exists() else {}
        if binding.get("topic_id") != str(uuid.UUID(sys.argv[3])):
            print("unknown")
        else:
            state = json.loads((directory / "state.json").read_text())
            print("alive" if _native_alive(state) else "dead")
    elif action == "adopt-room":
        raise SystemExit(adopt_room(Path(root)))
    elif action == "run-drainer":
        directory = Path(root)
        environment = json.loads((directory / "room-environment.json").read_text())
        environment["CHEESE_DRAIN_TETHER"] = sys.argv[3]
        os.execve(
            "/bin/sh",
            ["sh", str(Path(environment["HOME"]) / ".claude/cheese-drain")],
            environment,
        )
    elif action == "attach":
        terminal = connection(Path(root), *sys.argv[3:5])
        os.environ.pop("TMUX", None)
        os.execvp("tmux", terminal["command"])
    else:
        raise ValueError("Unknown warm session operation")
