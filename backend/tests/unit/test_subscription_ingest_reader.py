"""The usage-log reader: complete lines only, honest offsets, torn tails left
for the next pass. Pure file IO — no DB."""

import json
from pathlib import Path

from app.domain.usage.subscription_ingest import head_fingerprint, read_new_lines


def _write(path: Path, text: bytes) -> None:
    path.write_bytes(text)


def test_reads_complete_lines_and_advances_offset(tmp_path):
    log = tmp_path / "usage.jsonl"
    a = json.dumps({"project_id": "p", "total_tokens": 1}).encode()
    b = json.dumps({"project_id": "q", "total_tokens": 2}).encode()
    _write(log, a + b"\n" + b + b"\n")

    rows, offset = read_new_lines(log, 0)

    assert [r["total_tokens"] for r in rows] == [1, 2]
    assert offset == log.stat().st_size

    # Nothing new → nothing read, offset unchanged.
    rows, offset2 = read_new_lines(log, offset)
    assert rows == [] and offset2 == offset


def test_torn_tail_line_is_left_for_the_next_pass(tmp_path):
    log = tmp_path / "usage.jsonl"
    full = json.dumps({"total_tokens": 1}).encode() + b"\n"
    torn = b'{"total_tokens": 2'  # no newline: mid-append
    _write(log, full + torn)

    rows, offset = read_new_lines(log, 0)
    assert len(rows) == 1
    assert offset == len(full)

    # The append completes → the same line is read whole, exactly once.
    _write(log, full + torn + b"}\n")
    rows, offset = read_new_lines(log, offset)
    assert [r["total_tokens"] for r in rows] == [2]
    assert offset == log.stat().st_size


def test_garbage_line_is_consumed_not_wedged_on(tmp_path):
    log = tmp_path / "usage.jsonl"
    good = json.dumps({"total_tokens": 3}).encode() + b"\n"
    _write(log, b"not json at all\n" + good)

    rows, offset = read_new_lines(log, 0)

    assert [r["total_tokens"] for r in rows] == [3]
    assert offset == log.stat().st_size


def test_fingerprint_stable_under_append_changed_by_rotation(tmp_path):
    log = tmp_path / "usage.jsonl"
    first = b'{"a": 1}\n'
    _write(log, first)
    fp1 = head_fingerprint(log, len(first))
    with log.open("ab") as fh:
        fh.write(b'{"b": 2}\n')
    # Hashed over the consumed region only, so an append can't change it.
    assert head_fingerprint(log, len(first)) == fp1

    _write(log, b'{"c": 3}\n')  # replaced: new generation
    assert head_fingerprint(log, len(first)) != fp1
