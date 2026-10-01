# Slides / Design source map

## Checkpoint scope

The Slides reader accepts authorized PDF bytes through props and emits page/quote gestures. Office conversion remains with the existing attachment endpoint. That endpoint now returns `X-Cheese-Source-Version` for the same input bytes it converted, with `Cache-Control: no-store`. The frontend keeps those bytes and that fingerprint in one displayed snapshot. OnlyOffice, room draft history, accepted delivery history and original source downloads remain owned by existing shells. No additional iframe or session lifecycle is introduced; this is not a native PPTX editor or recovered Claude Deck.

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

## Integrated callers and displayed identity

1. `PanelPreviewView` routes presentation suffixes to the reader, preserving editor/history/RoomOutputs and the existing quote path. The host owns the title and original download once; the inner toolbar owns thumbnails, page navigation, fit, presentation and whole-page context.
2. `FileBytesPreview` routes presentation bytes through its existing reader boundary; `ArtifactVersionPreview` retains the selected accepted snapshot. PDF/Word and sheet behavior stay with their existing viewers.
3. `lib/documentBytes.ts` atomically captures bytes, source fingerprint and topic/path/task/source/version. Refresh pending or failure retains the displayed snapshot; path/topic/task/source replacement retires it. A late previous response cannot acquire newer metadata.
4. `usePanelPreview` exposes whole-page context only when the displayed snapshot matches the current source and the actual conversion fingerprint. `PanelPreviewView` checks that identity again on both opening and sending the locator. Whole PDF-page text keeps its page and source identity without the quote's 200-character truncation.
5. `PreviewSlides` keeps an already parsed page, sheet and scroll position while upstream conversion is pending or fails. Navigation remains available on those displayed bytes; whole-page asking is unavailable and pending text extraction is retired. Losing or restoring action context does not reset page 1. Replacing actual bytes does reset the reader.

Host/session/transport work remains PreviewS-owned; Docs selection/comment files remain separately owned. This continuation was integrated from S's exact source handoff on `80c9f46ee313e58c5e40b5066f135d0d5b10af84`, followed by the owning test/provider fixes and cached-reader regressions. The attachment response and small `lib/previewPdf.ts` module carry the byte identity contract without growing the oversized API file. Mutable room dependencies are not represented as a complete immutable file snapshot.

## Validation boundaries

`PreviewSlides.spec.ts` owns navigation, focus, presentation, bounded paints, late parse/render/text, original download and cached-reader refresh contracts. The two new refresh regressions failed before the correction and passed after it. PDF.js and ResizeObserver are substituted there; the local browser fixture uses actual PDF.js and synthetic PDF bytes. The owning panel/document/media/revision tests and byte identity tests exercise real containers with their external data seams substituted. Production conversion, authentication, WAN behavior and deployed OnlyOffice remain separate checks. Normal exact-head CI, merge queue and deployment are still required; pushing a checkpoint is not delivery.
