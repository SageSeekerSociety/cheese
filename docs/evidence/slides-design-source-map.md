# Slides / Design source map

## Checkpoint scope

The Slides reader is a props/emits-only PDF reader, not a native PPTX editor or recovered Claude Deck. It accepts already-authorized PDF bytes; Office conversion remains with existing callers. No additional iframe, POST, API, store or router boundary is introduced. OnlyOffice, room draft history, accepted delivery history and original source downloads remain owned by existing shells.

Implementation base: live main `b5f70bd3121fca39b11799c2f2b2612dcc347363`. The reference capsule base `689b2382fca257d6b8ba6416e06a4e2cac222d43` is not claimed to be current main. PR #1413 and #1207 were reference candidates, not merged code and were not copied over main.

## Verified reference capsules

| Capsule | Bytes | SHA256 |
| --- | ---: | --- |
| cheese-slides-design-seams-689b2382.zip | 649913 | f380fb52f8e7bd09b207fbac5a38c465e620db4550d1f342abb6e028d178e2b3 |
| claude-desktop-2.16120.0-deep-reference.zip | 2236176 | ce548ef3dc78a9b105c04e40e163bec2a70b102930f1e27290fb6a87ebfb7f6e |
| codex-desktop-26.928.2636.0-preview-reference.zip | 4722690 | ea5e7b48987d76793bf2b77bd32602c565ec2e0f2fefe1fd905c3e3181896ecb |

SHA256 and ZIP CRC verified before extraction. Original bundles are reference data, not executed code or implementation instructions.

## Actual functions read → Cheese boundary

| Reference | Actual behavior | Adaptation |
| --- | --- | --- |
| CC `c293c7b4d-DQRlCtOM.js`, Drawer return, readable 3130–3312; original SHA 8ea421fa2a476d531ab5c712690066423cdbfc76a4c32504aa8f6f1cd3732d50 | Header separates title/actions/status; content flex-1/min-h-0 | `PreviewSlides.vue` toolbar/body and narrow pane layout, not a fabricated Deck canvas |
| CC `c5b1e5e79-BoaHH4VC.js`, `J.hold/release/evict`, readable 194–280; SHA 67a2c2db8a245c0b8261d85377130583e2f33cabd407e2a2a223914ed3f24ae0 | Same boot identity reused, stale same-instance frame retired, parked pool capped | `useSlidesPdf` keeps parsing separate from paint/resize; `SlideThumbRail` caps mounted thumbnails at eight; presentation changes layout, not bytes |
| CC `frame-shell-Xw_NSXzx.js`, `Kt`, readable 4660–4703; SHA 719bc2529205a01014640f58a3cd05841df3dee8e6279824f1a96294d4220272 | Late handoff validates current generation/version and connected frame | Independent byte generation and cancellable host paint jobs; obsolete parse/text/render cannot publish into the new reader |
| Codex `site-preview-8c609cddd865.js`, `kn/An`, readable 208–250 | Message source window, approved origin, schema/session and annotation URLs validated | Future HTML/app selection handshake belongs to PreviewS; raster coordinates are not source-node authorization |
| CC `shared-16-vTpLIF6k.js`, `yP`, readable 20828–20838; SHA a600a8c2971127fc5235f7a461452cce4a8c81046b00198281474c6fdffa56a7 | Instance content lives in data stores/files, not common type HTML | Whole-page context comes from actual PDF text; no fake slide object schema or native Deck claims |
| CC `ea9…::v`, readable 33–59 | Creation POST validates returned UUID and separates failure status | No new creation API is inferred from the desktop reference |

The B/C/E/M/P/D/I/S12/S16/F/K report and indexed evidence were read. B03 describes type creation; C3 describes attachment selection. Neither supplies a recovered Deck/Canvas editor. Codex Sites/Owl cloud backend is absent; no claim of complete upstream recovery is made.

## Finite integration handoff (owner coordination required)

1. `PanelPreviewView`: import reader; choose it only for PPTX/PPT/ODP (and PDF if explicitly approved). Pass the existing PDF byte result and pending/error/rendererMissing states, not a second converter. Preserve editor/history/RoomOutputs and the existing quote path/200-character normalization.
2. `FileBytesPreview`: same suffix-based branch over its existing byte prop. No fetching in the reader. Keep PDF/Word behavior unless explicitly routed.
3. `ArtifactVersionPreview`: route selected delivered snapshot bytes; retain bare layout, selected accepted version, rendering error/original-download fallback. Do not substitute room live bytes for snapshot bytes.
4. Whole-page action: pass `context {topicId,path,source,taskId,version}`. Consume separate `pageContext {text,page,scope:'page',context}` through the real room locate chain. Label it as whole PDF-page context. Do not use quote's 200-character truncation, invent node coordinates, or drop file identity.

Existing host/session/transport files are PreviewS-owned; Docs selection/comment files and work.json are DocsS-owned. This checkpoint changes only new reader/composable/spec/local namespace files plus namespace registration and this evidence file. Product mounting is unfinished until the coordinated callers land.

## Validation boundaries

`PreviewSlides.spec.ts` owns reader navigation, focus, presentation, bounded thumbnail paints, late parse/render and original-download contracts. It uses the real reader and rail with substituted PDF.js and ResizeObserver. It cannot prove PDF conversion, pixel geometry, production authentication or deployed OnlyOffice behavior. Browser geometry, actual PDF.js fixture rendering and a Chinese review PDF must be added before delivery. Normal RequiredCI/merge queue/deployment remain required; pushing a checkpoint is not delivery.
