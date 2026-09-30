# Native receipt evidence

These tests exercise real PostgreSQL transactions. They do not prove live native-model consumption, runtime integration, a process restart, or injected commit failure.

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

Runtime registration before RPC, journal cursor retry, seen reactions and complete group effects remain unfinished at this checkpoint. The migration is not independently releasable.
