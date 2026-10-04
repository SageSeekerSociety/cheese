"""What the platform does to one session's home on a cloud host.

Piped to ``python3 -`` on the host (``lifecycle.program``) after the source of
``agent/resource_cleanup.py``, whose helpers arrive as ``cleanup``: this file
runs there with nothing of ours importable, like that one.

- ``sleep``: stop the session's sandbox. Its executor is asked to stop, then
  whatever still holds the home — a dev server, a background command — is
  ended. The home stays on the disk.
- ``archive``: write the stopped home to one ``.tar.gz`` and PUT it to the URL
  the platform signed for it. Answers the bytes' size and MD5, which the
  platform compares with what the bucket stored before it lets the home go.
- ``drop``: delete the home from this host, once its archive is verified.
- ``restore``: GET the archive, check it is the one that was written, and
  unpack it as the session's home, replacing any older copy left here.

Each answers one line of JSON.
"""

import hashlib
import io
import json
import os
import shutil
import sys
import tarfile
import time
import urllib.error
import urllib.request
from pathlib import Path

# R2 and S3 take at most 5 GiB in one PUT. A home past this stays on its host.
ARCHIVE_LIMIT = 4 * 1024**3
# Where an archive is written before upload, and unpacked before it replaces a
# home: on the same disk as the homes, so the final rename is a rename.
SCRATCH = Path.home() / ".cheese" / "archives"
CHUNK = 1024 * 1024


class _Counted(io.RawIOBase):
    """A file the archive is written through: it counts and hashes the bytes
    that reach the disk, which are the bytes the bucket will be sent."""

    def __init__(self, raw):
        super().__init__()
        self.raw = raw
        self.md5 = hashlib.md5()  # noqa: S324 — compared with the S3 ETag, not a security digest
        self.size = 0

    def writable(self):
        return True

    def write(self, data):
        self.md5.update(data)
        self.size += len(data)
        self.raw.write(data)
        return len(data)


def _paths(cleanup, project, resource):
    home, work = cleanup["resource_paths"](Path.home(), project, resource)
    return home, work


def sleep(cleanup, project, resource):
    home, work = _paths(cleanup, project, resource)
    cleanup["stop_executor"](home, resource)
    cleanup["end_holders"]([home, work])
    cleanup["check_no_writers"]([home, work])
    return {"asleep": True}


def archive(cleanup, project, resource, url):
    home, work = _paths(cleanup, project, resource)
    if not home.exists() and not work.exists():
        raise RuntimeError("the session's home is not on this host")
    # A still image: nothing may be writing while it is taken.
    cleanup["check_no_writers"]([home, work])
    SCRATCH.mkdir(parents=True, exist_ok=True, mode=0o700)
    part = SCRATCH / f"{resource}.tar.gz"

    def keep(info):
        # The sandbox's own /tmp lives in its home and is not kept.
        return None if info.name == "home/.cheese/tmp" else info

    try:
        with part.open("wb") as raw:
            counted = _Counted(raw)
            with tarfile.open(fileobj=counted, mode="w:gz", compresslevel=3) as bundle:
                for name, path in (("home", home), ("work", work)):
                    if path.exists():
                        # Links are stored as links, never followed; sockets
                        # are skipped, and so is the sandbox's /tmp.
                        bundle.add(path, arcname=name, filter=keep)
            raw.flush()
            os.fsync(raw.fileno())
        if counted.size > ARCHIVE_LIMIT:
            raise RuntimeError(
                f"the home is {counted.size} bytes compressed, over the archive limit"
            )
        with part.open("rb") as body:
            request = urllib.request.Request(
                url,
                data=body,
                method="PUT",
                headers={
                    "Content-Length": str(counted.size),
                    "Content-Type": "application/gzip",
                },
            )
            with urllib.request.urlopen(request, timeout=600) as response:
                response.read()
        return {"size": counted.size, "md5": counted.md5.hexdigest()}
    finally:
        part.unlink(missing_ok=True)


def drop(cleanup, project, resource):
    home, work = _paths(cleanup, project, resource)
    cleanup["check_no_writers"]([home, work])
    for path in (work, home, cleanup["resource_tmp"](resource)):
        if path.exists():
            cleanup["remove_tree"](path)
    return {"dropped": True}


def restore(cleanup, project, resource, url, size, md5):
    home, work = _paths(cleanup, project, resource)
    SCRATCH.mkdir(parents=True, exist_ok=True, mode=0o700)
    part = SCRATCH / f"{resource}.restore.tar.gz"
    staging = SCRATCH / f"{resource}.restore"
    try:
        digest = hashlib.md5()  # noqa: S324 — the S3 ETag's digest
        got = 0
        try:
            with urllib.request.urlopen(url, timeout=600) as response:
                with part.open("wb") as output:
                    while chunk := response.read(CHUNK):
                        digest.update(chunk)
                        got += len(chunk)
                        output.write(chunk)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                # Nothing to restore: the session starts from the repository.
                return {"missing": True}
            raise
        if got != size or digest.hexdigest() != md5:
            raise RuntimeError("the archive read back is not the one that was written")
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(mode=0o700)
        with tarfile.open(part, "r:gz") as bundle:
            members = bundle.getmembers()
            if any(m.name.split("/", 1)[0] not in ("home", "work") for m in members):
                raise RuntimeError("the archive holds something besides a home")
            bundle.extractall(staging, members=members, filter=_as_written)
        # The executor that ran here is gone, and its record names a release
        # this host may not have: the next start installs one afresh.
        shutil.rmtree(staging / "home/.cheese/executor", ignore_errors=True)
        for name, target in (("home", home), ("work", work)):
            if target.exists():
                # An older copy than the archive: left by a drop that failed.
                cleanup["check_no_writers"]([target])
                cleanup["remove_tree"](target)
            if (staging / name).exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staging / name, target)
        return {"restored": True}
    finally:
        part.unlink(missing_ok=True)
        shutil.rmtree(staging, ignore_errors=True)


def _as_written(member, path):
    """The `tar` filter's refusals — any member that would land outside the
    staging directory — with the permissions the home had. That filter also
    drops group and other write bits, which a host with umask 002 gives every
    file; only setuid, setgid and sticky go. Links stay as they were: a
    venv's interpreter is one."""
    checked = tarfile.tar_filter(member, path)
    return checked.replace(mode=member.mode & 0o777, deep=False)


ACTIONS = {"sleep": sleep, "archive": archive, "drop": drop, "restore": restore}


def main(cleanup, request):
    started = time.monotonic()
    answer = ACTIONS[request.pop("action")](cleanup, **request)
    answer["seconds"] = round(time.monotonic() - started, 3)
    print(json.dumps(answer))
    sys.stdout.flush()
