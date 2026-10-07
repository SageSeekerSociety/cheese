"""What the platform does to one session's home on a cloud host.

Piped to ``python3 -`` on the host (``lifecycle.program``) after the source of
``agent/resource_cleanup.py``, whose helpers arrive as ``cleanup``: this file
runs there with nothing of ours importable, like that one.

- ``sleep``: stop the session's sandbox. Its executor is asked to stop, then
  whatever still holds the home — a dev server, a background command — is
  ended. The home stays on the disk.
- ``archive``: write the stopped home to one ``.tar.gz`` and send it to the
  bucket in parts, through the URLs the platform signed for one multipart
  upload. The work runs in a process of its own that outlives the call, and
  keeps what it has done in a job directory: the archive written, each part
  the bucket took. Asked again for the same upload — after a deploy restarted
  the platform, or once a call's wait ran out — it carries on from there
  rather than from nothing. Answers ``pending`` while that work is not done;
  once it is, the bytes' size and MD5, each part's MD5 and the ETag the
  bucket gave it, which the platform completes the upload with and compares
  with what the bucket stored before it lets the home go, and whether
  everything in the home is on its remote: the room's cleanup deletes an
  archive only when it was, since an archive is where unpushed work goes once
  its home leaves the host. Also answers the core dumps it left out
  (``skipped``). A home not on the host answers ``absent``.
- ``drop``: delete the home from this host, once its archive is verified.
- ``restore``: GET the archive, check it is the one that was written, and
  unpack it as the session's home, replacing any older copy left here.

An archive holds the home, the legacy work directory, and the Python
interpreters uv installed into the project's package store, which the home's
virtual environments link to by absolute path: the store is not archived, and a
venv restored on another host would otherwise point at nothing.

Each answers one line of JSON.
"""

import fcntl
import hashlib
import http.client
import io
import json
import os
import signal
import stat
import sys
import tarfile
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path

# A home past this, compressed, stays on its host.
ARCHIVE_LIMIT = 4 * 1024**3
# Where an archive is written before upload, and unpacked before it replaces a
# home: on the same disk as the homes, so the final rename is a rename.
SCRATCH = Path.home() / ".cheese" / "archives"
CHUNK = 1024 * 1024
# A scratch file this old belongs to no restore still running: the platform
# gives one at most 25 minutes. One a killed run left goes.
STALE_SCRATCH_S = 3600
# An archive job no process is working on is kept this long: past the six
# hours the platform waits after a failed archive (``lifecycle.ARCHIVE_RETRY``),
# so the next try resumes it, and not for days, as the archive it wrote is the
# size of the home.
STALE_JOB_S = 86400
# Each part is tried this many times, a dropped connection or a 5xx from the
# bucket costing that part alone (S3's guidance for uploads over a spotty
# network); a socket that sends nothing for PART_TIMEOUT_S is a dropped one.
PART_TRIES = 5
PART_TIMEOUT_S = 120


class TooLarge(RuntimeError):
    pass


class _Counted(io.RawIOBase):
    """A file the archive is written through: it counts and hashes the bytes
    that reach the disk, which are the bytes the bucket will be sent, and stops
    the archive as soon as it passes ``limit`` rather than after the whole home
    has been written out."""

    def __init__(self, raw, limit=ARCHIVE_LIMIT, part_size=None):
        super().__init__()
        self.raw = raw
        self.limit = limit
        self.md5 = hashlib.md5()  # noqa: S324 — compared with the S3 ETag, not a security digest
        self.size = 0
        # Each part's MD5, in order: the bucket's ETag of a multipart object is
        # the MD5 of these.
        self.part_size = part_size or limit
        self.parts = []
        self._part = hashlib.md5()  # noqa: S324
        self._part_size = 0

    def writable(self):
        return True

    def write(self, data):
        self.size += len(data)
        if self.size > self.limit:
            raise TooLarge(f"the home is over {self.limit} bytes compressed")
        self.md5.update(data)
        view = memoryview(data)
        while view:
            take = view[: self.part_size - self._part_size]
            self._part.update(take)
            self._part_size += len(take)
            view = view[len(take) :]
            if self._part_size == self.part_size:
                self._close_part()
        self.raw.write(data)
        return len(data)

    def _close_part(self):
        self.parts.append(self._part.hexdigest())
        self._part = hashlib.md5()  # noqa: S324
        self._part_size = 0

    def finish(self):
        """The part MD5s, the last part included."""
        if self._part_size or not self.parts:
            self._close_part()
        return self.parts


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
        job = entry.name.endswith(".job")
        age = time.time() - entry.lstat().st_mtime
        if age < (STALE_JOB_S if job else STALE_SCRATCH_S):
            continue
        if job and _working(entry):
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


