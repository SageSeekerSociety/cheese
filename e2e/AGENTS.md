# e2e/ — local prerequisites

Read [`../AGENTS.md`](../AGENTS.md) and the existing
[e2e rules](../.claude/rules/e2e.md) first.

Start an isolated Forgejo before running `scripts.seed_fusion_demo`, not just
Postgres and migrations: the seeder creates hosted repositories for the demo
projects. Follow `.github/workflows/e2e.yml`'s ordering and the helper
`backend/tests/forgejo/start.sh`; a missing hosting configuration prevents the
seed from completing before browser tests begin.

For parallel local runs, choose an available Docker context and distinct
`FORGEJO_TEST_PROJECT`, `FORGEJO_TEST_PORT`, `FORGEJO_TEST_S3_PORT` and
`FORGEJO_TEST_TOKEN_FILE`. The helper exports connection settings via
`GITHUB_ENV` in CI; locally pass the resulting Forgejo and S3 settings privately
to the backend/seeder as well. Starting containers alone does not configure the
backend, and another run's token file or containers are not yours to reuse.
