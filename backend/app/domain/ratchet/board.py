"""The series the 棘轮 page draws, assembled from stored snapshots.

Reading rules, all of them from the design (docs/topics/棘轮页方案 §1.6) and none
of them invented here:

* **A hole is not a value.** A collection that failed, and a check that did not
  run inside a collection that did, are points with `status = not_collected` and
  `actual = None`. They break a comparison, which is the reason they are kept:
  a series that skipped them would show a drop that no check measured.
* **A rule change splits the series.** Each point carries the fingerprint of the
  files that decided it. A change of fingerprint opens a new comparison segment,
  so the segment before it and the one after are never subtracted from each
  other — 「判定变更」 and 「还了债」 look identical in a number and completely
  different in an archive.
* **One direction per check, from the check's own word.** `better` says which
  way is better; the direction is the first-to-last difference inside the last
  segment. Nothing is summed, weighted, or scored: the page shows ten
  answers, not one grade.
* **新豁免 is separate from 违规数.** A rise in the frozen count is a decision
  made in a commit, not debt discovered; it is marked on the point where the
  fingerprint did not change, and the two numbers are reported side by side.

The area order comes from the check registry itself — the order the checks
appear in a snapshot is the collector's own order, so adding a check to a new
area needs no change here and cannot drift.
"""

from datetime import UTC, datetime
from typing import Any

_DIRECTIONS = {"down", "up"}
_NOT_COLLECTED = "not_collected"

# The gate judges changed files; board.size measures the whole tree.
# A zero diff count does not mean the repository has no oversized files.
_SIZE_CHECK_ID = "file-sizes"


def _tree_size(payload: Any) -> dict[str, Any] | None:
    """The tree-wide size numbers out of the collector's own board, or None.

    None rather than zeros, for the same reason every other number here is optional: a
    collection whose board could not be measured has no answer to 「树上超了多少」, and
    drawing 0 would claim the tree is clean.
    """
    if not isinstance(payload, dict):
        return None
    board = payload.get("board")
    size = board.get("size") if isinstance(board, dict) else None
    if not isinstance(size, dict):
        return None
    offenders, excess = size.get("offenders"), size.get("excess_lines")
    if not _is_int(offenders) or not _is_int(excess):
        return None
    per_cap = size.get("per_cap")
    caps = [
        {
            "prefix": prefix,
            "cap": row.get("cap"),
            "judged": row.get("judged"),
            "over_cap": row.get("over_cap"),
            "excess_lines": row.get("excess_lines"),
        }
        for prefix, row in (per_cap.items() if isinstance(per_cap, dict) else [])
        if isinstance(row, dict)
    ]
    return {"offenders": offenders, "excess_lines": excess, "caps": caps}


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    return value if isinstance(value, str) else None


def _is_int(value: Any) -> bool:
    """A count, and not a bool. `True` is an `int` in python and is not a count."""
    return isinstance(value, int) and not isinstance(value, bool)


def _check_records(row: Any) -> list[dict[str, Any]]:
    payload = row.payload or {}
    checks = payload.get("checks")
    if not isinstance(checks, list):
        return []
    return [
        item
        for item in checks
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    ]


def _point_for(
    row: Any,
    record: dict[str, Any] | None,
    previous: dict[str, Any] | None,
) -> dict[str, Any]:
    """One point in one check's series, for one collection."""
    if record is None:
        status = _NOT_COLLECTED
        actual = frozen = stale_count = None
        fingerprint = None
        detail = None
        reason = "这次采集里没有这道检查"
    else:
        status = str(record.get("status") or _NOT_COLLECTED)
        # `actual` is kept as None unless the checker reported a number. Zero is
        # a measurement; a missing number is not, and conflating them is the one
        # mistake this whole arrangement exists to prevent.
        raw_actual = record.get("actual")
        actual = (
            raw_actual
            if isinstance(raw_actual, int) and not isinstance(raw_actual, bool)
            else None
        )
        raw_frozen = record.get("frozen")
        frozen = (
            raw_frozen
            if isinstance(raw_frozen, int) and not isinstance(raw_frozen, bool)
            else None
        )
        stale = record.get("stale")
        stale_count = len(stale) if isinstance(stale, list) else None
        fingerprint = record.get("rule_fingerprint")
        fingerprint = fingerprint if isinstance(fingerprint, str) else None
        detail = (
            record.get("details") if isinstance(record.get("details"), list) else None
        )
        reason = record.get("reason") if isinstance(record.get("reason"), str) else None
    rule_changed = bool(
        previous is not None
        and fingerprint
        and previous.get("rule_fingerprint")
        and fingerprint != previous["rule_fingerprint"]
    )
    # 新增豁免 is a decision, not a measurement: it is marked only where the
    # frozen count rose and the rules did NOT change. Across a rule change the
    # count can move for reasons that have nothing to do with a commit choosing
    # to freeze something, and the segment break already says so.
    new_exemptions = None
    if not rule_changed and frozen is not None and previous is not None:
        previous_frozen = previous.get("frozen")
        if previous_frozen is not None and frozen > previous_frozen:
            new_exemptions = frozen - previous_frozen
    return {
        "commit": row.commit_sha,
        "collected_at": _iso(row.collected_at),
        "run_url": row.run_url,
        "collection": row.collection,
        "status": status,
        "actual": actual,
        "frozen": frozen,
        "stale_count": stale_count,
        "rule_fingerprint": fingerprint,
        "rule_changed": rule_changed,
        "new_exemptions": new_exemptions if not rule_changed else None,
        "details": detail,
        "reason": reason,
    }


