# backend/tests/ — test setup and evidence

Read [`../AGENTS.md`](../AGENTS.md) first. Symptom-based advice remains in
[backend-tests.md](../../.claude/rules/backend-tests.md).

Each independent pytest run sharing Postgres needs a distinct `CHEESE_CI_SLOT`;
xdist workers distinguish workers within one run, not two runs using the same
slot. `conftest.py` recreates those databases with `DROP ... WITH (FORCE)`, so a
shared slot can disconnect another run halfway through its tests.

Also isolate Redis: `CHEESE_CI_REDIS_BASE_DB` selects the start of a block of 16
indices (`isolation.py`), not a second run ID. Use disjoint blocks only when the
server is configured to provide them; otherwise use separate Redis instances
or serialize the runs, rather than selecting a nonexistent database.

Real harness tests need the pinned executable, selected by `CHEESE_TEST_CLAUDE`
through the resolver in `.claude/scripts/dev-db.sh`, and a host that permits the
sandbox's user namespaces. A missing pinned-build setup error or an actual
`unshare: Operation not permitted` is a missing prerequisite, not evidence that
the assertions passed; distinguish that from a test failure on a supported host.

Read ratchet debt from its owner rather than another ledger's prose: repository
cross-domain exemptions are `_EXEMPT` in `unit/test_domain_import_guard.py`,
harness edges are `_LEDGER` in `unit/test_harness_boundary.py`, and private-read
points are `BASELINE` in `unit/test_is_private_read_points.py`. Import-linter's
separate contract exceptions live in `../.importlinter`; dropping one kind of
debt does not automatically lower the other ledgers.
