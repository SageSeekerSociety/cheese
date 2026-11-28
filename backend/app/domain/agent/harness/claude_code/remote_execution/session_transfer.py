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


def transcript_roots(home):
    """Where a room's transcripts are, as `device_launch` lays them out: each
    seat's own, and the room's, which holds those written before seats kept
    their own and those a handover brought. A seat still linked to the room's
    is the room's."""
    room = home / ".claude/projects"
    seats = sorted((home / ".cheese/seats").glob("*/.claude/projects"))
    return [room, *(seat for seat in seats if not seat.is_symlink())]


def transcript_at(home, relative):
    """The transcript ``relative`` names under the room's home, refused unless
    it is a transcript in one of `transcript_roots`, by its own path."""
    relative = Path(relative)
    if relative.is_absolute() or ".." in relative.parts or relative.suffix != ".jsonl":
        raise ValueError("Invalid transcript path")
    path = home / relative
    if path.is_symlink() or not any(
        path.resolve().is_relative_to(root.resolve()) and path.is_relative_to(root)
        for root in transcript_roots(home)
    ):
        raise RuntimeError("Transcript path escapes its recorded home")
    return path


def transfer(payload):
    home = (
        Path.home()
        / os.environ.get("CHEESE_PROFILE_ROOT", ".cheese")
        / "home"
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
                / os.environ.get("CHEESE_PROFILE_ROOT", ".cheese")
                / "work"
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
        # Named by their path under the room's home, every seat's with the
        # room's: the source is read where each one is.
        files = []
        for directory in transcript_roots(home):
            for path in sorted(directory.rglob("*.jsonl")):
                content = transcript_at(home, path.relative_to(home)).read_bytes()
                files.append(
                    {
                        "path": path.relative_to(home).as_posix(),
                        "size": len(content),
                        "sha256": hashlib.sha256(content).hexdigest(),
                    }
                )
        print(json.dumps({"files": files}))
        return
    if action == "read":
        path = transcript_at(home, payload["path"])
    else:
        # Written into the room's directory, below the directory its cwd
        # named, whichever seat held it here: the session that goes on with it
        # takes it from there into its own seat (`runner.adopt_room_transcripts`).
        relative = Path(payload["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Invalid transcript path")
        parts = relative.parts
        if "projects" not in parts or parts[-1] == "projects":
            raise ValueError("Invalid transcript path")
        path = root / Path(*parts[len(parts) - parts[::-1].index("projects") :])
        if path.suffix != ".jsonl":
            raise ValueError("Invalid transcript path")
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
