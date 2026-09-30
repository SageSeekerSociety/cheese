"""Build the ratchet-board preview page from a collected snapshot.

Inputs, both real:
  snapshot.json  one run of `.claude/scripts/ratchet-snapshot.py`: every
                 registered check's own record plus the board, at one commit
  series.json    the arch-metrics artifacts CI has stored on main pushes, which
                 is where the page's trend comes from

The page shows what the collector collected, not a number typed into the
template. Every count on it is addressed by `data-c="<check>.<field>"` and
filled from the snapshot, so a page that disagrees with the collector is a bug
in the page rather than a stale hand-copy.

    python3 build.py
"""
import json
import pathlib

HERE = pathlib.Path(__file__).parent

snap = json.loads((HERE / "snapshot.json").read_text())
series = json.loads((HERE / "series.json").read_text())

# The history stops at the collected commit when CI has a record for it, so the
# trend line and the numbers above it share one SHA. A snapshot from a branch
# CI never built has no such record and the history keeps running to its own
# last push — the page says which commit the numbers are from either way.
shas = [r["sha"] for r in series]
head = (snap["commit"] or "")[:9]
if head in shas:
    series = series[: shas.index(head) + 1]

DATA = {
    "snap": {
        "commit": snap["commit"],
        "commit_date": snap["commit_date"],
        "collected_at": snap["collected_at"],
        "run_url": snap["run_url"],
        "checks": snap["checks"],
    },
    "board": {
        "size": snap["board"]["size"],
        "hotspots": snap["board"].get("hotspots", {}),
    },
    "series": [
        {
            k: r[k]
            for k in (
                "date", "sha", "subject", "A", "comp", "fe_boundary", "c1", "c2",
                "c3", "over", "excess", "fe_over", "be_over", "chat", "deferred",
            )
        }
        for r in series
    ],
    "worst_now": snap["board"]["size"]["worst"],
    "worst_first": series[0]["worst"],
}

template = (HERE / "template.html").read_text()
(HERE / "index.html").write_text(
    template.replace("/*__DATA__*/null", json.dumps(DATA, ensure_ascii=False))
)
print(f"ok {len(DATA['series'])} history rows, {len(DATA['snap']['checks'])} checks, "
      f"collected at {DATA['snap']['commit'][:9]}")
