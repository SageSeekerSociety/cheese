# Canonical living-document data contract

This stage adds a transactional journal. It does not establish AI request/proposal execution or complete deployment/page verification.

## Source to implementation

- Existing `TopicService.edit_doc` at baseline be4b3b0d1 delegated block CAS, node reconciliation and persisted contribution events. Those effects now live in `block/documents.py:DocumentWriter`; `TopicService` retains room resolution and archive checks. The original block-level `SequenceMatcher` reuse and SQL `set_doc_content` are preserved, not replaced with rendered-text round-tripping.
- Existing route `topics.py:edit_topic_doc` performed mention canonicalization and post-commit notifications. It moves to `api/routes/living_docs.py`; the old GET/PUT paths remain. Ordinary PUT still canonicalizes friendly references. Restore writes the stored raw snapshot without a second whole-document canonicalization.
- Supplied `cheese-doc-ai-handoff-20261001/reference/cheese-doc-comments-permission-design.md`, sections “幂等与异步恢复” and “原文范围”: stable verified actor/document/action/op key, separate fingerprint, claim/effect/result transaction, exact source coordinates. `living_doc/services.py` implements room lock + operation claim + receipt; `source_span.py` defines UTF-8 byte offsets only. These primitives do not authorize AI acceptance by themselves.

## Stored contract

`living_doc_locks` serializes ordinary writes and operations per canonical room, including absent-document creation. The existing conditional SQL update remains the CAS check. Every new canonical write stores raw content, SHA256 of UTF-8 bytes, version, previous/base versions, actor, optional operation_id and persisted contribution event id in the same transaction as root/node changes.

`living_doc_operations` uniquely claims `(room_id, verified actor handle, action, operation_id)`. Payload fingerprint is a separate column. Same key/payload returns the original JSON receipt, even if another writer has advanced. Different payload returns 409. Older receipts are not latest snapshots: clients retain their response/version fences and use GET for current correctness.

The verified credential resolves the handle. `body.author` cannot provide operation authentication. Agent writers remain allowed for ordinary canonical maintenance; these receipts do not grant human AI-accept authority. New restore input forbids unknown fields.

Pre-journal deployed snapshots are seeded on the first successful update. Earlier unavailable versions are not fabricated. New brief seeds are journaled without inventing a conversation contribution event. History exposes persisted per-document version cursors; a durable notification outbox and reconnect transport are still pending.

Migration `e9c2a71d4b60` chains `c4d7e2a9f158`. Production migration execution is normal CI/CD only. Local tests build the isolated test schema through existing fixtures, not manual production migration commands.

## Actual verification

Exclusive machine: cheese-de808b-46. Resolved implementation: `/home/cheese/docs-sync-rebuild`. PostgreSQL test container `docs-s-journal-pg2`, host `127.0.0.1:5443`; Valkey `docs-s-journal-valkey`, host `127.0.0.1:6389`. Namespace `CHEESE_CI_SLOT=docs_s_journal`, Redis base DB 0. One integration pytest process at a time; existing fixture boundaries unchanged.

- First DB run: 3 passed / 12 failed, exit 1. HTTP failures exposed a misplaced persisted-notice import after commit. Original log retained.
- Corrected route run: 14 passed / 2 failed, exit 1. The two new operation cases used unauthenticated fixture requests and correctly received 401. No authorization was weakened.
- Authenticated journal run: 5 passed, exit 0. Independent sessions test concurrent first creation and same-base writes, exactly one winner and no losing node/event/history effects; rollback removes claim and effects; commit-lost-response replay and query preserve the original receipt; payload mismatch 409; restore creates N+1 while emoji/CRLF raw bytes survive.
- Ordinary node/notice/nudge regressions: 22 passed, exit 0.
- Exact source span + frozen repository guard: 29 passed, exit 0.
- Module boundary checker: three contracts kept, no baseline/exemption expansion. One Alembic head verified. Focused seeded-brief regression remains pending.

Original logs and exit files are in `/home/cheese/docs-evidence/runs`. The first test PostgreSQL initialization exited 1 during its temporary-server shutdown; that container and full original log are retained, not restarted. A separate container with a longer initialization timeout reached readiness.

## Remaining boundaries

No latest-head Required CI or independent canonical-stage review yet. No canonical-stage merge/deploy claimed. Historical rows are append-only through this application API; database-level immutability enforcement is pending. Durable notification dispatch, stricter operation actor identity lifecycle, concurrent operation replays, source-map selection issuance/node validation, AI persisted lease/recovery, tool-less project-bound completion and authenticated-human acceptance remain to implement and test.
