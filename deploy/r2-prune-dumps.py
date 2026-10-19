#!/usr/bin/env python3
"""Tiered retention for local DB dumps, gated on a verified R2 copy.

    r2-prune-dumps.py <backup-dir> --hourly-days 7 --retention-days 30 [--dry-run] [--log FILE]

Policy, by the timestamp in the dump's name (local time, as db-backup.sh writes it):
  - younger than --hourly-days: kept, every hour;
  - older: the last dump of each calendar day is kept; any other dump of that
    day is deleted only once R2 holds an object of the same size under
    <R2_PREFIX>/<name>. A missing or mismatched copy is re-uploaded and read
    back first; if that fails the dump stays and the run exits 1;
  - older than --retention-days full days: deleted, as `find -mtime +N` always
    did. The bucket's own 30-day lifecycle has expired the R2 copy by then.

The uploader records no checksum, so size is the only comparison available.
Writes one line per dump (kept / deleted / uploaded, reason, R2 key) to stdout
and to --log. R2 configuration comes from r2-common (~/ops/r2.env).
"""

import argparse
import importlib
import re
import sys
import time
from pathlib import Path

common = importlib.import_module("r2-common")

NAME = re.compile(r"cheese-(\d{8})-(\d{6})\.dump")
DAY = 86400


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("backup_dir", type=Path)
    parser.add_argument("--hourly-days", type=int, required=True)
    parser.add_argument("--retention-days", type=int, required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--log", type=Path)
    args = parser.parse_args()
    if not 0 < args.hourly_days <= args.retention_days:
        parser.error("need 0 < --hourly-days <= --retention-days")

    log_file = args.log.open("a") if args.log else None
    totals: dict[str, list[int]] = {}
    prefix_word = "would-" if args.dry_run else ""

    def record(action: str, path: Path, size: int, key: str, reason: str) -> None:
        action = prefix_word + action
        line = (f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} {action} {path.name} "
                f"bytes={size} key={key} reason={reason}")
        print(line)
        if log_file:
            log_file.write(line + "\n")
            log_file.flush()
        count = totals.setdefault(action, [0, 0])
        count[0] += 1
        count[1] += size

    dumps = []
    for path in sorted(args.backup_dir.iterdir()):
        match = NAME.fullmatch(path.name)
        if match and path.is_file():
            stamp = time.mktime(time.strptime(match[1] + match[2], "%Y%m%d%H%M%S"))
            dumps.append((path, match[1], stamp))
    last_of_day = {day: path for path, day, _ in dumps}  # names sort by time

    env = common.load_config()
    prefix = (env or {}).get("R2_PREFIX", "db").strip("/")
    s3 = remote = None
    unavailable = "R2 not configured"
    if env is not None:
        try:
            s3 = common.client(env)
            remote = {obj["Key"]: obj["Size"] for obj in common.objects(s3, env["R2_BUCKET"], prefix)}
        except Exception as exc:
            # Do not echo SDK exceptions: endpoint URLs can contain credentials.
            unavailable = f"R2 listing failed ({type(exc).__name__})"

    now = time.time()
    candidates = []
    unverified = False
    for path, day, stamp in dumps:
        size = path.stat().st_size
        key = f"{prefix}/{path.name}" if prefix else path.name
        age = now - stamp
        if age >= (args.retention_days + 1) * DAY:
            if not args.dry_run:
                path.unlink()
            record("deleted", path, size, key, f"older than {args.retention_days} days (existing horizon)")
        elif age < args.hourly_days * DAY:
            record("kept", path, size, key, f"within {args.hourly_days} days (hourly)")
        elif last_of_day[day] == path:
            record("kept", path, size, key, f"daily copy for {day}")
        elif remote is None:
            unverified = True
            record("kept", path, size, key, f"unverified: {unavailable}")
        else:
            candidates.append((path, size, key))

    # Verified deletions first: they free disk before any slow re-upload.
    candidates.sort(key=lambda item: remote.get(item[2]) != item[1])
    for path, size, key in candidates:
        remote_size = remote.get(key)
        if remote_size == size:
            if not args.dry_run:
                path.unlink()
            record("deleted", path, size, key, "not the daily copy; R2 size matches")
            continue
        found = "missing" if remote_size is None else f"size {remote_size} != local"
        if args.dry_run:
            record("uploaded", path, size, key, f"not the daily copy; R2 copy {found}; would re-upload")
            continue
        try:
            s3.upload_file(str(path), env["R2_BUCKET"], key)
            uploaded = s3.head_object(Bucket=env["R2_BUCKET"], Key=key)["ContentLength"]
        except Exception as exc:
            unverified = True
            record("kept", path, size, key, f"unverified: R2 copy {found}; re-upload failed ({type(exc).__name__})")
            continue
        if uploaded != size or path.stat().st_size != size:
            unverified = True
            record("kept", path, size, key, f"unverified: re-uploaded R2 size {uploaded} != local")
            continue
        path.unlink()
        record("uploaded", path, size, key, f"not the daily copy; R2 copy {found}; re-uploaded, size verified, local deleted")

    summary = " ".join(f"{action}={n}({nbytes}B)" for action, (n, nbytes) in sorted(totals.items()))
    print(f"summary: {summary or 'no dumps'}")
    if log_file:
        log_file.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} summary: {summary}\n")
        log_file.close()
    return 1 if unverified else 0


if __name__ == "__main__":
    sys.exit(main())