def _direction(points: list[dict[str, Any]], better: Any) -> str:
    """The last segment's first-to-last move, in the check's own terms."""
    if better not in _DIRECTIONS:
        return "unknown"
    # The tail of the series that shares one fingerprint. Points before a
    # changed fingerprint are a different measurement and are never subtracted.
    segment: list[dict[str, Any]] = []
    for point in reversed(points):
        if segment and point["rule_fingerprint"] != segment[-1]["rule_fingerprint"]:
            break
        segment.append(point)
    segment.reverse()
    # A hole ends the comparison, it does not get stepped over: the points before
    # it were measured in a state this series cannot see through. Taking the
    # first and last *measured* point of the segment would subtract 10 (before a
    # failed collection) from 3 (after it) and call the run an improvement — a
    # number the page would be the only witness to. So: only the unbroken run of
    # measured points at the end of the segment counts.
    measured: list[dict[str, Any]] = []
    for point in reversed(segment):
        if point["actual"] is None:
            break
        measured.append(point)
    measured.reverse()
    if len(measured) < 2:
        return "unknown"
    first, last = measured[0]["actual"], measured[-1]["actual"]
    if first == last:
        return "flat"
    improved = last < first if better == "down" else last > first
    return "improving" if improved else "worse"


def build_board(
    rows: list[Any],
    *,
    repo: str,
    deployed_commit: str,
    total_stored: int,
) -> dict[str, Any]:
    """Assemble the page's payload from stored rows (newest first)."""
    ordered = list(reversed(rows))
    collections: list[dict[str, Any]] = []
    areas: dict[str, list[dict[str, Any]]] = {}
    series: dict[str, list[dict[str, Any]]] = {}
    meta: dict[str, dict[str, Any]] = {}
    previous: dict[str, dict[str, Any]] = {}
    latest_stale: dict[str, list[Any]] = {}
    known_ids: list[str] = []

    for row in ordered:
        collections.append(
            {
                "commit": row.commit_sha,
                "collected_at": _iso(row.collected_at),
                "run_url": row.run_url,
                "collection": row.collection,
                "reason": row.reason,
            }
        )
        records = _check_records(row)
        by_id = {record["id"]: record for record in records}
        if records:
            # The registry's own order, and the ids the series is drawn with.
            for record in records:
                if record["id"] not in known_ids:
                    known_ids.append(record["id"])
        for check_id in known_ids:
            record = by_id.get(check_id)
            point = _point_for(row, record, previous.get(check_id))
            series.setdefault(check_id, []).append(point)
            previous[check_id] = point
            if record is not None:
                area = record.get("area")
                meta[check_id] = {
                    "area": area if isinstance(area, str) and area else "未分区",
                    "better": record.get("better")
                    if record.get("better") in _DIRECTIONS
                    else None,
                }
                if isinstance(record.get("stale"), list):
                    latest_stale[check_id] = record["stale"]

    # Use only the latest tree measurement; missing data must remain unknown.
    tree = _tree_size(getattr(rows[0], "payload", None) if rows else None)

    for check_id, points in series.items():
        info = meta.get(check_id, {"area": "未分区", "better": None})
        last = points[-1] if points else {}
        # The exemptions that no longer match anything, newest list wins: they
        # are a fact about the current tree, not a series.
        stale = latest_stale.get(check_id, [])
        entry = {
            "id": check_id,
            "area": info["area"],
            "better": info["better"],
            "direction": _direction(points, info["better"]),
            "status": last.get("status", _NOT_COLLECTED),
            "actual": last.get("actual"),
            "frozen": last.get("frozen"),
            "stale": stale,
            "stale_count": last.get("stale_count"),
            "rule_fingerprint": last.get("rule_fingerprint"),
            "points": points,
        }
        if check_id == _SIZE_CHECK_ID:
            entry["tree"] = tree
        areas.setdefault(info["area"], []).append(entry)

    newest = rows[0] if rows else None
    return {
        "repo": repo,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "deployed_commit": deployed_commit,
        "collected_commit": newest.commit_sha if newest else None,
        "collected_at": _iso(newest.collected_at) if newest else None,
        "run_url": newest.run_url if newest else None,
        "collection": newest.collection if newest else None,
        "points": len(collections),
        "total_stored": total_stored,
        "collections": collections,
        "areas": [
            {"area": area, "checks": checks} for area, checks in areas.items() if checks
        ],
    }
