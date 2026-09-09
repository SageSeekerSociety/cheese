"""Run an unassigned native session in its own home and tmux server.

This module is shipped to a cloud machine and uses only the standard library.
Room adoption is separate: preparation never submits a prompt or enables RC.
The native spare accepts its environment and launch arguments when claimed.
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


def build_warm_session_prepare(version: str, *, ca_pem: str = "") -> str:
    source = Path(__file__).read_text()
    transport = Path(__file__).with_name("webfetch_transport.cjs").read_text()
    certificate = (
        "cat > \"$HOME/.cheese/warm-proxy-ca.pem\" <<'CHEESE_WARM_CA'\n"
        + ca_pem.rstrip("\n")
        + "\nCHEESE_WARM_CA\n"
        if ca_pem
        else ""
    )
    return (
        'mkdir -p "$HOME/.cheese"\n'
        "cat > \"$HOME/.cheese/webfetch_transport.cjs\" <<'CHEESE_WEBFETCH'\n"
        f"{transport}\nCHEESE_WEBFETCH\n"
        "cat > \"$HOME/.cheese/warm-native-runner.py\" <<'CHEESE_WARM_RUNNER'\n"
        f"{source}\nCHEESE_WARM_RUNNER\n"
        + certificate
        + 'python3 "$HOME/.cheese/warm-native-runner.py" prepare-machine "$HOME" '
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
        # Refresh recovery assets without restarting a live spare.
        _write(directory / "runner.py", Path(__file__).read_text())
        _write(
            directory / "webfetch_transport.cjs",
            Path(__file__).with_name("webfetch_transport.cjs").read_text(),
        )
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
            "claim_socket": str(socket_root / "claim.sock"),
            "claim_auth": secrets.token_hex(16),
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
        (directory / "webfetch_transport.cjs").write_text(
            Path(__file__).with_name("webfetch_transport.cjs").read_text()
        )
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
        (config / "settings.json").write_text("{}")
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
    while (
        not Path(state["claim_socket"]).exists() and not (directory / "ready").exists()
    ):
        if not _native_alive(state):
            raise RuntimeError("Native spare exited before accepting a claim")
        if time.monotonic() >= deadline:
            raise TimeoutError("Native claim socket is still pending")
        time.sleep(0.05)
    (directory / "ready").touch()
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
        "CLAUDE_BG_CLAIM_AUTH": state["claim_auth"],
    }
    preload = shlex.quote("--preload=" + str(directory / "webfetch_transport.cjs"))
    # Bun skips a quoted preload after another option when the path has spaces.
    env["BUN_OPTIONS"] = (preload + " " + env.get("BUN_OPTIONS", "")).strip()
    os.chdir(state["work"])
    os.execve(
        state["binary"],
        [state["binary"], "--bg-spare", state["claim_socket"]],
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
    # This runs unattended during a claim. Persist ownership before any changes
    # so a crash or a second claimant cannot redirect a partially bound process.
    with (directory / "binding.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        session_id = None
        if binding_path.exists():
            binding = json.loads(binding_path.read_text())
            if any(
                binding.get(key) != value
                for key, value in intent.items()
                if key != "context"
            ):
                raise ValueError("Native session binding does not match this request")
            if binding["phase"] == "bound":
                return binding
            if (
                binding["phase"] == "binding"
                and not Path(state["claim_socket"]).exists()
            ):
                current = _tmux(
                    state,
                    "display-message",
                    "-p",
                    "-t",
                    state["pane"],
                    "#{pane_current_path}",
                )
                if current.returncode == 0 and current.stdout.decode().strip() == str(
                    work
                ):
                    # A launcher can exit after native accepted its one-shot claim
                    # but before recording success. Resume that same assignment.
                    binding["phase"] = "bound"
                    _write(binding_path, json.dumps(binding))
                    return binding
            session_id = binding.get("session_id")
        intent["session_id"] = session_id or str(uuid.uuid4())
        _write(binding_path, json.dumps({**intent, "phase": "binding"}))
        prompt_file = config / "cheese-system-prompt.md"
        _write(prompt_file, system_prompt)
        environment = json.loads((directory / "environment.json").read_text())
        room_environment = settings.get("env", {})
        environment.update(room_environment)
        proxy_environment = {}
        if "HTTPS_PROXY" in room_environment:
            # Native clients differ on proxy variable casing. A claim must not
            # retain the provider route through a lowercase or HTTP-only alias.
            for name in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "NO_PROXY"):
                value = room_environment.get(
                    name, room_environment.get(name.lower(), "")
                )
                proxy_environment[name] = proxy_environment[name.lower()] = value
            environment.update(proxy_environment)
        environment.update(
            PATH=os.environ["PATH"],
            HOME=state["home"],
            CLAUDE_CONFIG_DIR=str(config),
            DISABLE_AUTOUPDATER="1",
            CLAUDE_BG_BACKEND="daemon",
            CLAUDE_BG_RENDEZVOUS_SOCK=state["rendezvous"],
            CLAUDE_BG_RV_AUTH=Path(state["token_file"]).read_text(),
        )
        # Native reapplies settings.env while claiming. Keep its real socket
        # path there too, so it does not unlink the room's socket alias.
        settings = {
            **settings,
            "env": {
                **settings.get("env", {}),
                **proxy_environment,
                "CLAUDE_BG_RENDEZVOUS_SOCK": environment["CLAUDE_BG_RENDEZVOUS_SOCK"],
                "CLAUDE_BG_RV_AUTH": environment["CLAUDE_BG_RV_AUTH"],
            },
        }
        _write(config / "settings.json", json.dumps(settings))
        argv = [
            "--dangerously-skip-permissions",
            "--append-system-prompt-file",
            str(prompt_file),
        ]
        if environment.get("CLAUDE_MODEL"):
            argv.extend(["--model", environment["CLAUDE_MODEL"]])
        if environment.get("CHEESE_REMOTE_CONTROL") == "1":
            argv.extend(["--remote-control", "Cheese"])
        resume_id = environment.get("CHEESE_RESUME_SESSION") or session_id
        if resume_id and any((config / "projects").glob(f"*/{resume_id}.jsonl")):
            argv.extend(["--resume", resume_id])
        else:
            argv.extend(["--session-id", intent["session_id"]])
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(10)
            client.connect(state["claim_socket"])
            client.sendall(
                (
                    json.dumps(
                        {
                            "cwd": str(work),
                            "env": environment,
                            "argv": argv,
                            "sessionId": intent["session_id"],
                            "auth": state["claim_auth"],
                        }
                    )
                    + "\n"
                ).encode()
            )
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


def recover_room(directory: Path, project_id: str, topic_id: str) -> None:
    """Restart a dead claimed process while retaining its home and transcript."""
    binding_path = directory / "binding.json"
    if not binding_path.exists():
        return
    with (directory / "binding.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        binding = json.loads(binding_path.read_text())
        if binding["project_id"] != str(uuid.UUID(project_id)) or binding[
            "topic_id"
        ] != str(uuid.UUID(topic_id)):
            raise ValueError("Prepared session belongs to another room")
        state_path = directory / "state.json"
        state = json.loads(state_path.read_text())
        if _native_alive(state):
            return
        # This socket belonged to the verified dead process; never remove room data.
        Path(state["claim_socket"]).unlink(missing_ok=True)
        binding["phase"] = "staging"
        _write(binding_path, json.dumps(binding))
        existing = _tmux(state, "has-session", "-t", "native-warm").returncode == 0
        launched = _tmux(
            state,
            "new-window" if existing else "new-session",
            "-P",
            "-F",
            "#{pane_id}",
            "-d",
            "-t" if existing else "-s",
            "native-warm",
            "-c",
            state["work"],
            shlex.join(
                [sys.executable, str(directory / "runner.py"), "run", str(directory)]
            ),
        )
        launched.check_returncode()
        state["pane"] = launched.stdout.decode().strip()
        _write(state_path, json.dumps(state))
        _tmux(state, "select-window", "-t", state["pane"]).check_returncode()
        deadline = time.monotonic() + 45
        while not Path(state["claim_socket"]).exists():
            if not _native_alive(state):
                raise RuntimeError("Recovered spare exited before accepting its room")
            if time.monotonic() >= deadline:
                raise TimeoutError("Recovered spare claim socket is still pending")
            time.sleep(0.02)


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
        proxy_ca = owner / ".cheese/warm-proxy-ca.pem"
        if proxy_ca.exists():
            # Native proxy clients can cache trust before the room claim changes
            # NODE_EXTRA_CA_CERTS. Trust both deployment CAs before process birth.
            certificates = [proxy_ca.read_text()]
            if environment.get("NODE_EXTRA_CA_CERTS"):
                certificates.insert(
                    0, Path(environment["NODE_EXTRA_CA_CERTS"]).read_text()
                )
            bundle = owner / ".cheese/warm-ca-bundle.pem"
            _write(bundle, "\n".join(certificates))
            environment["NODE_EXTRA_CA_CERTS"] = str(bundle)
        binary = owner / ".cheese/claude/versions" / sys.argv[3]
        prepare(owner / ".cheese/native-warm", binary, environment)
        print("native spare: ready")
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
    elif action == "recover-room":
        recover_room(
            Path(root), os.environ["CHEESE_PROJECT"], os.environ["CHEESE_TOPIC"]
        )
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
