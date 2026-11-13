#!/usr/bin/python3 -I
"""Cache cloud sandbox images on the data disk; run prewarm as root.

crane exports the merged OCI filesystem, including whiteouts and hard links;
GNU tar unpacks it. A sandbox only leases a completed cache entry. Its bwrap
keeps that lease with --sync-fd, so pruning cannot remove a running rootfs.
Standard library only, also shipped to hosts with their enrollment.
"""

import argparse
import array
import contextlib
import ctypes
import fcntl
import functools
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

CACHE = Path("/data/cheese-sandbox-images")
CRANE = "/usr/local/libexec/cheese-sandbox-tools/crane"
ROOT_HELPER = "/usr/local/libexec/cheese-sandbox"
PREWARM_TIMEOUT_S = 3300
REFERENCE = re.compile(r"ghcr\.io/[a-z0-9._/-]+@sha256:([0-9a-f]{64})")


def helper_source():
    return (
        Path(__file__).parent / "harness/claude_code/remote_execution/sandbox_host.py"
    ).read_text()


def install_helper(source):
    target = Path(ROOT_HELPER)
    if target.is_file() and target.read_text() == source:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    staged = target.with_name(target.name + "." + uuid.uuid4().hex)
    try:
        staged.write_text(source)
        staged.chmod(0o755)
        staged.replace(target)
    finally:
        staged.unlink(missing_ok=True)


@contextlib.contextmanager
def namespace():
    """Lease the host's ID mapping in a fresh connector-owned user namespace."""
    channel, peer = socket.socketpair()
    with channel, peer:
        channel.settimeout(30)
        command = ([] if os.geteuid() == 0 else ["sudo", "-n"]) + [
            ROOT_HELPER,
            "userns",
        ]
        process = subprocess.Popen(
            command, stdin=peer.fileno(), stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        peer.close()
        descriptors = array.array("i")
        try:
            _, messages, _, _ = channel.recvmsg(
                1, socket.CMSG_SPACE(descriptors.itemsize)
            )
            for level, kind, data in messages:
                if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                    descriptors.frombytes(
                        data[: len(data) - len(data) % descriptors.itemsize]
                    )
            stdout, stderr = process.communicate(timeout=30)
            if process.returncode or len(descriptors) != 1:
                # i18n-exempt: host bootstrap diagnostic
                raise RuntimeError("could not prepare sandbox IDs: " + stderr.decode())
            yield descriptors[0], json.loads(stdout)
        finally:
            for descriptor in descriptors:
                os.close(descriptor)
            if process.poll() is None:
                process.kill()
                process.wait()


def enter_namespace(descriptor):
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.setns(descriptor, 0x10000000) != 0:
        raise OSError(ctypes.get_errno(), "setns user namespace")
    os.setgroups([])
    os.setgid(0)
    os.setuid(0)


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


def cache_reservation_gb():
    image = default_image()
    peak = (image["keep_old"] + 2) * image["rootfs_bytes"] + image["archive_bytes"]
    return (peak * 11 + 10**10 - 1) // 10**10


def home_image_reservation_gb():
    return (default_image()["rootfs_bytes"] + 10**9 - 1) // 10**9


def place_crane(url, expected, cache, target=CRANE):
    marker = cache / "crane.sha256"
    if (
        Path(target).is_file()
        and marker.is_file()
        and marker.read_text().strip() == expected
    ):
        return
    archive = cache / "crane.tar.gz.part"
    try:
        subprocess.run(
            [
                "curl",
                "--fail",
                "--location",
                "--max-time",
                "600",
                "--output",
                str(archive),
                url,
            ],
            check=True,
            timeout=610,
        )
        if hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
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


def prune(cache, current, keep, retained=()):
    """Keep the newest old digests; an image still leased is never removed."""
    entries = sorted(
        (p for p in cache.glob("sha256-*") if p.name != current),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for directory in entries[keep:]:
        if directory.name in retained:
            continue
        with (
            (cache / ".lock").open("rb") as guard,
            (directory / "lease").open("rb") as lock,
        ):
            fcntl.flock(guard, fcntl.LOCK_EX)
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                continue
            discarded = cache / (".unpack-pruned-" + uuid.uuid4().hex)
            directory.rename(discarded)
            fcntl.flock(guard, fcntl.LOCK_UN)
            shutil.rmtree(discarded)


def prewarm(
    reference,
    *,
    keep=2,
    cache=CACHE,
    crane=CRANE,
    crane_artifact=None,
    uid=None,
    gid=None,
    retained=(),
    userns=None,
):
    """Publish a fully unpacked rootfs atomically, before admitting sandboxes."""
    key = image_key(reference)
    uid = os.getuid() if uid is None else uid
    gid = os.getgid() if gid is None else gid
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
                if userns is not None:
                    for path in (staged, archive, rootfs):
                        os.chown(path, uid, gid)
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
                    preexec_fn=None
                    if userns is None
                    else functools.partial(enter_namespace, userns),
                    pass_fds=() if userns is None else (userns,),
                )
                unpacked = time.monotonic() - began
                archive_bytes = archive.stat().st_size
                rootfs_bytes = int(
                    subprocess.check_output(
                        ["du", "-s", "-B1", str(rootfs)], text=True, timeout=60
                    ).split()[0]
                )
                archive.unlink()
                (staged / "lease").touch(mode=0o644)
                (staged / "lease").chmod(0o644)
                rootfs.chmod(0o755)
                receipt = {
                    "reference": reference,
                    "export_s": round(exported, 3),
                    "unpack_s": round(unpacked, 3),
                    "archive_bytes": archive_bytes,
                    "rootfs_bytes": rootfs_bytes,
                }
                (staged / "ready.json").write_text(json.dumps(receipt))
                (staged / "ready.json").chmod(0o644)
                if userns is not None:
                    os.chown(staged, 0, 0)
                staged.chmod(0o755)
                fcntl.flock(guard, fcntl.LOCK_EX)
                staged.rename(directory)
                fcntl.flock(guard, fcntl.LOCK_UN)
                prune(cache, key, keep, {image_key(ref) for ref in retained})
            finally:
                if staged.exists():
                    shutil.rmtree(staged)
        receipt = json.loads((directory / "ready.json").read_text())
    return {**receipt, "prepare_s": round(time.monotonic() - started, 3)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference")
    parser.add_argument("--keep", type=int, default=2)
    parser.add_argument("--crane-artifacts", type=json.loads)
    parser.add_argument("--retain", type=json.loads, default=[])
    parser.add_argument("--host-helper", required=True)
    args = parser.parse_args(argv)
    if os.getuid() != 0:
        parser.error("prewarm must run as root")
    artifact = None
    if args.crane_artifacts:
        artifact = args.crane_artifacts[os.uname().machine]
    install_helper(args.host_helper)
    with namespace() as (userns, mapping):
        print(
            json.dumps(
                prewarm(
                    args.reference,
                    keep=args.keep,
                    crane_artifact=artifact,
                    uid=mapping["uid"],
                    gid=mapping["gid"],
                    retained=args.retain,
                    userns=userns,
                )
            )
        )


if __name__ == "__main__":
    sys.exit(main())
