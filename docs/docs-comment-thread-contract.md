# Document comment threads

A comment is a row of `document_comments`, under its document: one that opens a
thread, or a reply in one (`thread_id`, numbered by `sequence` from 1). A
thread's state is its row in `document_threads`. These routes do not use the
community-comment domain or its authorization.

`POST /documents/{document}/comments` starts a thread: `{content, quote?}`,
where `quote` is the selected words kept for display. Which words a thread is
about is not stored here: the commenter's editor puts a `commentAnchor` mark
carrying the thread id on them in the shared document
(`frontend/src/lib/docSchema/commentAnchors.ts`), so the words carry their
thread through every later edit. A frozen document (an archived room's, or any
document of an archived project) takes no new thread, reply, resolution or
reopening: 422.

## HTTP contract

Responses use the existing `{code, message, data}` envelope. Successful writes
return HTTP 200 and the full thread snapshot. Strict-input validation returns
400.

| Method | Path after `/documents/{document}/comments` | Request | Response data |
| --- | --- | --- | --- |
| GET | `/threads` | none | `{data: ListedThread[], total}` |
| GET | `/{comment_id}/thread` | none | Thread |
| POST | `/{comment_id}/replies` | `{operation_id, expected_revision, content}` | Thread |
| POST | `/{comment_id}/resolve` | `{operation_id, expected_revision}` | Thread |
| POST | `/{comment_id}/reopen` | `{operation_id, expected_revision}` | Thread |
| POST | `/{comment_id}/agent/stop` | none | `{}` |

Thread is `{comment: Comment, revision, state, replies}`; Comment is
`{id, author, content, anchor_quote, created_at}`. State is `open` or
`resolved`; initial revision is 1. Replies are `{sequence, comment: Comment}[]`,
ordered by sequence, starting at 1. ListedThread adds `answering`: `queued`
while the document's agent waits for a free session to answer the thread,
`working` while it answers, otherwise null. Opening comments are ordered by
`(created_at, id)`. These lists return all rows, with no cursor.

Every committed change to a document's threads (a new thread, a reply,
including the agent's, a resolution or reopening) is told to whoever has the
document open (`collab.tell`, a stateless message on their collaboration
connection): `{type: "state", resource: "comments"}`, and they read the list
again. While the agent answers a thread they also hear `{type:
"comment_activity", thread, state, tool?}`; neither is replayed, since the list
says the same to a page that opens later.

Every successful mutation increments revision once. Reply content is nonblank,
at most 16,000 characters and preserved verbatim. Resolved threads must be
reopened before another reply. A new operation attempting an already-achieved
state returns 409 instead of claiming a change.

## Identity and recovery

Reads and writes of threads require a verified live account that may reach the
document (`app/api/doc_access.py`): a room's document follows the room-member
policy, enforced even when the development authz switch is off; a document of
the project's own follows project membership. Authenticated agents may
participate. Body authors, parents, references and anchor fields are rejected.
The server records the resolved author and keys operation ownership by stable
numeric user id. Another document's comment and a reply used as a thread id
return 404 after caller authorization.

All mutations share the `comment-thread` document-journal action. Payload
fingerprints include the opening comment id, action, expected revision and
content. Same document/account/operation and identical request replays the
original receipt. Changing any of those payload fields returns 409. Different
operations competing on one revision yield one success and one 409, with no
failed reply or claim. The document's lock serializes the claim, revision
check, reply/state and receipt; commit makes all visible together.
Authorization is checked again on replay.

Persist operation_id before sending; retain it after unknown outcomes. Retry the
same request to recover its receipt, then GET thread to read current state. An old
receipt is not proof of the current revision. Do not retry a 409 as success or
silently create a new operation with another revision.

## Implementation and boundaries

`living_doc/comments.py` owns the service, with `living_doc/comment_schemas.py`;
`api/routes/document_comments.py` is the HTTP boundary. No agent turn, document
history entry or canonical refresh is emitted by thread mutations. Deleting the
document deletes its comments. There is no pagination, notification or
comment-edit/delete endpoint. The opening POST is not idempotent.
