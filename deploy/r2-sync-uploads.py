#!/usr/bin/env python3
"""Additive R2 uploads mirror, or restore and verify into a NEW directory.

The default writer still uploads missing/size-changed files and NEVER deletes
remote objects. Config: ~/ops/r2.env plus environment overrides; UPLOADS_PREFIX
(default prod-uploads). Successful mirror runs record a local success marker.
--restore DIR refuses an existing destination; --verify DIR reads back every
remote object and compares bytes, not multipart ETags.
"""

import argparse
import hashlib
import importlib
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path, PurePosixPath

common = importlib.import_module("r2-common")


def local_path(dst: Path, key: str, prefix: str) -> Path:
    relative = key.removeprefix(prefix + "/" if prefix else "")
    parts = PurePosixPath(relative).parts
    if not parts or relative.startswith("/") or ".." in parts or "\\" in relative:
        raise ValueError(f"unsafe uploads object key: {key}")
    path = dst.joinpath(*parts)
    if not path.resolve().is_relative_to(dst.resolve()):
        raise ValueError(f"uploads object escapes the destination: {key}")
    return path


def restore(s3, bucket: str, prefix: str, dst: Path) -> None:
    # Refuse live uploads roots and existing files rather than overwrite them.
    dst.mkdir(parents=True, exist_ok=False)
    count = 0
    for obj in common.objects(s3, bucket, prefix):
        if obj["Key"].endswith("/"):
            continue
        path = local_path(dst, obj["Key"], prefix)
        path.parent.mkdir(parents=True, exist_ok=True)
        s3.download_file(bucket, obj["Key"], str(path))
        if path.stat().st_size != obj["Size"]:
            raise ValueError(f"restored uploads size mismatch: {obj['Key']}")
        count += 1
    if not count:
        raise ValueError(f"no uploads objects in {prefix}/")
    print(f"restored {count} uploads objects into {dst}")


def digest(stream) -> bytes:
    value = hashlib.sha256()
    while chunk := stream.read(1024 * 1024):
        value.update(chunk)
    return value.digest()


def verify(s3, bucket: str, prefix: str, dst: Path) -> None:
    count = 0
    for obj in common.objects(s3, bucket, prefix):
        if obj["Key"].endswith("/"):
            continue
        path = local_path(dst, obj["Key"], prefix)
        if not path.is_file() or path.stat().st_size != obj["Size"]:
            raise ValueError(f"uploads missing or size mismatch: {obj['Key']}")
        body = s3.get_object(Bucket=bucket, Key=obj["Key"])["Body"]
        try:
            with path.open("rb") as local:
                matches = digest(local) == digest(body)
        finally:
            body.close()
        if not matches:
            raise ValueError(f"uploads content mismatch: {obj['Key']}")
        count += 1
    if not count:
        raise ValueError(f"no uploads objects in {prefix}/")
    print(f"UPLOADS VERIFY PASS: {count} objects compared byte-for-byte via SHA-256")


def mirror(s3, bucket: str, prefix: str, src: Path, env: dict[str, str]) -> int:
    if not src.is_dir():
        raise ValueError(f"not a dir: {src}")
    remote = {obj["Key"]: obj["Size"] for obj in common.objects(s3, bucket, prefix)}
    todo = []
    skipped = 0
    for root, _, files in os.walk(src):
        for name in files:
            path = Path(root) / name
            key = "/".join(filter(None, (prefix, path.relative_to(src).as_posix())))
            size = path.stat().st_size
            if remote.get(key) == size:
                skipped += 1
            else:
                todo.append((path, key, size))

    def put(item):
        path, key, size = item
        s3.upload_file(str(path), bucket, key)
        if s3.head_object(Bucket=bucket, Key=key)["ContentLength"] != size:
            raise ValueError(f"size mismatch after upload: {key}")
        return size

    uploaded = failed = bytes_up = 0
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(put, item): item for item in todo}
        for future in as_completed(futures):
            try:
                bytes_up += future.result()
                uploaded += 1
            except Exception as exc:
                failed += 1
                print(f"FAIL {futures[future][1]}: {type(exc).__name__}", file=sys.stderr)
    print(f"mirror {src} -> r2://{bucket}/{prefix}/: uploaded={uploaded} "
          f"({bytes_up / 1048576:.1f} MB) skipped={skipped} failed={failed}")
    if failed:
        return 1
    marker = common.uploads_marker(env)
    marker.parent.mkdir(parents=True, exist_ok=True)
    temporary = marker.with_name(f"{marker.name}.{os.getpid()}.tmp")
    temporary.write_text(f"{int(time.time())}\n")
    temporary.replace(marker)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("uploads_dir", nargs="?", default="~/cheese-backend-py/backend/uploads")
    parser.add_argument("--restore", metavar="NEW_DIR")
    parser.add_argument("--verify", metavar="DIR")
    args = parser.parse_args()
    env = common.load_config()
    if env is None:
        # A manual recovery must not silently succeed without restoring anything.
        raise ValueError("R2 must be configured for uploads operations")
    prefix = env.get("UPLOADS_PREFIX", "prod-uploads").strip("/")
    s3 = common.client(env)
    if args.restore:
        dst = Path(args.restore).expanduser()
        restore(s3, env["R2_BUCKET"], prefix, dst)
        if args.verify:
            if Path(args.verify).expanduser().resolve() != dst.resolve():
                raise ValueError("--restore and --verify must name the same directory")
            verify(s3, env["R2_BUCKET"], prefix, dst)
    elif args.verify:
        verify(s3, env["R2_BUCKET"], prefix, Path(args.verify).expanduser())
    else:
        return mirror(s3, env["R2_BUCKET"], prefix, Path(args.uploads_dir).expanduser(), env)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        message = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        print(f"UPLOADS FAIL: {message}", file=sys.stderr)
        sys.exit(1)
