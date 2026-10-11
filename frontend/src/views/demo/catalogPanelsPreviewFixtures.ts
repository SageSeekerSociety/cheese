/**
 * 「预览面板」那一册的样例数据：一张图、一叠幻灯片、一份 CSV、一栏修订、一栏保存
 * 历史，和几个取数的替身。
 *
 * 单独一份，和 `catalogPanelsFixtures.ts` 同一个理由：那一份已经够长，而这些只被
 * `catalogPanelsPreview.ts` 用。能用真类型的地方就用真类型（`DocumentRevision`、
 * `RoomFileRevision`、`SketchStroke`），预览站里画出来的也就是真的那种样子。
 */
import type { RoomFileEditorSession, RoomFileRevision } from '@/api'
import type { PenStroke, ShapeStroke } from '@/components/panels/preview/designSketch'
import type { DocumentRevisionsBundle } from '@/composables/useDocumentRevisions'
import type { RoomFileHistoryBundle } from '@/composables/useRoomFileHistory'
import type { DocumentRevision } from '@/cx_types'

import { ref } from 'vue'

import { fileHistoryBundle, revisionsBundle, roomFileEditorBundle } from '@/test/panelBundles'

/** 一张 1×1 的透明 PNG：图片预览要一个真的 src，但不需要真去加载。 */
export const PREVIEW_IMAGE_SRC =
  'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='

/** 一份 CSV：读者看到的是摆好的那张表，不是解析过程。 */
export function previewCsvBytes(): ArrayBuffer {
  return new TextEncoder().encode('姓名,分数\n张三,90\n李四,85\n').buffer as ArrayBuffer
}

/** 图上的一条自由画笔。 */
export const SKETCH_PEN: PenStroke = {
  tool: 'pen',
  color: '#E03131',
  width: 4,
  points: [
    { x: 20, y: 20 },
    { x: 80, y: 60 },
    { x: 140, y: 30 },
  ],
}

/** 图上的一块矩形标注（带一句写在上面的字）。 */
export const SKETCH_RECT: ShapeStroke = {
  tool: 'rect',
  color: '#1971C2',
  width: 3,
  region: { x: 60, y: 90, w: 160, h: 80 },
  text: '这块要改',
}

/** 三处待处理的修订。 */
export function revisionsWith(): DocumentRevisionsBundle {
  const revisions: DocumentRevision[] = [
    { number: 1, paragraph: 3, kind: 'replace', added: '60 天', removed: '30 天', author: '芝士', date: '2026-09-30' },
    {
      number: 2,
      paragraph: 5,
      kind: 'insert',
      added: '另加一句说明。',
      removed: '',
      author: '芝士',
      date: '2026-09-30',
    },
    {
      number: 3,
      paragraph: 8,
      kind: 'delete',
      added: '',
      removed: '这段先不要了。',
      author: '芝士',
      date: '2026-09-30',
    },
  ]
  return revisionsBundle({ revisions: ref<DocumentRevision[]>(revisions) })
}

const FILE_ROWS: RoomFileRevision[] = [
  {
    id: 'rev-3',
    path: 'readme.md',
    seq: 3,
    version: 3,
    size: 2048,
    author: 'wang',
    author_kind: 'human',
    source: 'editor',
    note: '改了开头一段',
    editor_key: null,
    created_at: '2026-09-30T02:00:00.000Z',
  },
  {
    id: 'rev-2',
    path: 'readme.md',
    seq: 2,
    version: 2,
    size: 1980,
    author: 'cheese',
    author_kind: 'agent',
    source: 'ai',
    note: '',
    editor_key: null,
    created_at: '2026-09-29T10:00:00.000Z',
  },
]

/** 一栏有过几次保存的保存历史。 */
export function fileHistoryWith(): RoomFileHistoryBundle {
  return fileHistoryBundle({ rows: ref<RoomFileRevision[]>(FILE_ROWS) })
}

/** 一个不能在线编辑的会话（这份格式只让看）。 */
export function fileEditorUnsupported() {
  const session: RoomFileEditorSession = { enabled: false, reason: 'unsupported' }
  return roomFileEditorBundle({ session: ref<RoomFileEditorSession | null>(session) })
}
