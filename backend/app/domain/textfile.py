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
    note: str | None = None,
) -> dict:
    """The unified diff of two texts, in the panel's read shape.

    Split out of `compare_bytes` because not every comparable text arrives as
    the file's own bytes: a `.docx` is a zip, and what two versions of it differ
    by is a difference in words, read out of the package elsewhere (see
    `domain.documents.text`). `identical` and `note` are the caller's to pass —
    it is the side that knows what it compared, and a note saying so is the
    difference between "these versions are the same" and "their words are".
    """
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
    return {"identical": identical, "diff": diff, "note": note}


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


def looks_binary(data: bytes) -> bool:
    """True when `data` must not be handed to a text editor."""
    return decode_text(data) is None


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
    still there so a later write can be rejected); and too large to build a
    body for at all, which decides on the stat and never holds the bytes.

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
        return {**meta, "content": None, "version": version, "too_large": True}
    return bytes_text_payload(target.read_bytes(), path)


def bytes_text_payload(data: bytes, path: str) -> dict:
    """`text_payload` 的同一种回答，给已经在手里的字节：资料库的字节不在一个能
    stat 的路径上，而是从它的存储里读出来的。"""
    meta = {"path": path, "bytes": len(data), "binary": False, "too_large": False}
    if len(data) > MAX_TEXT_BYTES:
        return {
            **meta,
            "content": None,
            "version": content_version(data),
            "too_large": True,
        }
    text = decode_text(data)
    if text is None:
        return {
            **meta,
            "content": None,
            "version": content_version(data),
            "binary": True,
        }
    return {**meta, "content": text, "version": content_version(data)}
