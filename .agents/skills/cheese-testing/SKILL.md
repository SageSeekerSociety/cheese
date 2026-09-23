---
name: cheese-testing
description: >-
  Design, write, review, and run tests for the Cheese repository. Use when changing
  Cheese behavior or an interface, adding regression coverage, judging whether
  tests can catch a failure, or changing test fixtures, selection, or CI execution.
  Includes backend, frontend, Go connector, harness, and deployment tests.
  Owns test design and test-entry selection when used with cheese-py-code-review;
  that skill retains the rest of the code review.
---

# Cheese testing

Code paths below are relative to the repository root. Use
[the contract map](references/contracts.md) for the boundary being changed;
start with its linked fixtures and tests, then inspect the responsible
implementation and workflow. Read `.claude/rules/backend-tests.md`
for backend fixture pitfalls and `.claude/rules/e2e.md` for browser fixtures.

## Choose the contract before the execution layer

For a behavior change, identify the Cheese promise it affects, the real boundary
the test must cross, and the existing suite that owns it. Put new failure cases
in that suite. When a new boundary has no owner, add its contract and test entry
to the map in the same change. Historical bug families in
`docs/plans/2026-09-19-bugs-and-testing.md` supply regression cases, not an
exhaustive list of future failures.

For execution and delivery changes, check the applicable sequences involving
disconnect, duplicate receipt, cancellation, and restart. For independent service
releases, check supported version combinations. For resource-lifetime changes,
check another caller's progress while the first caller is blocked. The contract
map names the existing places to extend.

Use shared doubles under `backend/tests/support/`. Wire and harness fixtures
have real consumers in two languages; update and exercise both sides when their
contract changes. A fake owner that invents a response header cannot verify
that the real owner emits it. Preserve the real component responsible for the
property under test, including request parsing and error serialization.

## Backend layers are assigned by collection

`backend/tests/conftest.py` owns `_layer_of`, `_reaches_for_db`, and
`pytest_collection_modifyitems`. Do not add `pure`, `contract`, or `integration`
markers to individual tests: collection rejects them.

| CI selection | Assignment | Consequence for a new test |
| --- | --- | --- |
| `-m pure` | Under `tests/unit/`, with no `_pg_schema` in its fixture closure | May be async. CI points database URLs at an unreachable port. |
| `-m contract` | Under `tests/contract/` | Directory membership does not imply no database or low cost. |
| `-m integration` | All remaining backend cases, including DB-backed cases under `tests/unit/` | Reuse the owning database/application fixtures. |

Acceptance and browser E2E run through separate workflows; frontend and Go are
language-specific entries, not additional levels. Select by both the behavior
being verified and the required resources. The design report's ban on event
loops in pure tests is not the implemented collection rule.

## Run through the existing entry

Follow `CLAUDE.local.md`, where present, for the execution host. Run commands
below on that host with the workflow's dependencies and test environment ready.
For backend services, `.claude/scripts/dev-db.sh` provides `status`, `start`, and
`env`; read its configuration before starting another instance. Each concurrent
pytest invocation needs an isolated database namespace, in addition to xdist's
per-worker isolation. Read `backend/tests/isolation.py` for its naming inputs.

| Scope | Working directory and entry | CI environment/selection owner |
| --- | --- | --- |
| One backend suite or layer | `backend/`: `uv run pytest tests/<path>` or `uv run pytest tests/ -m <layer>` | `.github/workflows/test.yml` |
| Python harness requests | `backend/`: `uv run python -m scripts.test_harness_contracts` | `.github/workflows/harness-contract.yml` |
| Go unit/protocol | `cli/`: `go test -race ./...` | `.github/workflows/cli.yml` |
| Real Claude interaction | `cli/`: `go test -tags claudee2e -v -timeout 35m ./e2e/` | `.github/workflows/cli.yml` supplies binaries/environment |
| Frontend unit | `frontend/`: `pnpm exec vitest run --dir src` | `.github/workflows/frontend.yml` |
| Browser | `e2e/`: `pnpm exec playwright test` | `.github/workflows/e2e.yml` supplies build, services, and seed data |
| Remote execution/private chat | Read the selected job's invocation and setup | `.github/workflows/remote-execution.yml` |
| Deployment scripts | Read the workflow's script list | `.github/workflows/deploy-scripts-test.yml` |

`.pre-commit-config.yaml` and `Taskfile.yml` own the broader check commands.
For a new test entry or shared fixture, inspect `.github/scripts/required-ci-paths.json`,
`.github/scripts/required-ci.py`, and the consuming workflow. Verify the job
executed on the relevant revision; a green scope job is not a test result.

## Evidence for the changed contract

For a new or changed boundary test, run a targeted negative control: restore the
bad behavior or remove the protection and show the expected assertion fails.
Restore the implementation before committing. Record the test, the temporary
change, and the observed failure in the PR's validation. Apply this to the
changed contract, rather than running whole-repository mutation testing.

For event-sequence exploration, retain the failing sequence as an explicit
regression case. Check existing tooling before adding a property-testing
dependency; the map does not imply a state-machine framework already exists.

When assessing coverage, name which boundary, failure state, or sequence was
exercised and what remained substituted. For pool claims, inspect
`CHEESEX_TEST_NULLPOOL`, `backend/app/core/db.py`, and the fixture's engine:
capacity arithmetic or pool-gauge tests alone do not verify business behavior
under saturation. For deployment claims, exercise the actual supported versions
and in-flight operation; same-revision in-process tests cannot establish that.

For CI optimization, compare queue/setup/test/teardown time, executed selections,
skips, cancellations, and retries. Keep required-check feedback targets and
unfinished gate work in issues #1279 and #1300; consult their current state
before treating a design target as an enforced budget or adding a new gate.
