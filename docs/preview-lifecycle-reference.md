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
| CC frame-shell `up` 4092–4137 separates incoming and usable outgoing frames                                               | `usePanelPreview.ts`, `PanelPreviewView.vue`                | Preserve unchanged named HTML context using a known topic/path/version identity. Keeping DOM is not a backend resource snapshot. Incoming/outgoing handoff remains to implement. |
| CC `ip` 4075–4083 binds origin and contentWindow; handoff generation checks 4676–4703                                     | Composable lifecycle and future controlled runtime messages | Topic/path changes invalidate old reads and grants even without component remount. Runtime ready messages must bind origin, source, frame generation; not yet introduced.        |
| CC load/ready/reveal 4530–4546 and 5082–5129; Codex main M03 96398–96510                                                  | Metadata, authorization, navigation and runtime observation | Authorization is not page ready. Navigation observation remains to implement; arbitrary sites cannot be required to emit desktop private ready messages.                         |
| Codex W 96–146 checks claims and mount generation before deferred detach; H 59–96 bootstraps hidden host without painting | Panel visibility and cleanup                                | Keep unchanged context; do not transplant native Electron/Owl APIs.                                                                                                              |
| Codex A 204650–204902 stages, awaits attachment/viewport, restores outgoing host on cancel                                | Incoming page replacement                                   | Separate pending/displayed identities; native 100ms timeout is not an HTML network budget. Not completed in identity checkpoint.                                                 |
| Codex main M06/M07 rejects late managed navigation/history restoration                                                    | Topic/file changes and retry targeting                      | Late grant cannot post into another topic. Full history restore is not implemented or verified.                                                                                  |
| C named HTML repro, formal component plus API/form substitutes                                                            | Owning `PanelPreview.session.spec.ts`                       | Same-v1 silent update, changed-v2/unknown version controls, different path and late topic grant are covered here.                                                                |

## Test boundary

The owning session suite mounts the real PanelPreview → usePanelPreview → PanelPreviewView chain. API results and HTMLFormElement.submit are substituted. It verifies grant/form intent, frame context retention and stale-operation rejection, not real content-domain navigation, cookies or backend availability.

Before the identity fix, the owning suite with regressions had 20 passes and 2 failures: same-version named HTML acquired a second grant; a topic change without remount did not load the new topic. The changed/unknown version controls passed. Browser input/scroll, runtime readiness, narrow panes, transport cancellation and deployed images are separate outstanding verification work.

## Ownership

Starting main: d322374275756ffc081e5cc41eb0da3695026ee7. Docs PR2300 and its machine40 worktree are untouched. Open PR1413 owns ArtifactVersionPreview/ProjectArtifactView and office rendering; open PR1207 owns documentBytes/fileKind and office rendering. Both share frontend/api.ts; this checkpoint does not edit those paths. Right-hand panel positioning, tabs, scene coupling, source/download/history, content-domain authorization POST and iframe sandbox are retained.
