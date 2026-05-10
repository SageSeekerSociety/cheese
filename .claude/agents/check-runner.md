---
name: check-runner
description: >
  Run ruff + pyright + pytest via check.sh. Run this in background while
  doing code review work in parallel. Reports concise pass/fail summary.
tools: Bash, Read
model: sonnet
---

Run the project check script:

```bash
bash .claude/scripts/check.sh
```

Set Bash timeout to **20 minutes** (1200000ms). Full tests take ~10-12 min.

When done, report only:
- PASS or FAIL per step (ruff / pyright / pytest)
- If FAIL: which tests failed (last ~30 lines of pytest output)
- If PASS: just say "All checks passed" — no more than 3 lines.
