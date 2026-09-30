"""棘轮: the architecture ratchet's collected snapshots, kept.

The admin 棘轮 page answers one question — 还债了没有 — and it answers it from
snapshots this platform COLLECTED, not from anything it re-measures. A snapshot
is one commit's worth of verdicts (`.claude/scripts/ratchet-snapshot.py`, run by
`arch-metrics.yml` after every merge into main), uploaded as a CI artifact named
`ratchet-snapshot`.

Why the numbers are copied out of GitHub and into a table: an artifact expires
after 90 days, and a series that quietly loses its oldest points reads as a
project whose history starts when the last cleanup ran. The table is the
archive; the artifacts are only the transport (see `artifacts`).

The pieces:

* ``models`` / ``store`` — the table and the only code that writes it. One row
  per CI run, keyed ``(repo, workflow_run_id)`` so a re-poll cannot duplicate a
  point, and carrying the snapshot document verbatim.
* ``artifacts`` — reading GitHub: list artifacts by name, download one, unzip
  the JSON out of it. It validates the snapshot version and refuses to guess:
  an artifact this code does not understand is skipped and counted, never
  stored as a snapshot with zero checks.
* ``ingest`` — one pull: walk the artifacts newest-first, skip the runs already
  stored, store the rest.
* ``board`` — the series the page draws: points per check, the fingerprint
  segments that break a series in two, and the direction of the last segment.

What this domain does NOT do: run any check, judge a snapshot, score an area, or
write a baseline. The checkers decide; this only remembers what they decided and
shows the remembering honestly — a check that did not run is `not_collected`
with no number, never a zero.
"""
