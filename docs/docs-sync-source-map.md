# Docs synchronization: source map and verification

## Reference boundaries

The reference is Claude Desktop 2.16120.0 shipped JavaScript, statically read, not an executed application. `c05322358-BgaPs5b0.js` SHA256 is `7b4dc2fb0c227c538c96dac0b034bdc591c04420bb66ae6c3d5c4accd439d104`. Lines below refer to the supplied readable copy; original bytes/offsets are in the project library capsules.

| Reference function | Observed boundary | Cheese implementation |
| --- | --- | --- |
| `Et`, lines 1461–1499 (raw 28985–29609) | External callbacks check their generation and whether the conflict still exists. | `docRequestGate.ts` scopes responses to a topic generation; `usePanelDoc.ts` checks ownership after document awaits and invalidates pre-write reads. |
| `Ut`, lines 1879–1972 | Confirmation acknowledges submitted source; subsequent input remains pending. | `usePanelDoc.save` establishes the base from canonical receipt content/version, never installs over newer input, and queues remaining edits. |
| `Dt`, lines 1501–1623 (raw 29609–31277) | Incoming, persisted base and live source remain distinct. Merge depends on unrecovered WASM. | Existing conflict UI, lossy pause/source mode and stash remain. No WASM merge or approximate matching authorizes writes. |

Evidence: `cheese-docs-save-runtime-f135b3be.zip`, SHA256 `0636e569f18d07f4477ddc0a51c6ca3d6fc8af9d509d90fcc3b776030bcbaafc`. All 32 payload hashes and CRCs were verified, as were the seven original Git blobs. These files were unchanged at starting main `c24bd75b7aae1d1b38d7976188c04e53addad1b7`.

## First-stage contract

- Responses belong to a topic generation. Switching a surviving panel or unmounting cannot install another topic's response or clear the successor's status.
- Accepted GET versions are monotonic. An earlier request cannot replace an already accepted later request. PUT invalidates reads issued before it, even if they arrive after PUT settles.
- Initial/activity loads decide whether local input needs preservation after awaiting.
- Notifications during PUT latch one follow-up GET. Success, lost responses and CAS refusal preserve the notification. No error retry loop or blind PUT replay.
- Canonical receipts establish persisted content/version, not submitted drafts. Input during PUT stays dirty in the visual/source editor.
- Title preservation, lossy pause, source mode, stash, CAS choices and incremental Editor installation remain. No UI-owner file changes.

## Verification boundary

Owning `PanelDoc.save.spec.ts` and `PanelDoc.load.spec.ts` mount real `PanelDoc → PanelDocView → DocSurface → Tiptap Editor`. Deferred API doubles control GET/PUT ordering; `activityTick` supplies notifications at the actual panel prop boundary. Monaco retains the existing textarea double. This does not test production WebSocket, dual browser sessions or database transactions.

Before fix: the two supplied schedules reproduced 7 passed / 2 failed, exit 1. Expanded suites exercise overlapping GETs, slow mount, dirty changes during await, pre-write GETs, canonical receipts, visual/source input during PUT, success/failure/409 reconciliation, topic switches and unmounting. Full commands/output/exits are retained in the implementation evidence bundle. Skips and empty stdout are not success.

This phase does not implement immutable database history, operation receipts, durable events, AI requests/proposals or human acceptance. Those require separate transactional/authentication verification.
