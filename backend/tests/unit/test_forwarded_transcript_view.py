"""The executor's view of its session's transcript: one read-only file.

The mount itself needs a kernel with FUSE; these are the operations, called
directly, so what the kernel would ask is answered the way it must be without a
mount to ask it. The view serves exactly one file — this session's transcript —
and refuses every mutation, because the only process allowed to write it is the
build on the other machine.
"""

import base64
import errno
import os
import stat

import pytest

from app.domain.agent.harness.claude_code.remote_execution.forwarded_transcript import (
    TRANSCRIPT_NAME,
    TranscriptView,
)

WHOLE = b'{"type":"assistant"}\n{"type":"user"}\n'


def _view(chunks=None, missing=False):
    calls = []

    def call(params):
        calls.append(params)
        if missing:
            return {"missing": True}
        offset, size = params["offset"], params["size"]
        return {
            "data": base64.b64encode(WHOLE[offset : offset + size]).decode(),
            "size": len(WHOLE),
            "eof": offset + size >= len(WHOLE),
        }

    view = TranscriptView(call)
    view.calls = calls
    return view


def test_the_one_file_is_read_at_the_offset_and_size_asked_for():
    view = _view()

    assert view.read("/" + TRANSCRIPT_NAME, 10, 4) == WHOLE[4:14]
    assert view.read("/" + TRANSCRIPT_NAME, len(WHOLE), 0) == WHOLE


def test_the_same_bytes_are_not_asked_for_twice():
    """What the kernel re-reads is answered here, not over the wire.

    The kernel asks for pages out of order and asks for some of them again;
    one round trip per request is what makes a `grep` over a long transcript
    cost a minute. A block is kept, so the repeats do not leave this side.
    """
    view = _view()

    assert view.read("/" + TRANSCRIPT_NAME, 6, 0) == WHOLE[:6]
    asked_once = len(view.calls)
    assert view.read("/" + TRANSCRIPT_NAME, 6, 0) == WHOLE[:6]
    assert view.read("/" + TRANSCRIPT_NAME, 4, 2) == WHOLE[2:6]
    assert len(view.calls) == asked_once


def test_what_is_kept_is_bounded_however_long_the_transcript_is():
    """A block past `TRANSCRIPT_BLOCKS_KEPT` drops the oldest.

    A view's memory must not grow with the transcript: a 200 MB one is not
    held twice. Dropping a block is not a correctness problem — reading it
    again fetches it again.
    """
    from app.domain.agent.harness.claude_code.remote_execution import (
        forwarded_transcript,
    )

    view = _view()
    view.known_size = 10 * forwarded_transcript.TRANSCRIPT_BLOCK_BYTES

    for index in range(forwarded_transcript.TRANSCRIPT_BLOCKS_KEPT + 5):
        view._block(index * forwarded_transcript.TRANSCRIPT_BLOCK_BYTES)
    assert len(view.blocks) == forwarded_transcript.TRANSCRIPT_BLOCKS_KEPT


def test_a_full_last_block_is_last_on_a_hit_as_well_as_on_the_fetch():
    """A transcript whose size is a whole number of blocks ends in a full one.

    The fetch says `eof`; re-deriving "last" from the block's length says the
    opposite, and the reader walks past the end of the file asking the relay
    for an offset there. Kept with the block, both agree.
    """
    from app.domain.agent.harness.claude_code.remote_execution import (
        forwarded_transcript,
    )

    size = forwarded_transcript.TRANSCRIPT_BLOCK_BYTES
    whole = b"x" * size

    def call(params):
        return {
            "data": base64.b64encode(whole).decode(),
            "size": size,
            "eof": True,
        }

    view = TranscriptView(call)
    assert view._block(0) == (whole, True)
    assert view._block(0) == (whole, True)


def test_the_file_reports_the_whole_size_reads_do_not_change_it():
    view = _view()

    metadata = view.getattr("/" + TRANSCRIPT_NAME)
    assert stat.S_ISREG(metadata["st_mode"])
    assert metadata["st_size"] == len(WHOLE)


def test_a_path_that_is_not_the_transcript_is_not_there():
    view = _view()

    with pytest.raises(OSError) as other:
        view.read("/elsewhere.jsonl", 10, 0)
    assert other.value.errno == errno.ENOENT


def test_a_transcript_not_written_yet_reads_as_not_there():
    view = _view(missing=True)

    with pytest.raises(OSError) as not_yet:
        view.getattr("/" + TRANSCRIPT_NAME)
    assert not_yet.value.errno == errno.ENOENT


def test_every_write_is_refused():
    view = _view()

    for mutate in (
        lambda: view.open("/" + TRANSCRIPT_NAME, os.O_RDWR),
        lambda: view.write("/" + TRANSCRIPT_NAME, b"x", 0),
        lambda: view.truncate("/" + TRANSCRIPT_NAME, 0),
        lambda: view.unlink("/" + TRANSCRIPT_NAME),
        lambda: view.create("/new.jsonl", 0o600),
        lambda: view.mkdir("/d", 0o700),
        lambda: view.rename("/a", "/b"),
    ):
        with pytest.raises(OSError) as refused:
            mutate()
        assert refused.value.errno == errno.EROFS


def test_the_directory_lists_the_transcript_row():
    view = _view()

    assert view.readdir("/") == [".", "..", TRANSCRIPT_NAME]


def test_a_directory_lists_nothing_until_the_transcript_exists():
    view = _view(missing=True)

    assert view.readdir("/") == [".", ".."]
