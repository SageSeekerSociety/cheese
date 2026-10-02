# Docs paper checkpoint

This checkpoint changes the production topic-document panel, not a separate HTML prototype. It preserves the right-side location, OverviewAuto, task/turn decorations, Markdown source mode, TableKit, incremental remote installation, draft retention and save concurrency checks. It does not implement comment/AI separation, durable replies, version restoration, exact-range proposals or section reconciliation.

## Source to implementation

Claude Desktop 2.16120.0 original resources were extracted separately without modification. The reviewed excerpts are from the supplied readable copies; their line numbers differ between reference packages. The lines below refer specifically to `deep/readable/c05322358-BgaPs5b0.js`.

| Source / symbol | Adopted behavior | Cheese target | Verification |
| --- | --- | --- | --- |
| `ze`, lines 640–672 | Transparent editor, 16px/24px prose, 22/18/16/14px heading hierarchy | `DocSurface.vue` | Actual browser typography/pane checks pending |
| Clarkdown host, lines 2334–2339 | 48rem paper column, 24px narrow and 44px wide horizontal padding | `PanelDocView.vue` | Container query uses the actual pane, not the window; browser checks pending |
| `ze`, lines 680–700 | 32px ordinary-list indentation, task-checkbox behavior remains distinct | `DocSurface.vue` | Existing Markdown corpus and task-list tests |

Cheese-specific adaptations: the topic title remains outside editable Markdown.

## Validation boundaries

The targeted frontend suite covers real TipTap/ProseMirror commands and production PanelDoc integration. Panel tests substitute API responses; they do not establish backend collaboration, websocket delivery, cross-process recovery or deployment. Browser verification and the Chinese actual-page PDF remain outstanding at this checkpoint. Required CI, merge and deployment are separate evidence stages.
