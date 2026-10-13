#!/usr/bin/env python3
"""Download the newest R2 DB dump, then restore only into a throwaway container."""

import argparse
import importlib
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

common = importlib.import_module("r2-common")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", help="R2 key prefix (default: R2_PREFIX or db)")
    args = parser.parse_args()
    env = common.load_config()
    if env is None:
        return 0
    prefix = (args.prefix if args.prefix is not None else env.get("R2_PREFIX", "db")).strip("/")
    start = prefix + "/" if prefix else ""
    s3 = common.client(env)
    candidates = [obj for obj in common.objects(s3, env["R2_BUCKET"], prefix)
                  if re.fullmatch(r"cheese-\d{8}-\d{6}\.dump", obj["Key"].removeprefix(start))]
    if not candidates:
        raise ValueError(f"no DB dumps in R2 prefix {prefix!r}")
    latest = max(candidates, key=lambda obj: obj["LastModified"])
    if latest["Size"] <= 0:
        raise ValueError("newest R2 dump is empty")
    # Downloaded application data must not persist after the drill.
    with tempfile.TemporaryDirectory(prefix="cheese-r2-restore-") as directory:
        dump = Path(directory) / Path(latest["Key"]).name
        s3.download_file(env["R2_BUCKET"], latest["Key"], str(dump))
        if dump.stat().st_size != latest["Size"]:
            raise ValueError("downloaded R2 dump size does not match the listed object")
        print(f"downloaded {latest['Key']} ({dump.stat().st_size} bytes); starting isolated restore", flush=True)
        result = subprocess.run(["bash", str(Path(__file__).with_name("db-restore-test.sh")), str(dump)])
        return result.returncode


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        message = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        print(f"R2 RESTORE FAIL: {message}", file=sys.stderr)
        sys.exit(1)
