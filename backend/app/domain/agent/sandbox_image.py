#!/usr/bin/python3 -I
"""Cache cloud sandbox images on the data disk; run prewarm as root.

crane exports the merged OCI filesystem, including whiteouts and hard links;
GNU tar unpacks it. A sandbox only leases a completed cache entry. Its bwrap
keeps that lease with --sync-fd, so pruning cannot remove a running rootfs.
Standard library only, also shipped to hosts with their enrollment.
"""

import argparse
import contextlib
import fcntl
import functools
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path

CACHE = Path("/data/cheese-sandbox-images")
CRANE = "/usr/local/libexec/cheese-sandbox-tools/crane"
REFERENCE = re.compile(r"ghcr\.io/[a-z0-9._/-]+@sha256:([0-9a-f]{64})")


def image_key(reference):
    match = REFERENCE.fullmatch(reference)
    if not match:
        # i18n-exempt: configuration validation for the host CLI
        raise ValueError("a sandbox image must be a digest-pinned GHCR reference")
    return "sha256-" + match[1]


@functools.cache
def default_image():
    path = Path(__file__).resolve().parents[3] / "sandbox/image/default.json"
    spec = json.loads(path.read_text())
    image_key(spec["reference"])
    return spec


def place_crane(url, expected, cache, target=CRANE):
    marker = cache / "crane.sha256"
    if Path(target).is_file() and marker.is_file() and marker.read_text() == expected:
        return
    archive = cache / "crane.tar.gz.part"
    try:
        digest = hashlib.sha256()
        with (
            urllib.request.urlopen(url, timeout=600) as source,
            archive.open("wb") as out,
        ):
            while block := source.read(1 << 20):
                digest.update(block)
                out.write(block)
        if digest.hexdigest() != expected:
            # i18n-exempt: host tool installation diagnostic
            raise RuntimeError("crane artifact failed its pinned checksum")
        placed = Path(target)
        placed.parent.mkdir(parents=True, exist_ok=True)
        staged = placed.with_suffix(".next")
        with staged.open("wb") as out:
            subprocess.run(
                ["tar", "-xOf", str(archive), "crane"], stdout=out, check=True
            )
        staged.chmod(0o755)
        staged.replace(placed)
        marker.write_text(expected)
    finally:
        archive.unlink(missing_ok=True)


@contextlib.contextmanager
def lease(reference, cache=CACHE):
    """Hold a cached image until bwrap inherits the returned descriptor."""
    with (cache / ".lock").open("rb") as guard:
        fcntl.flock(guard, fcntl.LOCK_SH)
        directory = cache / image_key(reference)
        if not (directory / "ready.json").is_file():
            # i18n-exempt: executor bootstrap diagnostic
            raise RuntimeError("sandbox image is not prewarmed: " + reference)
        descriptor = os.open(directory / "lease", os.O_RDONLY)
        fcntl.flock(descriptor, fcntl.LOCK_SH)
    try:
        yield directory / "rootfs", descriptor
    finally:
        os.close(descriptor)


def prune(cache, current, keep):
    """Keep the newest old digests; an image still leased is never removed."""
    entries = sorted(
        (p for p in cache.glob("sha256-*") if p.name != current),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for directory in entries[keep:]:
        with (directory / "lease").open("rb") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                continue
            shutil.rmtree(directory)


def prewarm(reference, *, keep=2, cache=CACHE, crane=CRANE, crane_artifact=None):
    """Publish a fully unpacked rootfs atomically, before admitting sandboxes."""
    key = image_key(reference)
    if keep < 0:
        # i18n-exempt: host CLI argument validation
        raise ValueError("old image retention must be nonnegative")
    cache.mkdir(mode=0o755, parents=True, exist_ok=True)
    cache.chmod(0o755)
    started = time.monotonic()
    with (
        (cache / ".prepare.lock").open("a+b") as preparing,
        (cache / ".lock").open("a+b") as guard,
    ):
        os.chmod(cache / ".lock", 0o644)
        fcntl.flock(preparing, fcntl.LOCK_EX)
        if crane_artifact:
            place_crane(crane_artifact["url"], crane_artifact["sha256"], cache, crane)
        for abandoned in cache.glob(".unpack-*"):
            shutil.rmtree(abandoned)
        directory = cache / key
        if not (directory / "ready.json").is_file():
            staged = cache / (".unpack-" + uuid.uuid4().hex)
            staged.mkdir(mode=0o700)
            try:
                archive = staged / "rootfs.tar"
                platform = {"x86_64": "amd64", "aarch64": "arm64", "arm64": "arm64"}[
                    os.uname().machine
                ]
                began = time.monotonic()
                subprocess.run(
                    [
                        crane,
                        "export",
                        "--platform",
                        "linux/" + platform,
                        reference,
                        str(archive),
                    ],
                    check=True,
                    timeout=1200,
                )
                exported = time.monotonic() - began
                rootfs = staged / "rootfs"
                rootfs.mkdir(mode=0o755)
                began = time.monotonic()
                subprocess.run(
                    [
                        "tar",
                        "--extract",
                        "--file",
                        str(archive),
                        "--directory",
                        str(rootfs),
                        "--numeric-owner",
                        "--same-owner",
                    ],
                    check=True,
                    timeout=600,
                )
                unpacked = time.monotonic() - began
                archive.unlink()
                (staged / "lease").touch(mode=0o644)
                (staged / "lease").chmod(0o644)
                rootfs.chmod(0o755)
                receipt = {
                    "reference": reference,
                    "export_s": round(exported, 3),
                    "unpack_s": round(unpacked, 3),
                }
                (staged / "ready.json").write_text(json.dumps(receipt))
                (staged / "ready.json").chmod(0o644)
                staged.chmod(0o755)
                fcntl.flock(guard, fcntl.LOCK_EX)
                staged.rename(directory)
            finally:
                if staged.exists():
                    shutil.rmtree(staged)
        fcntl.flock(guard, fcntl.LOCK_EX)
        receipt = json.loads((directory / "ready.json").read_text())
        prune(cache, key, keep)
    return {**receipt, "prepare_s": round(time.monotonic() - started, 3)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference")
    parser.add_argument("--keep", type=int, default=2)
    parser.add_argument("--crane-artifacts", type=json.loads)
    args = parser.parse_args(argv)
    if os.getuid() != 0:
        parser.error("prewarm must run as root")
    artifact = None
    if args.crane_artifacts:
        artifact = args.crane_artifacts[os.uname().machine]
    print(json.dumps(prewarm(args.reference, keep=args.keep, crane_artifact=artifact)))


if __name__ == "__main__":
    sys.exit(main())
