# Passive document comments and draft checkpoint

This checkpoint makes ordinary document comments passive and protects unsent text. It does not implement comment threads, the desktop sidebar, or cloud Slides/Design editors.

## Source-to-behavior map

Reference: verified Claude Desktop 2.16120.0 deep archive, `readable/frame-shell-chrome-CXu04DiJ.js`.

| Source symbol and lines | Observed behavior | Cheese implementation / boundary |
| --- | --- | --- |
| `Sv`, 11153–11190 | Host passes `draftActive`, `editActive`, and `busy`; these aggregate into a guard. | `useDocCommentDraft.ts` owns active selection records and per-draft sending state. `DocComments.vue` receives the transport as a prop. Cancel during posting is blocked. |
| `Sv`, 11191–11196 | Blocked switching explains posting/writing/editing rather than silently dropping text. | This checkpoint retains drafts by authenticated UI author, topic, anchor and quote instead of dropping them when navigation occurs. Sidebar switching explanations remain a later UI step. |
| `kE`, 17846–17878 | Active composer survives filtering; its list order freezes while composing. | Source requirement retained for the next thread/sidebar checkpoint. This flat comment list has no thread filtering or sort operation; no claim of desktop sidebar parity. |
| `kE`, 17940–17948 | Closing while posting is disabled. | Cancel and Escape cannot delete a sending draft. Composition Enter (`isComposing` or keyCode 229) is not a send action. |

No original JavaScript bundle is executed. The source establishes interaction constraints, not Cheese authorization. Passive comments are a separate product contract: recording a comment changes no document text, and only a comment that @-mentions the room's agent is answered.

## Server contract

`POST /topics/{id}/comments` inserts a UUID document-view comment and commits it. Only a comment, or a reply in its thread, that @-mentions an agent seated in the room (`<@handle>`) is answered: by the thread's own session on the session host, not by a turn of the room, and the answer is the agent's reply in the thread (`app/domain/agent/document/thread.py`). A comment names no document node: the commenter's editor marks the selected words in the shared document with the thread id, and the comment keeps a bounded display quote, which is not write authorization.

General room-agent document writing remains untouched.

## Draft and transport contract

Networking is in `usePanelDoc.ts`; the view passes a transport function down. The draft composable knows no HTTP client. Session storage is best-effort; failures retain the in-memory draft. Author/topic/selection records survive component unmounts. A successful receipt only clears its submitted revision; later edits and other selections remain. Old-topic receipts do not refresh the new topic. Unmounted senders do not emit refreshes.

An explicit cancel discards the active unsent draft. No reload recovery claim is made for the outcome of an upstream send: a reload during posting may leave a retained draft whose delivery must be checked before retrying. No request idempotency or exactly-once delivery claim is made here.

## Verification

Production component and state tests cover IME Enter, newline Enter, successful send, failure/retry, later edits, topic switching, selection switching, author isolation, unmounts and persistence after remount.

Backend tests use the owning FastAPI `client` fixture and actual isolated PostgreSQL/Redis. `seed_user` provides a DB-backed human with a numeric user-id bearer token. Runner is replaced by a recording boundary; no upstream model is called. Assertions compare the persisted document/version, node tree and conversation timeline before/after commenting. Invalid historical/orphan anchors are seeded as UUID `Block` records, not the integer community `Comment` model.

Raw local logs live outside the repository under `/var/tmp/docs-comments-*`. Exact results and commit identity are reported with the review checkpoint. Chinese screenshots/PDF remain required before UI acceptance. Fixture screenshots do not prove backend collaboration, multi-session synchronization or deployment.
