"""Move one conversation's Claude Code files from one room home to another.

Runs on a machine, piped to `python3 -` with a call to ``main`` appended
(`session_carry`): nothing of the backend is importable there. ``pack`` runs on
the computer the conversation was on and sends its files to the private bucket
by one presigned PUT; ``unpack`` runs on the computer the session starts on and
lays them into its room home from one presigned GET. Only a short JSON answer
comes back over the exec, whose output the connector caps.

What moves is every piece of the conversation under the room's
`.claude/projects`: its transcript in each working directory that wrote one
(`<cwd>/<id>.jsonl`), and the directory beside each holding its subagents'
transcripts and saved tool results (`<cwd>/<id>/**`). Nothing else: the seat's
memory is laid out again by the platform on every turn, and a runner's journal
belongs to its machine.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import uuid
from pathlib import Path

# Copies of `resource_cleanup`'s names, for the same reason as there: this file
# runs with nothing of ours importable.
FOOTPRINT_ROOT = ".cheese"
SEATS_DIR = "seats"
TRANSCRIPTS_ROOT = "transcripts"
#: Where this program writes its archive before sending it, and where it puts
#: what it received before checking it: under the machine's own platform root.
CARRY_TMP = "carry"


def _profile() -> Path:
    return Path.home() / os.environ.get("CHEESE_PROFILE_ROOT", FOOTPRINT_ROOT)


def room_home(project: str, resource: str) -> Path:
    return _profile() / "home" / str(uuid.UUID(project)) / str(uuid.UUID(resource))


def _is_claude(argv0: str) -> bool:
    # The runner's own test of a Claude Code process (`runner._is_claude`).
    return (
        argv0 == "claude" or "/claude/versions/" in argv0 or argv0.endswith("/claude")
    )


def still_running(config_dir: Path) -> list[int]:
    """Claude Code processes whose config directory is ``config_dir``: the
    seat's conversation is still being written while any is there."""
    marker = f"CLAUDE_CONFIG_DIR={config_dir}"
    found = []
    proc = Path("/proc")
    if proc.is_dir():
        for entry in proc.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                argv0 = (entry / "cmdline").read_bytes().split(b"\0")[0].decode()
                environ = (entry / "environ").read_bytes().split(b"\0")
            except (OSError, UnicodeDecodeError):
                continue
            if _is_claude(argv0) and marker.encode() in environ:
                found.append(int(entry.name))
        return found
    if sys.platform == "win32":
        # A process's environment cannot be read there; the runner that
        # started the agent ends it when it stops (`runner.end_left_agent`).
        return found
    listing = subprocess.run(
        ["ps", "-axo", "pid=,comm="], capture_output=True, text=True, timeout=10
    ).stdout
    for line in listing.splitlines():
        pid, _, command = line.strip().partition(" ")
        if not pid.isdigit() or not _is_claude(command.strip()):
            continue
        detail = subprocess.run(
            ["ps", "eww", "-p", pid, "-o", "command="],
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
        if marker in detail.split():
            found.append(int(pid))
    return found


def _pieces(root: Path, sid: str) -> dict[str, bytes]:
    """Every piece of conversation ``sid`` under ``root`` (a `projects`
    directory), by its path under it. A link is followed only to a file inside
    ``root``: Claude Code links a resumed conversation's subagent files to the
    originals in the same tree, and a link out of it is not ours to read."""
    if root.is_symlink() or not root.is_dir():
        return {}
    inside = root.resolve()
    found: dict[str, bytes] = {}
    paths = [*root.glob(f"*/{sid}.jsonl")]
    for directory in root.glob(f"*/{sid}"):
        if directory.is_symlink() or not directory.is_dir():
            continue
        paths += [p for p in directory.rglob("*") if p.is_file() or p.is_symlink()]
    for path in sorted(paths):
        real = path.resolve()
        if not real.is_relative_to(inside):
            # i18n-exempt: runs on the machine; a carry's reason, for the backend's log
            raise RuntimeError(f"{path.relative_to(root)} leads out of the room")
        if real.is_file():
            found[path.relative_to(root).as_posix()] = real.read_bytes()
    return found


def _retained(archive: Path, sid: str) -> dict[str, bytes]:
    """The pieces of ``sid`` in a room's retained transcripts (written by
    `resource_cleanup.retain_transcripts` before the room's home went), under
    the same paths: its `projects/` prefix taken off."""
    found: dict[str, bytes] = {}
    if not archive.is_file():
        return found
    with tarfile.open(archive, "r:gz") as bundle:
        for member in bundle.getmembers():
            parts = Path(member.name).parts
            if len(parts) < 3 or parts[0] != "projects" or not member.isfile():
                continue
            relative = Path(*parts[1:])
            if parts[2] != sid and not (len(parts) == 3 and parts[2] == f"{sid}.jsonl"):
                continue
            if ".." in relative.parts:
                # i18n-exempt: runs on the machine; a carry's reason, for the backend's log
                raise RuntimeError(f"{member.name} leads out of the room")
            source = bundle.extractfile(member)
            if source is not None:
                found[relative.as_posix()] = source.read()
    return found


def _put(data: bytes, url: str) -> dict:
    """Send ``data`` by one PUT to ``url``; its size and MD5, which the
    platform holds against what the bucket reports."""
    request = urllib.request.Request(
        url,
        data=data,
        method="PUT",
        headers={"Content-Length": str(len(data)), "Content-Type": "application/gzip"},
    )
    with urllib.request.urlopen(request, timeout=600) as response:
        if response.status // 100 != 2:
            # i18n-exempt: runs on the machine; a carry's reason, for the backend's log
            raise RuntimeError(f"bucket answered {response.status}")
    return {"size": len(data), "md5": hashlib.md5(data).hexdigest()}


def pack(payload: dict) -> dict:
    """Send conversation ``payload["sid"]`` of a room home to the bucket.

    Refuses while a Claude Code process of the seat still runs: it would go on
    writing what is being sent. A room whose home is gone (it was archived and
    cleaned up) is read from the transcripts kept when it went."""
    home = room_home(payload["project"], payload["resource"])
    seat = home / FOOTPRINT_ROOT / SEATS_DIR / payload["seat"] / ".claude"
    if still_running(seat):
        return {"running": True}
    pieces = _pieces(home / ".claude" / "projects", payload["sid"])
    if not pieces and payload.get("room"):
        pieces = _retained(
            _profile()
            / TRANSCRIPTS_ROOT
            / str(uuid.UUID(payload["project"]))
            / str(uuid.UUID(payload["room"]))
            / (str(uuid.UUID(payload["resource"])) + ".tar.gz"),
            payload["sid"],
        )
    if not any(Path(path).name == f"{payload['sid']}.jsonl" for path in pieces):
        return {"missing": True}
    size = sum(len(data) for data in pieces.values())
    if size > payload["max_bytes"]:
        return {"too_large": size}
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as bundle:
        for path, data in pieces.items():
            info = tarfile.TarInfo(path)
            info.size = len(data)
            info.mode = 0o600
            bundle.addfile(info, io.BytesIO(data))
    manifest = [
        {"path": path, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        for path, data in pieces.items()
    ]
    return {"manifest": manifest, "sent": _put(buffer.getvalue(), payload["url"])}


def unpack(payload: dict) -> dict:
    """Lay the conversation the bucket holds into this room home, every file
    checked against ``payload["manifest"]`` first.

    Read whole into a staging directory, then renamed into place, so a room
    never holds half of it. A file already there with other bytes is not
    overwritten: that is a conversation this machine already has."""
    root = room_home(payload["project"], payload["resource"]) / ".claude" / "projects"
    expected = {item["path"]: item for item in payload["manifest"]}
    with urllib.request.urlopen(payload["url"], timeout=600) as response:
        archive = response.read()
    received: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as bundle:
        for member in bundle.getmembers():
            relative = Path(member.name)
            if (
                not member.isfile()
                or relative.is_absolute()
                or ".." in relative.parts
                or member.name not in expected
            ):
                # i18n-exempt: runs on the machine; a carry's reason, for the backend's log
                raise RuntimeError(f"{member.name} is not a file of this conversation")
            source = bundle.extractfile(member)
            received[member.name] = source.read() if source is not None else b""
    for path, item in expected.items():
        data = received.get(path)
        if data is None or hashlib.sha256(data).hexdigest() != item["sha256"]:
            # i18n-exempt: runs on the machine; a carry's reason, for the backend's log
            raise RuntimeError(f"{path} did not arrive as it was sent")
        target = root / path
        if target.exists() and target.read_bytes() != data:
            # i18n-exempt: runs on the machine; a carry's reason, for the backend's log
            raise RuntimeError(f"{path} is here already with other bytes")
    root.mkdir(parents=True, exist_ok=True)
    scratch = _profile() / CARRY_TMP
    scratch.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(dir=scratch))
    try:
        for path, data in received.items():
            staged = staging / path
            staged.parent.mkdir(parents=True, exist_ok=True)
            staged.write_bytes(data)
        for path in received:
            target = root / path
            if target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staging / path, target)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return {"placed": len(received)}


def main(payload: dict) -> None:
    action = {"pack": pack, "unpack": unpack}[payload["action"]]
    print(json.dumps(action(payload)))
