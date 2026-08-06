# Restore Drill Failure Visibility Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the weekly restore drill fail visibly whenever `pg_restore` fails or a supposedly populated backup restores no application data.

**Architecture:** Treat the `pg_restore` process result as authoritative and stop before sanity queries on any non-zero exit. After a successful restore, keep the schema checks and add a small critical-table row total; default to requiring data while allowing deliberately empty installations through an explicit, logged environment override. Preserve each restore log under the repository-local `tmp/restore-tests/` directory and print its path plus tail on failure.

**Tech Stack:** Bash, Docker CLI, PostgreSQL `pg_restore`/`psql`, GitHub Actions.

---

### Task 1: Capture the false-green restore result

**Files:**

- Create: `deploy/tests/test-db-restore-test.sh`
- Test: `deploy/db-restore-test.sh`

**Step 1: Write the failing test**

Create a fake `docker` executable that returns exit 42 for `pg_restore` while
returning 50 public tables, one Alembic row, and seven business rows. Run the
real restore script and require a non-zero exit plus an explicit restore-failure
message.

**Step 2: Run test to verify it fails**

Run: `bash deploy/tests/test-db-restore-test.sh restore_failure`

Expected pre-fix result: FAIL because the real script prints
`RESTORE-TEST PASS` and exits 0 after the stubbed `pg_restore` exits 42.

### Task 2: Make process and data failures observable

**Files:**

- Modify: `deploy/db-restore-test.sh`
- Modify: `deploy/tests/test-db-restore-test.sh`

**Step 1: Implement the minimal process fix**

Run `pg_restore` with `--exit-on-error`, capture its exact status, and on any
non-zero value print `RESTORE-TEST FAIL`, the retained log path, and the log
tail before exiting non-zero.

**Step 2: Add a non-brittle data invariant**

Sum rows from the critical `user`, `projects`, `topics`, and `blocks` tables.
Require at least one row unless `CHEESE_RESTORE_ALLOW_EMPTY=1`; when the override
is used, print that it allowed the empty restore. This preserves the legitimate
fresh-install caller without letting it pass silently.

**Step 3: Exercise all contracts**

Add shell cases for failed restore, populated successful restore, empty restore
rejected by default, and empty restore accepted only with the explicit override.

Run: `bash deploy/tests/test-db-restore-test.sh`

Expected post-fix result: five PASS lines (including a failed-query case) and
exit 0.

### Task 3: Put the regression in the scheduled path

**Files:**

- Modify: `.github/workflows/backup-restore-test.yml`

**Step 1: Run the shell regression before the live drill**

Add a workflow step that invokes `bash deploy/tests/test-db-restore-test.sh`.
The test uses only a local fake and cannot touch Docker or the development box.

**Step 2: Verify and checkpoint**

Run:

```bash
bash deploy/tests/test-db-restore-test.sh
bash .claude/scripts/check.sh
```

Record the exact outputs in `tmp/audit/FIXES.md`, then commit only the restore
files on `fix/audit-restore-drill`. If Git metadata remains sandbox-blocked,
record the failure rather than claiming a commit.
