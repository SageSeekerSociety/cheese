独立 review：head 6244ba12 结论 有 3 条｜档位 B

**Independent review** of `6244ba12d812`, method `.claude/pr-review/prompt.md` at `caecb51b6a18`

The change makes the assistant store drop stale conversation opens (a newer open, a new topic or sign-out wins) and stops show() from reopening while an answer streams, so one answer is no longer followed and written twice. OCR selected 1 file and completed 1 (0 failed, 0 waived); it reported 2 candidates, both survived step 3 and were reproduced with new failing tests (0 dropped), and step 5 added 1 claim candidate (the PR body does not answer the template's API-meaning question). No subagent read the PR's own words; the coordinator did.

Finders raised 3 candidates; refuters killed 0; 3 survived.

### 1. [major] reset() leaves an answer opened by open()/heard() streaming, so busy stays true and the next person's load() is swallowed by the new busy guard
`frontend/src/stores/agentThread.ts:407` · runtime · evidence rung 5

Failure: Panel opens a conversation still answering (open -> follow), the person signs out (reset) before the answer ends: the old stream keeps writing streaming, follow's finally skips clearing because current is now null, busy stays true; the next person's load() -> open() returns at `turn !== opening || busy.value`, so their panel shows nothing until a page reload, and the last person's half answer stays in streaming.

Evidence: agentThread.ts:370 (open returns when busy), :397-409 (reset clears neither streaming nor the live follow), follow's finally clears only when current === conversation. New test 'signing out while an opened answer is still coming…' fails at 6244ba12d: expected '先会 gdb。' to be null.

### 2. [major] A question asked while an open() read is in flight is replaced by that read when the answer finishes first
`frontend/src/stores/agentThread.ts:370` · runtime · evidence rung 5

Failure: show('c1') starts reading c1; the person asks a question, which creates c2 and is answered before c1's read returns; the read then passes `turn === opening` and `busy` false and swaps current/messages to c1, so the question just asked disappears from the panel.

Evidence: ask() at 6244ba12d never bumps `opening`; open() only checks turn and busy (agentThread.ts:366-370). New test 'a question asked while a conversation is still being read…' fails at 6244ba12d: expected 'c1' to be 'c2'.

### 3. [major] PR body does not answer the template's yes/no question on changes to existing API or push-message meaning
`frontend/src/stores/agentThread.ts:0` · claim · evidence rung 4

Failure: The body at the pinned commit is only the title, Link and Co-authored-by, so a reader cannot tell whether existing API or realtime message meaning changed.

Evidence: grep -c '是否改变了已有接口' pr.md -> 0.

**Not covered**

- frontend/src/stores/agentThread.spec.ts: test file, excluded by OCR's default allowlist; read by the coordinator only while adding the regression cases, not reviewed.
- Step 5b: no spec named for this PR.
- Behaviour in the real assistant backend stream (OCR reads code only); the browser repro used an in-page stand-in for /api/assistant/conversations*.

<sub>Evidence rungs: 1 stated · 2 code read · 3 related test run · 4 a loud-failing command run · 5 reproduced live. Only rung 4 and above is filed: a candidate below it is dropped, not rendered.</sub>
