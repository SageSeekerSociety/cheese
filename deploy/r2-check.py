#!/usr/bin/env python3
"""CI-only freshness checks; never changes the backup writers or remote objects."""

import argparse
import importlib
import re
import sys
import time
from pathlib import Path

common = importlib.import_module("r2-common")


def check_marker(path: Path, label: str, now: int, limit: int) -> None:
    if not path.is_file():
        raise ValueError(f"{label}: missing success marker ({path})")
    text = path.read_text().strip()
    if not text.isdecimal():
        raise ValueError(f"{label}: invalid success marker ({path})")
    age = now - int(text)
    if age < 0 or age > limit:
        raise ValueError(f"{label}: stale or future success marker (age={age}s, limit={limit}s)")
    print(f"{label}: fresh (age={age}s, limit={limit}s)")


def check_remote(s3, bucket: str, prefix: str, pattern: str, label: str, now: int, limit: int) -> None:
    candidates = [obj for obj in common.objects(s3, bucket, prefix)
                  if re.fullmatch(pattern, obj["Key"].removeprefix(prefix.strip("/") + "/"))]
    if not candidates:
        raise ValueError(f"{label}: no backup objects in {prefix}/")
    latest = max(candidates, key=lambda obj: obj["LastModified"])
    age = now - int(latest["LastModified"].timestamp())
    if latest["Size"] <= 0 or age < 0 or age > limit:
        raise ValueError(f"{label}: empty, stale or future R2 backup (age={age}s, limit={limit}s)")
    print(f"{label}: fresh R2 object {latest['Key']} (age={age}s)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("configured", "local", "remote"))
    parser.add_argument("--max-age-hours", type=int, default=6)
    args = parser.parse_args()
    if args.max_age_hours <= 0:
        parser.error("max age must be positive")
    env = common.load_config()
    if env is None:
        return 3 if args.mode == "configured" else 0
    if args.mode == "configured":
        print("R2 is configured")
        return 0
    now = int(time.time())
    limit = args.max_age_hours * 3600
    errors = []

    def check(fn, *values):
        try:
            fn(*values)
        except Exception as exc:
            # Do not echo SDK exceptions: endpoint URLs can contain credentials.
            message = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
            print(f"::error::{message}", file=sys.stderr)
            errors.append(message)

    if args.mode == "local":
        backup_dir = Path(env.get("CHEESE_BACKUP_DIR", "~/backups")).expanduser()
        check(check_marker, backup_dir / ".last-offsite-success", "DB off-site upload", now, limit)
        check(check_marker, common.uploads_marker(env), "uploads mirror", now, limit)
    else:
        s3 = common.client(env)
        stamp = r"\d{8}-\d{6}"
        for prefix, pattern, label in (
            (env.get("R2_PREFIX", "db").strip("/"), rf"cheese-{stamp}\.dump", "dev DB"),
            ("prod-db", rf"cheese-{stamp}\.dump", "prod DB"),
            ("etrip", rf"etrip-cheese-db-{stamp}\.dump", "etrip cheese DB"),
            ("etrip", rf"etrip-cheesex-db-{stamp}\.dump", "etrip agent DB"),
            ("etrip", rf"etrip-uploads-{stamp}\.tar", "etrip uploads volume"),
        ):
            check(check_remote, s3, env["R2_BUCKET"], prefix, pattern, label, now, limit)
    return 1 if errors else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        message = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        print(f"::error::R2 freshness check failed: {message}", file=sys.stderr)
        sys.exit(1)
