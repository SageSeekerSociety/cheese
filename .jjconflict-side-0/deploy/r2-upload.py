#!/usr/bin/env python3
"""Upload a verified DB dump to Cloudflare R2 — the off-site leg of 3-2-1 backups.

Called by db-backup.sh after a local dump has been taken and verified. Config
comes from the environment (db-backup.sh sources ~/ops/r2.env, which is chmod 600
and NOT in git):

    R2_ACCESS_KEY_ID      scoped R2 API token — Access Key ID
    R2_SECRET_ACCESS_KEY  scoped R2 API token — Secret Access Key
    R2_ENDPOINT           https://<account-id>.r2.cloudflarestorage.com
    R2_BUCKET             target bucket (e.g. cheese-db-backups)
    R2_PREFIX             optional key prefix, default "db"

Usage: r2-upload.py <dump-file>
Exit 0 only after the remote object is confirmed present with a matching size;
non-zero otherwise so the caller can log the failure without losing the good
local backup.
"""

import os
import sys

import boto3
from botocore.config import Config

_REQUIRED = ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_ENDPOINT", "R2_BUCKET")


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: r2-upload.py <dump-file>", file=sys.stderr)
        return 2

    path = sys.argv[1]
    if not os.path.isfile(path):
        print(f"not a file: {path}", file=sys.stderr)
        return 2

    missing = [k for k in _REQUIRED if not os.environ.get(k)]
    if missing:
        print(f"missing env: {', '.join(missing)}", file=sys.stderr)
        return 2

    bucket = os.environ["R2_BUCKET"]
    prefix = os.environ.get("R2_PREFIX", "db").strip("/")
    name = os.path.basename(path)
    key = f"{prefix}/{name}" if prefix else name
    local_size = os.path.getsize(path)

    s3 = boto3.client(
        "s3",
        endpoint_url=os.environ["R2_ENDPOINT"],
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
        config=Config(retries={"max_attempts": 3, "mode": "standard"}),
    )

    s3.upload_file(path, bucket, key)

    # A write that isn't read back isn't verified. Confirm the object landed
    # and its size matches the local dump before reporting success.
    head = s3.head_object(Bucket=bucket, Key=key)
    remote_size = head["ContentLength"]
    if remote_size != local_size:
        print(
            f"size mismatch for r2://{bucket}/{key}: local={local_size} remote={remote_size}",
            file=sys.stderr,
        )
        return 1

    print(f"uploaded r2://{bucket}/{key} ({local_size} bytes) verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
