# Room document comment threads

The original comments are `BlockKind.comment` rows. These routes do not use the
community-comment domain or its authorization. `POST /topics/{room}/comments`
starts a thread: `{content, quote?}`, where `quote` is the selected words kept
for display. Which words a thread is about is not stored here: the commenter's
editor puts a `commentAnchor` mark carrying the thread id on them in the shared
document (`frontend/src/lib/docSchema/commentAnchors.ts`), so the words carry
their thread through every later edit. The room id is a real topic, not a task
workspace id. An archived room's document is frozen: starting, replying to,
resolving and reopening threads there is refused with 422.

## HTTP contract

Responses use the existing `{code, message, data}` envelope. Successful writes
return HTTP 200 and the full thread snapshot. Strict-input validation returns
400.

| Method | Path after `/topics/{room}/comments` | Request | Response data |
| --- | --- | --- | --- |
| GET | `/threads` | none | `{data: ListedThread[], total}` |
| GET | `/{comment_id}/thread` | none | Thread |
| POST | `/{comment_id}/replies` | `{operation_id, expected_revision, content}` | Thread |
| POST | `/{comment_id}/resolve` | `{operation_id, expected_revision}` | Thread |
| POST | `/{comment_id}/reopen` | `{operation_id, expected_revision}` | Thread |

Thread is `{comment: BlockOut, revision, state, replies}`. State is `open` or
`resolved`; initial revision is 1. Replies are `{sequence, comment: BlockOut}[]`,
ordered by persisted sequence, starting at 1. ListedThread adds `answering`:
`queued` while the room's agent waits for a free session to answer the thread,
`working` while it answers, otherwise null. Original comments are ordered by
`(created_at, id)`. These lists currently return all rows, with no cursor.

Every committed change to a room's threads (a new thread, a reply, including
the agent's, a resolution or reopening) sends the room a `{type: "state",
resource: "comments"}` frame, and open pages read the list again. While the
agent answers a thread the room also hears `{type: "comment_activity", thread,
state, tool?}`; those frames are fanned out live and never replayed, since the
list says the same to a page that opens later.

Every successful mutation increments revision once. Reply content is nonblank,
at most 16,000 characters and preserved verbatim. Resolved threads must be
reopened before another reply. A new operation attempting an already-achieved
state returns 409 instead of claiming a change.

## Identity and recovery

New thread reads and writes require a verified live account and the existing
room-member policy, enforced even when the development authz switch is off.
Authenticated room agents may participate. Body authors, parents, references
and anchor fields are rejected.
The server records the resolved author and keys operation ownership by stable
numeric user id. Foreign-room, task-scoped, non-comment and reply-as-root ids
return 404 after caller authorization.

All new mutations share the `comment-thread` document-journal action. Payload
fingerprints include the root comment id, action, expected revision and content.
Same room/account/operation and identical request replays the original receipt.
Changing any of those payload fields returns 409. Different operations competing
on one revision yield one success and one 409, with no failed reply or claim.
Room locking serializes the claim, revision check, reply/state and receipt;
commit makes all visible together. Authorization is checked again on replay.

Persist operation_id before sending; retain it after unknown outcomes. Retry the
same request to recover its receipt, then GET thread to read current state. An old
receipt is not proof of the current revision. Do not retry a 409 as success or
silently create a new operation with another revision.

## Implementation and boundaries

`block/comment_threads.py` owns the bounded service, with `comment_models.py` and
`comment_schemas.py`; `topics_comment_threads.py` is the thin HTTP boundary and
`topics_comments.py` starts threads. No room-agent turn, document history entry
or canonical refresh is emitted by thread mutations; the service uses the
existing room journal lock and completed immutable receipt transaction, and
replies remain comment blocks linked to their original root. Parent room
deletion still cascades. There is no pagination, notification or
comment-edit/delete endpoint. The root POST is not idempotent.
