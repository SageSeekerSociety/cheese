#!/usr/bin/env python3
"""Archive the session mirrors nothing reads any more to the private bucket,
then remove them from the box.

A backend keeps its copy of every session journal it has read under
``<workspace_root>/.harness/<project>/<place>/<harness>/<hash>/<mirror>.sqlite``
(``session_host.sessions._mirror``). The rows in one are pruned only while the
session is being read (``driven.subscription.RETENTION_S``), and the file itself
is never removed, so a session nobody reads again leaves its mirror on the disk
for good. On dev, 2026-10-19, that was 11.7G across 5,257 mirrors, the oldest
from 2026-09-13.

A mirror is the raw record of what a session did, so it is not regenerable:
the session machine that held the other copy is usually gone. Each one is
therefore uploaded, read back, and only then deleted.

WHAT MAKES DELETING SAFE for a session that is read again later: a fresh mirror
pulls the runner's journal from the start, and every record older than
``STALE_S`` (two hours) is stepped over rather than landed. So a mirror nobody
has written for ``--older-than-days`` cannot replay anything into its room.

Gates, all of them per mirror directory:

    stale       its data unmodified for --older-than-days: the ``.sqlite``
                and a non-empty ``-wal``. Not the ``-shm`` index, which any
                open touches (a backend start opens every mirror), and not an
                empty ``-wal``; neither holds a record, so neither is uploaded
    not open    no process this script can see holds one of its files
    uploaded    each file's object exists with the same size and sha256
    unchanged   size and mtime still what they were when it was hashed

A directory that fails a gate is kept and the reason logged.

Runs where the mirrors and the bucket credentials are, i.e. inside the backend
container, as the backend's own user. The bucket is ``TRANSCRIPT_S3_BUCKET``
(the private one) with the ``S3_*`` credentials; secret values are never
printed. Every item is logged as one JSON line on stdout; the caller keeps it
on the box:

    c=$(docker ps --format '{{.Names}}' | grep -E '^cheese-backend(-b)?-1$' | head -1)
    log=~/ops/logs/archive-session-mirrors-$(date -u +%Y%m%dT%H%M%SZ).jsonl
    docker exec -i "$c" python - [--apply] < deploy/archive-session-mirrors.py | tee "$log"

Reports by default. Pass --apply to upload and delete. Rerunnable: a mirror
already uploaded with the same sha256 is not uploaded again.
"""

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

MIRRORS = ("records.sqlite", "entries.sqlite", "events.sqlite")


def log(event: str, **fields) -> None:
    line = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "event": event}
    line.update(fields)
    print(json.dumps(line, ensure_ascii=False), flush=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def holds_records(path: Path, stat: os.stat_result) -> bool:
    """The mirror itself, or a write-ahead log with frames in it."""
    if path.name in MIRRORS:
        return True
    return path.name.endswith("-wal") and stat.st_size > 0


def mirror_dirs(root: Path):
    """Every directory holding a mirror, in a stable order."""
    seen = set()
    for name in MIRRORS:
        for path in root.rglob(name):
            seen.add(path.parent)
    return sorted(seen)


def open_files(proc: Path = Path("/proc")) -> set[str]:
    """Paths any process visible here holds open."""
    held = set()
    for fd_dir in proc.glob("[0-9]*/fd"):
        try:
            for fd in fd_dir.iterdir():
                try:
                    held.add(os.readlink(fd))
                except OSError:
                    continue
        except OSError:
            continue
    return held


def client():
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=os.environ["S3_ENDPOINT_URL"],
        aws_access_key_id=os.environ["S3_ACCESS_KEY"],
        aws_secret_access_key=os.environ["S3_SECRET_KEY"],
        region_name=os.environ.get("S3_REGION") or "auto",
        config=Config(retries={"max_attempts": 5, "mode": "standard"}),
    )


def remote(s3, bucket: str, key: str) -> dict | None:
    try:
        head = s3.head_object(Bucket=bucket, Key=key)
    except Exception as error:  # botocore's ClientError; 404 is the expected case
        code = getattr(error, "response", {}).get("Error", {}).get("Code")
        if code in ("404", "NoSuchKey", "NotFound"):
            return None
        raise
    return {"size": head["ContentLength"], "sha256": head.get("Metadata", {}).get("sha256")}


