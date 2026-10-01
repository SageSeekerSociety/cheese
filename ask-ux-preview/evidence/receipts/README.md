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

## Persisted echo identity and new-process settlement reader

Source: `aa65abcd4928e00eeea4a0e44e4c5a42a3a2998d`.

`echo-recovery-routing.txt` and its manifest record 16 passed, one test-JWT key-length warning, exit 0. Six new correlation cases use native-shaped records, SQLite and a child interpreter. They preserve the input's session/work identity after runner replacement and a substituted stdin drain failure, even when active work changes; unknown, other-session and child echoes cannot borrow it; failed local identity persistence writes no stdin. Recovery does not resend.

One new integration case uses the real registrar, runner stamping, mirror/subscription and PostgreSQL ChatService settlement. Stdin and the echo are substitutes, not a live Claude binary/model. A `before_commit` hook flushes and executes `SELECT 1 / 0`: delivery, two consumption markers and two seen reactions all roll back, and received=1/landed=0. A child interpreter reconstructs the settlement-only ChatService and subscription, commits those same-input effects and advances landed=1. Its channel allows only events, never send/steer. A second drain adds no duplicate reactions. The child does not restart ComputePool, the full executor or converse.

Nine existing drain/retention cases were migrated to the structured callback and explicit receiver/session/work fields because the protocol changed. They are not nine new cases or a repeat request for the previously approved narrow review.

`echo-correlation-negative.txt` records 2 failed, 4 deselected, exit 1 at `3fd1a367`. `missing_echo_identity_control.py` suppresses only the durable receipt association, without editing product source or assertions. Both child-interpreter recovery cases reach the echo observation and fail on the absent original `receipt_work_id`, including failed-drain admission. This is a correlation negative control, not a native/database-chain control. The manifest preserves output/control hashes.

`echo-recovery-first.txt` records six passed and one failed at `90e62d0b`: the integration fault predicate checked ORM dirty membership after effects had already autoflushed. The commit abort did not fire, and the test rejected that outcome. The corrected fixed-source run above supersedes it; the first run is not a product negative control.

## Pinned Claude echo through the settlement chain

Source: `bdbe461d0b0534751915ed521f57935a9d83d65c`.

`echo-native-first.txt` and its manifest record 2 passed, two test-JWT key-length warnings, exit 0: the substituted case and a new native case. Claude Code 2.1.282 runs in an isolated HOME/workspace with the existing deterministic localhost Messages API fixture. The stdin wrapper checks PostgreSQL registration before forwarding to real stdin; it does not manufacture the native echo. The runner journals exactly one `isReplay` record for the original input UUID and persisted work. The binary SHA256 is `3afe8535c0cc33f0e24f7b25dab7a1727b8b592196f8496a8bc302ba2161eed3`, matched against the official release manifest. Existing binaries and services were not replaced.

The same real PostgreSQL commit-path abort leaves the landing cursor before that echo and rolls back receipt/delivery/two consumed blocks/two seen reactions. A new Python interpreter reopens the existing journal, reconstructs the settlement-only ChatService/subscription and commits those effects without send/steer. Only the isolated test runner is closed after it produces its result. The native process is not restarted, no external live-model reasoning is tested, and neither full converse nor original-executor recovery is exercised. This is not COMMIT-success/confirmation-loss evidence.

## Shared-batch admission and consumption-release query (WIP)

Source: `148eaa1e`. `batch-ownership-first.txt` and manifest record 10 passed, 12 test-JWT key-length warnings, exit 0. Two real PostgreSQL transactions contend for the same two block rows through separate delivery attempts. A third connection observes the actual blocking PID before releasing the first transaction; registration commits before a counted external admission. Only one new identity registers and reaches that counter. Same-input retries verify one persisted identity; this test does not prove transport deduplication on that retry.

Three cases let independent seat/topic/project batches commit while the first holds its block locks. They use disjoint block IDs, not overlapping blocks assigned to two seats. Another case keeps initial holds after accepted/native_echo and after a different work's consumption marker, then removes them after the registered work's consumption. Four validation cases reject missing/foreign blocks or another receiver's seen effects without persisting a registration/reaction.

The external channel is a counter, not a native executor. Consumption in the release case is a direct repository operation, not a recovered executor Stop. The query uses the current block consumption marker; durable completion history and proven-never-sent runtime release still need implementation. Complete group effects are not exercised here.

