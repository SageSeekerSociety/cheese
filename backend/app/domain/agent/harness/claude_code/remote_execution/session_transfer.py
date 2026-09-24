"""Copy original conversation files between device homes before central resume."""

import base64
import hashlib
import json
import os
import signal
import subprocess
import uuid
from pathlib import Path

# The checkout inside a room's home, whose path is one of the two a session name
# is derived from. A copy of `place.CHECKOUT_DIR`: this file is piped to the
# machine on stdin and runs with nothing of ours importable. It is held to the
# original by test_footprint_root.py — a name that drifted here would hash to a
# session nobody has, and every transfer would report "another room".
CHECKOUT_DIR = "room"


def transfer(payload):
    home = (
        Path.home()
        / ".cheese/home"
        / str(uuid.UUID(payload["project"]))
        / str(uuid.UUID(payload["resource"]))
    )
    root = home / ".claude/projects"
    action = payload["action"]
    if action == "stop":
        marker = home / ".cheese/environment-session.json"
        if marker.exists():
            socket, session, *_ = json.loads(marker.read_text())
            work = (
                Path.home()
                / ".cheese/work"
                / str(uuid.UUID(payload["project"]))
                / str(uuid.UUID(payload["resource"]))
            )
            sessions = {
                "cheese_"
                + subprocess.check_output(["cksum"], input=str(path).encode())
                .decode()
                .split()[0]
                for path in (work, home / CHECKOUT_DIR)
            }
            if session not in sessions:
                raise RuntimeError("Session marker names another room")
            if not Path(socket).exists():
                print(json.dumps({"stopped": True}))
                return
            if Path(socket).stat().st_uid != os.getuid():
                raise RuntimeError("Session socket belongs to another user")
            probe = subprocess.run(
                ["tmux", "-S", socket, "has-session", "-t", session],
                capture_output=True,
            )
            if probe.returncode == 0:
                # The connector retains exited panes for terminal inspection.
                # Their session still exists after Claude has flushed and exited.
                panes = [
                    line.split()
                    for line in subprocess.check_output(
                        [
                            "tmux",
                            "-S",
                            socket,
                            "list-panes",
                            "-s",
                            "-t",
                            session,
                            "-F",
                            "#{pane_dead} #{pane_pid}",
                        ],
                        text=True,
                    ).splitlines()
                    if line.split()
                ]
                if panes and all(pane[0] == "1" for pane in panes):
                    print(json.dumps({"stopped": True}))
                    return
                if payload.get("request_exit", True):
                    # The pane's program is the launcher: it traps TERM, stops
                    # the session's runner, and the runner stops the agent,
                    # which leaves its conversation files complete.
                    for pane in panes:
                        if pane[0] != "1" and len(pane) > 1:
                            try:
                                os.kill(int(pane[1]), signal.SIGTERM)
                            except ProcessLookupError:
                                pass
                    print(json.dumps({"stopped": False}))
                    return
            if probe.returncode == 0:
                print(json.dumps({"stopped": False}))
                return
        print(json.dumps({"stopped": True}))
        return
    if action == "list":
        files = []
        for path in sorted(root.rglob("*.jsonl")):
            if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
                raise RuntimeError("Transcript path escapes its recorded home")
            content = path.read_bytes()
            files.append(
                {
                    "path": str(path.relative_to(root)),
                    "size": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                }
            )
        print(json.dumps({"files": files}))
        return
    relative = Path(payload["path"])
    if relative.is_absolute() or ".." in relative.parts or relative.suffix != ".jsonl":
        raise ValueError("Invalid transcript path")
    path = root / relative
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise RuntimeError("Transcript path escapes its recorded home")
    if action == "read":
        with path.open("rb") as source:
            source.seek(payload["offset"])
            data = source.read(384 * 1024)
        print(json.dumps({"data": base64.b64encode(data).decode()}))
    elif action == "write":
        path.parent.mkdir(parents=True, exist_ok=True)
        staging = path.with_suffix(".transferring")
        with staging.open("wb" if payload["offset"] == 0 else "ab") as output:
            if output.tell() != payload["offset"]:
                raise RuntimeError("Transcript transfer offset changed")
            output.write(base64.b64decode(payload["data"], validate=True))
        if payload["final"]:
            if hashlib.sha256(staging.read_bytes()).hexdigest() != payload["sha256"]:
                raise RuntimeError("Transcript changed during transfer")
            if path.exists():
                if path.read_bytes() != staging.read_bytes():
                    raise RuntimeError("Central transcript already has different bytes")
                staging.unlink()
            else:
                os.replace(staging, path)
        print("{}")
    else:
        raise ValueError("Unknown transcript transfer action")
