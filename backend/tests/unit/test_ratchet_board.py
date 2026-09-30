"""棘轮页的走势：哪些点算数、哪里断开、方向怎么读。

这一批钉的都是「算错了也看不出来」的地方：

* 采集失败、或某道检查这次没跑，是 `not_collected` + `actual = null`，**不是 0**。
  一个 0 读起来是「量过了，没问题」——正是这一整套安排要防的那件事。
* 规则指纹一变就开新段，方向只看最后一段；跨段相减会把「判定变了」画成「还了债」。
* 「新增豁免」只在冻结数上升、且指纹没变时标出；那是有人决定冻结，不是发现了债。
* 方向用检查自己声明的 `better`；没有它就不给方向，不猜。
"""

from datetime import UTC, datetime
from types import SimpleNamespace

from app.domain.ratchet.board import build_board

T0 = datetime(2026, 9, 1, 4, 23, tzinfo=UTC)


def _row(
    commit: str,
    checks: list[dict],
    *,
    collection: str = "ok",
    day: int = 1,
    hours: int = 0,
):
    payload = None
    if collection != "failed":
        payload = {
            "version": 1,
            "collection": collection,
            "commit": commit,
            "checks": checks,
        }
    return SimpleNamespace(
        commit_sha=commit,
        collected_at=T0.replace(day=day, hour=hours or 4),
        run_url=f"https://example.invalid/runs/{commit}",
        collection=collection,
        reason=None if collection != "failed" else "采集失败",
        payload=payload,
    )


def _check(
    check_id: str,
    *,
    status: str = "pass",
    actual=3,
    frozen=5,
    fingerprint="fp1",
    **extra,
):
    record = {
        "id": check_id,
        "area": "边界",
        "status": status,
        "frozen": frozen,
        "rule_fingerprint": fingerprint,
        "better": "down",
    }
    if actual is not None:
        record["actual"] = actual
    record.update(extra)
    return record


def _board(rows):
    return build_board(
        list(reversed(rows)),
        repo="o/r",
        deployed_commit="d" * 40,
        total_stored=len(rows),
    )


def _check_of(board, check_id):
    for area in board["areas"]:
        for check in area["checks"]:
            if check["id"] == check_id:
                return check
    raise AssertionError(f"{check_id} not in board")


def test_a_failed_collection_is_not_a_zero():
    rows = [
        _row("a" * 40, [_check("be-contracts", actual=3)], day=1),
        _row("b" * 40, [], collection="failed", day=2),
    ]
    check = _check_of(_board(rows), "be-contracts")

    assert [point["status"] for point in check["points"]] == ["pass", "not_collected"]
    assert check["points"][1]["actual"] is None
    # The last point measured nothing, so the series has no direction to report
    # — reading the previous value forward would be inventing a measurement.
    assert check["direction"] == "unknown"


def test_a_check_that_reported_no_count_is_not_a_zero():
    rows = [
        _row("a" * 40, [_check("files", actual=None)], day=1),
        _row("b" * 40, [_check("files", actual=None)], day=2),
    ]
    check = _check_of(_board(rows), "files")

    assert [point["actual"] for point in check["points"]] == [None, None]
    assert check["direction"] == "unknown"


def test_direction_comes_from_better_and_the_last_segment_only():
    rows = [
        _row("a" * 40, [_check("be-contracts", actual=5)], day=1),
        _row("b" * 40, [_check("be-contracts", actual=4)], day=2),
        _row("c" * 40, [_check("be-contracts", actual=1, fingerprint="fp2")], day=3),
    ]
    check = _check_of(_board(rows), "be-contracts")

    # 5 → 4 is progress, but the rule changed before the third collection, so the
    # new segment holds ONE point and no direction — the drop across the rule
    # change is not this page's to claim.
    assert check["direction"] == "unknown"
    assert [point["rule_changed"] for point in check["points"]] == [False, False, True]


def test_direction_within_one_segment():
    rows = [
        _row("a" * 40, [_check("be-contracts", actual=5)], day=1),
        _row("b" * 40, [_check("be-contracts", actual=2)], day=2),
    ]
    assert _check_of(_board(rows), "be-contracts")["direction"] == "improving"

    worse = [
        _row("a" * 40, [_check("be-contracts", actual=2)], day=1),
        _row("b" * 40, [_check("be-contracts", actual=9)], day=2),
    ]
    assert _check_of(_board(worse), "be-contracts")["direction"] == "worse"

    flat = [
        _row("a" * 40, [_check("be-contracts", actual=9)], day=1),
        _row("b" * 40, [_check("be-contracts", actual=9)], day=2),
    ]
    assert _check_of(_board(flat), "be-contracts")["direction"] == "flat"


def test_better_up_reads_the_other_way():
    rows = [
        _row("a" * 40, [_check("scenes", actual=100, better="up")], day=1),
        _row("b" * 40, [_check("scenes", actual=120, better="up")], day=2),
    ]
    assert _check_of(_board(rows), "scenes")["direction"] == "improving"


def test_a_check_without_a_declared_direction_gets_no_direction():
    rows = [
        _row("a" * 40, [_check("scenes", actual=100, better=None)], day=1),
        _row("b" * 40, [_check("scenes", actual=10, better=None)], day=2),
    ]
    assert _check_of(_board(rows), "scenes")["direction"] == "unknown"


def test_new_exemptions_are_marked_only_where_the_rules_held_still():
    rows = [
        _row("a" * 40, [_check("be-contracts", actual=5, frozen=5)], day=1),
        _row("b" * 40, [_check("be-contracts", actual=5, frozen=7)], day=2),
        _row(
            "c" * 40,
            [_check("be-contracts", actual=7, frozen=9, fingerprint="fp2")],
            day=3,
        ),
    ]
    check = _check_of(_board(rows), "be-contracts")

    assert [point["new_exemptions"] for point in check["points"]] == [None, 2, None]
    # Frozen and actual stay two numbers, side by side, never netted off.
    assert (check["frozen"], check["actual"]) == (9, 7)


def test_the_page_reports_the_collection_and_the_deployed_commit_separately():
    rows = [
        _row("a" * 40, [_check("be-contracts", actual=5)], day=1),
        _row("b" * 40, [_check("be-contracts", actual=4)], day=2),
    ]
    board = _board(rows)

    assert board["collected_commit"] == "b" * 40
    assert board["deployed_commit"] == "d" * 40
    assert board["collection"] == "ok"
    assert [point["collection"] for point in board["collections"]] == ["ok", "ok"]


def test_an_empty_archive_says_so_rather_than_drawing_zeroes():
    board = build_board([], repo="o/r", deployed_commit="dev", total_stored=0)

    assert board["areas"] == []
    assert board["points"] == 0
    assert board["collection"] is None
    assert board["collected_commit"] is None


def test_areas_keep_the_check_registry_order():
    rows = [
        _row(
            "a" * 40,
            [
                _check("scene-ratchet", area="场景", actual=138),
                _check("fe-boundary", area="边界", actual=116),
                _check("file-sizes", area="规模", actual=28),
            ],
            day=1,
        )
    ]
    board = _board(rows)

    assert [area["area"] for area in board["areas"]] == ["场景", "边界", "规模"]