def archive(cleanup, project, resource, job, upload, urls, part_size, wait):
    """Start, or come back to, the job that archives the home under ``job``
    (its object's name) through multipart upload ``upload``, and wait up to
    ``wait`` seconds for it. ``upload`` None asks only for the answer of a job
    already done: the platform found its object in the bucket."""
    home, work = _paths(cleanup, project, resource)
    folder = _scratch(cleanup) / f"{resource}.job"
    state = _read(folder / "job.json")
    if state is not None and state.get("job") != job:
        # A job for an earlier sleep of this home: the home has been used
        # since, so neither its archive nor its parts are this one's.
        _end_job(cleanup, folder)
        state = None
    done = _read(folder / "done.json")
    if done is not None and upload is not None and done["upload"] != upload:
        # Its upload is gone from the bucket, and its parts with it.
        _end_job(cleanup, folder)
        state = done = None
    if done is not None:
        return done
    if upload is None:
        return {"unknown": True}
    if state is None:
        if not home.exists() and not work.exists():
            # Nothing of the session's is here — a room's cleanup removed it —
            # so there is nothing to archive and nothing for this host to keep.
            return {"absent": True}
        folder.mkdir(mode=0o700)
        state = {"job": job, "part_size": part_size}
    if state.get("upload") != upload:
        # The parts sent belong to the upload they were sent to.
        _stop_worker(folder)
        (folder / "parts.json").unlink(missing_ok=True)
        state["upload"] = upload
    _write(folder / "job.json", state)
    _write(folder / "urls.json", urls)
    deadline = time.monotonic() + wait
    while True:
        done = _read(folder / "done.json")
        if done is not None:
            return done
        if not _working(folder):
            failed = _read(folder / "failed.json")
            if failed is not None:
                # Said once; the next try starts the work again from where it
                # got to.
                (folder / "failed.json").unlink()
                raise RuntimeError(failed["error"])
            _start(cleanup, project, resource, folder)
        if time.monotonic() >= deadline:
            return {"pending": _progress(folder)}
        time.sleep(0.2)


def _read(path):
    """What ``path`` holds, or None when nothing was written there."""
    try:
        return _load(path)
    except FileNotFoundError:
        return None


def _load(path):
    return json.loads(path.read_text())


def _write(path, value):
    part = path.with_name(path.name + ".part")
    part.write_text(json.dumps(value))
    os.replace(part, path)


def _working(folder):
    """Whether a process holds the job: its worker takes the lock for as long
    as it runs, and the kernel lets it go however that process ends."""
    try:
        lock = os.open(folder / "lock", os.O_RDONLY)
    except FileNotFoundError:
        return False
    try:
        fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    finally:
        os.close(lock)
    return False


def _stop_worker(folder):
    if not _working(folder):
        return
    try:
        pid = int((folder / "pid").read_text())
    except (FileNotFoundError, ValueError):
        pid = None
    for sig in (signal.SIGTERM, signal.SIGKILL):
        if pid is not None:
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                pass
        for _ in range(50):
            if not _working(folder):
                return
            time.sleep(0.2)
    raise RuntimeError("an earlier archive of this home would not stop")


def _end_job(cleanup, folder):
    if folder.exists():
        _stop_worker(folder)
        cleanup["remove_tree"](folder)


def _start(cleanup, project, resource, folder):
    """Run the job in a process of its own, detached from this call: the
    connector ends a call's process at its timeout, and the platform may stop
    waiting on the call before then."""
    sys.stdout.flush()
    child = os.fork()
    if child:
        os.waitpid(child, 0)
        return
    try:
        os.setsid()
        if os.fork():
            os._exit(0)
        null = os.open(os.devnull, os.O_RDWR)
        for fd in (0, 1, 2):
            os.dup2(null, fd)
        _work(cleanup, project, resource, folder)
    finally:
        os._exit(0)


