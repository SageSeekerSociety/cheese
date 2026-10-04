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

An archive holds the home, the legacy work directory, and the Python
interpreters uv installed into the project's package store, which the home's
virtual environments link to by absolute path: the store is not archived, and a
venv restored on another host would otherwise point at nothing.

Each answers one line of JSON.
"""

import hashlib
import io
import json
import os
import stat
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
# A scratch file this old belongs to no archive or restore still running: the
# platform gives either at most 25 minutes. One a killed run left goes.
STALE_SCRATCH_S = 3600


class TooLarge(RuntimeError):
    pass


class _Counted(io.RawIOBase):
    """A file the archive is written through: it counts and hashes the bytes
    that reach the disk, which are the bytes the bucket will be sent, and stops
    the archive as soon as it passes ``limit`` rather than after the whole home
    has been written out."""

    def __init__(self, raw, limit=ARCHIVE_LIMIT):
        super().__init__()
        self.raw = raw
        self.limit = limit
        self.md5 = hashlib.md5()  # noqa: S324 — compared with the S3 ETag, not a security digest
        self.size = 0

    def writable(self):
        return True

    def write(self, data):
        self.size += len(data)
        if self.size > self.limit:
            raise TooLarge(f"the home is over {self.limit} bytes compressed")
        self.md5.update(data)
        self.raw.write(data)
        return len(data)


def _paths(cleanup, project, resource):
    home, work = cleanup["resource_paths"](Path.home(), project, resource)
    return home, work


def _interpreters(project):
    """Where uv installs Python for the project's sandboxes (``bootstrap``'s
    ``UV_PYTHON_INSTALL_DIR``), which the home's venvs link to."""
    return Path.home() / ".cheese" / "store" / str(project) / "uv-python"


def _scratch(cleanup):
    """The scratch directory, with what killed runs left in it removed."""
    SCRATCH.mkdir(parents=True, exist_ok=True, mode=0o700)
    for entry in SCRATCH.iterdir():
        if time.time() - entry.lstat().st_mtime < STALE_SCRATCH_S:
            continue
        if entry.is_dir() and not entry.is_symlink():
            cleanup["remove_tree"](entry)
        else:
            entry.unlink(missing_ok=True)
    return SCRATCH


def _real_dir(path):
    """Whether ``path`` is a directory itself, not a link to one."""
    try:
        return stat.S_ISDIR(os.lstat(path).st_mode)
    except FileNotFoundError:
        return False


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
    part = _scratch(cleanup) / f"{resource}.tar.gz"
    interpreters = _interpreters(project)

    def keep(info):
        # The sandbox's own /tmp lives in its home and is not kept.
        return None if info.name == "home/.cheese/tmp" else info

    try:
        with part.open("wb") as raw:
            counted = _Counted(raw)
            with tarfile.open(fileobj=counted, mode="w:gz", compresslevel=3) as bundle:
                for name, path in (
                    ("home", home),
                    ("work", work),
                    ("uv-python", interpreters),
                ):
                    if _real_dir(path):
                        # Links are stored as links, never followed; sockets
                        # are skipped, and so is the sandbox's /tmp.
                        bundle.add(path, arcname=name, filter=keep)
            raw.flush()
            os.fsync(raw.fileno())
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
    # Last, as the room's cleanup does: until the home is gone, it is the
    # sandbox's to have written. A restore writes the record again on install.
    cleanup["sandbox_marker"](home).unlink(missing_ok=True)
    return {"dropped": True}


def restore(cleanup, project, resource, url, size, md5):
    home, work = _paths(cleanup, project, resource)
    scratch = _scratch(cleanup)
    part = scratch / f"{resource}.restore.tar.gz"
    staging = scratch / f"{resource}.restore"
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
        if staging.exists() or staging.is_symlink():
            # A failed restore's leftovers; a Go module cache in them is
            # read-only, which only `remove_tree` gets through.
            cleanup["remove_tree"](staging)
        staging.mkdir(mode=0o700)
        with tarfile.open(part, "r:gz") as bundle:
            members = bundle.getmembers()
            if any(
                m.name.split("/", 1)[0] not in ("home", "work", "uv-python")
                for m in members
            ):
                raise RuntimeError("the archive holds something besides a home")
            bundle.extractall(staging, members=members, filter=_as_written)
        # The session wrote this tree. A link where the platform's directory
        # should be would carry what is done to it below — and to it on every
        # later install — to wherever the link points, outside the sandbox.
        for directory in (staging / "home", staging / "home/.cheese"):
            if directory.exists() or directory.is_symlink():
                if not _real_dir(directory):
                    raise RuntimeError(
                        "the archived home's platform directory is not a directory"
                    )
        # The executor that ran here is gone, and its record names a release
        # this host may not have: the next start installs one afresh.
        executor = staging / "home/.cheese/executor"
        if _real_dir(executor):
            cleanup["remove_tree"](executor)
        elif executor.is_symlink():
            executor.unlink()
        _merge_interpreters(staging / "uv-python", _interpreters(project))
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
        if staging.exists() and not staging.is_symlink():
            try:
                cleanup["remove_tree"](staging)
            except Exception:  # noqa: BLE001 — never hide why the restore failed
                pass  # the next restore, or the stale-scratch sweep, retries it


def _merge_interpreters(unpacked, store):
    """Put back the interpreters this host's store lacks. One it has already
    is the same uv build under the same name, and may be in use: kept."""
    if not _real_dir(unpacked):
        return
    store.mkdir(parents=True, exist_ok=True)
    for entry in unpacked.iterdir():
        target = store / entry.name
        if not (target.exists() or target.is_symlink()):
            os.replace(entry, target)


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
