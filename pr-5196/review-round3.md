独立 review：head 6b4cf95d（增量自 5dcef00a）结论 有 2 条｜档位 B

**Independent review** of `5dcef00abf50..6b4cf95d6b44`, method `.claude/pr-review/prompt.md` at `caecb51b6a18`

Delta 5dcef00a..6b4cf95d: follow's finally is skipped for a dropped follow and the token is renamed `following`; the round-2 major is fixed (its test fails at 5dcef00a and passes here). OCR selected 1 file and completed 1 (0 failed, 0 waived) and reported 2 candidates; both survived and were reproduced with new failing tests (0 dropped): sign-out before ask() reaches follow() is not caught, and ask()'s finally decides by the shared `following` instead of by its own request. No subagent read the PR's own words; the coordinator did.

Finders raised 2 candidates; refuters killed 0; 2 survived.

### 1. [major] Sign-out while ask() is still creating its conversation lets the last person's question run under the next session
`frontend/src/stores/agentThread.ts:409` · runtime · evidence rung 5

Failure: ask() is awaiting ensureConversation() when the person signs out; there is no follow to mark yet, so when the POST returns ensureConversation sets current and conversations, and ask() posts the last person's question and streams its answer into the cleared panel.

Evidence: agentThread.ts:409-410 marks only an installed follow; ensureConversation and ask() check nothing after their awaits. New test 'signing out while a question is still creating its conversation…' fails at 6b4cf95d: /ask was called once.

### 2. [major] The last person's ask() finishing clears the next person's question while it is still being set up
`frontend/src/stores/agentThread.ts:556` · runtime · evidence rung 5

Failure: After sign-out the next person asks (streaming '' but no follow yet, so `following` is null); the last person's ask() then ends and its finally sees `!following`, sets streaming to null, and busy drops to false mid-question.

Evidence: agentThread.ts:556 keys on the shared `following`. New test "the last person's question ending while the next person's question is being set up…" fails at 6b4cf95d: expected busy false to be true.

**Not covered**

- frontend/src/stores/agentThread.spec.ts: test file, excluded by OCR's default allowlist; not reviewed.
- Step 5b: no spec named for this PR.
- Code outside the increment (out of scope for a delta round).

<sub>Evidence rungs: 1 stated · 2 code read · 3 related test run · 4 a loud-failing command run · 5 reproduced live. Only rung 4 and above is filed: a candidate below it is dropped, not rendered.</sub>
