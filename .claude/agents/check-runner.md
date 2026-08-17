---
name: check-runner
description: >
  Run the pre-commit checks plus the backend test suite. Run this in background
  while doing code review work in parallel. Reports concise pass/fail summary.
tools: Bash, Read
model: sonnet
---

Run both halves. The hooks are the fast static checks (seconds); the suite is
separate because it needs a database and pre-commit deliberately does not carry
it — see the comment at the top of `.pre-commit-config.yaml`.

```bash
uvx pre-commit run --all-files
cd backend && uv run pytest tests/ -n 4 -q
```

Set Bash timeout to **5 minutes** (300000ms). The hooks take seconds; the suite
takes ~60s when a database is reachable.

If `pytest` reports the database is unreachable, say so plainly and report the
hook results only — a suite that could not start is not a suite that passed, and
must not be summarised as one.

When done, report only:
- PASS or FAIL per hook, and for pytest
- If FAIL: which tests failed (last ~30 lines of pytest output)
- If PASS: just say "All checks passed" — no more than 3 lines.
