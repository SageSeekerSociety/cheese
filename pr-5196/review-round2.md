独立 review：head 5dcef00a（增量自 6244ba12）结论 有 1 条｜档位 B

**Independent review** of `6244ba12d812..5dcef00abf50`, method `.claude/pr-review/prompt.md` at `caecb51b6a18`

Delta 6244ba12..5dcef00a: reset() now marks the live follow dropped and clears the turn, ask() invalidates a pending open, and the PR body now answers the API-meaning question (否); all three round-1 majors are fixed, each with a test that fails at 6244ba12 and passes here. OCR selected 1 file and completed 1 (0 failed, 0 waived) and reported 2 candidates: 1 survived and was reproduced with a new failing test, 1 was dropped as naming (the token shared the name `answering` with open()'s local answer id; renamed anyway). No subagent read the PR's own words; the coordinator did.

Finders raised 2 candidates; refuters killed 1; 1 survived.

### 1. [major] A follow dropped by sign-out still runs its finally, settling the next person's live steps
`frontend/src/stores/agentThread.ts:810` · runtime · evidence rung 5

Failure: Person A's opened answer is still streaming when A signs out; B signs in and B's answer starts a running tool step; when A's old stream ends, follow's finally calls settleSteps() on the shared refs and marks B's running step done before B's tool returns.

Evidence: agentThread.ts:808-838 at 5dcef00a: the catch returns early when mine.dropped, but finally still runs settleSteps() and the append/clear block. New test "the last person's answer ending after sign-out does not touch the next person's answer" fails at 5dcef00a: expected [['r1','done']] to equal [['r1','run']].

**Not covered**

- frontend/src/stores/agentThread.spec.ts: test file, excluded by OCR's default allowlist; not reviewed.
- Step 5b: no spec named for this PR.
- Code outside the increment (out of scope for a delta round).

<sub>Evidence rungs: 1 stated · 2 code read · 3 related test run · 4 a loud-failing command run · 5 reproduced live. Only rung 4 and above is filed: a candidate below it is dropped, not rendered.</sub>
