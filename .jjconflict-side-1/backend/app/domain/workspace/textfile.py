"""Text/binary discrimination and content versioning for workspace file IO.

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

# Above this, the panel offers download instead of an editor. The old unbounded
# read turned a 52MB executable into a 127MB JSON body that froze the browser.
MAX_TEXT_BYTES = 1024 * 1024

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
