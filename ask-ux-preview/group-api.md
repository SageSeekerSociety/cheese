# Ask group adapter contract

Implementation target for S and O, fixed 2026-10-01. This specifies the approved `contract.md` group behaviour for a real frontend adapter; the group routes and receipt reader are not yet implemented. Existing single-answer responses remain a Block. Do not simulate atomic group settlement with a loop of single-answer requests.

## Scope and question creation

The unique group is `(topic_id, asked_by, group_id)`. `topic_id` is the place identifier on the question Block (a task thread keeps its thread ID). `asked_by` is the authenticated question author's conversation handle, not a display name or an acting-seat token handle. It is addressing data, never an authorization credential.

`POST /topics/{topic_id}/ask` receives:

```json
{
  "questions": [
    {
      "question": "Which entry?",
      "options": [{"text": "A", "explain": "First entry"}, {"text": "B"}],
      "allow_other": true,
      "reject_option": true
    }
  ],
  "ask_group": "caller-generated-or-omitted-group-id"
}
```

One to eight questions; each has two to three object options. The service generates a group UUID if omitted. It validates the whole request before adding questions, fixes the member Block UUIDs in array order and persists identical members/total/asked_by/id with each Block's own index. No adding members to an existing group. The sandbox schema and callers cut over together; there is no string-option alternate path.

The normal `ok(...)` wrapper contains this data:

```ts
interface AskGroupData {
  group: {
    topic_id: string
    asked_by: string
    id: string
    members: string[]
    total: number
  }
  blocks: Block[] // full member set in fixed index order
  settlement: GroupSettlement | null
  receipt: AskReceipt | null
}
```

Creation returns `settlement: null, receipt: null`. Group progress counts answer_log latest versions across the persisted members, not just currently loaded chat Blocks.

## Atomic settlement

`POST /topics/asks/{group_id}/settle`:

```ts
interface GroupSettlementRequest {
  topic_id: string
  asked_by: string
  answered: Array<AnswerPayload & { block_id: string }>
  later: Array<{ block_id: string; client_op_id: string }>
  unanswered: Array<{ block_id: string; client_op_id: string }>
  expect_version: number // group settlement version, initially 0
  client_op_id: string // stable whole-group operation key
  author?: string // legacy fallback only; authenticated actor owns all answers
}
interface AnswerPayload {
  kind: 'option' | 'note' | 'reject'
  option?: string
  note?: string
  expect_version: number // this question's latest answer version, initially 0
  client_op_id: string // stable per-question operation key
}
```

- Each question has its own expect_version, independent of the group expect_version. Reusing the group version as a question version is invalid.
- The three lists contain no duplicate Block IDs, do not overlap and exactly cover group.members. List order may differ; the response order cannot.
- Authenticate the caller before reading/replaying a result; lock all member Blocks by ascending UUID, refresh under the locks, validate their identical scope and complete membership. Validate/apply all answers in that transaction; any failure rolls back the whole group.
- Replay the same group operation with identical canonical payload returns the existing result without another answer version, timeline wake or Delivery. Changing payload under that operation key is 409. A new operation requires the current group version. Payload normalization includes both scope and every question operation/version/content, with lists sorted by Block UUID.
- later/unanswered never append answer_log. Previously answered questions remain answered and count toward progress, even when listed later/unanswered. The wake describes the submitted/later/unanswered lists and explicitly mentions previously answered questions; it never falsely says the whole group is answered.
- One successful settlement persists group_settle on every member and one independent timeline wake/Delivery event, not one Delivery per answered question plus a group wake. The event belongs to the precise scope/version.
- “回去补” sends nothing. “照样交” may submit with unanswered members. Failed transport retries reuse the exact group/per-question keys and payload. A fresh edit is a new operation, not a mutated retry.

Success returns `ok(AskGroupData)` with all committed Blocks and:

```ts
interface GroupSettlement {
  v: number
  at: string | null
  by: string
  answered: string[]
  later: string[]
  unanswered: string[]
  payload_hash: string
  client_op_id: string
  delivery_event_id: string
}
```

422 means invalid contents/member coverage/original-answerer restriction; 409 means version or operation-payload conflict. Standard 401/403 protect membership; no unauthorized result replay.

## Refresh and receipt reading

`GET /topics/asks/{group_id}?topic_id=...&asked_by=...` returns authenticated `ok(AskGroupData)`. This is the same scope and response used after settlement; it re-reads persisted members, settlement and exact latest event receipt, never fabricates successful continuation from local selection. O's adapter may define this read target now, but must not call an absent API as if it worked.

```ts
interface AskReceipt {
  event_id: string
  state: 'pending' | 'claimed' | 'sending' | 'uncertain' | 'failed' | 'received' | 'unavailable'
  attempts: number
  last_error: string | null
  sent_at: string | null
  received_at: string | null
  completed_at: string | null
}
```

Only the exact group event and original recipient are read. `received_at` means ledger receiver confirmation; `completed_at` requires verified completion of all exact associated inputs, not RPC accepted, echo alone or idle ping. `unavailable` means no addressable original executor/receipt row; show that continuation is unconfirmed, not delivered. Null receipt means no settlement event exists yet. Unknown/uncertain never authorizes blind resend.

The refresh/receipt route is a reader, not a reconciliation writer or manual resend action. A genuine delayed receipt can advance the ledger; polling cannot. Existing single-answer API remains `ok(Block)`; the new group envelope must not silently replace that response. Single legacy question receipt display remains an explicit integration/acceptance item rather than pretending it has group metadata.

## Delivery ownership

O exclusively owns frontend in its isolated checkout. S owns backend/group/CLI/C2 and integrates the reviewed frontend fixed head into the existing draft PR. The API shapes above are the target, not evidence that these routes work. Full uncertain read/late receipt, proven-never-sent explicit release and all harness recovery wiring remain required acceptance items. No migration-only release, shared-service restart or deployment is authorized by this adapter contract.
