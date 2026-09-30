# Native receipt evidence

The primitive, lock-order and send-fault tests exercise real PostgreSQL transactions. The retention tests use real SQLite journals and controlled callbacks. Send faults abort a flushed PostgreSQL transaction inside SQLAlchemy's commit path, against a scripted channel. None proves live native-model consumption, complete runtime integration, or a new-process restart.

## Primitive baseline

`primitives-first.txt`: 10 passed at source `43b73ca22d8df32ee86676c2542c0252f5de9f88`. Seven mismatched identity cases, acceptance versus echo with fresh SQLAlchemy sessions, explicit rollback/retry, and replaced attempt rejection. Published at `802047926f4c430c5d370b4230f793e317efc269`. Not repeated for the lock-order check.

## Registration retry overlapping echo

Source: `478355e5151e913f068b84668d0c76ab1fd9d7c7`.

- `lock-order-green.txt`: 2 passed, 10 deselected; exit 0.
- `lock-order-negative.txt`: 2 failed, 10 deselected; exit 1. Both fail with PostgreSQL `DeadlockDetectedError`, not setup failure or the test's safety timeout.
- The companion JSON files preserve argv, environment, source SHA, UTC timestamps, output SHA256 and exit status.
- `reversed_lock_control.py` introduces only a NativeInput lock before the production receipt operation. It does not rewrite production files or weaken assertions. Its hash is in the negative manifest. This is a lock-inversion negative control, not a claim to execute the exact previous implementation.

The parameterized test pauses whichever transaction holds its first real row lock. A third connection observes `pg_blocking_pids` before releasing it. It exercises registration-first and echo-first ordering. Both must commit; a new session verifies exactly one input row and a settled delivery. No database lock is mocked and no deadlock is swallowed.

The implementation locks Delivery before NativeInput in both functions. Receipt first discovers the immutable link without a row lock and then revalidates identity/link after locking. Registration after settlement may verify an identical existing registration, but cannot create a new input for a non-sending attempt.

## Journal retention checkpoint

Source: `a3b7ea982ef35aec948324a47fab27da298e745d`.

`retention-final.txt` and its JSON manifest record 9 passed, exit 0: three new retention cases plus six existing drain cases. New cases rebuild the reader after five callback failures spaced beyond the refusal threshold, for fresh and three-day-old echoes. They verify the durable landing cursor stays behind the echo, backend pruning preserves it, successful settlement then advances the cursor, and the next drain does not repeat it. A separate runner-journal case verifies output expiry keeps echoes while deleting ordinary output.

These are reconstructed objects in the same test process, with an `OSError` callback, not process restart or database commit fault injection. The callback protocol is still topic/text at this checkpoint; durable identity runtime wiring is pending. The existing ChatService swallowing of transaction errors is NOT yet removed, so the complete product receipt pipeline is not fixed by these tests alone.

`retention-first.txt` records 3 passed before formatting. `drain-integration-first.txt` records 5 failed/1 passed because existing test subscriptions omitted a receipt callback. Those tests now provide one explicitly; the bulk-old-output fixture excludes receipts because receipts must no longer be discarded as output. The final nine-case run uses the committed source above.

## Commit failure before and after external admission

Source: `e782649261e186a025705d59c6c8ac9b78a387c3`.

`send-fault-routing.txt` and its JSON manifest record 6 passed, 2 test-JWT key-length warnings, exit 0. Four cases cover initial and busy inputs with either registration or accepted-receipt commit failure. Two more follow the real ChatService → ComputePool → driven runtime → scripted channel → ChatService path. The accepted-fault routing case then checks the caller suppresses normal queue admission and retains the explicit reconciliation result, not delivered=true.

The commit hook flushes real writes and executes `SELECT 1 / 0` on the same PostgreSQL transaction during `Session.commit`. This is a PostgreSQL transaction error in the commit path; it does not simulate an acknowledgement lost after COMMIT succeeded or a network disconnect during COMMIT. Registration failure leaves no NativeInput and zero external calls. Accepted-receipt failure leaves the committed registration, one external call and an unconfirmed accepted timestamp. Same-identity accepted/echo evidence retry settles one row without another channel call. These direct receipt calls are not native echo or journal cursor evidence.

`send-fault-negative.txt` records 3 failed, 3 deselected, exit 1. `unclassified_send_control.py` removes only post-send failure classification: the same PostgreSQL accepted-receipt error escapes as a generic DBAPIError in two cases and becomes False in live chat, failing the explicit reconciliation assertion. The control does not edit product source or weaken tests. Its SHA256 is in the manifest.

`send-fault-first.txt` records an earlier 4-pass dirty-worktree run. Its manifest HEAD is the pre-edit base, NOT the tested source; do not use it as fixed-source evidence. It also includes an async connection teardown error. The fixed-source six-case run above supersedes it.

## Durable held batch read from a new process

Source: `64d4e4ec0dd83dff11a7b2eb75d1778c6433b655`.

`held-input-process.txt` and its manifest record 1 passed, exit 0. A separate Python interpreter connects to PostgreSQL and reads the exact registered batch, excluding other projects/topics/receiver seats. Repeating that child-process read after a direct echo transaction still finds the initial held batch: an echo must not authorize a different input UUID before model work completes. Held IDs remain distinct from consumption IDs.

This proves the hold query is process-independent. It does not restart ChatService, its runtime, a journal reader or a native executor; it does not exercise full prompt assembly, concurrent registration of overlapping batches or explicit safe re-admission after a never-sent outcome. Those gates remain open.

The staged runtime/ChatService code registers before RPC and settles structured identities. All harness/caller cutovers, complete group effects, native echo → commit → journal cursor fault tests, and full new-process recovery remain incomplete. The migration and this draft PR are not independently releasable.
