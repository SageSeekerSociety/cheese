"""Read the existing R2 environment contract without changing backup writers."""

import os
import shlex
from pathlib import Path

REQUIRED = ("R2_ENDPOINT", "R2_BUCKET", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")


def load_config() -> dict[str, str] | None:
    path = Path(os.environ.get("CHEESE_R2_ENV", "~/ops/r2.env")).expanduser()
    env: dict[str, str] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            parts = shlex.split(line, comments=True)
            if parts and parts[0] == "export":
                parts = parts[1:]
            if not parts:
                continue
            if len(parts) != 1 or "=" not in parts[0]:
                raise ValueError(f"invalid R2 environment entry in {path}")
            key, value = parts[0].split("=", 1)
            env[key] = value
    env.update(os.environ)
    if not path.exists() and not any(env.get(key) for key in REQUIRED):
        print("SKIP: R2 is not configured (no environment file or R2 settings)")
        return None
    missing = [key for key in REQUIRED if not env.get(key)]
    if missing:
        raise ValueError(f"incomplete R2 configuration: missing {', '.join(missing)}")
    return env


def client(env: dict[str, str]):
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3", endpoint_url=env["R2_ENDPOINT"],
        aws_access_key_id=env["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=env["R2_SECRET_ACCESS_KEY"], region_name="auto",
        config=Config(retries={"max_attempts": 3, "mode": "standard"}, max_pool_connections=16),
    )


def objects(s3, bucket: str, prefix: str, *, uploads: bool = False):
    prefix = prefix.strip("/")
    for page in s3.get_paginator("list_objects_v2").paginate(
        Bucket=bucket, Prefix=prefix + "/" if prefix or uploads else ""
    ):
        yield from page.get("Contents", [])


def uploads_marker(env: dict[str, str]) -> Path:
    # Transcript runs use the same writer and must not refresh the uploads mark.
    prefix = env.get("UPLOADS_PREFIX", "prod-uploads").strip("/")
    suffix = "uploads" if prefix == "prod-uploads" else prefix.replace("/", "-")
    return Path(env.get(
        "CHEESE_UPLOADS_MIRROR_MARKER",
        str(Path(env.get("CHEESE_BACKUP_DIR", "~/backups")).expanduser() / f".last-{suffix}-mirror-success"),
    )).expanduser()
