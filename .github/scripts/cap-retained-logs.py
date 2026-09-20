#!/usr/bin/env python3
"""Cap the files a CI run retains, so one runaway log cannot fill the account.

On 2026-09-12 four `harness-contract` uploads carried 4.5 GB between them — one
of them 2.25 GB — because the upload takes `*.log` at whatever size it happens
to be. Nothing noticed until four days later, when the account's artifact
storage ran out and every later job in the repository failed its upload with
`Artifact storage quota has been hit`. The tests had passed; only the upload was
red, which is the confusing kind of failure.

A log's tail is where a failure is, so a capped log keeps what anyone would have
read. Anything else that outgrows the cap is replaced by a note: truncating JSON
or XML produces a file that looks readable and is not, which is worse than an
honest absence.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

DEFAULT_CAP_BYTES = 8 * 1024 * 1024
TAIL_KEPT_SUFFIXES = {".log", ".txt"}


def cap_file(path: pathlib.Path, cap: int) -> str | None:
    """Shrink one file to the cap. Returns what was done, or None if untouched."""
    size = path.stat().st_size
    if size <= cap:
        return None
    if path.suffix in TAIL_KEPT_SUFFIXES:
        with path.open("rb") as handle:
            handle.seek(-cap, 2)
            tail = handle.read()
        # Say so IN the file: whoever opens it must not read a truncated log as
        # a complete one and conclude the run started where the tail begins.
        banner = (
            f"[cap-retained-logs] first {size - cap} bytes dropped;"
            f" this is the last {cap} bytes of a {size}-byte file\n"
        ).encode()
        path.write_bytes(banner + tail)
        return f"tailed {path} ({size} -> {cap + len(banner)} bytes)"
    path.write_bytes(
        f"[cap-retained-logs] dropped: {size} bytes exceeds the {cap}-byte cap,"
        f" and a partial {path.suffix or 'file'} would not be readable\n".encode()
    )
    return f"dropped {path} ({size} bytes)"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", help="directory to walk")
    parser.add_argument("--cap-bytes", type=int, default=DEFAULT_CAP_BYTES)
    args = parser.parse_args(argv)

    root = pathlib.Path(args.root)
    if not root.exists():
        # Nothing was produced; that is the job's business, not this script's.
        return 0
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        done = cap_file(path, args.cap_bytes)
        if done:
            print(done)
    return 0


if __name__ == "__main__":
    sys.exit(main())