`batch-ownership-negative.txt` and manifest record 1 failed, 9 deselected, exit 1 at `79728bbb`. `overlapping_batch_control.py` suppresses only overlap detection for new registration, retaining real row locks and all assertions. Both distinct identities register; the assertion rejects the second admission. It is an observable overlap failure, not a deadlock, setup failure or safety-timeout result. The manifest preserves the control and stdout hashes.

The existing contract now includes Delivery→NativeInput→sorted Block ordering, including INSERT/unique-key waiting before block locks. The new overlap/independent-batch tests do not rerun the previously closed Delivery→NativeInput checkpoint. Work completion and remaining group/caller transaction paths still require consistent locking and validation.

## Monotonic work-completion transaction (WIP)

Source: `8877768c`. `work-completion-routing.txt` and manifest record 8 passed, 8 test-JWT key-length warnings, 7.95s, exit 0. Native-shaped results pass through the real Claude assembler and ChatService completion transaction, against PostgreSQL. Exact clean work with a settled echo consumes and durably releases its registered batch; wrong work/session/receiver, error, interrupted result and missing echo do not release. Repeated completion is idempotent, and later consumption-marker replacement cannot resurrect released ownership.

A real `before_commit` flush followed by PostgreSQL `SELECT 1 / 0` abort rolls back consumption and release together; direct retry of the same completion commits both. This is not successful-COMMIT/lost-ack evidence. It is not a real native Stop, journal/cursor recovery, a new interpreter or original-executor restart. Hook completion now propagates transaction failure and keeps context, but its full subscription/recovery path remains unverified.

`work-completion-first.txt` records 2 failed/6 passed at `29488e43`: the shared helper used `claude_code`, while the real parser produces `claude-code`. Both positive completions were correctly excluded by exact identity. The fixture now uses the real harness constant; no identity check was removed. This first run is not a product negative control.

The completion path locks input rows by stable ID before block rows by stable ID. Released block IDs persist monotonically in the input row in the same transaction as original-work consumption. Synthetic/interrupted results do not carry successful completion evidence. Remaining harnesses, full runtime/caller cutover, safe never-sent release, group effects and original-executor recovery are still incomplete. The migration and this draft PR are not independently releasable.

## Native completion journal and late-steer execution owner (WIP)

Source `2b098b02613c225d1521a31f551efb7cc0f98e56`: `completion-execution-owner-first.txt` and manifest record 3 passed, 3 test-JWT warnings, 23.13s, exit 0. All three run pinned Claude 2.1.282 against the deterministic local Messages API, runner journaling, SQLite mirroring, PostgreSQL and a fresh Python settlement-only reader. They do not reconstruct full converse or original-executor continuation.

Two cases land the start and echo first, then abort completion commits four times with `before_commit` flush + `SELECT 1 / 0`. A preloaded refusal count above three/eleven minutes or three-day-old output cannot land the result before settlement. Runner expiry retains completion; mirror pruning retains its unlanded result. A wrong work result is rejected without release or cursor advance. New Python commits exact consumption/release, without send/steer, after the runner clears working/work. This is not COMMIT-success/lost-ack proof.

The late-steer case registers an input under W while W is working, then delays stdin until W completes. Pinned native echo opens unsolicited U, preserving admission W and recording execution U. W completes only its already-read input and leaves the late batch held. A new Python reader lands the late echo and U completion, consuming/releasing the late batch under U. This tests the runtime-to-runner boundary race, not the entire runtime submission path.

`completion-cursor-first.txt` at `9a0a9536` records 2 failures at the final cursor assertion: shutdown appended a record after result (actual 7, expected 6). Prior commit-failure/retention steps completed; the fixture now compares final landing with the full journal tail. No production guard was weakened. This is not a product negative control.

`completion-cursor-negative.txt` records the first suppression control: 1 failed, 1 deselected, 1 teardown error. Suppression reached `DID NOT RAISE DBAPIError`, but the TestClient portal also failed during teardown. This is not a clean red/green pair. A cursor-first assertion now checks premature landing before reporting the missing transaction failure; new control execution remains pending.

Legacy protocol strategies remain release blockers: main runners without a completion stamp have no new retention guarantee; the earlier draft stamp without input IDs cannot establish the actual completion input set; new-format stamps require exact echoed execution ownership. No format may invent completion from admission. Protocol discovery, retained legacy-log reconciliation and safe original-executor upgrade require implementation and verification. No shared runner is restarted. Full group/caller/UI/PDF goals remain in this draft.
