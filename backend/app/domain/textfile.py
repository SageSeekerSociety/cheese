"""Text/binary discrimination, content versioning, and the panel's read payload.

Shared by the two halves the workspace split into — the project's git source
(:mod:`app.domain.repository.service`) and the library
(:mod:`app.domain.library.service`) — which is why it sits beside them rather
than inside either.

The 文件 panel edits worktree files in Monaco, and two questions have to be
answered from the file's CONTENT rather than from its name:

1. Can this file survive a text round-trip at all? Deciding by extension is how a
   2048-byte executable came back as 4096 bytes of U+FFFD: every byte utf-8 could
   not decode was replaced on read, and the save wrote the replacements to disk.
2. Is the copy being saved still the copy that was read? Without an answer, a
   human pressing 保存 silently erases whatever 芝士 wrote to the same file in
   between.

Both helpers are pure functions over bytes so the backend and the compute-node
daemon (cheesed) can share them instead of drifting apart.
"""

import hashlib
from difflib import unified_diff
from pathlib import Path

from app.core.errors import ValidationError

# Above this, the panel offers download instead of an editor. The old unbounded
# read turned a 52MB executable into a 127MB JSON body that froze the browser.
MAX_TEXT_BYTES = 1024 * 1024

# How much of a text file the PREVIEW hands a reader, and why it is not
# `MAX_TEXT_BYTES`:
#
# The two numbers answer different questions. An editor needs every byte or
# refuses — a text editor seeded with a prefix writes that prefix back over the
# file. A reader wants the top of it. Serving them the same ceiling is what made
# a 3 MB log report itself as "not text": the only answers available were "the
# whole thing" and "nothing".
#
# Both limits below follow the measured knee rather than a round number. What
# costs the preview panel is the LINE COUNT, not the bytes — every line is its
# own element — so a cap on bytes alone does not bound the work: 256 KB of
# 4-byte lines is 52,428 lines and scrolls at 245 ms a frame, while the same
# bytes as 120-byte lines is 2,169 lines at 17 ms. Measured in the panel
# (546 px, 13 px mono): 4,000 lines 25 ms, 5,000 lines 31 ms, 8,000 lines 49 ms.
# So the head is the smaller of the two: the byte cut bounds long-line files,
# the line cut bounds short-line ones. Raise either only with a fresh
# measurement.
PREVIEW_TEXT_BYTES = 256 * 1024
PREVIEW_TEXT_LINES = 4_000


# The longest UTF-8 sequence is 4 bytes, so a prefix cut anywhere inside one
# leaves at most that many bytes to drop before it decodes.
_MULTIBYTE = 4


def head_text(data: bytes) -> str | None:
    """The reader's copy of a text file's opening, or None when it is not text.

    ``data`` is a byte prefix of a longer file, so the cut may land inside the
    last line, and even inside a multi-byte UTF-8 sequence. Three things happen
    here, in order: keep it to whole lines, keep it to a line budget, and make
    sure the bytes left actually decode.

    The line budget is the one that bounds the reader's cost — a prefix of
    short lines can be far more lines than the preview can lay out. It never
    cuts below ``PREVIEW_TEXT_MIN_BYTES``, because one long line is worth more
    than several short ones and the byte ceiling already bounds that side.
    """
    if b"\x00" in data[:_SNIFF_BYTES]:
        return None
    limit = min(len(data), PREVIEW_TEXT_BYTES)
    # Cut back to the last newline. A newline byte never occurs inside a
    # multi-byte sequence, so that alone settles the tail, and every boundary
    # below stays on a newline so the result always decodes.
    cut = data.rfind(b"\n", 0, limit)
    if cut >= 0:
        end = cut + 1
        # Whichever of the two budgets is smaller. Files with long lines are
        # already bounded by the byte cut — fewer than `PREVIEW_TEXT_LINES` of
        # them fit — so this only shortens the short-line ones, which is the
        # side the byte cap cannot bound.
        line_cut = _nth_newline(data, PREVIEW_TEXT_LINES)
        if line_cut is not None and line_cut < end:
            end = line_cut
        return data[:end].decode("utf-8")
    # No newline in the prefix at all — a minified JSON, a long first line:
    # drop the trailing bytes of whatever sequence the cut landed in instead.
    # Without this a one-line file reported itself as non-text, which is exactly
    # the answer this ceiling change exists to stop giving.
    for drop in range(_MULTIBYTE):
        try:
            return data[: limit - drop].decode("utf-8")
        except UnicodeDecodeError:
            continue
    return None


def _nth_newline(data: bytes, n: int) -> int | None:
    """The offset just past the ``n``-th newline, or None when there are fewer.

    Bounds the head by lines rather than bytes: a prefix of short lines can hold
    many more lines than the panel can lay out, and the line count — not the
    byte count — is what costs it.
    """
    at = -1
    for _ in range(n):
        at = data.find(b"\n", at + 1)
        if at < 0:
            return None
    return at + 1


