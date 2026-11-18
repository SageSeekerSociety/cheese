"""Root diffs in GNU tar format; run outside the executor, with its UID map.

Only upper is saved. GNU tar preserves links, numeric owners and overlay xattrs.
The executor and archive use the same UID/GID mapping from confinement,
without storing a host's numeric IDs in the object.
"""

import hashlib
import resource
import subprocess
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path

ARCHIVE_MAX = 512 * 1024**2
EXPANDED_MAX = 2 * 1024**3
MEMBERS_MAX = 200_000
TRANSFER_S = 600


class TooLarge(ValueError):
    pass


def layer_for(home, image):
    digest = image.rsplit("@sha256:", 1)[-1]
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        # i18n-exempt: stdlib host archive diagnostic, handled by executor setup
        raise ValueError("a sha256 image digest is required")
    return home / ".cheese/rootfs" / ("sha256-" + digest)


def inspect_archive(archive):
    """Bound unpacked bytes and inode demand before extraction writes anything."""
    size, count = 0, 0
    with tarfile.open(archive, "r|gz") as bundle:
        for member in bundle:
            size += member.size
            count += 1
            if size > EXPANDED_MAX or count > MEMBERS_MAX:
                # i18n-exempt: converted to the backup's too_large status
                raise TooLarge("root diff exceeds the unpacked backup limit")
            name = Path(member.name)
            if name.is_absolute() or ".." in name.parts:
                # i18n-exempt: stdlib host archive diagnostic, handled by executor setup
                raise ValueError("root diff contains an escaping path")
            if member.islnk():
                link = Path(member.linkname)
                if link.is_absolute() or ".." in link.parts:
                    # i18n-exempt: stdlib host archive diagnostic, handled by executor setup
                    raise ValueError("root diff contains an escaping hard link")
    return size, count


def _inside(bwrap, namespace, writable, argv, *, fds=(), preexec_fn=None):
    # Only the staging directory is writable. Even a hostile archive's symlink
    # cannot make tar write into the host or another sandbox's home.
    return subprocess.run(
        [
            bwrap,
            *namespace,
            "--ro-bind",
            "/",
            "/",
            "--bind",
            str(writable),
            str(writable),
            "--",
            *map(str, argv),
        ],
        capture_output=True,
        text=True,
        timeout=TRANSFER_S,
        pass_fds=fds,
        preexec_fn=preexec_fn,
    )


def pack(upper, archive, *, bwrap, namespace, fds=()):
    """Archive an unmounted upper in the executor's user namespace."""

    def capped():
        resource.setrlimit(resource.RLIMIT_FSIZE, (ARCHIVE_MAX, ARCHIVE_MAX))

    result = _inside(
        bwrap,
        namespace,
        archive.parent,
        [
            "tar",
            "--xattrs",
            "--xattrs-include=*",
            "--numeric-owner",
            "-czf",
            str(archive),
            "-C",
            str(upper),
            ".",
        ],
        fds=fds,
        preexec_fn=capped,
    )
    if result.returncode:
        if archive.exists() and archive.stat().st_size >= ARCHIVE_MAX:
            # i18n-exempt: converted to the backup's too_large status
            raise TooLarge("root diff exceeds the compressed backup limit")
        # i18n-exempt: stdlib host archive diagnostic, handled by executor setup
        raise RuntimeError("root diff archive failed: " + result.stderr[-500:])
    expanded, members = inspect_archive(archive)
    digest = hashlib.md5()
    with archive.open("rb") as source:
        for block in iter(lambda: source.read(1024**2), b""):
            digest.update(block)
    return {
        "size": archive.stat().st_size,
        "md5": digest.hexdigest(),
        "expanded": expanded,
        "members": members,
    }


def restore(home, image, snapshot, *, bwrap, namespace, fds=()):
    """Publish a restored upper atomically, before overlayfs mounts it.

    Quotas must already cover home. Archive and extraction both count against
    that quota; an error removes staging and leaves the live upper untouched.
    """
    started = time.monotonic()
    layer = layer_for(home, image)
    if layer.exists():
        return {"status": "existing"}
    if snapshot["image"] != image:
        return {"status": "image_changed"}
    layer.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix=".restore-", dir=layer.parent) as scratch:
        staging = Path(scratch)
        archive = staging / "upper.tar.gz"
        with urllib.request.urlopen(snapshot["url"], timeout=TRANSFER_S) as source:
            digest = hashlib.md5()
            size = 0
            with archive.open("wb") as target:
                while block := source.read(1024**2):
                    size += len(block)
                    if size > ARCHIVE_MAX or size > snapshot["size"]:
                        # i18n-exempt: stdlib host archive diagnostic, handled by executor setup
                        raise TooLarge("root diff exceeds the compressed backup limit")
                    digest.update(block)
                    target.write(block)
        if size != snapshot["size"] or digest.hexdigest() != snapshot["md5"]:
            # i18n-exempt: stdlib host archive diagnostic, handled by executor setup
            raise ValueError("root diff checksum mismatch")
        expanded, members = inspect_archive(archive)
        upper = staging / "upper"
        upper.mkdir(mode=0o700)
        try:
            result = _inside(
                bwrap,
                namespace,
                upper,
                [
                    "tar",
                    "--xattrs",
                    "--xattrs-include=*",
                    "--numeric-owner",
                    "-xzf",
                    archive,
                    "-C",
                    upper,
                ],
                fds=fds,
            )
            if result.returncode:
                # i18n-exempt: stdlib host archive diagnostic, handled by executor setup
                raise RuntimeError("root diff restore failed: " + result.stderr[-500:])
            archive.unlink()
            (staging / "work").mkdir(mode=0o700)
            staging.rename(layer)
        finally:
            if upper.exists():
                # Mapped service users can own private directories that the host
                # connector cannot traverse. Clean them in the same namespace.
                cleaned = _inside(
                    bwrap, namespace, staging, ["rm", "-rf", "--", upper], fds=fds
                )
                if cleaned.returncode:
                    # i18n-exempt: stdlib host archive diagnostic, handled by executor setup
                    raise RuntimeError(
                        "root diff cleanup failed: " + cleaned.stderr[-500:]
                    )
    return {
        "status": "restored",
        "seconds": round(time.monotonic() - started, 3),
        "expanded": expanded,
        "members": members,
    }


def save(home, image, url, *, bwrap, namespace, put_archive, fds=()):
    upper = layer_for(home, image) / "upper"
    if not upper.is_dir():
        return {"status": "absent"}
    # Scratch lives outside the sandbox's quota: a full upper must be saveable.
    scratch = home.parents[2] / "root-snapshots"
    scratch.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix="save-", dir=scratch) as temporary:
        archive = Path(temporary) / "upper.tar.gz"
        try:
            result = pack(upper, archive, bwrap=bwrap, namespace=namespace, fds=fds)
        except TooLarge:
            return {"status": "too_large"}
        sent = put_archive(archive, url)
        if any(sent[key] != result[key] for key in ("size", "md5")):
            # i18n-exempt: host transport diagnostic; lifecycle retains the source home
            raise RuntimeError("root diff changed during upload")
        return {"status": "saved", **result}