def archive(root: Path, *, s3, bucket: str, prefix: str, older_than_s: float,
            apply: bool, held: set[str], now: float | None = None) -> dict:
    now = time.time() if now is None else now
    totals = {"kept": 0, "candidates": 0, "archived": 0, "files": 0, "bytes": 0, "failed": 0}
    for directory in mirror_dirs(root):
        relative = directory.relative_to(root).as_posix()
        everything = sorted(p for p in directory.iterdir() if p.is_file())
        stats = {p: p.stat() for p in everything}
        files = [p for p in everything if holds_records(p, stats[p])]
        newest = max(stats[p].st_mtime for p in files)
        size = sum(stats[p].st_size for p in files)
        if now - newest < older_than_s:
            totals["kept"] += 1
            log("keep", dir=relative, reason="recent",
                newest=datetime.fromtimestamp(newest, timezone.utc).isoformat(timespec="seconds"),
                bytes=size)
            continue
        if any(str(p) in held for p in everything):
            totals["kept"] += 1
            log("keep", dir=relative, reason="open", bytes=size)
            continue
        totals["candidates"] += 1
        if not apply:
            log("would-archive", dir=relative, files=len(files), bytes=size,
                newest=datetime.fromtimestamp(newest, timezone.utc).isoformat(timespec="seconds"))
            continue
        try:
            done = []
            for path in files:
                key = f"{prefix}/{relative}/{path.name}"
                digest = sha256(path)
                found = remote(s3, bucket, key)
                if not (found and found["size"] == stats[path].st_size and found["sha256"] == digest):
                    s3.upload_file(str(path), bucket, key,
                                   ExtraArgs={"Metadata": {"sha256": digest}})
                    found = remote(s3, bucket, key)
                if not (found and found["size"] == stats[path].st_size and found["sha256"] == digest):
                    raise RuntimeError(f"read-back of {key} does not match: {found}")
                after = path.stat()
                if (after.st_size, after.st_mtime) != (stats[path].st_size, stats[path].st_mtime):
                    raise RuntimeError(f"{path.name} changed while it was archived")
                done.append((path, key, digest, after.st_size))
            if any(str(p) in open_files() for p in everything):
                raise RuntimeError("opened while it was archived")
            for path, key, digest, nbytes in done:
                path.unlink()
                totals["files"] += 1
                totals["bytes"] += nbytes
                log("archived", file=f"{relative}/{path.name}", key=key, bytes=nbytes, sha256=digest)
            for path in everything:
                if path.exists():
                    path.unlink()
            # Only the now-empty chain up to the root; never anything with content.
            parent = directory
            while parent != root and not any(parent.iterdir()):
                parent.rmdir()
                parent = parent.parent
            totals["archived"] += 1
        except Exception as error:
            totals["failed"] += 1
            log("failed", dir=relative, error=str(error)[:500])
    return totals


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--root", type=Path,
                        default=Path(os.environ.get("WORKSPACE_ROOT", "/app/.workspaces")) / ".harness")
    parser.add_argument("--older-than-days", type=float, default=7)
    parser.add_argument("--prefix", default="archive/session-mirrors/"
                        + datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    bucket = os.environ.get("TRANSCRIPT_S3_BUCKET", "")
    if not bucket:
        log("abort", reason="TRANSCRIPT_S3_BUCKET is not set: no private bucket to archive to")
        return 2
    prefix = args.prefix.strip("/")
    log("start", root=str(args.root), bucket=bucket, prefix=prefix, apply=args.apply,
        older_than_days=args.older_than_days)
    s3 = client() if args.apply else None
    totals = archive(args.root, s3=s3, bucket=bucket, prefix=prefix,
                     older_than_s=args.older_than_days * 86400, apply=args.apply,
                     held=open_files())
    if args.apply:
        listed = {"objects": 0, "bytes": 0}
        for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix + "/"):
            for item in page.get("Contents", []):
                listed["objects"] += 1
                listed["bytes"] += item["Size"]
        totals["bucket_prefix"] = listed
    log("done", **totals)
    return 1 if totals["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
