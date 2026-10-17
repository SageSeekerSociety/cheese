#!/usr/bin/env python3
"""Verify paths in an orchestrator brief against the repository and in-flight work.

Usage:
    verify-brief-paths.py --brief brief.txt
    verify-brief-paths.py --brief-path path/to/file.py --new-path path/to/new.py \\
        --in-flight-actual path/to/changed.py --in-flight-declared path/to/claimed.py

The brief may be a text file, '-' for stdin, or omitted to read stdin. Paths
marked "新建" / "new" are expected not to exist in HEAD or origin/main. In-flight
actual and declared paths may be supplied repeatedly, or as newline-delimited
files with --in-flight-actual-list / --in-flight-declared-list.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PATH_TOKEN = re.compile(r"(?<![\w./-])(?:\.\.?/)?[\w.@+-][\w./@+-]*")
NEW_MARKER = re.compile(r"(?:新建|新增|new(?:\s+file)?)\s*[:：]?\s*", re.IGNORECASE)


def normalize_path(value: str) -> str | None:
    """Return a safe repository-relative path, rejecting traversal and URLs."""
    value = value.strip().strip("`'\"()[]{}<>,;:。；，")
    if not value or "://" in value or value.startswith("/"):
        return None
    path = Path(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        return None
    return path.as_posix()


def extract_brief_paths(text: str) -> tuple[dict[str, bool], list[str]]:
    """Map paths to whether they are marked new; return unsafe path-like tokens."""
    paths: dict[str, bool] = {}
    unsafe: list[str] = []
    for line in text.splitlines():
        matches = list(PATH_TOKEN.finditer(line))
        markers = list(NEW_MARKER.finditer(line))
        for match in matches:
            token = match.group().rstrip(".,;:!?")
            # Ignore ordinary prose words; paths must look like paths or filenames.
            if "/" not in token and "." not in token:
                continue
            marked_new = any(
                marker.end() <= match.start()
                and not any(marker.end() <= prior.start() < match.start() for prior in matches)
                for marker in markers
            )
            path = normalize_path(token)
            if path is None:
                unsafe.append(token)
            else:
                paths[path] = paths.get(path, False) or marked_new
    return paths, unsafe


def read_list_file(path: str) -> list[str]:
    text = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    return [line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]


def git_path_exists(root: Path, ref: str, path: str) -> bool:
    result = subprocess.run(
        ["git", "cat-file", "-e", f"{ref}:{path}"], cwd=root, capture_output=True
    )
    if result.returncode not in (0, 1, 128):
        raise RuntimeError(f"git could not inspect {ref}:{path}")
    return result.returncode == 0


def check(
    root: Path,
    brief_paths: dict[str, bool],
    unsafe: list[str],
    in_flight: set[str],
    refs: tuple[str, ...] = ("HEAD", "origin/main"),
) -> list[str]:
    for ref in refs:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{tree}}"],
            cwd=root,
            capture_output=True,
        )
        if result.returncode:
            raise RuntimeError(f"required tree is unavailable: {ref}")
    errors = [f"invalid or unsafe path in brief: {path}" for path in unsafe]
    for path, is_new in sorted(brief_paths.items()):
        present = [ref for ref in refs if git_path_exists(root, ref, path)]
        if is_new and present:
            errors.append(f"marked new but already exists in {' and '.join(present)}: {path}")
        elif not is_new and not present:
            errors.append(f"path does not exist in HEAD or origin/main: {path}")
        if path in in_flight:
            errors.append(f"overlaps in-flight PR actual changes or declared files: {path}")
    return errors


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--brief", help="brief text file, or '-' for stdin (default: stdin)")
    result.add_argument("--brief-path", action="append", default=[], help="explicit path in the brief; repeatable")
    result.add_argument("--new-path", action="append", default=[], help="explicit path marked as new; repeatable")
    result.add_argument("--in-flight-actual", action="append", default=[], help="path changed by an in-flight PR; repeatable")
    result.add_argument("--in-flight-declared", action="append", default=[], help="path declared by in-flight work; repeatable")
    result.add_argument("--in-flight-actual-list", action="append", default=[], help="newline-delimited list of actual PR changes")
    result.add_argument("--in-flight-declared-list", action="append", default=[], help="newline-delimited list of declared files")
    result.add_argument("--root", type=Path, default=REPO_ROOT, help="repository root (default: detected from script location)")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        root = args.root.resolve()
        brief = sys.stdin.read() if args.brief in (None, "-") else Path(args.brief).read_text(encoding="utf-8")
        brief_paths, unsafe = extract_brief_paths(brief)
        for value in args.brief_path:
            path = normalize_path(value)
            if path is None:
                unsafe.append(value)
            else:
                brief_paths.setdefault(path, False)
        for value in args.new_path:
            path = normalize_path(value)
            if path is None:
                unsafe.append(value)
            else:
                brief_paths[path] = True

        in_flight_values = list(args.in_flight_actual) + list(args.in_flight_declared)
        for list_path in args.in_flight_actual_list + args.in_flight_declared_list:
            in_flight_values.extend(read_list_file(list_path))
        in_flight: set[str] = set()
        for value in in_flight_values:
            path = normalize_path(value)
            if path is None:
                raise ValueError(f"invalid in-flight path: {value}")
            in_flight.add(path)

        errors = check(root, brief_paths, unsafe, in_flight)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        print(f"cannot verify brief paths: {exc}", file=sys.stderr)
        return 2

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"OK: verified {len(brief_paths)} brief path(s); no in-flight overlap")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
