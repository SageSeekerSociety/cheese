#!/usr/bin/env python3
"""CI-only freshness checks; never changes the backup writers or remote objects."""

import argparse
import importlib
import os
import re
import sys
import time
import subprocess
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


def observe_uploads_service(env: dict[str, str]) -> None:
    # CI can observe the old dev writer without installing anything in ~/ops.
    # A receipt uses its actual successful exit time, never the check's time.
    result = subprocess.run([
        "systemctl", "show", "cheese-uploads-mirror.service", "--no-pager",
        "--property=Result,ExecMainStatus,ExecMainCode,ExecMainExitTimestamp",
    ], capture_output=True, text=True, env={**os.environ, "LC_ALL": "C", "TZ": "UTC"})
    if result.returncode:
        print("uploads service observation unavailable; requiring its existing marker")
        return
    props = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    if (props.get("Result"), props.get("ExecMainCode"), props.get("ExecMainStatus")) != ("success", "1", "0"):
        print("uploads service has no completed successful exit; retaining the existing marker")
        return
    timestamp = props.get("ExecMainExitTimestamp", "")
    if not timestamp or timestamp == "n/a":
        return
    parsed = subprocess.run(["date", "--date", timestamp, "+%s"], capture_output=True, text=True)
    if parsed.returncode or not parsed.stdout.strip().isdecimal():
        raise ValueError("could not parse uploads service success timestamp")
    marker = common.uploads_marker(env)
    marker.parent.mkdir(parents=True, exist_ok=True)
    temporary = marker.with_name(f"{marker.name}.{os.getpid()}.tmp")
    temporary.write_text(parsed.stdout.strip() + "\n")
    temporary.replace(marker)
    print("uploads marker records the dev service's successful exit timestamp")


def check_remote(s3, bucket: str, prefix: str, pattern: str, label: str, now: int, limit: int) -> None:
    start = prefix.strip("/") + "/" if prefix.strip("/") else ""
    candidates = [obj for obj in common.objects(s3, bucket, prefix)
                  if re.fullmatch(pattern, obj["Key"].removeprefix(start))]
    if not candidates:
        raise ValueError(f"{label}: no backup objects in {prefix}/")
    latest = max(candidates, key=lambda obj: obj["LastModified"])
    age = now - int(latest["LastModified"].timestamp())
    if latest["Size"] <= 0 or age < 0 or age > limit:
        raise ValueError(f"{label}: empty, stale or future R2 backup (age={age}s, limit={limit}s)")
    print(f"{label}: fresh R2 object {latest['Key']} (age={age}s)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("configured", "mirror-receipt", "local", "remote"))
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
    if args.mode == "mirror-receipt":
        if env.get("UPLOADS_PREFIX", "prod-uploads").strip("/") != "prod-uploads":
            raise ValueError("dev uploads service uses prod-uploads; cannot mark another prefix")
        observe_uploads_service(env)
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
