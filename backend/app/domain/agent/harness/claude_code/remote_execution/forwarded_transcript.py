"""A session's transcript, read-only, where its tools run.

Claude Code writes a session's transcript on the session host. After a
compaction it tells the model to read that file for detail, naming the path it
wrote — a path the machine the tools run on does not have, so the model reads
"No such file". This is the other half of the fix (`runner.transcript_chunk`
is the read): a read-only FUSE view, mounted on the executor at exactly the
path the build names, whose bytes come from the session host through the
platform.

Nothing is copied and nothing is written: `read` asks for the bytes it needs
at the offset it needs them, and every mutating operation is refused with
EROFS. The view serves one file — this session's own transcript — so a path
outside it is ENOENT rather than a second door.
"""

from __future__ import annotations

import base64
import errno
import json
import os
import stat
import sys
import threading
from pathlib import Path

#: The one file this view serves, as its mountpoint sees it.
TRANSCRIPT_NAME = "transcript.jsonl"
#: How many bytes one read of the relay asks for, and how big a piece of the
#: file is kept here once it has been read.
#:
#: The kernel does not read its file in order: it asks for pages as it faults
#: them in, asks for the same page again, and reads ahead by a few. Asking the
#: relay once per request means one round trip per request — measured on a
#: 40 MB transcript: 429 round trips for one `grep`, and 43 s at 100 ms of
#: round trip. Asking for a whole block and keeping it turns those repeats
#: into hits: the same `grep`, with the same flags, is 42 round trips and
#: 1.3 s, 0.2 s on the second pass.
TRANSCRIPT_BLOCK_BYTES = 1024 * 1024
#: How many blocks are kept. Past this the oldest is dropped, so the memory a
#: view holds is bounded however large the transcript is — a 200 MB transcript
#: is not held twice. This is the file's own read-ahead in effect, not a
#: cache that has to be kept warm for correctness: a dropped block is read
#: again.
TRANSCRIPT_BLOCKS_KEPT = 64


def _entry(kind, *, size=0):
    """One entry as `fusepy` wants it, read-only: a file is 0o400, a
    directory 0o500, and neither is writable."""
    directory = kind == "directory"
    return {
        "st_mode": (stat.S_IFDIR if directory else stat.S_IFREG)
        | (0o500 if directory else 0o400),
        "st_nlink": 2 if directory else 1,
        "st_size": size,
        "st_mtime_ns": 0,
        "st_mtime": 0,
        "st_ctime": 0,
        "st_atime": 0,
    }


