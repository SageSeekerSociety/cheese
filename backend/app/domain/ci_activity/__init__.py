"""CI activity: what the platform's own Actions runners and merge queue did.

The admin page this feeds answers 「CI 和合并队列为什么慢」: how long jobs wait
for a runner and how long they take on each one, how busy the runners are over
the day, how long a PR spends from entering the merge queue to merging, how
many CI rounds it takes, and why the queue throws PRs out.

The numbers are copied out of GitHub into tables by a periodic sweep
(``ingest``) and the page reads only the tables, so opening it costs GitHub
nothing and cannot be what spends the installation's quota. The repository is
the deployment's own (`settings.platform_repository`), read with the platform
App's installation token, the same identity the ratchet ingest uses.

* ``models`` — the five tables and what each column means.
* ``github`` — the three reads: run listing, a run's jobs, merge-queue events.
* ``store`` — the only code that writes the tables.
* ``ingest`` — one sweep, bounded per step, reporting instead of raising.

The history kept is a window (`ingest.WINDOW`, 30 days), not an
archive: rows older than it are deleted by the sweep.
"""
