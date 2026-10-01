# Preview lifecycle implementation map

Reference ZIPs were downloaded from the project library on 2026-10-01. Exact size, SHA256, ZIP CRC and extraction paths were verified before reading. The desktop bundles were read, not executed; their private runtime/backend services were not restored.

| Reference                                         |   Bytes | SHA256                                                           |
| ------------------------------------------------- | ------: | ---------------------------------------------------------------- |
| claude-desktop-2.16120.0-deep-reference.zip       | 2236176 | ce548ef3dc78a9b105c04e40e163bec2a70b102930f1e27290fb6a87ebfb7f6e |
| codex-desktop-26.928.2636.0-preview-reference.zip | 4722690 | ea5e7b48987d76793bf2b77bd32602c565ec2e0f2fefe1fd905c3e3181896ecb |
| cheese-doc-ai-handoff-20261001.zip                |   54113 | 6c3a892d7527d39159c5a0097e7d0bf61e0111efe335d47aa727fce2162989c9 |

## Source to product

| Source evidence                                                                                                           | Product boundary                                            | Checkpoint                                                                                                                                                                       |
| ------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| CC frame-shell `up` 4092–4137 separates incoming and usable outgoing frames                                               | `usePanelPreview.ts`, `PanelPreviewView.vue`                | Preserve unchanged named HTML context using a known topic/path/version identity. Keeping DOM is not a backend resource snapshot. Incoming/outgoing frames now stage replacement, retain the last observed loaded context on failure, and label its metadata identity. |
| CC `ip` 4075–4083 binds origin and contentWindow; handoff generation checks 4676–4703                                     | Composable lifecycle and future controlled runtime messages | Topic/path changes invalidate old reads and grants even without component remount. Runtime ready messages must bind origin, source, frame generation; not yet introduced.        |
| CC load/ready/reveal 4530–4546 and 5082–5129; Codex main M03 96398–96510                                                  | Metadata, authorization, navigation and runtime observation | Authorization is not page ready. Navigation load/error observation rejects readable about:blank and stale frame ids; arbitrary sites cannot be required to emit desktop private ready messages.                         |
| Codex W 96–146 checks claims and mount generation before deferred detach; H 59–96 bootstraps hidden host without painting | Panel visibility and cleanup                                | Keep unchanged context; do not transplant native Electron/Owl APIs.                                                                                                              |
| Codex A 204650–204902 stages, awaits attachment/viewport, restores outgoing host on cancel                                | Incoming page replacement                                   | Separate pending/displayed identities; native 100ms timeout is not an HTML network budget. Implemented with a 30-second visible-time navigation budget and explicit retry.                                                 |
| Codex main M06/M07 rejects late managed navigation/history restoration                                                    | Topic/file changes and retry targeting                      | Late grant cannot post into another topic. Full history restore is not implemented or verified.                                                                                  |
| C named HTML repro, formal component plus API/form substitutes                                                            | Owning `PanelPreview.session.spec.ts`                       | Same-v1 silent update, changed-v2/unknown version controls, different path and late topic grant are covered here.                                                                |

## Test boundary

The owning session suite mounts the real PanelPreview → usePanelPreview → PanelPreviewView chain. API results and HTMLFormElement.submit are substituted. It verifies grant/form intent, frame context retention and stale-operation rejection, not real content-domain navigation, cookies or backend availability.

Before the identity fix, the owning suite with regressions had 20 passes and 2 failures: same-version named HTML acquired a second grant; a topic change without remount did not load the new topic. The changed/unknown version controls passed. Browser input/scroll, runtime readiness, narrow panes, transport cancellation and deployed images are separate outstanding verification work.

## Independent review follow-up

The reviewer locked f63083d4 and reported 49 passes / 2 failures in the four owning suites, plus two independent component regressions and a visibility-budget regression. RequiredCI run 36792262752 frontend job 110148081364 failed the hardcoded-Chinese ratchet. That head remains draft and must not merge.

Downloaded review archives were verified against supplied size/SHA256 and ZIP CRC:

- `preview-handoff-f63083d4-review-evidence.zip`: 8075 bytes, `276d7f21cab9c0c719036383e3d5587abe8e93f9b65dd79560b5b89335ee9d7e`.
- `preview-visible-budget-f63083d4-review.zip`: 6645 bytes, `b9356d4f6ef12b659b87d60e9bdcd4fb54186b64a0bc680db0474c08d744b5e2`.

The owning suites now include canceled v2 reactivation while displaying v1, retained app unavailability, failure followed by unchanged/new metadata controls, and a no-interval hidden-time sequence. Reuse is based on displayed/incoming identity, not a separate POST-success cache. Failed targets stop automatic navigation attempts but do not stop metadata discovery. Version labels explicitly mean metadata at read time, without a content-domain server precondition. New strings use the existing English/Chinese catalogs; no baseline is relaxed.

The budget test uses the real composable and visibilitychange listener with clock/interval substitutes. It does not prove actual browser suspension. Poll tests now supply cross-origin navigation-load substitutes instead of letting blank frames time out. Real browser input/scroll, content-domain authorization, controlled runtime readiness, transport and deployment remain outstanding. Raw local and independent logs are retained in `docs/evidence/preview-lifecycle/`.

## Ownership

Starting main: d322374275756ffc081e5cc41eb0da3695026ee7. Docs PR2300 and its machine40 worktree are untouched. Open PR1413 owns ArtifactVersionPreview/ProjectArtifactView and office rendering; open PR1207 owns documentBytes/fileKind and office rendering. Both share frontend/api.ts; this checkpoint does not edit those paths. Right-hand panel positioning, tabs, scene coupling, source/download/history, content-domain authorization POST and iframe sandbox are retained.