def _work(cleanup, project, resource, folder):
    lock = os.open(folder / "lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return  # another worker has it
    (folder / "pid").write_text(str(os.getpid()))
    try:
        state = _load(folder / "job.json")
        written = _read(folder / "written.json")
        if written is None:
            written = _write_archive(cleanup, project, resource, folder, state)
        etags = {}
        parts = _read(folder / "parts.json")
        if parts is not None and parts["upload"] == state["upload"]:
            etags = parts["etags"]
        size, part_size = written["size"], state["part_size"]
        for number in range(1, len(written["parts"]) + 1):
            if str(number) in etags:
                continue
            offset = (number - 1) * part_size
            etags[str(number)] = _put_part(
                folder, number, offset, min(part_size, size - offset)
            )
            _write(folder / "parts.json", {"upload": state["upload"], "etags": etags})
        _write(
            folder / "done.json",
            {
                "upload": state["upload"],
                "size": size,
                "md5": written["md5"],
                "parts": written["parts"],
                "etags": [etags[str(n)] for n in range(1, len(written["parts"]) + 1)],
                "published": written["published"],
                "skipped": written["skipped"],
            },
        )
        (folder / "archive.tar.gz").unlink(missing_ok=True)
    except BaseException:  # noqa: BLE001 — told to whoever asks next
        _write(folder / "failed.json", {"error": traceback.format_exc()[-1500:]})


def _write_archive(cleanup, project, resource, folder, state):
    home, work = _paths(cleanup, project, resource)
    # A still image: nothing may be writing while it is taken.
    cleanup["check_no_writers"]([home, work])
    published = _published(cleanup, home, work, resource)
    target = folder / "archive.tar.gz"
    part = folder / "archive.tar.gz.part"
    interpreters = _interpreters(project)
    urls = _load(folder / "urls.json")
    limit = min(ARCHIVE_LIMIT, state["part_size"] * len(urls))

    roots = {"home": home, "work": work, "uv-python": interpreters}
    skipped = []

    def keep(info):
        # The sandbox's own /tmp lives in its home and is not kept.
        if info.name == "home/.cheese/tmp":
            return None
        # A crashed process's core dump is written under the crashing uid,
        # often unreadable here, and is nobody's work; reading it would fail
        # the whole archive on every try. Any other unreadable file still
        # fails it, so no work is left behind unnoticed.
        if info.isfile() and _crash_dump(info.name):
            top, _, rest = info.name.partition("/")
            if not os.access(roots[top] / rest, os.R_OK):
                skipped.append(info.name)
                return None
        return info

    try:
        with part.open("wb") as raw:
            counted = _Counted(raw, limit, state["part_size"])
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
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    os.replace(part, target)
    written = {
        "size": counted.size,
        "md5": counted.md5.hexdigest(),
        "parts": counted.finish(),
        "published": published,
        "skipped": skipped,
    }
    _write(folder / "written.json", written)
    return written


def _put_part(folder, number, offset, length):
    """PUT one part to the URL signed for it; answers the bucket's ETag."""
    with (folder / "archive.tar.gz").open("rb") as source:
        source.seek(offset)
        body = source.read(length)
    for attempt in range(PART_TRIES):
        # Read each time: a later call may have signed the URLs afresh.
        url = _load(folder / "urls.json")[number - 1]
        request = urllib.request.Request(
            url,
            data=body,
            method="PUT",
            headers={"Content-Length": str(length)},
        )
        try:
            with urllib.request.urlopen(request, timeout=PART_TIMEOUT_S) as response:
                response.read()
                return response.headers["ETag"].strip('"')
        except urllib.error.HTTPError as exc:
            if exc.code < 500 and exc.code not in (408, 429):
                raise
            if attempt == PART_TRIES - 1:
                raise
        # A connection the bucket closed mid-answer: a reset is an OSError,
        # a cut-short body or status line these two.
        except (
            urllib.error.URLError,
            OSError,
            http.client.IncompleteRead,
            http.client.BadStatusLine,
        ):
            if attempt == PART_TRIES - 1:
                raise
        time.sleep(2**attempt)
    raise AssertionError("unreachable")


def _progress(folder):
    written = _read(folder / "written.json")
    parts = _read(folder / "parts.json")
    return {
        "written": written is not None,
        "parts": len(parts["etags"]) if parts else 0,
        "of": len(written["parts"]) if written else None,
    }


def _crash_dump(name):
    """Whether ``name`` is what the kernel calls a core dump: ``core`` or
    ``core.<pid>``."""
    base = name.rsplit("/", 1)[-1]
    return base == "core" or (base.startswith("core.") and base[5:].isdigit())


def _published(cleanup, home, work, resource):
    """Whether the stopped home holds nothing its remote does not: the check
    the room's cleanup makes before it deletes a home (``publication`` in
    ``resource_cleanup.main``), made here because once archived the home is on
    no machine to be asked. Any refusal, or a check that cannot be made, is
    an answer of no."""
    try:
        if cleanup["session_target"](home, resource) is None:
            cleanup["check_resource_publication"](home, work)
    except RuntimeError:
        return False
    return True


def drop(cleanup, project, resource):
    home, work = _paths(cleanup, project, resource)
    cleanup["check_no_writers"]([home, work])
    for path in (work, home, cleanup["resource_tmp"](resource)):
        if path.exists():
            cleanup["remove_tree"](path)
    _end_job(cleanup, _scratch(cleanup) / f"{resource}.job")
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
