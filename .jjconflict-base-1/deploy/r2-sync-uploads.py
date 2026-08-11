#!/usr/bin/env python3
"""Incrementally mirror a local uploads/ dir to Cloudflare R2 (off-site backup).

Additive: uploads any file missing from R2 or whose size differs; NEVER deletes
remote objects, so a file removed locally stays backed up (R2 is a superset).
This is the ongoing off-site mechanism for STORAGE_TYPE=local deployments where
user-uploaded files (e.g. PDF 赛题) live on the app box's disk.

Config from ~/ops/r2.env (R2_ENDPOINT/R2_ACCESS_KEY_ID/R2_SECRET_ACCESS_KEY/
R2_BUCKET) + UPLOADS_PREFIX env (default "prod-uploads").
Usage: r2-sync-uploads.py [uploads-dir]
"""
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import boto3
from botocore.config import Config


def load_env(path: str) -> dict[str, str]:
    env: dict[str, str] = {}
    with open(os.path.expanduser(path)) as fh:
        for line in fh:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k] = v
    return env


def main() -> int:
    env = load_env("~/ops/r2.env")
    src = os.path.expanduser(
        sys.argv[1] if len(sys.argv) > 1 else "~/cheese-backend-py/backend/uploads"
    )
    if not os.path.isdir(src):
        print(f"not a dir: {src}", file=sys.stderr)
        return 2
    bucket = env["R2_BUCKET"]
    prefix = os.environ.get("UPLOADS_PREFIX", "prod-uploads").strip("/")
    s3 = boto3.client(
        "s3",
        endpoint_url=env["R2_ENDPOINT"],
        aws_access_key_id=env["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=env["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
        config=Config(retries={"max_attempts": 3, "mode": "standard"}, max_pool_connections=16),
    )

    # remote index (key -> size), so we upload only new/changed files
    remote: dict[str, int] = {}
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix + "/"):
        for o in page.get("Contents", []):
            remote[o["Key"]] = o["Size"]

    todo: list[tuple[str, str, int]] = []
    skipped = 0
    for root, _, files in os.walk(src):
        for name in files:
            fp = os.path.join(root, name)
            key = f"{prefix}/{os.path.relpath(fp, src)}"
            size = os.path.getsize(fp)
            if remote.get(key) == size:
                skipped += 1
            else:
                todo.append((fp, key, size))

    uploaded = 0
    failed = 0
    bytes_up = 0

    def _put(item: tuple[str, str, int]) -> tuple[bool, int]:
        fp, key, size = item
        s3.upload_file(fp, bucket, key)
        if s3.head_object(Bucket=bucket, Key=key)["ContentLength"] != size:
            raise RuntimeError(f"size mismatch after upload: {key}")
        return True, size

    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(_put, it): it for it in todo}
        for fut in as_completed(futs):
            try:
                _, size = fut.result()
                uploaded += 1
                bytes_up += size
            except Exception as exc:  # noqa: BLE001 - report and continue mirroring the rest
                failed += 1
                print(f"  FAIL {futs[fut][1]}: {type(exc).__name__} {exc}", file=sys.stderr)

    print(
        f"mirror {src} -> r2://{bucket}/{prefix}/ : uploaded={uploaded} "
        f"({bytes_up / 1048576:.1f} MB) skipped={skipped} failed={failed}"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
