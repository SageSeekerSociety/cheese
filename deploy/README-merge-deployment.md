# Merging another deployment into this one

Two Cheese deployments that started from the same database and then ran apart
can be brought back together: the rows and files one of them gained are merged
into the other, as one batch that can be taken back. The workflow
**Merge another deployment (dev box)** (`.github/workflows/merge-deployment.yml`)
runs `deploy/merge-deployment.sh` on the dev box, which runs
`backend/scripts/merge_deployment.py` beside the running backend. Its docstring
has the rules row by row; this page is what an operator needs.

## The export

A directory on the dev box, put there by whoever prepares the merge. Nothing
about the other deployment goes into the repository.

| Path | |
|---|---|
| `database.dump` | `pg_dump -Fc` of the other deployment's database, UTF8, at any revision on this repository's migration chain (the job migrates it to this release's head). Required. |
| `uploads/` | A copy of the other deployment's uploads tree. Files that are not here are copied in; a file here is never overwritten. |
| `manifest.json` | `{"changed": {"<table>": [<id>, …]}, "skip_account_email_domains": ["…"]}`. `changed` lists the rows of the history both deployments share that the other deployment changed since they ran apart; for those the other side wins. A database dump cannot tell which rows those are, so they are read from the running source (for example rows whose `xmin` is newer than the transaction that seeded it) before it is dumped. Accounts under a listed domain are not brought, nor anything that names them. |
| `secrets.env` | The other deployment's own keys, so its secrets can be opened: `REALNAME_ENCRYPTION_KEY` or `JWT_SECRET` if its database predates `DATA_ENCRYPTION_KEY`, `SOURCE_DATA_ENCRYPTION_KEY` otherwise. `chmod 600`. |

## Running it

1. Dispatch with `action: dry-run`, a batch name and `export_dir`. It does the
   whole import in a transaction it rolls back and prints what it would write:
   rows per table, accounts merged by email and renamed for a taken username,
   rows both sides changed (this side's later change is kept), files that
   differ, and source rows in tables the merge does not carry. Read it.
2. Dispatch `action: import` with the same batch and directory. It waits for an
   approval, takes a verified backup (`deploy/db-backup.sh`), and lands the
   batch in one transaction. Dispatching it again for a batch that is in does
   nothing, and the same export under another batch name brings nothing the
   first batch already brought.
3. To take it back: `revert-dry-run`, then `revert`, naming the batch. A revert
   refuses, changing nothing, while any row the batch wrote has changed since,
   or while a row made since points at a row it brought, because taking it back
   would lose that change or leave that row pointing at nothing. Once people
   have used what a batch brought, the way back is the backup taken before it.

The job's own log in Actions shows counts only. The row-by-row log names
accounts and files of both deployments, so it stays on the box.

Each run's log is `~/backups/merge-deployment-<batch>-<action>-<time>.log` on
the box (mode 600); the journal of a batch is in `deployment_merge_batches`,
`deployment_merge_rows` and `deployment_merge_files`.

## What it does not do

- It writes only the kinds listed in `KINDS` (accounts and their profiles,
  sign-in methods and real-name records, spaces, teams, tasks and what was handed
  in for them, with attachments and avatars). Anything else in the export is
  counted under `leftovers` in the report and left behind.
- A row the other deployment deleted outright is not deleted here.
- A task's files other than its 题目说明 (attachments a migration moved into
  the task's library on the source copy) are not carried; the report counts them
  under `leftovers` if there are any.
