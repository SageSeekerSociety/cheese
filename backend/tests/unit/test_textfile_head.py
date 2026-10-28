"""A text file too big for an editor is still something a reader can read.

The two answers the panel used to give were "the whole file" and "nothing",
so a 3 MB log reported itself as 「这个文件不是文本」 — an untrue sentence
about a plain text file. These pin the split: `content` keeps meaning the whole
file (editors need that, and a panel seeded with a prefix writes the prefix
back), `head` is what a reader gets.
"""

from pathlib import Path

import pytest

from app.domain.textfile import (
    MAX_TEXT_BYTES,
    PREVIEW_TEXT_BYTES,
    head_text,
    text_payload,
)


def write(tmp_path: Path, name: str, body: bytes) -> Path:
    target = tmp_path / name
    target.write_bytes(body)
    return target


def log_of(lines: int) -> bytes:
    return b"".join(b"line %d of a log\n" % i for i in range(lines))


def test_a_large_log_gives_the_reader_its_opening_instead_of_nothing(tmp_path):
    payload = text_payload(write(tmp_path, "big.log", log_of(200_000)), "big.log")

    assert payload["too_large"] is True
    assert payload["head"] is not None
    assert payload["head"].startswith("line 0 of a log\n")
    assert len(payload["head"].encode()) <= PREVIEW_TEXT_BYTES


def test_the_whole_file_stays_absent_so_it_can_never_be_saved_back(tmp_path):
    payload = text_payload(write(tmp_path, "big.log", log_of(200_000)), "big.log")

    # `content` is what the editor is seeded with; a prefix here would be written
    # straight back over the file.
    assert payload["content"] is None


def test_a_large_binary_gives_the_reader_nothing_to_read(tmp_path):
    payload = text_payload(write(tmp_path, "blob.bin", b"\x00" * (MAX_TEXT_BYTES + 1)), "blob.bin")

    # `binary` stays False for anything over the ceiling: the older answer never
    # looked at the bytes either, and `too_large` already carries the reason.
    # What a reader can tell apart is `head`: null means there is nothing to read.
    assert payload["head"] is None
    assert payload["too_large"] is True


def test_a_file_that_fits_is_unchanged(tmp_path):
    payload = text_payload(write(tmp_path, "small.txt", b"hello\nworld\n"), "small.txt")

    assert payload["too_large"] is False
    assert payload["content"] == "hello\nworld\n"
    assert payload["head"] == payload["content"]


def test_a_one_line_file_larger_than_the_head_still_reads_as_text():
    # A minified JSON has no newline in its first PREVIEW_TEXT_BYTES, so cutting
    # at a newline cannot apply; what is left must not be mistaken for binary.
    data = b'{"a":1},' * (PREVIEW_TEXT_BYTES // 4)

    head = head_text(data)

    assert head is not None
    assert head.startswith('{"a":1},')


def test_a_cut_inside_a_character_does_not_make_it_binary():
    # The prefix ends one byte into a 3-byte character.
    data = (b"a" * (PREVIEW_TEXT_BYTES - 1) + "中".encode())[:PREVIEW_TEXT_BYTES]

    head = head_text(data)

    assert head is not None
    assert head.endswith("a")


@pytest.mark.parametrize("unit", ["中", "𝄞"])
def test_a_cut_inside_any_width_of_character_still_decodes(unit):
    body = (b"\n" + unit.encode() * 2) * 20_000
    data = body[:PREVIEW_TEXT_BYTES]

    head = head_text(data)

    assert head is not None
    # Whole lines: the cut backed off to the last newline, never mid-character.
    assert head.endswith("\n")