class TranscriptView:
    """The operations `fusepy` calls, over one transcript on another machine.

    ``call`` is one chunk, as the relay answers it: ``{"data": <base64>,
    "size": <whole file>, "eof": <bool>}``, or ``{"missing": True}`` before the
    build has written it. Kept separate from the FUSE class so the operations
    can be tested without a kernel to mount on.
    """

    def __init__(self, call):
        self.call = call
        self.known_size = None
        #: Block start offset -> ``(bytes, last)``, oldest first. Bounded by
        #: `TRANSCRIPT_BLOCKS_KEPT`, so a view's memory does not grow with the
        #: transcript — see `_block` for why it is here at all. `last` is kept
        #: with the block rather than re-derived from its length: a transcript
        #: whose size is a whole number of blocks ends in a full one, and
        #: re-deriving would make a hit disagree with the fetch that filled it.
        self.blocks = {}
        self.lock = threading.Lock()

    def _stat_size(self):
        if self.known_size is None:
            # A normal read, not the relay's zero-size question: that one
            # answers a *path* (`transcript_path`), which this side has no use
            # for. One block here also warms the first block a read needs.
            # Not written yet is size 0, which the callers read as "not there".
            try:
                self._block(0)
            except OSError:
                self.known_size = 0
        return self.known_size or 0

    def _block(self, start):
        """The block at `start`, from here or from the relay.

        The kernel does not read in order and asks for the same pages more
        than once, so a request answered straight from the relay is a round
        trip each time; a block kept here answers those repeats. The oldest is
        dropped past `TRANSCRIPT_BLOCKS_KEPT`, so what a view holds is bounded
        however long the transcript gets. Under a lock because `fusepy` runs
        these in threads (`nothreads=False`), and two threads reading the same
        block at once would otherwise each ask for it.
        """
        with self.lock:
            kept = self.blocks.get(start)
            if kept is not None:
                return kept
        answer = self.call({"offset": start, "size": TRANSCRIPT_BLOCK_BYTES})
        if answer.get("missing"):
            raise OSError(errno.ENOENT, TRANSCRIPT_NAME)
        block = base64.b64decode(answer["data"])
        last = bool(answer.get("eof")) or len(block) < TRANSCRIPT_BLOCK_BYTES
        self.known_size = int(answer["size"])
        with self.lock:
            self.blocks[start] = (block, last)
            while len(self.blocks) > TRANSCRIPT_BLOCKS_KEPT:
                del self.blocks[next(iter(self.blocks))]
        return block, last

    def _check(self, path):
        name = self._name(path)
        if name not in ("", TRANSCRIPT_NAME):
            raise OSError(errno.ENOENT, name)

    @staticmethod
    def _name(path):
        return path.lstrip("/").rstrip("/")

    def getattr(self, path, fh=None):
        name = self._name(path)
        if name == "":
            return _entry("directory")
        if name != TRANSCRIPT_NAME:
            raise OSError(errno.ENOENT, name)
        size = self._stat_size()
        if size == 0:
            # Not written yet is "not there", as it is for the build's own
            # tools: a transcript appears once Claude Code has said something.
            raise OSError(errno.ENOENT, name)
        return _entry("file", size=size)

    def readdir(self, path, fh=None):
        name = self._name(path)
        if name == "":
            names = [".", ".."]
            try:
                self.getattr("/" + TRANSCRIPT_NAME)
            except OSError:
                return names
            return [*names, TRANSCRIPT_NAME]
        raise OSError(errno.ENOTDIR, name)

    def open(self, path, flags):
        self._check(path)
        if flags & (os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_TRUNC):
            raise OSError(errno.EROFS, self._name(path))
        self.getattr(path)
        return 0

    def read(self, path, size, offset, fh=None):
        self._check(path)
        if not self._name(path):
            raise OSError(errno.EISDIR, path)
        if size <= 0:
            return b""
        start = offset - (offset % TRANSCRIPT_BLOCK_BYTES)
        end = offset + size
        pieces = bytearray()
        at = start
        while at < end:
            block, last = self._block(at)
            pieces += block
            if last or not block:
                break
            at += TRANSCRIPT_BLOCK_BYTES
        first = offset - start
        return bytes(pieces[first : first + size])

    # Every mutation is refused: this is the session's history, and the only
    # process allowed to write it is the build on the other machine.
    def _refuse(self, path):
        raise OSError(errno.EROFS, path)

    def create(self, path, mode, fh=None):
        self._refuse(path)

    def write(self, path, data, offset, fh=None):
        self._refuse(path)

    def truncate(self, path, size, fh=None):
        self._refuse(path)

    def unlink(self, path):
        self._refuse(path)

    def rmdir(self, path):
        self._refuse(path)

    def mkdir(self, path, mode):
        self._refuse(path)

    def rename(self, old, new):
        self._refuse(old)

    def symlink(self, target, path):
        self._refuse(path)

    def link(self, target, path):
        self._refuse(path)

    def chmod(self, path, mode):
        self._refuse(path)

    def chown(self, path, uid, gid):
        self._refuse(path)

    def utimens(self, path, times=None):
        self._refuse(path)

    def access(self, path, mode):
        self.getattr(path)
        if mode & os.W_OK:
            raise OSError(errno.EROFS, path)
        return 0

    def statfs(self, path):
        return {"f_bsize": 4096, "f_frsize": 4096, "f_blocks": 0, "f_bfree": 0}


class FuseTranscript(TranscriptView):
    """The same operations, as `fusepy` wants them: a class it instantiates."""

    def __init__(self, call):
        TranscriptView.__init__(self, call)


def mount(target_path, mountpoint):
    """Serve `target_path`'s transcript at `mountpoint`, until killed.

    `target_path` is the session's target file, which the relay's credential
    is read from (`client.py`). The mountpoint is the path the build named, so
    the model's read lands here.
    """

    # deferred-import: deferred: bare name, shipped beside this file
    from client import RemoteClient

    # deferred-import: fusepy needs libfuse, which is not installed everywhere
    from fuse import FUSE, Operations

    target_path = Path(target_path)
    client = RemoteClient(json.loads(target_path.read_text()))

    def call(params):
        answer = client.transcript_chunk(params)
        return answer

    class Serve(FuseTranscript, Operations):
        pass

    FUSE(
        Serve(call),
        mountpoint,
        foreground=True,
        ro=True,
        nothreads=False,
        direct_io=True,
        attr_timeout=0,
        entry_timeout=0,
        negative_timeout=0,
    )


if __name__ == "__main__":
    mount(sys.argv[1], sys.argv[2])
