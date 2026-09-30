# Answer concurrency evidence

The final test source and lock-refresh fix are committed at
`fd844ba6415d3f11a64b95499ff225c89be6b916`. The evidence-only follow-up does not
change that source. `final-runs.json` records each complete argv, environment,
working directory, source commit, timestamps, exit code, output path and SHA-256.
All runs used the isolated `ask_s_concurrency` PostgreSQL slot and Redis DB 12.

## Final-source results

| Run | Result | Exit | Raw output |
| --- | --- | --- | --- |
| Three concurrent cases | 3 passed, 9 warnings, 5.99 s | 0 | `final-three-green.txt` |
| Concurrency + answers + migration | 22 passed, 35 warnings, 25.54 s | 0 | `final-22-green.txt` |
| Only lock-read refresh disabled | 3 failed, 9 warnings, 6.92 s | 1 | `final-refresh-disabled-red.txt` |

The three cases are same-operation retry, different operations against one old
version, and two corrections against one old version. A ContextVar binds HTTP
request A/B to its own ORM session independently of arrival order. Both sessions
finish their initial reads, retain their Block objects, and PostgreSQL locks A's
row before B requests it. An independent connection observes A in
`pg_blocking_pids(B)` before A may continue. A fresh session then checks persisted
answer versions, winner payload, timeline text count and Delivery event linkage.
The original authentication, transaction and delivery producer paths run intact.

`negative_control.py` disables only `populate_existing` on the locked select;
it does not change request payloads, row locks, assertions or product source.
Failures reach the persisted-state assertions, not setup or timeouts. Same-key
retry produces two timeline texts; competing old-version operations both return
200 and overwrite the winner; competing corrections also both return 200.

The 22-test run includes the three concurrency cases; it is not 25 unique tests.
Warnings are the test JWT signing-key-length warnings. Test delivery uses the
existing StubChannel fixture: these runs prove PostgreSQL/API behavior, not
native-model receipt, model consumption or restart recovery.

## Preserved earlier evidence

- `overlap-red.txt`: inherited backend at `1b639fb5`, three concurrent failures;
  test and evidence committed as `905a7f93`.
- `refresh-green.txt`: first lock-refresh run, before explicit HTTP A/B binding;
  22 passed, exit 0 (background handle `be9q9hmsw`).
- `refresh-disabled-red.txt`: first refresh-disabled control, before explicit
  HTTP A/B binding; three failed, exit 1 (handle `b6zmtuc29`).
- `setup-role-error.txt`: first invocation used the wrong default PostgreSQL
  role and failed during setup. This is NOT concurrency failure evidence.

The earlier test assigned A/B by initial-read arrival and assumed request A won.
The final source removes that scheduling ambiguity without weakening the winner
assertions. Older outputs remain untouched for comparison.
