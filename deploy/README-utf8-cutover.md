# Rebuilding a `SQL_ASCII` database as UTF8 — runbook

Companion to [`cutover-sqlascii-to-utf8.sh`](cutover-sqlascii-to-utf8.sh). That
script ran on the **dev** DB host on 2026-08-16 and rebuilt `cheese` from
`SQL_ASCII` to `UTF8`. **Production (`192.168.16.10`) is still `SQL_ASCII`** and
will reuse the same script; this file is what stands between the next operator
and re-deriving everything below from scratch.

## What problem this solves

PostgreSQL refuses non-ASCII `\uXXXX` escapes inside `jsonb` unless the **server**
encoding is UTF8. The dev cluster was `initdb`'d as `SQL_ASCII` years ago, so the
first GitHub profile with a Chinese display name blew up the OAuth callback with
a 500 (issue #222 → #233).

Two layers exist, and they are not alternatives:

- **The stopgap** (#232, merged 2026-08-10): `_json_dumps_utf8` in
  `backend/app/core/db.py` sends JSON binds as raw UTF-8 instead of `\uXXXX`
  escapes. Raw bytes pass under either server encoding. This makes the app stop
  crashing; it does **not** make the database UTF8.
- **The fix** (this script): rebuild the database itself as UTF8, so anything
  that reaches Postgres by any other path is also safe.

Check which of these production is running before planning a cutover — as of
2026-08-16 the newest release (`0.17.0`, 2026-07-15) predates the stopgap by a
month, so prod has **neither** layer.

## Where it runs, and why not from the app box

**On the DB host, as the `postgres` OS user** — not over TCP from the app box.
`pg_hba.conf` on the DB host admits exactly one pair from the app box (user
`cheese`, database `cheese`). `CREATE DATABASE`, the restore, and both renames
all need a connection to some *other* database, and every such attempt is
refused at the pg_hba stage, before authentication. No amount of role privilege
(`CREATEDB` included) unblocks it. An earlier plan that assumed otherwise cost a
round trip; don't re-derive it.

```bash
# on the DB host (dev: 192.168.16.7 · prod: 192.168.16.10)
sudo -u postgres bash cutover-sqlascii-to-utf8.sh
```

Authentication is local peer — **the script contains no password and needs no
credential file.** Keep it that way.

## What it does, in order

1. **Preflight** (read-only, nothing changed yet): superuser check, current
   encoding really is `SQL_ASCII`, no leftover `cheese_utf8` /
   `cheese_sqlascii_old`, ≥ 2 GB free on both the work dir and PGDATA, and the
   old DB's `public` objects are all owned by `cheese`.
2. **Freeze**: `REVOKE CONNECT` + terminate sessions, scoped to `datname='cheese'`.
   *The outage starts here.*
3. **Baseline** from the frozen DB: per-table row counts, plus an md5 fingerprint
   over `blocks.content` and a count of rows holding multi-byte text.
4. **Dump** (`pg_dump -Fp`, owners and grants kept — see the pitfall below),
   sanity-checked for `COPY` sections and valid UTF-8 bytes.
5. **Create** `cheese_utf8` with `ENCODING 'UTF8'` from `template0`, trying the
   PG 17 builtin `C.UTF-8` provider first and falling back through the libc
   spellings.
6. **Restore**, forcing `client_encoding = UTF8`.
7. **Verify**: encoding, per-table row counts, ownership, per-table and
   per-sequence privileges for `cheese`, `USAGE`+`CREATE` on schema `public`,
   the `blocks` fingerprint byte-for-byte, the multi-byte row count, and finally
   a real Chinese-in-`jsonb` insert **as role `cheese`** — the exact write that
   started #222.
8. **Swap** names: `cheese` → `cheese_sqlascii_old`, `cheese_utf8` → `cheese`,
   restore `CONNECT`.

**It never drops or truncates anything.** The old database is renamed aside and
kept. Every failure path leaves the old data intact.

## What success looks like

The last lines are `DONE.` plus a reminder to restart the backend. The dev run
(2026-08-16) produced:

| | |
|---|---|
| Wall clock | ~16 s for the whole script |
| Outage window | ~1 min (freeze → backend restart) |
| Tables verified | 102 in `public`, row counts identical table by table |
| Sequences | 65, all owned by `cheese`, `last_value` carried over |
| Content fingerprint | identical old vs new (`blocks`) |
| Multi-byte rows | 51117, unchanged |
| Chinese `jsonb` as role `cheese` | inserted, read back correct |
| Dump size | 44–46 MB plain; database ~65 MB |

Then, **on the app box**:

```bash
docker restart cheese-backend-1      # dev (Docker)
sudo systemctl restart cheese-backend-py   # prod (bare-metal systemd)
```

The backend logs `permission denied for database "cheese"` throughout the freeze
window — that is expected and stops at the restart. Confirm the last such line
predates the restart before treating it as a problem.

Afterwards, verify:

```sql
SELECT datname, pg_encoding_to_char(encoding), datcollate
  FROM pg_database WHERE datname LIKE 'cheese%';
```

Keep `cheese_sqlascii_old` for a few days of clean running, then
`DROP DATABASE cheese_sqlascii_old;` (~65 MB). Note it deliberately keeps
`CONNECT` revoked, so nothing silently reconnects to the stale copy.

## When it fails

The script has a recovery handler on `EXIT` that does not trust its own
bookkeeping — it asks `pg_database` what exists **right now** and acts on that:

| Catalog state | What the handler does |
|---|---|
| No DB named `cheese`, `cheese_sqlascii_old` present | The rename got half-way. Renames it back and restores `CONNECT`. |
| Both `cheese` and `cheese_sqlascii_old` present | The swap already completed and a later step failed; the new UTF8 DB is serving. It prints the two-statement manual rollback and changes nothing. |
| Frozen, nothing renamed | Restores `CONNECT` so the site comes back on the old DB. Warns if a leftover `cheese_utf8` must be dropped before rerunning. |
| Server unreachable | Says so and stops — assess by hand. |

Rerunning after a clean abort is safe: preflight refuses to start while a
leftover `cheese_utf8` or `cheese_sqlascii_old` exists, so you cannot stack two
half-runs on top of each other.

### The one case it cannot defend against

`kill -9`, power loss, or the machine rebooting mid-run. The handler never gets
to execute and the database stays frozen. **There is no data risk** — the
symptom is purely that connections are refused:

```
FATAL: permission denied for database "cheese"
DETAIL: User does not have CONNECT privilege.
```

One command, on the DB host as `postgres`:

```sql
GRANT CONNECT ON DATABASE cheese TO PUBLIC, cheese;
```

Then restart the backend on the app box.

## Do not "clean up" these four things

Each was a real defect found by rehearsing against a throwaway `SQL_ASCII`
cluster loaded with a real dump, then injecting failures. They look like
noise; they are load-bearing.

1. **`pg_dump` does NOT pass `--no-owner`.** The restore runs as the `postgres`
   superuser. With `--no-owner`, all 102 tables and 65 sequences would come back
   owned by `postgres`, and the backend's `cheese` login would hit
   `permission denied for table ...` on its first query. The nasty part: every
   verification step also runs as `postgres`, so **all checks would still pass**
   — you'd find out after the rename, in production. Hence the dump keeps its
   `ALTER ... OWNER TO` / `GRANT` statements, and step 8 verifies ownership and
   privileges from `cheese`'s point of view explicitly.

2. **`trap '' INT TERM HUP QUIT PIPE` inside the logging process substitution.**
   Ctrl-C reaches the whole process group, so a plain `tee` dies with the script;
   the recovery handler then writes into a broken pipe, takes `SIGPIPE`, and dies
   before it can un-freeze the database. Measured: without this, an interrupted
   run left dev stuck refusing all connections. Same reason for the `set +e` and
   the second `trap ''` at the top of `on_exit`/`on_signal` — recovery must run
   to completion and must not be interruptible by a second Ctrl-C.

3. **Freeze (step 1) happens BEFORE the baseline (step 2).** The row counts and
   the fingerprint are compared against the restored copy, so they must come
   from a database that can no longer change. dev's backend writes constantly —
   `blocks` grew by 2000+ rows in one day. Taking the baseline first guarantees
   it disagrees with the dump, and the script aborts on a mismatch that was never
   a real problem: an outage spent for nothing.

4. **`flock` around the whole run.** Two concurrent copies are genuinely
   destructive: the second one's failure handler would `GRANT CONNECT` back while
   the first is still dumping, letting the backend write rows the dump has
   already passed — silent data loss. The lock is refused loudly instead.

A fifth, smaller one: `offset 0` in the sequence-privilege query in step 8. Without
it the planner hoists `has_sequence_privilege()` above the `relkind='S'` filter and
the query errors with `"..." is not a sequence`. It is an optimisation fence, not
a typo.

Two silent-pass holes are also plugged deliberately: an empty table listing would
make the comparison loop run zero times and report "all tables match" (hence the
`≥ 50 tables` floor), and a failed count query would write an empty field (hence
the `awk` assertion that every count is numeric).

## Reusing this on production

The script is archived **verbatim as it ran**. Its value is that it was executed
end-to-end against real data and survived five injected failures (abort
mid-run → auto-unfreeze; half-completed rename → auto-rollback; dropped ssh
session → auto-unfreeze; Ctrl-C → auto-unfreeze; two concurrent runs → blocked by
`flock` without a spurious unfreeze). **Any edit is outside that evidence**, which
is also why it is not parameterised — a `${DB:-cheese}` version would not be the
version that was tested.

The header comment carries the change list. In short:

- **Host**: `192.168.16.10`, reached from the prod app box `192.168.16.8`. The
  dev app box `192.168.16.5` cannot reach it at all.
- **Restart command**: prod is bare-metal systemd (`cheese-backend-py.service`),
  not Docker.
- **Disk**: preflight demands > 2048 MB free on both the work dir and PGDATA. The
  prod box had ~14 GB free as of 2026-08-16 — check again before starting. A full
  PGDATA filesystem **PANICs the whole instance**; this is the only step that can
  take the server down rather than merely abort.
- **Table-count floor**: the `≥ 50` sanity check is a guess at cluster shape.
  Confirm prod's `public` has more than 50 tables or a good run will abort.
- **Release**: make sure the deployed release contains the #232 stopgap first.
  Until the cutover completes, prod keeps 500'ing on any non-ASCII `jsonb` write.

Take a normal backup before starting anyway (`deploy/db-backup.sh`, see
[`README-backup.md`](README-backup.md)) — the script keeps the old database, but
a dump on the app box costs nothing.

## Preventing a repeat

New databases must be created with an explicit `ENCODING 'UTF8'` rather than
inheriting whatever `initdb`'s locale defaults to — recorded as a convention in
[`docs/infrastructure.md`](../docs/infrastructure.md). Note that **no amount of
testing catches this class of bug**: every test environment is already UTF8, so
the failure only exists on dev and prod.
