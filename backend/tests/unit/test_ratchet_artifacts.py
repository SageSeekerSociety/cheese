"""一个工件读出来是什么：快照、失败记录，还是读不了。

这一批钉的是「读不懂的时候不要猜」：未知版本、坏 zip、没有快照成员的工件，
都必须落到 `unreadable`/`reason` 上，不能返回一份空 checks 的「快照」——那会
在页面上变成一条「量过了，全 0」的点。
"""

import io
import json
import zipfile
from datetime import UTC, datetime

from app.domain.ratchet.artifacts import read_snapshot
from app.domain.ratchet.ingest import row_for


def _zip(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, blob in members.items():
            archive.writestr(name, blob)
    return buffer.getvalue()


def _snapshot(**overrides) -> bytes:
    payload = {
        "version": 1,
        "collection": "ok",
        "commit": "a" * 40,
        "commit_date": "2026-09-30T08:00:00+00:00",
        "collected_at": "2026-09-30T08:05:00+00:00",
        "run_url": "https://example.invalid/runs/1",
        "checks": [],
        "board": None,
    }
    payload.update(overrides)
    return json.dumps(payload).encode()


def test_a_snapshot_is_read_with_its_version():
    read = read_snapshot(_zip({"ratchet-snapshot.json": _snapshot()}))

    assert read.version == 1
    assert read.payload["commit"] == "a" * 40
    assert read.reason is None
    assert read.unreadable is False


def test_a_failed_collection_is_read_as_a_failure_not_an_empty_snapshot():
    read = read_snapshot(
        _zip({"ratchet-snapshot.failed.txt": b"the ratchet snapshot was not collected"})
    )

    assert read.payload is None
    assert read.version is None
    assert read.unreadable is False
    assert "not collected" in read.reason


def test_an_unknown_version_is_refused_rather_than_guessed_at():
    read = read_snapshot(_zip({"ratchet-snapshot.json": _snapshot(version=99)}))

    assert read.payload is None
    assert read.unreadable is True
    assert "99" in read.reason


def test_a_snapshot_without_a_commit_is_refused():
    read = read_snapshot(_zip({"ratchet-snapshot.json": _snapshot(commit="")}))

    assert read.payload is None
    assert read.unreadable is True


def test_a_zip_without_a_snapshot_member_is_refused():
    read = read_snapshot(_zip({"arch-metrics.json": b"{}"}))

    assert read.payload is None
    assert read.unreadable is True
    assert "ratchet-snapshot.json" in read.reason


def test_something_that_is_not_a_zip_is_refused():
    read = read_snapshot(b"not a zip at all")

    assert read.payload is None
    assert read.unreadable is True


def _artifact(run_id: int = 7, head_sha: str = "b" * 40):
    from app.domain.ratchet.artifacts import Artifact

    return Artifact(
        id=11,
        run_id=run_id,
        head_sha=head_sha,
        created_at=datetime(2026, 9, 30, 9, 0, tzinfo=UTC),
        run_url="https://example.invalid/runs/7",
        expired=False,
    )


def test_a_readable_snapshot_keeps_the_checkers_own_commit_and_times():
    pulled = row_for(
        _artifact(), read_snapshot(_zip({"ratchet-snapshot.json": _snapshot()})), "o/r"
    )

    assert pulled.row["commit_sha"] == "a" * 40
    assert pulled.row["collection"] == "ok"
    assert pulled.row["collected_at"] == datetime(2026, 9, 30, 8, 5, tzinfo=UTC)
    assert pulled.row["workflow_run_id"] == 7
    assert pulled.failed is False


def test_a_failed_artifact_still_becomes_a_row():
    pulled = row_for(
        _artifact(),
        read_snapshot(_zip({"ratchet-snapshot.failed.txt": b"no interpreter"})),
        "o/r",
    )

    assert pulled.row["commit_sha"] == "b" * 40  # the run's commit, from the listing
    assert pulled.row["collection"] == "failed"
    assert pulled.row["payload"] is None
    assert pulled.row["reason"] == "no interpreter"
    assert pulled.failed is True


def test_an_unreadable_artifact_is_labelled_and_not_kept_as_a_snapshot():
    pulled = row_for(_artifact(), read_snapshot(b"junk"), "o/r")

    assert pulled.row["collection"] == "failed"
    assert pulled.row["payload"] is None
    assert pulled.unreadable is True
