# Room document comment threads

The original comments are `BlockKind.comment` rows. These routes do not use the
community-comment domain or its authorization. Existing GET/POST
`/topics/{room}/comments` remain available; GET lists original comments only.
The room id is a real topic, not a task workspace id.

## HTTP contract

Responses use the existing `{code, message, data}` envelope. Successful writes
return HTTP 200 and the full thread snapshot. Strict-input validation returns
400; invalid existing anchor input retains the existing 422 behavior.

| Method | Path after `/topics/{room}/comments` | Request | Response data |
| --- | --- | --- | --- |
| GET | `/threads` | none | `{data: ThreadSummary[], total}` |
| GET | `/{comment_id}/thread` | none | Thread |
| POST | `/{comment_id}/replies` | `{operation_id, expected_revision, content}` | Thread |
| POST | `/{comment_id}/resolve` | `{operation_id, expected_revision}` | Thread |
| POST | `/{comment_id}/reopen` | `{operation_id, expected_revision}` | Thread |

Thread is `{comment: BlockOut, revision, state, anchor, replies}`. State is
`open` or `resolved`; initial revision is 1. Replies are
`{sequence, comment: BlockOut}[]`, ordered by persisted sequence, starting at 1.
ThreadSummary replaces replies with `reply_count`. Original comments are ordered
by `(created_at, id)`. These lists currently return all rows, with no cursor.

Every successful mutation increments revision once. Reply content is nonblank,
at most 16,000 characters and preserved verbatim. Resolved threads must be
reopened before another reply. A new operation attempting an already-achieved
state returns 409 instead of claiming a change.

## Identity and recovery

New thread reads and writes require a verified live account and the existing
room-member policy, enforced even when the development authz switch is off.
Authenticated room agents may participate; human-only AI-proposal acceptance is
unrelated. Body authors, parents, references and anchor overrides are rejected.
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

## Historical anchor

Anchor contains `{node_id, quote, node_content, document_id, base_version,
start, end, offset_unit: 'utf8-bytes'}`. New root comments capture the whole
anchored node's exact raw byte range on their saved canonical version, under the
same lock as document writes. This is NOT a quote's exact subrange. Quote remains
display evidence; no quote search or relocation occurs. Repeated text, CRLF and
emoji do not change the coordinate unit. Comments do not authorize document edits.

Migration preserves old node id, surviving node content, root id and quote. Old
version/range remains null because creation history cannot be reconstructed.
Already-deleted nodes cannot be recovered. Later canonical edits may null the
original block's node foreign key but cannot change the saved historical anchor.
Resolve/reopen retain original content, replies, references and anchor.

## Implementation and boundaries

`block/comment_threads.py` owns the bounded service, with `comment_models.py` and
`comment_schemas.py`; `topics_comment_threads.py` is the thin HTTP boundary.
`topics_comments.py` only captures root anchors and filters root reads. The
normal migration adds thread/reply metadata and backfills surviving old comments;
existing Block data and deployed migrations are unchanged. No room-agent turn,
document history entry or canonical refresh is emitted by thread mutations.

The service uses the existing room journal lock and completed immutable receipt
transaction; replies remain comment blocks linked to their original root.
Thread anchor updates are rejected by a database trigger. Parent room deletion
still cascades; direct low-level block deletion and migration downgrade are not
user-facing thread actions. There is no thread WebSocket event, pagination,
notification or comment-edit/delete endpoint in this stage. Existing root POST
is still the original non-idempotent API. UI owners implement their own wiring;
this backend change does not modify UI, AI request/proposal/accept, preview,
network, Slides or admin.