def compare_bytes(before: bytes, after: bytes, left: str, right: str) -> dict:
    """Compare retained bytes; an unavailable text diff never means equality."""
    identical = before == after
    if max(len(before), len(after)) > MAX_TEXT_BYTES:
        return {
            "identical": identical,
            "diff": None,
            "note": "oversized",
        }
    old, new = decode_text(before), decode_text(after)
    if old is None or new is None:
        return {
            "identical": identical,
            "diff": None,
            "note": "binary",
        }
    return text_comparison(old, new, left, right, identical=identical)


def text_comparison(
    old: str,
    new: str,
    left: str,
    right: str,
    *,
    identical: bool,
) -> dict:
    """The unified diff of two texts, in the panel's read shape."""
    # Keep line endings: a final newline is part of the delivered content too.
    lines = unified_diff(
        old.splitlines(keepends=True),
        new.splitlines(keepends=True),
        fromfile=left,
        tofile=right,
    )
    diff = "".join(
        line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
        for line in lines
    )
    return {"identical": identical, "diff": diff, "note": None}


# A NUL in the first block is the classic binary signal, and cheap to check
# before attempting a decode of the whole file.
_SNIFF_BYTES = 8192


def decode_text(data: bytes) -> str | None:
    """The file's text, or None when it cannot be edited as text safely.

    Strict utf-8 over the WHOLE buffer, not a sample: a file whose first 8KB
    decode cleanly can still hold invalid bytes further in, and decoding that
    with ``errors="replace"`` corrupts it the moment it is written back. Text in
    a non-utf-8 encoding (GBK, latin-1) is therefore reported as binary too —
    read-only is the honest answer, since saving it would transcode it.
    """
    if b"\x00" in data[:_SNIFF_BYTES]:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def content_version(data: bytes) -> str:
    """Stable id for one exact file content.

    A read hands this back to the client, which echoes it on save; a mismatch
    means someone else wrote in between. Content hash rather than mtime so that
    two writes of identical content are not reported as a conflict, and so the
    id survives a worktree being re-materialised.
    """
    return hashlib.sha256(data).hexdigest()[:16]


def file_version(target: Path) -> str:
    """`content_version` of a file on disk, hashed in chunks.

    Same id as `content_version(target.read_bytes())` — this exists so a caller
    that must not hold the file in memory can still name the exact bytes it
    found. A copy of the bytes costs one streamed pass, never one giant buffer.
    """
    with target.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()[:16]


def text_payload(target: Path, path: str) -> dict:
    """One file read the way the 文件 panel wants it, or the reason it cannot be.

    Three answers, and the caller does not get to tell them apart by guessing:
    the text plus its version; binary (`content` is None, and the version is
    still there so a later write can be rejected); and too large to build an
    editor body for, which decides on the stat and never holds the whole file.

    `head` is the one field that is not about editing. It is what a READER
    gets — the whole text when the file fits, the prefix otherwise (see
    `PREVIEW_TEXT_BYTES`), `None` only when the bytes are not text at all.
    `content` keeps its single meaning, the WHOLE file or nothing, because a
    text editor seeded with a prefix writes the prefix back over the file;
    keeping the reader's copy in a field of its own is what makes that
    impossible, and it is why a too-large file stopped being unreadable.

    The version is a reading of the file, not a by-product of having its text:
    every answer carries one, including the too-large one. A caller that gets
    `version: None` learns the file itself is gone, nothing weaker. The preview
    panel is why — it opens a sandbox for the version a reader was shown, and a
    reader told `null` for a large file could only ever be refused.
    """
    if not target.is_file():
        raise ValidationError("file not found")
    size = target.stat().st_size
    meta = {"path": path, "bytes": size, "binary": False, "too_large": False}
    if size > MAX_TEXT_BYTES:
        # Deliberately not read into memory: the point is to not build the giant
        # body. The version still has to be honest, so the bytes are streamed
        # past the hasher — the same pass `preview_file_version` makes for the
        # same file. A file that vanished under us reads as "no version", which
        # is what the caller would have got from the stat a moment earlier.
        try:
            version = file_version(target)
        except OSError:
            version = None
        with target.open("rb") as source:
            head = head_text(source.read(PREVIEW_TEXT_BYTES))
        return {
            **meta,
            "content": None,
            "head": head,
            "version": version,
            "too_large": True,
        }
    return bytes_text_payload(target.read_bytes(), path)


def bytes_text_payload(data: bytes, path: str) -> dict:
    """`text_payload` 的同一种回答，给已经在手里的字节：资料库的字节不在一个能
    stat 的路径上，而是从它的存储里读出来的。"""
    meta = {"path": path, "bytes": len(data), "binary": False, "too_large": False}
    if len(data) > MAX_TEXT_BYTES:
        return {
            **meta,
            "content": None,
            "head": head_text(data[:PREVIEW_TEXT_BYTES]),
            "version": content_version(data),
            "too_large": True,
        }
    text = decode_text(data)
    if text is None:
        return {
            **meta,
            "content": None,
            "head": None,
            "version": content_version(data),
            "binary": True,
        }
    return {**meta, "content": text, "head": text, "version": content_version(data)}
