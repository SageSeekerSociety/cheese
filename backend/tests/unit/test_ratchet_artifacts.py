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


def _zip(members: dict[str, bytes], *, compress: int = zipfile.ZIP_STORED) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=compress) as archive:
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


def test_an_unreadable_artifact_does_not_enter_the_archive():
    pulled = row_for(_artifact(), read_snapshot(b"junk"), "o/r")

    # 读不了**不是**一次失败的采集：这次 run 可能采得好好的，是读的人看不懂。存成
    # failed 会往序列里放一个没人量过的点，而且因为存过的 run 不再拉，这个错会冻在
    # 归档里 —— 以后读得懂了也补不回来。所以这里什么都不存，下一次拉还会再拿到它。
    assert pulled.row is None
    assert pulled.unreadable is True
    assert pulled.failed is False


def test_an_unknown_version_does_not_enter_the_archive_either():
    read = read_snapshot(_zip({"ratchet-snapshot.json": _snapshot(version=99)}))

    assert read.unreadable is True
    assert row_for(_artifact(), read, "o/r").row is None


def test_a_snapshot_member_that_expands_past_the_cap_is_refused():
    # 压缩后几 KB、解压后 9 MB 的成员：工件本身远在 32 MB 那道闸门之内，涨的是解压
    # 这一步。不设这道上限，`ZipFile.read` 会照着头部声明的长度在内存里把它拼出来。
    blob = _zip(
        {
            "ratchet-snapshot.json": json.dumps(
                {"version": 1, "pad": "0" * (9 << 20)}
            ).encode()
        },
        compress=zipfile.ZIP_DEFLATED,
    )
    assert len(blob) < 100_000  # 证据本身：小工件，大成员

    read = read_snapshot(blob)

    assert read.payload is None
    assert read.unreadable is True
    assert "超过" in read.reason
