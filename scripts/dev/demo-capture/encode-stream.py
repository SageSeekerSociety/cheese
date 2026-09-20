#!/usr/bin/env python3
"""Encode captured frames using their actual capture intervals, not a fixed rate.

Usage: encode-stream.py TAKE_DIR OUT.mp4 [START_SECONDS]

START_SECONDS drops everything before that mark, which is how you cut a slow
opening (login, first page load) off the front of a take.
"""

import json
from pathlib import Path
import subprocess
import sys

folder = Path(sys.argv[1]).resolve()
target = Path(sys.argv[2]).resolve()
records = [
    json.loads(line) for line in (folder / "frames.jsonl").read_text().splitlines()
]
start = float(sys.argv[3]) if len(sys.argv) > 3 else 0
frames = [row for row in records if row["event"] == "frame" and row["elapsed"] >= start]
if not frames or records[-1]["event"] != "stop":
    raise SystemExit("Recording must have frames and a completed stop record.")
if target.exists():
    raise SystemExit("Keep the existing video; choose a new filename.")
concat = folder / f"{target.stem}.ffconcat"
lines = ["ffconcat version 1.0"]
for index, frame in enumerate(frames):
    end = (
        frames[index + 1]["elapsed"]
        if index + 1 < len(frames)
        else records[-1]["elapsed"]
    )
    lines.extend(
        [
            f"file '{frame['file']}'",
            "option framerate 1000",
            f"duration {max(end - frame['elapsed'], 0.001):.6f}",
        ]
    )
lines.extend(
    [f"file '{frames[-1]['file']}'", "option framerate 1000", "duration 0.001"]
)
concat.write_text("\n".join(lines) + "\n")
subprocess.run(
    [
        "ffmpeg",
        "-nostdin",
        "-n",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat),
        "-vf",
        "fps=15",
        "-t",
        str(records[-1]["elapsed"] - frames[0]["elapsed"]),
        "-c:v",
        "libx264",
        "-threads",
        "1",
        "-preset",
        "fast",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(target),
    ],
    check=True,
)
print(target)
