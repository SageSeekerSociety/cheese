"""Count cold and warm reader costs; HTTP attempts are not quota charges."""

import json
from datetime import timedelta

from tests.integration.test_ci_activity_sweep import (
    NOW,
    _connect,
    _iso,
    _NativeGitHub,
    _run,
    _sweep,
)
from tests.integration.test_ci_activity_sweep import tokens as tokens


def test_an_hour_of_fixed_window_revalidation_counts_cold_and_warm_reads(
    db_session, _portal, tokens
):
    remote = _NativeGitHub()
    for n, offset in enumerate(range(-3 * 3600, 3600, 45), start=1):
        created = NOW + timedelta(seconds=offset)
        remote.runs[n] = _run(
            n,
            age=-timedelta(seconds=offset),
            updated_at=_iso(created),
            conclusion="skipped",
        )
    reader = remote.reader()

    async def run():
        await _connect(db_session)
        cold = await _sweep(db_session, reader, backfill_slices=0, jobs_budget=0)
        assert cold["error"] == ""
        cold_reads = sum(request.method == "GET" for request, _ in remote.responses)
        before = len(remote.responses)
        sweeps = 0
        for minute in range(5, 61, 5):
            report = await _sweep(
                db_session,
                reader,
                at=NOW + timedelta(minutes=minute),
                backfill_slices=0,
                jobs_budget=0,
            )
            assert report["error"] == ""
            sweeps += 1
        replies = remote.responses[before:]
        rest = [
            (request, status) for request, status in replies if request.method == "GET"
        ]
        counts = {
            "sweeps": sweeps,
            "cold_rest_reads": cold_reads,
            "warm_rest_reads": len(rest),
            "warm_304": sum(status == 304 for _, status in rest),
            "warm_non_304": sum(status != 304 for _, status in rest),
            "graphql": sum(request.method == "POST" for request, _ in replies),
        }
        print(json.dumps(counts, sort_keys=True))
        assert counts["cold_rest_reads"] == 36
        assert counts["warm_rest_reads"] == 432
        assert counts["warm_304"] == 420
        assert counts["warm_non_304"] == 12
        assert counts["graphql"] == counts["sweeps"] == 12

    _portal.call(run)
