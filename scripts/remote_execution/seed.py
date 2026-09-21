"""Prepare an isolated, disposable project on the execution host."""

import json
import random
import struct
import subprocess
import sys
import zlib
from pathlib import Path

root = Path(sys.argv[1]).resolve()
work = root / "remote project"
work.mkdir(parents=True)
(work / "target.txt").write_text("BEFORE_EDIT\n")


# Incompressible pixels exceed MCP's text limit if encoded as a JSON string.
def chunk(kind, data):
    return (
        struct.pack("!I", len(data))
        + kind
        + data
        + struct.pack("!I", zlib.crc32(kind + data))
    )


pixels = random.Random(0).randbytes(320 * 240 * 3)
png = b"\x89PNG\r\n\x1a\n" + chunk(
    b"IHDR", struct.pack("!2I5B", 320, 240, 8, 2, 0, 0, 0)
)
png += chunk(
    b"IDAT",
    zlib.compress(
        b"".join(b"\0" + pixels[y * 960 : (y + 1) * 960] for y in range(240))
    ),
)
png += chunk(b"IEND", b"")
(work / "image.png").write_bytes(png)
(work / "CLAUDE.md").write_text("Project marker: REMOTE_PROJECT_INSTRUCTIONS.\n")
skill = work / ".claude/skills/remote-check"
skill.mkdir(parents=True)
(skill / "SKILL.md").write_text(
    "---\nname: remote-check\ndescription: Check the remote execution acceptance marker.\nallowed-tools: Bash\n---\nREMOTE_SKILL_SENTINEL\nEnvironment: !`printf '%s' \"$EXECUTION_ENV\"`\n"
)
subprocess.run(["git", "init", "-q", str(work)], check=True)
subprocess.run(["git", "add", "."], cwd=work, check=True)
subprocess.run(
    [
        "git",
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-qm",
        "seed",
    ],
    cwd=work,
    check=True,
)
print(json.dumps({"workspace": str(work)}))
